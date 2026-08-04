"""Tests for logging, telemetry, diagnostics, crash dumps, and runtime config."""

from __future__ import annotations

import argparse
import logging
import os
import zipfile
from unittest.mock import MagicMock, patch

import pytest

# Structured logging


class TestLogging:
    def _fresh_logger(self):
        """Return a fresh dolphin logger with no handlers (test isolation)."""
        logger = logging.getLogger("dolphin_desktop")
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True  # restore default so caplog in other tests keeps working
        return logger

    def test_setup_logging_adds_handler(self):
        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("INFO")
        logger = logging.getLogger("dolphin_desktop")
        assert len(logger.handlers) == 1

    def test_setup_logging_is_idempotent(self):
        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("INFO")
        setup_logging("DEBUG")  # second call must not add another handler
        assert len(logging.getLogger("dolphin_desktop").handlers) == 1

    def test_debug_level_propagates(self):
        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("DEBUG")
        assert logging.getLogger("dolphin_desktop").level == logging.DEBUG

    def test_error_level_propagates(self):
        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("ERROR")
        assert logging.getLogger("dolphin_desktop").level == logging.ERROR

    def test_second_setup_logging_applies_new_level(self):
        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("ERROR")
        setup_logging("DEBUG")
        logger = logging.getLogger("dolphin_desktop")
        assert logger.level == logging.DEBUG
        assert all(h.level == logging.DEBUG for h in logger.handlers)

    def test_get_logger_returns_root(self):
        from dolphin_desktop._logging import get_logger

        assert get_logger().name == "dolphin_desktop"

    def test_get_logger_returns_child(self):
        from dolphin_desktop._logging import get_logger

        child = get_logger("plugin")
        assert child.name == "dolphin_desktop.plugin"

    def test_get_logger_accepts_fully_qualified_name(self):
        from dolphin_desktop._logging import get_logger

        assert get_logger("dolphin_desktop.mainframe").name == "dolphin_desktop.mainframe"

    def test_legacy_dolphin_name_maps_to_root(self):
        from dolphin_desktop._logging import get_logger

        assert get_logger("dolphin").name == "dolphin_desktop"

    def test_setup_logging_governs_real_emitters(self):
        """Every logger the library actually emits through is a configured child."""
        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("ERROR")
        root = logging.getLogger("dolphin_desktop")
        for name in (
            "dolphin_desktop.selfheal",
            "dolphin_desktop.plugin",
            "dolphin_desktop.mainframe",
        ):
            child = logging.getLogger(name)
            assert child.getEffectiveLevel() == logging.ERROR
            assert not child.isEnabledFor(logging.INFO)
            parent = child
            while parent.parent is not None and parent is not root:
                parent = parent.parent
            assert parent is root

    def test_redaction_applies_to_child_logger_records(self):
        """A record emitted by a real child logger reaches the redacting handler."""
        import io

        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("DEBUG")
        root = logging.getLogger("dolphin_desktop")
        buf = io.StringIO()
        root.handlers[0].setStream(buf)
        try:
            logging.getLogger("dolphin_desktop.selfheal").error("token=abcdef1234")
            out = buf.getvalue()
        finally:
            self._fresh_logger()
        assert "abcdef1234" not in out
        assert "***" in out

    def _capture_dolphin_log(self, msg: str, level: str = "INFO") -> str:
        """Emit *msg* through the dolphin logger and return what a handler would see.

        Restores propagate=True after the call so other tests using caplog still work.
        """
        import io

        from dolphin_desktop._logging import _RedactingFormatter, setup_logging

        self._fresh_logger()
        setup_logging(level)
        root = logging.getLogger("dolphin_desktop")

        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(_RedactingFormatter("%(message)s"))
        h.setLevel(logging.DEBUG)
        root.addHandler(h)

        try:
            root.info(msg)
            return buf.getvalue()
        finally:
            self._fresh_logger()  # restore propagate=True for following tests

    def test_secret_redaction_password(self):
        out = self._capture_dolphin_log("connecting with password=supersecret123")
        assert "supersecret123" not in out
        assert "***" in out

    def test_secret_redaction_token(self):
        out = self._capture_dolphin_log("auth token=abcdef1234567890")
        assert "abcdef1234567890" not in out
        assert "***" in out

    def test_normal_message_not_redacted(self):
        out = self._capture_dolphin_log("clicking button OK in window Notepad")
        assert "clicking button OK" in out

    def test_redact_function_standalone(self):
        from dolphin_desktop._logging import _redact

        assert _redact("password=hunter2") == "password=***"
        assert _redact("api_key=sk-12345abc") == "api_key=***"
        assert _redact("no secrets here") == "no secrets here"


