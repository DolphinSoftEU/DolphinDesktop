"""Trace engine - per-step screenshot, UIA tree dump, SQLite storage.

Schema version history:
  v1 (initial) - runs + steps tables.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from ._logging import _redact, get_logger

_log = get_logger("trace")

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


def _dump_uia_tree(element: Any, max_depth: int = 4, max_children: int = 30) -> str | None:
    """Return a JSON string representing the subtree rooted at *element*."""
    try:
        nodes: list[dict[str, Any]] = []
        _collect(element, nodes, 0, max_depth, max_children)
        return json.dumps(nodes)
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
        self.test_nodeid = _redact(test_nodeid)
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
        """
        if self.mode == "off":
            return

        action = _redact(action)
        selector = _redact(selector) if selector is not None else None
        error = _redact(error) if error is not None else None

        with self._lock:
            if self._closed:
                return
            self._seq += 1
            seq = self._seq
        ts = time.time() - self._started_at
        result = "error" if error else "ok"

        screenshot_file: str | None = None
        capture_shot = self.mode == "always" or bool(error)
        if capture_shot:
            try:
                fname = f"step_{seq:04d}.jpg"
                _capture_screenshot(self.run_dir / "screenshots" / fname)
                screenshot_file = fname
            except Exception:
                pass

        uia_tree: str | None = None
        if bool(error) and element is not None:
            uia_tree = _dump_uia_tree(element)

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
        error_message = (
            _redact(error_message) if error_message is not None else None
        )
        error_traceback = (
            _redact(error_traceback) if error_traceback is not None else None
        )
        with self._lock:
            if self._closed:
                return
            self._closed = True

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


def generate_html(run_dir: Path) -> Path:
    """Read trace.db in *run_dir*, write trace.html, and return its path.

    A trace.db normally holds exactly one run; if an older writer left more than
    one behind, the most recently started run wins.

    Raises:
        FileNotFoundError: if *run_dir* holds no ``trace.db``.
    """
    db_path = run_dir / "trace.db"
    # sqlite3.connect() creates the file it cannot open, so an unchecked connect
    # would leave an empty trace.db behind in the caller's tree.
    if not db_path.is_file():
        raise FileNotFoundError(f"No trace data found in {run_dir}")
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    try:
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
    finally:
        db.close()

    html = _render_html(run, steps)
    out = run_dir / "trace.html"
    out.write_text(html, encoding="utf-8")
    return out


def _render_html(run: dict[str, Any], steps: list[dict[str, Any]]) -> str:
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

    steps_html = _render_steps(steps)

    return f"""<!DOCTYPE html>
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


def _render_steps(steps: list[dict[str, Any]]) -> str:
    import html as _h

    parts: list[str] = []
    for s in steps:
        seq = s["seq"]
        act = _h.escape(s["action"])
        sel = _h.escape(s.get("selector") or "")
        ts_str = f"{s.get('ts', 0):.3f}s"
        is_err = s.get("result") == "error"
        status_cls = "er" if is_err else "ok"
        status_txt = "err" if is_err else "ok"

        img_html = ""
        if s.get("screenshot_file"):
            rel = f"screenshots/{_h.escape(s['screenshot_file'])}"
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


def _nodes_to_text(nodes: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for n in nodes:
        depth = n.get("d", 0)
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
