"""Tests for the trace engine's failure isolation and run selection."""


from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from dolphin_desktop import _trace


class _FailingConnection:
    """Stands in for a sqlite connection whose every operation fails."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def execute(self, *args, **kwargs):
        raise self._exc

    def commit(self):
        raise self._exc

    def close(self):
        raise self._exc


def _install_failing_db(session, exc: Exception) -> None:
    """Swap a session's live connection for one whose every call fails.

    The real connection is closed first. Dropping the last reference to an
    open ``sqlite3.Connection`` leaks it until the garbage collector runs,
    which surfaces as a ``ResourceWarning`` charged to whatever unrelated
    test happens to be running when that collection happens.
    """
    session._db.close()
    session._db = _FailingConnection(exc)


# Storage failures must never reach the traced action


class TestRecordStepIsNonFatal:
    def _session(self, tmp_path) -> _trace.TraceSession:
        return _trace.TraceSession(
            test_nodeid="tests/t.py::test_x", run_dir=tmp_path / "run", mode="on-failure"
        )

    def test_records_normally(self, tmp_path):
        session = self._session(tmp_path)
        session.record_step("click", "{'auto_id': 'ok'}")
        session.finish("failed")
        db = sqlite3.connect(str(tmp_path / "run" / "trace.db"))
        assert db.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 1
        db.close()

    def test_closed_connection_does_not_raise(self, tmp_path):
        session = self._session(tmp_path)
        session._db.close()
        session.record_step("click", "{'auto_id': 'ok'}")
        # The lost write stays contained in storage: the session is not closed by
        # it and still accepts the steps that follow.
        assert session._closed is False
        session.record_step("type_text", "{'auto_id': 'edit'}")
        assert session._seq == 2

    @pytest.mark.parametrize(
        "exc",
        [
            sqlite3.OperationalError("database or disk is full"),
            sqlite3.OperationalError("database is locked"),
            sqlite3.ProgrammingError("SQLite objects created in a thread can only be used"),
        ],
        ids=["disk-full", "locked", "cross-thread"],
    )
    def test_storage_error_does_not_raise(self, tmp_path, exc):
        session = self._session(tmp_path)
        _install_failing_db(session, exc)
        session.record_step("type_text", "{'auto_id': 'edit'}")
        # Every sqlite failure mode is swallowed the same way: the step is lost,
        # the run is flagged, and the session stays open for the next action.
        assert session.degraded is True
        assert session._closed is False

    def test_error_is_logged_above_the_default_level(self, tmp_path, caplog):
        """At DEBUG the message sits below the plugin's own INFO default — invisible."""
        session = self._session(tmp_path)
        session._db.close()
        with caplog.at_level("INFO", logger="dolphin_desktop.trace"):
            session.record_step("click", "{'auto_id': 'ok'}")
        assert "not recorded" in caplog.text
        assert caplog.records[-1].levelname == "WARNING"

    def test_lost_step_marks_the_session_degraded(self, tmp_path):
        session = self._session(tmp_path)
        session._db.close()
        session.record_step("click", "{'auto_id': 'ok'}")
        assert session.degraded is True

    def test_finish_error_does_not_raise(self, tmp_path):
        session = self._session(tmp_path)
        _install_failing_db(session, sqlite3.OperationalError("database is locked"))
        session.finish("failed", error_message="boom")
        # The status write and the close both failed; the session is still
        # finished, flagged degraded, and a failed run keeps its directory.
        assert session._closed is True
        assert session.degraded is True
        assert session.run_dir.is_dir()

    def test_recording_onto_a_closed_db_does_not_raise(self, tmp_path):
        """A dead DB handle must not turn tracing into a test failure.

        The connection is closed underneath the session; both ``record_step``
        and ``finish`` have to swallow the resulting error and record the loss,
        and the pass is still cleaned up as one.
        """
        session = self._session(tmp_path)
        session._db.close()
        session.record_step("click", None)
        session.finish("passed")
        assert session.degraded is True
        assert not (tmp_path / "run").exists()

    def test_lost_finish_is_logged_and_flagged(self, tmp_path, caplog):
        session = self._session(tmp_path)
        _install_failing_db(session, sqlite3.OperationalError("database is locked"))
        with caplog.at_level("INFO", logger="dolphin_desktop.trace"):
            session.finish("passed")
        assert session.degraded is True
        assert caplog.records[0].levelname == "WARNING"