# Telemetry


class TestTelemetry:
    def _reset(self):
        import dolphin_desktop._telemetry as tel

        tel._initialized = False

    def test_off_by_default(self):
        from dolphin_desktop._telemetry import init, is_enabled

        self._reset()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DOLPHIN_TELEMETRY", None)
            init()
        assert not is_enabled()

    def test_on_requires_env_var(self):
        from dolphin_desktop._telemetry import init, is_enabled

        self._reset()
        with patch.dict(os.environ, {"DOLPHIN_TELEMETRY": "on"}):
            init()
        # _DOLPHIN_DSN is empty so still not initialized
        assert not is_enabled()

    def test_before_send_drops_non_dolphin_exception(self):
        from dolphin_desktop._telemetry import _before_send

        event: dict = {"message": "test"}
        hint: dict = {"exc_info": (ValueError, ValueError("oops"), None)}
        result = _before_send(event, hint)
        assert result is None

    def test_before_send_passes_dolphin_exception(self):
        from dolphin_desktop._exceptions import ElementNotFoundError
        from dolphin_desktop._telemetry import _before_send

        exc = ElementNotFoundError("el")
        event: dict = {"message": "test"}
        hint: dict = {"exc_info": (type(exc), exc, None)}
        result = _before_send(event, hint)
        assert result is event

    def test_before_send_drops_event_without_exc_info(self):
        """LoggingIntegration events carry no exc_info — they must never be sent."""
        from dolphin_desktop._telemetry import _before_send

        event: dict = {"message": "user typed password=hunter2", "logger": "myapp"}
        assert _before_send(event, {}) is None

    def test_before_send_drops_dolphin_prefixed_third_party_module(self):
        from dolphin_desktop._telemetry import _before_send

        exc_type = type("Boom", (Exception,), {"__module__": "dolphinctl.cli"})
        hint: dict = {"exc_info": (exc_type, exc_type("x"), None)}
        assert _before_send({"message": "t"}, hint) is None

    def test_before_send_passes_builtin_raised_in_dolphin_frame(self):
        from dolphin_desktop._telemetry import _before_send

        event: dict = {
            "exception": {
                "values": [
                    {
                        "stacktrace": {
                            "frames": [
                                {"module": "mytests.test_login"},
                                {"module": "dolphin_desktop._locator"},
                            ]
                        }
                    }
                ]
            }
        }
        hint: dict = {"exc_info": (AttributeError, AttributeError("x"), None)}
        assert _before_send(event, hint) is event

    def test_before_send_drops_builtin_raised_in_user_frame(self):
        from dolphin_desktop._telemetry import _before_send

        event: dict = {
            "exception": {
                "values": [
                    {
                        "stacktrace": {
                            "frames": [
                                {"module": "dolphin_desktop._locator"},
                                {"module": "mytests.test_login"},
                            ]
                        }
                    }
                ]
            }
        }
        hint: dict = {"exc_info": (AssertionError, AssertionError("x"), None)}
        assert _before_send(event, hint) is None

    def test_init_disables_default_integrations_and_local_variables(self):
        import sys

        import dolphin_desktop._telemetry as tel

        self._reset()
        fake = MagicMock()
        with (
            patch.dict(sys.modules, {"sentry_sdk": fake}),
            patch.dict(os.environ, {"DOLPHIN_TELEMETRY": "on"}),
            patch.object(tel, "_DOLPHIN_DSN", "https://key@example.invalid/1"),
        ):
            tel.init()
        self._reset()
        kwargs = fake.init.call_args.kwargs
        assert kwargs["default_integrations"] is False
        assert kwargs["include_local_variables"] is False
        assert kwargs["send_default_pii"] is False
        assert kwargs["before_send"] is tel._before_send

    def test_capture_exception_noop_when_disabled(self):
        """capture_exception must not raise when telemetry is off."""
        from dolphin_desktop._telemetry import capture_exception

        self._reset()
        capture_exception(ValueError("harmless"))  # must not raise

    def test_telemetry_exported_from_package(self):
        import dolphin_desktop

        assert callable(dolphin_desktop.telemetry_init)
        assert callable(dolphin_desktop.telemetry_enabled)
        assert callable(dolphin_desktop.telemetry_capture_exception)


