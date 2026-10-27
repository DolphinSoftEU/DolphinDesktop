"""Trace engine - per-step screenshot, UIA tree dump, SQLite storage.

Schema version history:
  v1 (initial) - runs + steps tables.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ._logging import _redact, get_logger, redact_value
from ._redaction import _PARAMETER_VALUES, _redact_identifier

_log = get_logger("trace")


def _redact_optional(text: str | None) -> str | None:
    """Redact *text* for storage; ``None`` stays ``None``."""
    if text is None:
        return None
    return _redact(str(text))


# Schema — version-gated so external tooling can detect incompatible files

SCHEMA_VERSION = 1

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id               TEXT PRIMARY KEY,
    test_nodeid      TEXT NOT NULL,
    started_at       REAL NOT NULL,
    finished_at      REAL,
    status           TEXT,          -- 'running' | 'passed' | 'failed' | 'error'
    error_message    TEXT,
    error_traceback  TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT    NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    ts               REAL    NOT NULL,
    action           TEXT    NOT NULL,
    selector         TEXT,
    result           TEXT,           -- 'ok' | 'error'
    error            TEXT,
    screenshot_file  TEXT,           -- filename relative to screenshots/ subdir
    uia_tree         TEXT            -- JSON array of node objects
);
"""

# Module-level current session (set by pytest plugin, one per test)

_current: TraceSession | None = None


def current_session() -> TraceSession | None:
    return _current


def set_current_session(session: TraceSession | None) -> None:
    global _current
    _current = session


# Screenshot


def _capture_screenshot(path: Path) -> None:
    """Write a JPEG of the whole virtual desktop to *path*. Uses mss if available, else PIL.

    Both paths must span every monitor: ``mss.monitors[0]`` already is the virtual
    desktop, and Pillow needs ``all_screens=True`` to match it — without it a window
    on a secondary display is captured as the wrong (or a black) region.
    """
    try:
        import mss  # type: ignore[import-untyped]

        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[0])
            # mss BGRA → PIL RGB → JPEG
            from PIL import Image

            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            img.save(path, "JPEG", quality=75, optimize=True)
        return
    except ImportError:
        pass

    from PIL import ImageGrab

    ImageGrab.grab(all_screens=True).convert("RGB").save(path, "JPEG", quality=75, optimize=True)


# UIA tree


def _dump_uia_tree(
    element: Any,
    max_depth: int = 4,
    max_children: int = 30,
    *,
    include_root: bool = False,
) -> str | None:
    """Return a JSON string representing the subtree rooted at *element*.

    Node names are the controls' current text — an Edit control's ``name``
    can be the value someone typed into it — so every string in the tree is
    redacted before it is serialised.
    """
    try:
        nodes: list[dict[str, Any]] = []
        if include_root:
            info = element.element_info
            nodes.append(
                {
                    "d": 0,
                    "ctrl": str(getattr(info, "control_type", "") or ""),
                    "name": str(getattr(info, "name", "") or ""),
                    "id": str(getattr(info, "automation_id", "") or ""),
                }
            )
        _collect(element, nodes, 1 if include_root else 0, max_depth, max_children)
        return json.dumps(redact_value(nodes))
    except Exception:
        return None


def _collect(
    node: Any,
    out: list[dict[str, Any]],
    depth: int,
    max_depth: int,
    max_children: int,
) -> None:
    if depth > max_depth:
        return
    try:
        children = node.children()
    except Exception:
        return
    for child in children[:max_children]:
        try:
            info = child.element_info
            out.append(
                {
                    "d": depth,
                    "ctrl": str(getattr(info, "control_type", "") or ""),
                    "name": str(getattr(info, "name", "") or ""),
                    "id": str(getattr(info, "automation_id", "") or ""),
                }
            )
            _collect(child, out, depth + 1, max_depth, max_children)
        except Exception:
            continue


# TraceSession