# A lost final status must not be rendered as a verdict


class TestDegradedRunRendering:
    def _run(self, status: str) -> dict:
        return {
            "test_nodeid": "tests/t.py::test_x",
            "started_at": 100.0,
            "finished_at": 101.0,
            "status": status,
        }

    def test_unfinalised_run_is_not_badged_as_a_failure(self, tmp_path):
        """finish() failed, so the row still says 'running' — the test itself passed."""
        html = _trace._render_html(self._run("running"), [])
        assert 'class="badge fail"' not in html
        assert 'class="badge warn"' in html

    @pytest.mark.parametrize("status", ["failed", "error"])
    def test_real_verdicts_still_badge_as_failures(self, status):
        assert 'class="badge fail"' in _trace._render_html(self._run(status), [])

    def test_passed_still_badges_as_a_pass(self):
        assert 'class="badge pass"' in _trace._render_html(self._run("passed"), [])


# The shared connection must only ever be touched under the lock


class TestConnectionCloseIsSerialised:
    class _LockAssertingConnection:
        """Fails if closed while the session lock is not held by the closer."""

        def __init__(self, lock) -> None:
            self._lock = lock
            self.closed_under_lock: bool | None = None

        def execute(self, *args, **kwargs):
            return None

        def commit(self):
            return None

        def close(self):
            self.closed_under_lock = self._lock.locked()

    def test_finish_closes_under_the_lock(self, tmp_path):
        session = _trace.TraceSession("tests/t.py::test_x", tmp_path / "run", mode="always")
        session._db.close()
        session._db = self._LockAssertingConnection(session._lock)
        session.finish("failed")
        assert session._db.closed_under_lock is True

    def test_close_without_finish_closes_under_the_lock(self, tmp_path):
        session = _trace.TraceSession("tests/t.py::test_x", tmp_path / "run", mode="always")
        session._db.close()
        session._db = self._LockAssertingConnection(session._lock)
        session.close_without_finish()
        assert session._db.closed_under_lock is True

    def test_close_without_finish_is_idempotent(self, tmp_path):
        session = _trace.TraceSession("tests/t.py::test_x", tmp_path / "run", mode="always")
        session.close_without_finish()
        # A connection installed after the first close must never be touched:
        # the second call has to return on the _closed flag alone.
        session._db = self._LockAssertingConnection(session._lock)
        session.close_without_finish()
        assert session._db.closed_under_lock is None
        assert session._closed is True


# A trace screenshot must show the monitor the failure happened on


class TestCaptureScreenshotSpansEveryMonitor:
    def test_mss_path_grabs_the_virtual_desktop(self, tmp_path, monkeypatch):
        """mss.monitors[0] is the whole virtual desktop; [1] would be the primary only."""
        grabbed: dict = {}
        all_monitors: list[dict] = [{"virtual": True}, {"primary": True}]

        class _Raw:
            size = (2, 1)
            bgra = b"pixels"

        class _FakeSct:
            monitors: ClassVar[list[dict]] = all_monitors

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def grab(self, monitor):
                grabbed["monitor"] = monitor
                return _Raw()

        class _FakeImage:
            def save(self, path, *args, **kwargs):
                grabbed["save"] = (path, args, kwargs)

        from PIL import Image

        monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=_FakeSct))
        monkeypatch.setattr(Image, "frombytes", lambda *args: _FakeImage())
        _trace._capture_screenshot(tmp_path / "shot.jpg")
        assert grabbed["monitor"] is all_monitors[0]
        assert grabbed["save"] == (
            tmp_path / "shot.jpg",
            ("JPEG",),
            {"quality": 75, "optimize": True},
        )

    def test_pil_fallback_spans_every_monitor(self, tmp_path, monkeypatch):
        import builtins

        from PIL import ImageGrab

        real_import = builtins.__import__

        def _no_mss(name, *args, **kwargs):
            if name == "mss":
                raise ImportError("no mss")
            return real_import(name, *args, **kwargs)

        seen: dict = {}

        class _FakeImage:
            def convert(self, mode):
                return self

            def save(self, path, *args, **kwargs):
                path.write_bytes(b"jpg")

        def _grab(**kwargs):
            seen.update(kwargs)
            return _FakeImage()

        monkeypatch.setattr(builtins, "__import__", _no_mss)
        monkeypatch.setattr(ImageGrab, "grab", _grab)
        _trace._capture_screenshot(tmp_path / "shot.jpg")
        assert seen["all_screens"] is True