# Crash dump


class TestCrashDump:
    pytestmark = pytest.mark.integration

    def test_creates_zip(self, tmp_path):
        from dolphin_desktop._crash import write_crash_dump

        path = write_crash_dump(output_dir=tmp_path)
        assert path.exists()
        assert path.suffix == ".zip"

    def test_zip_contains_stack(self, tmp_path):
        from dolphin_desktop._crash import write_crash_dump

        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            path = write_crash_dump(exc=exc, output_dir=tmp_path)

        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            assert "stack.txt" in names
            stack = zf.read("stack.txt").decode()
        assert "boom" in stack

    def test_zip_contains_environment(self, tmp_path):
        from dolphin_desktop._crash import write_crash_dump

        path = write_crash_dump(output_dir=tmp_path)
        with zipfile.ZipFile(path) as zf:
            assert "environment.txt" in zf.namelist()
            env = zf.read("environment.txt").decode()
        assert "python" in env.lower()

    def test_zip_contains_uia_tree(self, tmp_path):
        from dolphin_desktop._crash import write_crash_dump

        path = write_crash_dump(output_dir=tmp_path)
        with zipfile.ZipFile(path) as zf:
            assert "uia_tree.txt" in zf.namelist()

    def test_extra_written_when_provided(self, tmp_path):
        from dolphin_desktop._crash import write_crash_dump

        path = write_crash_dump(
            output_dir=tmp_path,
            extra={"test_id": "my_test::test_foo", "attempt": "2"},
        )
        with zipfile.ZipFile(path) as zf:
            assert "extra.txt" in zf.namelist()
            extra = zf.read("extra.txt").decode()
        assert "my_test::test_foo" in extra
        assert "attempt: 2" in extra

    def test_no_extra_file_when_none(self, tmp_path):
        from dolphin_desktop._crash import write_crash_dump

        path = write_crash_dump(output_dir=tmp_path)
        with zipfile.ZipFile(path) as zf:
            assert "extra.txt" not in zf.namelist()

    def test_filename_uses_the_crash_prefix(self, tmp_path):
        """Dump files are named ``crash-*`` so they sort and glob together."""
        from dolphin_desktop._crash import write_crash_dump

        path = write_crash_dump(output_dir=tmp_path)
        assert path.name.startswith("crash-")

    def test_exported_from_package(self):
        import dolphin_desktop

        assert callable(dolphin_desktop.write_crash_dump)


# dolphin doctor CLI


class TestDoctorCommand:
    pytestmark = pytest.mark.integration

    def test_doctor_prints_dolphin_version(self, capsys):
        from dolphin_desktop._cli import _doctor_cmd

        _doctor_cmd(argparse.Namespace())
        out = capsys.readouterr().out
        assert "dolphin" in out.lower()

    def test_doctor_prints_python_version(self, capsys):
        import sys

        from dolphin_desktop._cli import _doctor_cmd

        _doctor_cmd(argparse.Namespace())
        out = capsys.readouterr().out
        major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
        assert major_minor in out

    def test_doctor_reports_required_deps(self, capsys):
        from dolphin_desktop._cli import _doctor_cmd

        _doctor_cmd(argparse.Namespace())
        out = capsys.readouterr().out
        assert "pywinauto" in out
        assert "Pillow" in out

    def test_doctor_reports_optional_deps(self, capsys):
        from dolphin_desktop._cli import _doctor_cmd

        _doctor_cmd(argparse.Namespace())
        out = capsys.readouterr().out
        assert "sentry-sdk" in out
        assert "mss" in out

    def test_doctor_reports_env_vars(self, capsys):
        from dolphin_desktop._cli import _doctor_cmd

        _doctor_cmd(argparse.Namespace())
        out = capsys.readouterr().out
        assert "DOLPHIN_TIMEOUT" in out
        assert "DOLPHIN_TELEMETRY" in out

    def test_doctor_dispatched_from_main(self, capsys):
        from dolphin_desktop._cli import main

        with patch("sys.argv", ["dolphin", "doctor"]):
            main()

        out = capsys.readouterr().out
        assert "dolphin" in out.lower()