class TraceSession:
    """Records one test run to a local directory (trace.db + screenshots/)."""

    def __init__(
        self,
        test_nodeid: str,
        run_dir: Path,
        mode: str = "on-failure",
    ) -> None:
        if mode not in ("off", "on-failure", "always"):
            raise ValueError(f"Invalid trace mode: {mode!r}")

        self.run_id: str = uuid.uuid4().hex[:12]
        current_values = _PARAMETER_VALUES.get()
        self._parameter_values = current_values if current_values is not None else set()
        self.test_nodeid = self._redact(test_nodeid)
        self.run_dir = run_dir
        self.mode = mode

        self._started_at = time.time()
        self._seq = 0
        self._closed = False
        # True once any write was lost — the stored run is incomplete, not failed
        self.degraded = False
        # Actions may be driven from a worker thread while the session is created on
        # the fixture thread, so the connection is shared explicitly and serialised.
        self._lock = threading.Lock()

        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "screenshots").mkdir(exist_ok=True)

        self._db: sqlite3.Connection = sqlite3.connect(
            str(run_dir / "trace.db"), check_same_thread=False
        )
        self._db.executescript(_DDL)
        if self._db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
            self._db.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        self._db.execute(
            "INSERT INTO runs (id, test_nodeid, started_at, status) VALUES (?, ?, ?, 'running')",
            (self.run_id, self.test_nodeid, self._started_at),
        )
        self._db.commit()

    def _redact(self, text: str) -> str:
        return _redact_identifier(text, self._parameter_values)

    def record_step(
        self,
        action: str,
        selector: str | None,
        element: Any = None,
        error: str | None = None,
    ) -> None:
        """Record one action step.  In *on-failure* mode screenshots are only
        taken when the step produced an error; in *always* mode every step gets
        a screenshot.

        Never raises: tracing is observational, so a storage failure is logged and
        dropped rather than turned into a failure of the action being traced.

        Every text column is redacted at this boundary — the selector and the
        error message are built from user input and exception text, and the
        store outlives the test run.

        Only ``error=None`` means success. An empty error message still marks
        a failed action and is stored with a nonempty diagnostic fallback.
        """
        if self.mode == "off":
            return

        action = self._redact(action)
        selector = self._redact(selector) if selector is not None else None
        failed = error is not None
        error = self._redact(error or "Action failed without an error message") if failed else None

        with self._lock:
            if self._closed:
                return
            self._seq += 1
            seq = self._seq
        ts = time.time() - self._started_at
        result = "error" if failed else "ok"
        action = self._redact(str(action))
        selector = _redact_optional(selector)
        error = _redact_optional(error)

        screenshot_file: str | None = None
        capture_shot = self.mode == "always" or failed
        if capture_shot:
            try:
                fname = f"step_{seq:04d}.jpg"
                _capture_screenshot(self.run_dir / "screenshots" / fname)
                screenshot_file = fname
            except Exception:
                pass

        uia_tree: str | None = None
        if element is not None:
            if failed:
                uia_tree = _dump_uia_tree(element)
            elif self.mode == "always":
                uia_tree = _dump_uia_tree(element, include_root=True)
        if uia_tree is not None:
            uia_tree = self._redact(uia_tree)

        try:
            with self._lock:
                if self._closed:
                    # The session was finished while this step was being captured.
                    # A shutdown race is not a storage failure: marking the run
                    # degraded would report a healthy run as incomplete.
                    return
                self._db.execute(
                    """INSERT INTO steps
                       (run_id, seq, ts, action, selector, result, error, screenshot_file,
                        uia_tree)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        self.run_id,
                        seq,
                        ts,
                        action,
                        selector,
                        result,
                        error,
                        screenshot_file,
                        uia_tree,
                    ),
                )
                self._db.commit()
        except Exception as exc:
            self.degraded = True
            _log.warning("trace step %d (%s) not recorded: %s", seq, action, exc)

    def finish(
        self,
        status: str,
        error_message: str | None = None,
        error_traceback: str | None = None,
    ) -> None:
        """Finalise the run record.  Discards the run dir on pass in on-failure mode.

        Never raises — see :meth:`record_step`.
        """
        error_message = self._redact(error_message) if error_message is not None else None
        error_traceback = self._redact(error_traceback) if error_traceback is not None else None
        with self._lock:
            if self._closed:
                return
            self._closed = True

        error_message = _redact_optional(error_message)
        error_traceback = _redact_optional(error_traceback)
        try:
            with self._lock:
                self._db.execute(
                    """UPDATE runs
                       SET finished_at=?, status=?, error_message=?, error_traceback=?
                       WHERE id=?""",
                    (time.time(), status, error_message, error_traceback, self.run_id),
                )
                self._db.commit()
        except Exception as exc:
            self.degraded = True
            _log.warning(
                "trace run %s stays at status='running' — final status %r not written: %s",
                self.run_id,
                status,
                exc,
            )
        # Worker threads record steps under _lock on this same connection, so closing
        # it outside the lock is unserialised sqlite access.
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass

        if self.mode == "on-failure" and status == "passed":
            shutil.rmtree(self.run_dir, ignore_errors=True)

    def close_without_finish(self) -> None:
        """Close the DB connection without writing a final status (e.g. on error)."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._db.close()
            except Exception:
                pass


# HTML viewer

_LATEST_RUN_SQL = "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT 1"

_TRACE_READ_SQL = {
    "schema_version": ("PRAGMA table_xinfo(schema_version)", "SELECT * FROM schema_version"),
    "runs": ("PRAGMA table_xinfo(runs)", "SELECT * FROM runs"),
    "steps": ("PRAGMA table_xinfo(steps)", "SELECT * FROM steps"),
}


_TRACE_COLUMNS = {
    "schema_version": {"version": "INTEGER"},
    "runs": {
        "id": "TEXT",
        "test_nodeid": "TEXT",
        "started_at": "REAL",
        "finished_at": "REAL",
        "status": "TEXT",
        "error_message": "TEXT",
        "error_traceback": "TEXT",
    },
    "steps": {
        "id": "INTEGER",
        "run_id": "TEXT",
        "seq": "INTEGER",
        "ts": "REAL",
        "action": "TEXT",
        "selector": "TEXT",
        "result": "TEXT",
        "error": "TEXT",
        "screenshot_file": "TEXT",
        "uia_tree": "TEXT",
    },
}
_NULLABLE_COLUMNS = {
    "runs": {"finished_at", "status", "error_message", "error_traceback"},
    "steps": {"selector", "result", "error", "screenshot_file", "uia_tree"},
}


def _validate_trace_db(db: sqlite3.Connection) -> None:
    """Validate schema and actual SQLite values before querying report data."""
    for table, columns in _TRACE_COLUMNS.items():
        kind = db.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
        if kind is None or kind[0] != "table":
            raise ValueError(f"Invalid trace.db: missing table {table}")
        metadata_sql, rows_sql = _TRACE_READ_SQL[table]
        metadata = list(db.execute(metadata_sql))
        declared = {r[1]: r[2].upper() for r in metadata}
        if declared != columns:
            raise ValueError(f"Invalid trace.db: incompatible schema for {table}")
        for column in metadata:
            name = column[1]
            required = name not in _NULLABLE_COLUMNS.get(table, set()) and name != "id"
            primary_key = table != "schema_version" and name == "id"
            if column[3] != int(required) or column[5] != int(primary_key) or column[6] != 0:
                raise ValueError(f"Invalid trace.db: incompatible constraints for {table}.{name}")
        if table == "steps":
            foreign_keys = list(db.execute("PRAGMA foreign_key_list(steps)"))
            if len(foreign_keys) != 1 or tuple(foreign_keys[0][2:5]) != ("runs", "run_id", "id"):
                raise ValueError("Invalid trace.db: incompatible steps foreign key")
        for row in db.execute(rows_sql):
            for name, sql_type in columns.items():
                value = row[name]
                if value is None and name in _NULLABLE_COLUMNS.get(table, set()):
                    continue
                if sql_type == "TEXT":
                    valid = isinstance(value, str)
                elif sql_type == "INTEGER":
                    valid = type(value) is int
                else:
                    valid = isinstance(value, (int, float)) and math.isfinite(value)
                if not valid:
                    raise ValueError(f"Invalid trace.db: invalid value for {table}.{name}")
                if name in {"seq", "ts"} and value < 0:
                    raise ValueError(f"Invalid trace.db: negative value for {table}.{name}")
                if table == "runs" and name in {"started_at", "finished_at"}:
                    import datetime

                    try:
                        datetime.datetime.fromtimestamp(value)
                    except (ValueError, OverflowError, OSError):
                        raise ValueError(f"Invalid trace.db: invalid timestamp {name}") from None
    versions = [r[0] for r in db.execute("SELECT version FROM schema_version")]
    if versions != [SCHEMA_VERSION]:
        raise ValueError("Invalid trace.db: unsupported schema version")


def _screenshot_url(filename: Any, screenshot_dir: Path | None) -> str | None:
    """Allow only a single filename, encoded as a URL path component."""
    if not isinstance(filename, str) or not filename or filename in {".", ".."}:
        return None
    # The writer emits flat filenames. Reject both OS separators, drive/ADS
    # syntax and control characters rather than relying on browser URL cleanup.
    if any(c in filename for c in "/\\:") or any(ord(c) < 32 for c in filename):
        return None
    if filename.endswith((".", " ")):
        return None
    if screenshot_dir is not None:
        try:
            root = screenshot_dir.resolve()
            if root != screenshot_dir.parent.resolve() / "screenshots":
                return None
            if not (screenshot_dir / filename).resolve().is_relative_to(root):
                return None
        except (OSError, RuntimeError, ValueError):
            return None
    return "screenshots/" + quote(filename, safe="")


def generate_html(run_dir: Path) -> Path:
    """Read trace.db in *run_dir*, write trace.html, and return its path.

    A trace.db normally holds exactly one run; if an older writer left more than
    one behind, the most recently started run wins.

    Raises:
        FileNotFoundError: if *run_dir* holds no ``trace.db``.
        ValueError: if the database schema or stored values are invalid.
    """
    db_path = run_dir / "trace.db"
    # sqlite3.connect() creates the file it cannot open, so an unchecked connect
    # would leave an empty trace.db behind in the caller's tree.
    if not db_path.is_file():
        raise FileNotFoundError(f"No trace data found in {run_dir}")
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        _validate_trace_db(db)
        run_row = db.execute(_LATEST_RUN_SQL).fetchone()
        if run_row is None:
            raise FileNotFoundError(f"No trace data found in {run_dir}")
        run = dict(run_row)
        steps = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM steps WHERE run_id=? ORDER BY seq", (run["id"],)
            ).fetchall()
        ]
    except sqlite3.DatabaseError:
        raise ValueError("Invalid trace.db: unreadable or incompatible database") from None
    finally:
        db.close()

    html = _render_html(run, steps, run_dir / "screenshots")
    out = run_dir / "trace.html"
    out.write_text(html, encoding="utf-8")
    return out