# Run selection must not depend on physical row order


class TestRunSelection:
    def _db_with_two_runs(self, run_dir):
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "screenshots").mkdir(exist_ok=True)
        db = sqlite3.connect(str(run_dir / "trace.db"))
        db.executescript(_trace._DDL)
        db.execute("INSERT INTO schema_version VALUES (?)", (_trace.SCHEMA_VERSION,))
        db.execute(
            "INSERT INTO runs (id, test_nodeid, started_at, finished_at, status) "
            "VALUES ('older', 'tests/t.py::test_x', 100.0, 101.0, 'passed')"
        )
        db.execute(
            "INSERT INTO runs (id, test_nodeid, started_at, finished_at, status) "
            "VALUES ('newer', 'tests/t.py::test_x', 200.0, 201.0, 'failed')"
        )
        db.commit()
        db.close()

    def test_generate_html_picks_the_latest_run(self, tmp_path):
        run_dir = tmp_path / "run"
        self._db_with_two_runs(run_dir)
        html = _trace.generate_html(run_dir).read_text(encoding="utf-8")
        assert "failed" in html
        assert ">passed<" not in html

    def test_list_runs_picks_the_latest_run(self, tmp_path):
        self._db_with_two_runs(tmp_path / "run")
        runs = _trace.list_runs(tmp_path)
        assert [r["id"] for r in runs] == ["newer"]

    def test_generate_html_on_empty_db_raises(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        db = sqlite3.connect(str(run_dir / "trace.db"))
        db.executescript(_trace._DDL)
        db.close()
        with pytest.raises(FileNotFoundError):
            _trace.generate_html(run_dir)


# A directory without a trace.db is a user error, not a place to write one


class TestMissingDatabase:
    def test_a_directory_without_a_db_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _trace.generate_html(tmp_path)

    def test_no_database_is_created_in_the_users_tree(self, tmp_path):
        """sqlite3.connect() creates the file it cannot open."""
        with pytest.raises(FileNotFoundError):
            _trace.generate_html(tmp_path)
        assert list(tmp_path.iterdir()) == []


# A step recorded while the session is closing is a shutdown race, not a storage failure


class TestCloseRace:
    def _session(self, tmp_path) -> _trace.TraceSession:
        return _trace.TraceSession("tests/t.py::test_x", tmp_path / "run", mode="always")

    def test_a_step_after_finish_does_not_degrade_the_run(self, tmp_path):
        session = self._session(tmp_path)
        session.finish("passed")
        session.record_step("click", "{'auto_id': 'ok'}")
        assert session.degraded is False

    def test_a_step_closed_mid_capture_does_not_degrade_the_run(self, tmp_path, monkeypatch):
        """The session is closed between the sequence bump and the insert."""
        session = self._session(tmp_path)

        def _capture(path):
            session.finish("passed")
            path.write_bytes(b"jpg")

        monkeypatch.setattr(_trace, "_capture_screenshot", _capture)
        session.record_step("click", "{'auto_id': 'ok'}")
        assert session.degraded is False

    def test_a_real_storage_failure_still_degrades_the_run(self, tmp_path):
        session = self._session(tmp_path)
        _install_failing_db(session, sqlite3.OperationalError("disk I/O error"))
        session.record_step("click", "{'auto_id': 'ok'}")
        assert session.degraded is True


def test_current_session_can_be_set_and_cleared() -> None:
    marker = object()
    _trace.set_current_session(marker)  # type: ignore[arg-type]
    assert _trace.current_session() is marker
    _trace.set_current_session(None)
    assert _trace.current_session() is None


class TestUiATreeDump:
    class _Info:
        def __init__(self, control_type="", name="", automation_id="") -> None:
            self.control_type = control_type
            self.name = name
            self.automation_id = automation_id

    class _Node:
        def __init__(
            self, info=None, children=None, children_error: Exception | None = None
        ) -> None:
            self.element_info = info
            self._children = children or []
            self._children_error = children_error

        def children(self):
            if self._children_error is not None:
                raise self._children_error
            return self._children

    class _BrokenInfoNode:
        @property
        def element_info(self):
            raise RuntimeError("element disappeared")

    def test_collects_nested_nodes_with_limits_and_skips_bad_children(self):
        leaf = self._Node(self._Info(None, "", None))
        nested = self._Node(self._Info("Button", "Save", "save"), [leaf])
        bad_children = self._Node(self._Info("Text", "status", ""), children_error=RuntimeError())
        root = self._Node(
            children=[
                nested,
                self._BrokenInfoNode(),
                bad_children,
                self._Node(self._Info("ignored")),
            ]
        )

        dumped = _trace._dump_uia_tree(root, max_depth=1, max_children=3)

        assert dumped is not None
        assert json.loads(dumped) == [
            {"d": 0, "ctrl": "Button", "name": "Save", "id": "save"},
            {"d": 1, "ctrl": "", "name": "", "id": ""},
            {"d": 0, "ctrl": "Text", "name": "status", "id": ""},
        ]

    def test_collect_returns_when_the_root_cannot_enumerate_children(self):
        root = self._Node(children_error=RuntimeError("no longer available"))

        assert _trace._dump_uia_tree(root) == "[]"

    def test_dump_returns_none_when_serialisation_fails(self, monkeypatch):
        monkeypatch.setattr(_trace, "_collect", lambda *args: (_ for _ in ()).throw(RuntimeError()))

        assert _trace._dump_uia_tree(self._Node()) is None


class TestTraceSessionBranches:
    def test_invalid_mode_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid trace mode"):
            _trace.TraceSession("tests/t.py::test_x", tmp_path / "run", mode="sometimes")

    def test_off_mode_does_not_record_steps(self, tmp_path):
        session = _trace.TraceSession("tests/t.py::test_x", tmp_path / "run", mode="off")
        session.record_step("click", "selector")
        session.finish("failed")

        db = sqlite3.connect(str(tmp_path / "run" / "trace.db"))
        assert db.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 0
        db.close()

    def test_existing_schema_is_reused(self, tmp_path):
        run_dir = tmp_path / "run"
        first = _trace.TraceSession("tests/t.py::first", run_dir)
        second = _trace.TraceSession("tests/t.py::second", run_dir)
        first.finish("failed")
        second.finish("failed")

        db = sqlite3.connect(str(run_dir / "trace.db"))
        assert db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
        db.close()

    def test_successful_screenshot_and_uia_tree_are_stored(self, tmp_path, monkeypatch):
        session = _trace.TraceSession("tests/t.py::test_x", tmp_path / "run", mode="always")
        captured: list = []

        def capture(path):
            captured.append(path)
            path.write_bytes(b"jpeg")

        monkeypatch.setattr(_trace, "_capture_screenshot", capture)
        monkeypatch.setattr(_trace, "_dump_uia_tree", lambda element: '[{"ctrl": "Button"}]')
        session.record_step("click", "<save>", element=object(), error="failed")

        db = sqlite3.connect(str(session.run_dir / "trace.db"))
        row = db.execute(
            "SELECT seq, action, selector, result, error, screenshot_file, uia_tree FROM steps"
        ).fetchone()
        db.close()
        session.close_without_finish()

        assert captured == [session.run_dir / "screenshots" / "step_0001.jpg"]
        assert row == (
            1,
            "click",
            "<save>",
            "error",
            "failed",
            "step_0001.jpg",
            '[{"ctrl": "Button"}]',
        )

    def test_screenshot_failure_is_dropped(self, tmp_path, monkeypatch):
        session = _trace.TraceSession("tests/t.py::test_x", tmp_path / "run", mode="always")

        def fail_capture(path):
            raise OSError()

        monkeypatch.setattr(_trace, "_capture_screenshot", fail_capture)
        session.record_step("click", None)

        db = sqlite3.connect(str(session.run_dir / "trace.db"))
        assert db.execute("SELECT screenshot_file FROM steps").fetchone()[0] is None
        db.close()
        session.finish("failed")

    def test_finish_is_idempotent_after_the_first_call(self, tmp_path):
        session = _trace.TraceSession("tests/t.py::test_x", tmp_path / "run")
        session.finish("failed", error_message="first")
        session.finish("passed", error_message="second")

        db = sqlite3.connect(str(tmp_path / "run" / "trace.db"))
        assert db.execute("SELECT status, error_message FROM runs").fetchone() == (
            "failed",
            "first",
        )
        db.close()

    def test_close_without_finish_swallows_close_failure(self, tmp_path):
        session = _trace.TraceSession("tests/t.py::test_x", tmp_path / "run")
        session._db.close()
        session._db = _FailingConnection(RuntimeError("close failed"))

        session.close_without_finish()

        assert session._closed is True


class TestHtmlRenderingBranches:
    def test_render_steps_includes_all_optional_content_and_escapes_it(self):
        html = _trace._render_steps(
            [
                {
                    "seq": 1,
                    "action": "<click>",
                    "selector": "<save>",
                    "ts": 1.23456,
                    "result": "ok",
                    "screenshot_file": 'shot"&.jpg',
                },
                {
                    "seq": 2,
                    "action": "type_text",
                    "selector": None,
                    "ts": 2.0,
                    "result": "error",
                    "error": "<bad>&",
                    "uia_tree": json.dumps(
                        [
                            {"d": 0, "ctrl": "Window", "name": "Main", "id": "root"},
                            {"d": 1, "ctrl": "", "name": "", "id": ""},
                            {},
                        ]
                    ),
                },
                {
                    "seq": 3,
                    "action": "noop",
                    "selector": "",
                    "result": "unexpected",
                    "uia_tree": "<invalid tree>",
                },
            ]
        )

        assert "&lt;click&gt;" in html
        assert "selector" not in html  # the selector value itself is rendered, not its field name
        assert 'src="screenshots/shot&quot;&amp;.jpg"' in html
        assert '<span class="ok">ok</span>' in html
        assert '<span class="er">err</span>' in html
        assert "&lt;bad&gt;&amp;" in html
        assert "UIA tree (3 nodes)" in html
        assert "Window name=&#x27;Main&#x27; id=&#x27;root&#x27;" in html
        assert "&lt;invalid tree&gt;" in html
        assert "UIA tree (? nodes)" in html

    def test_render_html_handles_unknown_status_missing_finish_and_error(self):
        html = _trace._render_html(
            {
                "test_nodeid": "tests/test_<x>",
                "started_at": 100.0,
                "finished_at": None,
                "status": None,
                "error_message": "<failure>&",
            },
            [],
        )

        assert 'class="badge warn">unknown</span>' in html
        assert "Dolphin Trace: tests/test_&lt;x&gt;" in html
        assert "— &bull; 0 steps" in html
        assert '<div class="err-box"><pre>&lt;failure&gt;&amp;</pre></div>' in html


class TestRunListingBranches:
    def test_list_runs_ignores_missing_dirs_files_empty_dbs_and_corrupt_dbs(self, tmp_path):
        assert _trace.list_runs(tmp_path / "missing") == []

        (tmp_path / "not-a-run.txt").write_text("ignore", encoding="utf-8")
        (tmp_path / "without-db").mkdir()

        empty = tmp_path / "empty"
        empty.mkdir()
        db = sqlite3.connect(str(empty / "trace.db"))
        db.executescript(_trace._DDL)
        db.close()

        corrupt = tmp_path / "corrupt"
        corrupt.mkdir()
        (corrupt / "trace.db").write_text("not sqlite", encoding="utf-8")

        valid = _trace.TraceSession("tests/t.py::test_valid", tmp_path / "valid", mode="always")
        valid.finish("failed")

        runs = _trace.list_runs(tmp_path)

        assert [run["test_nodeid"] for run in runs] == ["tests/t.py::test_valid"]
        assert runs[0]["run_dir"] == str(tmp_path / "valid")


def test_trace_renders_nodes_and_ignores_missing_run_directories(tmp_path: Path) -> None:
    from dolphin_desktop._trace import _nodes_to_text, list_runs

    assert _nodes_to_text([{"d": 1, "ctrl": "Button", "name": "Save", "id": "save"}]) == (
        "  Button name='Save' id='save'"
    )
    assert list_runs(tmp_path / "missing") == []