# Config round-trip for runtime settings


class TestConfigRoundTrip:
    def test_log_level_default(self):
        from dolphin_desktop._config import _defaults

        assert _defaults["log_level"] in ("DEBUG", "INFO", "ERROR", "WARNING")

    def test_log_level_set_via_config(self):
        from dolphin_desktop import config
        from dolphin_desktop._config import get_log_level

        original = get_log_level()
        try:
            config(log_level="DEBUG")
            assert get_log_level() == "DEBUG"
        finally:
            config(log_level=original)

    def test_retry_count_default_is_zero(self):
        from dolphin_desktop._config import get_retry_count

        # unless DOLPHIN_RETRY is set in the test environment
        if os.environ.get("DOLPHIN_RETRY"):
            return
        assert get_retry_count() == 0

    def test_retry_count_set_via_config(self):
        from dolphin_desktop import config
        from dolphin_desktop._config import get_retry_count

        original = get_retry_count()
        try:
            config(retry_count=3)
            assert get_retry_count() == 3
        finally:
            config(retry_count=original)


# Zombie process registry


class TestZombieRegistry:
    def test_pid_registered_on_application_init(self):

        from dolphin_desktop import _application

        mock_app = MagicMock()
        mock_app.process = 99999

        prev = set(_application._live_pids)
        app = _application.Application(mock_app, "uia")
        assert app.process_id in _application._live_pids

        # cleanup
        _application._live_pids.discard(99999)
        assert _application._live_pids == prev

    def test_kill_removes_the_pid_from_the_live_registry(self):
        """``kill()`` unregisters the PID from ``_live_pids``."""
        from dolphin_desktop import _application

        mock_app = MagicMock()
        mock_app.process = 88888

        app = _application.Application(mock_app, "uia")
        assert 88888 in _application._live_pids

        app.kill()
        assert 88888 not in _application._live_pids

    def test_pid_removed_on_kill(self):

        from dolphin_desktop import _application

        mock_app = MagicMock()
        mock_app.process = 77777

        app = _application.Application(mock_app, "uia")
        assert 77777 in _application._live_pids

        app.kill()
        assert 77777 not in _application._live_pids

    def test_attached_process_is_never_owned(self, monkeypatch):
        """attach_* factories connect to somebody else's process — the
        teardown reaper must never see that PID."""
        from dolphin_desktop import _application
        from dolphin_desktop._desktop import Desktop

        mock_app = MagicMock()
        mock_app.process = 66666
        monkeypatch.setattr("dolphin_desktop._desktop._PyWinApp", MagicMock(return_value=mock_app))

        app = Desktop()._connect_raw(backend="uia", timeout=1.0, process=66666)
        assert app._owns_process is False
        assert 66666 not in _application._live_pids
        assert 66666 not in _application._session_pids


