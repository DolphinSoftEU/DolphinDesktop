"""Tests for the trace engine's failure isolation and run selection."""

from __future__ import annotations

import sqlite3
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
        session._db = _FailingConnection(exc)
        session.record_step("type_text", "{'auto_id': 'edit'}")

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
        session._db = _FailingConnection(sqlite3.OperationalError("database is locked"))
        session.finish("failed", error_message="boom")

    def test_recording_onto_a_closed_db_does_not_raise(self, tmp_path):
        """A dead DB handle must not turn tracing into a test failure.

        The connection is closed underneath the session; both ``record_step``
        and ``finish`` have to swallow the resulting error. The run directory
        itself stays in place.
        """
        session = self._session(tmp_path)
        session._db.close()
        session.record_step("click", None)
        session.finish("passed")

    def test_lost_finish_is_logged_and_flagged(self, tmp_path, caplog):
        session = self._session(tmp_path)
        session._db = _FailingConnection(sqlite3.OperationalError("database is locked"))
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
        session.close_without_finish()


# A trace screenshot must show the monitor the failure happened on


class TestCaptureScreenshotSpansEveryMonitor:
    def test_mss_path_grabs_the_virtual_desktop(self, tmp_path, monkeypatch):
        """mss.monitors[0] is the whole virtual desktop; [1] would be the primary only."""
        pytest.importorskip("mss")
        import mss

        grabbed: dict = {}
        all_monitors: list[dict] = [{"virtual": True}, {"primary": True}]

        class _FakeSct:
            monitors: ClassVar[list[dict]] = all_monitors

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def grab(self, monitor):
                grabbed["monitor"] = monitor
                # Not ImportError: that is the one exception the PIL fallback catches.
                raise RuntimeError("stop before the PIL conversion")

        monkeypatch.setattr(mss, "mss", _FakeSct)
        with pytest.raises(RuntimeError):
            _trace._capture_screenshot(tmp_path / "shot.jpg")
        assert grabbed["monitor"] is all_monitors[0]

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
        session._db = _FailingConnection(sqlite3.OperationalError("disk I/O error"))
        session.record_step("click", "{'auto_id': 'ok'}")
        assert session.degraded is True