def _render_html(
    run: dict[str, Any], steps: list[dict[str, Any]], screenshot_dir: Path | None = None
) -> str:
    import datetime
    import html as _h

    status = run.get("status") or "unknown"
    nodeid = _h.escape(run.get("test_nodeid", ""))
    started = datetime.datetime.fromtimestamp(run["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
    duration = f"{run['finished_at'] - run['started_at']:.2f}s" if run.get("finished_at") else "—"
    # A run whose finish() write was lost stays at 'running'; rendering that as FAIL
    # would report a passing test as failed, so only a real verdict gets a verdict badge.
    badge_cls = {"passed": "pass", "failed": "fail", "error": "fail"}.get(status, "warn")

    error_block = ""
    if run.get("error_message"):
        error_block = (
            '<div class="err-box"><pre>' + _h.escape(str(run["error_message"])) + "</pre></div>"
        )

    steps_html = _render_steps(steps, screenshot_dir)

    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Dolphin Trace: {nodeid}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;padding:16px;font-size:13px}}
h1{{font-size:14px;color:#8b949e;margin-bottom:10px}}
.summary{{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 14px;margin-bottom:12px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}}
.badge{{padding:2px 9px;border-radius:10px;font-size:11px;font-weight:700;text-transform:uppercase}}
.badge.pass{{background:#1a4731;color:#3fb950}}
.badge.fail{{background:#490202;color:#f85149}}
.badge.warn{{background:#3d2c05;color:#d29922}}
.meta{{color:#8b949e;font-size:12px}}
.err-box{{background:#160b0b;border-left:3px solid #f85149;padding:8px 10px;margin-bottom:12px;border-radius:3px;overflow-x:auto}}
.err-box pre{{white-space:pre-wrap;color:#ffa198;font-size:11px}}
.step{{background:#161b22;border:1px solid #21262d;border-radius:5px;margin-bottom:5px;overflow:hidden}}
.step-hd{{display:flex;gap:8px;align-items:center;padding:6px 10px;cursor:pointer;user-select:none}}
.step-hd:hover{{background:#1c2128}}
.seq{{color:#484f58;font-size:11px;width:28px;text-align:right;flex-shrink:0}}
.act{{color:#58a6ff;font-weight:600;font-size:12px;min-width:80px}}
.sel{{color:#8b949e;font-size:11px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.ts{{color:#484f58;font-size:11px;flex-shrink:0}}
.ok{{color:#3fb950;font-size:11px;flex-shrink:0}}
.er{{color:#f85149;font-size:11px;flex-shrink:0}}
.step-bd{{padding:0 10px 10px;display:none}}
.step-bd.open{{display:block}}
.step-bd img{{max-width:100%;border-radius:3px;margin-top:6px;border:1px solid #30363d}}
.step-err{{color:#ffa198;font-size:11px;background:#1a0505;padding:5px 7px;border-radius:3px;margin-top:6px;white-space:pre-wrap}}
.uia details{{margin-top:6px}}
.uia summary{{cursor:pointer;color:#58a6ff;font-size:11px;padding:3px 0}}
.uia pre{{font-size:11px;color:#8b949e;overflow:auto;max-height:260px;margin-top:4px;background:#0d1117;padding:6px;border-radius:3px;white-space:pre;border:1px solid #21262d}}
</style>
</head>
<body>
<h1>Dolphin Trace Viewer</h1>
<div class="summary">
  <span class="badge {badge_cls}">{_h.escape(status)}</span>
  <span class="meta"><b>{nodeid}</b></span>
  <span class="meta">{started}</span>
  <span class="meta">{duration} &bull; {len(steps)} steps</span>
</div>
{error_block}
<div id="tl">{steps_html}</div>
<script>
document.querySelectorAll('.step-hd').forEach(h=>{{
  h.addEventListener('click',()=>h.nextElementSibling.classList.toggle('open'));
}});
// auto-open last failing step
const errs=document.querySelectorAll('.er');
if(errs.length)errs[errs.length-1].closest('.step').querySelector('.step-bd').classList.add('open');
</script>
</body>
</html>"""
    # Hash only the fixed viewer script; event-handler payloads and arbitrary
    # injected scripts remain blocked while expanding steps still works.
    script = document.split("<script>", 1)[1].split("</script>", 1)[0]
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    policy = (
        "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{digest}'; base-uri 'none'; form-action 'none'; object-src 'none'"
    )
    return document.replace(
        '<meta charset="utf-8">',
        f'<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="{policy}">',
        1,
    )


def _render_steps(steps: list[dict[str, Any]], screenshot_dir: Path | None = None) -> str:
    import html as _h

    parts: list[str] = []
    for s in steps:
        # trace.db is a persisted artifact — a crafted/corrupted row must not
        # turn into HTML/JS via the un-typed 'seq' column (CWE-79).
        seq: int | str
        try:
            seq = int(s["seq"])
        except (TypeError, ValueError, OverflowError):
            seq = "?"
        act = _h.escape(s["action"])
        sel = _h.escape(s.get("selector") or "")
        ts_str = f"{s.get('ts', 0):.3f}s"
        is_err = s.get("result") == "error"
        status_cls = "er" if is_err else "ok"
        status_txt = "err" if is_err else "ok"

        img_html = ""
        rel = _screenshot_url(s.get("screenshot_file"), screenshot_dir)
        if rel is not None:
            rel = _h.escape(rel, quote=True)
            img_html = f'<img src="{rel}" alt="step {seq}" loading="lazy">'

        err_html = ""
        if s.get("error"):
            err_html = f'<div class="step-err">{_h.escape(s["error"])}</div>'

        uia_html = ""
        if s.get("uia_tree"):
            try:
                nodes: list[dict[str, Any]] = json.loads(s["uia_tree"])
                tree_txt = _nodes_to_text(nodes)
                count: int | str = len(nodes)
            except Exception:
                tree_txt = s["uia_tree"]
                count = "?"
            uia_html = (
                f'<div class="uia"><details><summary>UIA tree ({count} nodes)'
                f"</summary><pre>{_h.escape(tree_txt)}</pre></details></div>"
            )

        body = img_html + err_html + uia_html
        parts.append(
            f'<div class="step">'
            f'<div class="step-hd">'
            f'<span class="seq">#{seq}</span>'
            f'<span class="act">{act}</span>'
            f'<span class="sel">{sel}</span>'
            f'<span class="{status_cls}">{status_txt}</span>'
            f'<span class="ts">{ts_str}</span>'
            f"</div>"
            f'<div class="step-bd">{body}</div>'
            f"</div>"
        )
    return "\n".join(parts)


_MAX_UIA_DISPLAY_DEPTH = 32


def _nodes_to_text(nodes: list[dict[str, Any]]) -> str:
    if not isinstance(nodes, list):
        raise ValueError("UIA tree must be a list of nodes")
    lines: list[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            raise ValueError("UIA tree nodes must be objects")
        depth = n.get("d", 0)
        if type(depth) is not int:
            raise ValueError("UIA tree depth must be an integer")
        depth = max(0, min(depth, _MAX_UIA_DISPLAY_DEPTH))
        ctrl = n.get("ctrl", "?")
        name = n.get("name", "")
        auto_id = n.get("id", "")
        indent = "  " * depth
        parts = [ctrl or "?"]
        if name:
            parts.append(f"name={name!r}")
        if auto_id:
            parts.append(f"id={auto_id!r}")
        lines.append(f"{indent}{' '.join(parts)}")
    return "\n".join(lines)


# Helpers used by the CLI to list / locate runs


def list_runs(trace_dir: Path) -> list[dict[str, Any]]:
    """Return a list of run metadata dicts from all runs under *trace_dir*."""
    results: list[dict[str, Any]] = []
    if not trace_dir.is_dir():
        return results
    for run_dir in trace_dir.iterdir():
        db_path = run_dir / "trace.db"
        if not db_path.is_file():
            continue
        try:
            # ``closing`` rather than a bare close() after the query: the
            # except below swallows a corrupt or locked trace.db to keep
            # scanning the other runs, and on that path the close() never
            # ran — leaking the connection for the rest of the process.
            with closing(sqlite3.connect(str(db_path))) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(_LATEST_RUN_SQL).fetchone()
            if row:
                d = dict(row)
                d["run_dir"] = str(run_dir)
                results.append(d)
        except Exception:
            continue
    # Directory mtime changes when the viewer or another artifact is written;
    # the trace's persisted start time is the stable source of truth for --last.
    results.sort(
        key=lambda run: (run.get("started_at", 0), run.get("id", "")),
        reverse=True,
    )
    return results