class TestDesktopWideWindowFallback:
    """``_find_window`` may only adopt a PID it can prove is the same program."""

    def _app_whose_own_process_has_no_window(self, pid: int):
        from dolphin_desktop import _application

        mock_app = MagicMock()
        mock_app.process = pid
        mock_app.window.side_effect = Exception("no matching window in this process")
        return _application.Application(mock_app, "uia", owns_process=True)

    def _desktop_returning_pid(self, pid: int):
        spec = MagicMock()
        spec.wrapper_object.return_value.process_id.return_value = pid
        desktop = MagicMock()
        desktop.window.return_value = spec
        return MagicMock(return_value=desktop)

    def test_unrelated_process_is_not_adopted(self, monkeypatch):
        from dolphin_desktop import WindowNotFoundError, _application

        app = self._app_whose_own_process_has_no_window(4242)
        try:
            monkeypatch.setattr(_application, "_PwDesktop", self._desktop_returning_pid(9999))
            monkeypatch.setattr(_application, "_process_parent_map", dict)
            monkeypatch.setattr(
                _application, "_process_image_path", lambda pid: rf"c:\{pid}\other.exe"
            )
            connect = MagicMock()
            monkeypatch.setattr(_application, "_PyWinApp", MagicMock(return_value=connect))

            with pytest.raises(WindowNotFoundError):
                app._find_window({"title_re": ".*Report.*"}, 0.1)

            connect.connect.assert_not_called()
            assert app._app.process == 4242
            assert 9999 not in _application._live_pids
            assert 9999 not in _application._session_pids
        finally:
            _application._live_pids.discard(4242)
            _application._session_pids.discard(4242)

    def test_same_image_hand_off_is_adopted(self, monkeypatch):
        from dolphin_desktop import _application

        app = self._app_whose_own_process_has_no_window(4242)
        try:
            app._image_path = r"c:\windows\notepad.exe"
            monkeypatch.setattr(_application, "_PwDesktop", self._desktop_returning_pid(9999))
            monkeypatch.setattr(_application, "_process_parent_map", dict)
            monkeypatch.setattr(
                _application, "_process_image_path", lambda pid: r"c:\windows\notepad.exe"
            )
            new_app = MagicMock()
            new_app.process = 9999
            monkeypatch.setattr(_application, "_PyWinApp", MagicMock(return_value=new_app))

            app._find_window({"title_re": ".*Notepad.*"}, 0.1)

            new_app.connect.assert_called_once_with(process=9999)
            assert app._app is new_app
            assert 9999 in _application._live_pids
            assert 4242 not in _application._live_pids
        finally:
            for pid in (4242, 9999):
                _application._live_pids.discard(pid)
                _application._session_pids.discard(pid)

    def test_child_process_is_adopted(self, monkeypatch):
        from dolphin_desktop import _application

        app = self._app_whose_own_process_has_no_window(4242)
        try:
            app._image_path = None  # launcher already gone — only parentage proves it
            monkeypatch.setattr(_application, "_PwDesktop", self._desktop_returning_pid(9999))
            monkeypatch.setattr(_application, "_process_parent_map", lambda: {4242: [9999]})
            new_app = MagicMock()
            new_app.process = 9999
            monkeypatch.setattr(_application, "_PyWinApp", MagicMock(return_value=new_app))

            app._find_window({"title": "Main"}, 0.1)

            new_app.connect.assert_called_once_with(process=9999)
        finally:
            for pid in (4242, 9999):
                _application._live_pids.discard(pid)
                _application._session_pids.discard(pid)

    def test_stranger_window_is_never_bound_to_this_application(self, monkeypatch):
        """A refused PID must not come back as a Window of this Application.

        Handing it over means every later click/type — and every
        qml()/qt_widget() call routed through ``Window._application`` — lands
        in a program the caller never launched.
        """
        from dolphin_desktop import WindowNotFoundError, _application

        app = self._app_whose_own_process_has_no_window(4242)
        try:
            monkeypatch.setattr(_application, "_PwDesktop", self._desktop_returning_pid(9999))
            monkeypatch.setattr(_application, "_process_parent_map", dict)
            monkeypatch.setattr(_application, "_process_image_path", lambda pid: None)

            with pytest.raises(WindowNotFoundError) as exc_info:
                app._find_window({"title": "Report"}, 0.1)
            assert "9999" in str(exc_info.value)
        finally:
            _application._live_pids.discard(4242)
            _application._session_pids.discard(4242)

    def test_unreadable_owning_pid_is_refused_not_waved_through(self, monkeypatch):
        """An owner that cannot be read is the looser case, not the safer one.

        Returning it unverified binds an unidentified process to this
        Application exactly as a mismatched pid would.
        """
        from dolphin_desktop import WindowNotFoundError, _application

        app = self._app_whose_own_process_has_no_window(4242)
        try:
            spec = MagicMock()
            spec.wrapper_object.side_effect = Exception("window vanished")
            desktop = MagicMock()
            desktop.window.return_value = spec
            monkeypatch.setattr(_application, "_PwDesktop", MagicMock(return_value=desktop))

            with pytest.raises(WindowNotFoundError):
                app._find_window({"title": "Report"}, 0.1)
        finally:
            _application._live_pids.discard(4242)
            _application._session_pids.discard(4242)

    def test_grandchild_pid_counts_as_a_hand_off(self, monkeypatch):
        """Launcher chains are often two deep — one level of children is not enough."""
        from dolphin_desktop import _application

        app = self._app_whose_own_process_has_no_window(4242)
        try:
            app._image_path = None  # only parentage can prove the relationship
            monkeypatch.setattr(
                _application, "_process_parent_map", lambda: {4242: [5000], 5000: [6000]}
            )
            assert app._is_hand_off_pid(6000) is True
            assert app._is_hand_off_pid(7000) is False
        finally:
            _application._live_pids.discard(4242)
            _application._session_pids.discard(4242)

    def test_descendant_scan_takes_one_process_snapshot(self, monkeypatch):
        """One CreateToolhelp32Snapshot per node is 20+ table walks on Chromium."""
        from dolphin_desktop import _application

        calls = {"n": 0}

        def _snapshot():
            calls["n"] += 1
            return {1: [2, 3, 4], 2: [5, 6], 3: [7], 5: [8]}

        monkeypatch.setattr(_application, "_process_parent_map", _snapshot)
        assert _application._enumerate_descendant_pids(1) == {2, 3, 4, 5, 6, 7, 8}
        assert calls["n"] == 1


class TestLaunchCapturesImagePath:
    """The image path must be read at spawn, not after ``startup_delay``.

    Single-instance launchers hand off and exit within milliseconds; once the
    stub is gone the path is unrecoverable and ``_is_hand_off_pid`` degrades to
    "same PID or descendant".
    """

    def test_image_path_survives_a_launcher_that_exits_during_startup_delay(self, monkeypatch):
        from dolphin_desktop import _application, _desktop

        pw = MagicMock()
        pw.process = 31313
        monkeypatch.setattr(_desktop, "_PyWinApp", MagicMock(return_value=pw))

        alive = {"value": True}

        def _image_path(_pid):
            return r"c:\apps\stub.exe" if alive["value"] else None

        monkeypatch.setattr(_desktop, "_process_image_path", _image_path)
        monkeypatch.setattr(_application, "_process_image_path", _image_path)
        # The launcher stub exits while Desktop.launch is sleeping.
        monkeypatch.setattr(_desktop.time, "sleep", lambda _s: alive.update(value=False))

        app = _desktop.Desktop().launch("stub.exe", startup_delay=0.5)
        try:
            assert app._image_path == r"c:\apps\stub.exe"
        finally:
            _application._live_pids.discard(31313)
            _application._session_pids.discard(31313)


class TestProcessOwnership:
    """``close()`` and ``kill()`` must apply the same ownership rule."""

    def _app(self, pid: int, *, owns: bool):
        from dolphin_desktop import _application

        mock_app = MagicMock()
        mock_app.process = pid
        return _application.Application(mock_app, "uia", owns_process=owns)

    def _discard(self, pid: int) -> None:
        from dolphin_desktop import _application

        _application._live_pids.discard(pid)
        _application._session_pids.discard(pid)

    def test_close_leaves_an_attached_process_running(self):
        app = self._app(55555, owns=False)
        app.close()
        app._app.kill.assert_not_called()

    def test_close_terminates_a_launched_process(self):
        app = self._app(55556, owns=True)
        try:
            app.close()
        finally:
            self._discard(55556)
        app._app.kill.assert_called_once_with(soft=True)

    def test_kill_leaves_an_attached_process_running(self):
        app = self._app(55557, owns=False)
        app.kill()
        app._app.kill.assert_not_called()

    def test_kill_terminates_a_launched_process(self):
        app = self._app(55558, owns=True)
        try:
            app.kill()
        finally:
            self._discard(55558)
        app._app.kill.assert_called_once_with(soft=False)

    def test_kill_still_terminates_children_of_an_attached_app(self):
        parent = self._app(55559, owns=False)
        child = self._app(55560, owns=True)
        try:
            parent._launched_apps.append(("child.exe", child))
            parent.kill()
        finally:
            self._discard(55560)
        child._app.kill.assert_called_once_with(soft=False)
        parent._app.kill.assert_not_called()

    def test_docstrings_do_not_promise_termination_of_attached_processes(self):
        from dolphin_desktop._application import Application

        for doc in (Application.kill.__doc__, Application.close.__doc__):
            assert doc is not None
            assert "attached" in doc.lower()


class TestStackLauncherWiring:
    """``_launch_raw`` / ``_launch_hidden`` must produce fully-wired Applications."""

    def test_launch_raw_binds_desktop_and_default_timeout(self, monkeypatch):
        from dolphin_desktop import _application, _desktop

        pw = MagicMock()
        pw.process = 40404
        monkeypatch.setattr(_desktop, "_PyWinApp", MagicMock(return_value=pw))
        monkeypatch.setattr(_desktop, "_process_image_path", lambda _pid: None)

        desktop = _desktop.Desktop(default_timeout_ms=2500)
        app = desktop._launch_raw("app.exe", backend="uia", startup_delay=0)
        try:
            assert app.default_timeout_ms == 2500
            # DelphiApp.launch() would raise RuntimeError without the back-pointer.
            assert app._desktop is desktop
            app.launch("second.exe")
        finally:
            for pid in (40404,):
                _application._live_pids.discard(pid)
                _application._session_pids.discard(pid)

    def test_launch_hidden_binds_desktop_and_default_timeout(self, monkeypatch):
        from dolphin_desktop import _application, _desktop, _runner

        monkeypatch.setattr(_runner, "launch_cmd_on_desktop", lambda cmd, work_dir=None: (50505, 1))
        monkeypatch.setattr(_runner, "close_process_handle", lambda _h: None)
        pw = MagicMock()
        pw.process = 50505
        monkeypatch.setattr(_desktop, "_PyWinApp", MagicMock(return_value=pw))
        monkeypatch.setattr(_desktop, "_process_image_path", lambda _pid: r"c:\h\app.exe")

        desktop = _desktop.Desktop(hidden=True, default_timeout_ms=3300)
        desktop._hidden_initialized = True
        app = desktop._launch_hidden("app.exe", timeout=1.0, work_dir=None, startup_delay=0)
        try:
            assert app.default_timeout_ms == 3300
            assert app._desktop is desktop
            assert app._image_path == r"c:\h\app.exe"
        finally:
            _application._live_pids.discard(50505)
            _application._session_pids.discard(50505)


class TestTempFile:
    """A failed write must surface its own error and leave no file behind."""

    def _pattern(self) -> str:
        import tempfile

        return os.path.join(tempfile.gettempdir(), "dolphin_badwrite_*")

    def test_write_failure_reports_the_real_error(self):
        import glob

        from dolphin_desktop import temp_file

        before = set(glob.glob(self._pattern()))
        # A lone surrogate cannot be encoded by the text writer.
        with pytest.raises(UnicodeEncodeError):
            temp_file(prefix="dolphin_badwrite_", content="\ud800")
        assert set(glob.glob(self._pattern())) == before

    def test_successful_write_still_returns_a_readable_path(self):
        from dolphin_desktop import path_exists, remove_file, temp_file

        path = temp_file(prefix="dolphin_goodwrite_", content="hello")
        try:
            assert path_exists(path)
            with open(path, encoding="utf-8") as fh:
                assert fh.read() == "hello"
        finally:
            remove_file(path)


class TestImagePathIsReadableAtSpawn:
    """``_image_path_now`` must work the moment ``CreateProcess`` returns.

    The module-list route (``EnumProcessModules``) fails until the target's
    loader has populated the PEB, so it answers ``None`` for every freshly
    spawned process — which is exactly the window in which a single-instance
    launcher hands off and exits.
    """

    def test_path_is_known_immediately_after_create_process(self):
        import subprocess
        import sys

        from dolphin_desktop._application import _process_image_path

        for _ in range(5):
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                path = _process_image_path(proc.pid)
            finally:
                proc.kill()
                proc.wait()
            assert path is not None
            assert path.endswith(".exe")

    def test_launch_passes_a_real_path_to_the_application(self, monkeypatch):
        import subprocess
        import sys

        from dolphin_desktop import _application, _desktop

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        pw = MagicMock()
        pw.process = proc.pid
        monkeypatch.setattr(_desktop, "_PyWinApp", MagicMock(return_value=pw))
        try:
            app = _desktop.Desktop().launch("stub.exe", startup_delay=0)
            assert app._image_path is not None
        finally:
            _application._live_pids.discard(proc.pid)
            _application._session_pids.discard(proc.pid)
            proc.kill()
            proc.wait()


class TestCloseMatchesKillOnChildren:
    """``close()`` and ``kill()`` must treat launched children the same way."""

    def _app(self, pid: int):
        from dolphin_desktop import _application

        mock_app = MagicMock()
        mock_app.process = pid
        return _application.Application(mock_app, "uia", owns_process=True)

    def test_close_closes_and_forgets_launched_children(self):
        from dolphin_desktop import _application

        app = self._app(55570)
        child = MagicMock()
        app._launched_apps.append(("child.exe", child))
        try:
            app.close()
        finally:
            _application._live_pids.discard(55570)
            _application._session_pids.discard(55570)
        child.close.assert_called_once()
        assert app._launched_apps == []

    def test_close_on_an_attached_app_still_closes_its_children(self):
        from dolphin_desktop import _application

        mock_app = MagicMock()
        mock_app.process = 55571
        app = _application.Application(mock_app, "uia", owns_process=False)
        child = MagicMock()
        app._launched_apps.append(("child.exe", child))
        app.close()
        child.close.assert_called_once()
        mock_app.kill.assert_not_called()


class TestAttachedPidsAreTrackedButNeverKilled:
    """Attached processes must be visible to crash dumps and invisible to reapers.

    ``owns_process=False`` keeps a PID out of the kill lists — which is also
    what kept every SAP / mainframe / Oracle Forms session out of the crash
    dump's capture scope, leaving ``uia_tree.txt`` empty exactly when it mattered.
    """

    def _attached(self, pid: int):
        from dolphin_desktop import _application

        mock_app = MagicMock()
        mock_app.process = pid
        return _application.Application(mock_app, "uia", owns_process=False)

    def test_attaching_records_the_pid(self):
        from dolphin_desktop import _application

        self._attached(61001)
        try:
            assert 61001 in _application._attached_pids
        finally:
            _application._attached_pids.discard(61001)

    def test_an_attached_pid_never_enters_a_kill_list(self):
        from dolphin_desktop import _application

        self._attached(61002)
        try:
            assert 61002 not in _application._live_pids
            assert 61002 not in _application._session_pids
        finally:
            _application._attached_pids.discard(61002)

    def test_teardown_and_session_reapers_leave_it_alone(self):
        from dolphin_desktop import _application, pytest_plugin

        self._attached(61003)
        try:
            pytest_plugin.pytest_runtest_teardown(MagicMock(), None)
            assert 61003 in _application._attached_pids
        finally:
            _application._attached_pids.discard(61003)

    def test_a_launched_process_is_not_recorded_as_attached(self):
        from dolphin_desktop import _application

        mock_app = MagicMock()
        mock_app.process = 61004
        _application.Application(mock_app, "uia", owns_process=True)
        try:
            assert 61004 not in _application._attached_pids
        finally:
            _application._live_pids.discard(61004)
            _application._session_pids.discard(61004)

    def test_close_drops_the_attached_pid(self):
        from dolphin_desktop import _application

        app = self._attached(61005)
        try:
            app.close()
            assert 61005 not in _application._attached_pids
        finally:
            _application._attached_pids.discard(61005)

    def test_crash_dump_scopes_its_capture_to_attached_processes(self):
        from dolphin_desktop import _application
        from dolphin_desktop._crash import _target_pids

        self._attached(61006)
        try:
            assert 61006 in _target_pids(None)
        finally:
            _application._attached_pids.discard(61006)
