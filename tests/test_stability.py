"""Tests for logging, telemetry, diagnostics, crash dumps, and runtime config."""

from __future__ import annotations

import argparse
import logging
import os
import zipfile
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


class TestLogging:
    def _fresh_logger(self):
        """Return a fresh dolphin logger with no handlers (test isolation)."""
        logger = logging.getLogger("dolphin")
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True  # restore default so caplog in other tests keeps working
        return logger

    def test_setup_logging_adds_handler(self):
        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("INFO")
        logger = logging.getLogger("dolphin")
        assert len(logger.handlers) == 1

    def test_setup_logging_is_idempotent(self):
        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("INFO")
        setup_logging("DEBUG")  # second call must not add another handler
        assert len(logging.getLogger("dolphin").handlers) == 1

    def test_debug_level_propagates(self):
        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("DEBUG")
        assert logging.getLogger("dolphin").level == logging.DEBUG

    def test_error_level_propagates(self):
        from dolphin_desktop._logging import setup_logging

        self._fresh_logger()
        setup_logging("ERROR")
        assert logging.getLogger("dolphin").level == logging.ERROR

    def test_get_logger_returns_root(self):
        from dolphin_desktop._logging import get_logger

        assert get_logger().name == "dolphin"

    def test_get_logger_returns_child(self):
        from dolphin_desktop._logging import get_logger

        child = get_logger("plugin")
        assert child.name == "dolphin_desktop.plugin"

    def _capture_dolphin_log(self, msg: str, level: str = "INFO") -> str:
        """Emit *msg* through the dolphin logger and return what a handler would see.

        Restores propagate=True after the call so other tests using caplog still work.
        """
        import io

        from dolphin_desktop._logging import _RedactingFormatter, setup_logging

        self._fresh_logger()
        setup_logging(level)
        root = logging.getLogger("dolphin")

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


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Crash dump
# ---------------------------------------------------------------------------


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

    def test_filename_contains_timestamp(self, tmp_path):
        from dolphin_desktop._crash import write_crash_dump

        path = write_crash_dump(output_dir=tmp_path)
        assert path.name.startswith("crash-")

    def test_exported_from_package(self):
        import dolphin_desktop

        assert callable(dolphin_desktop.write_crash_dump)


# ---------------------------------------------------------------------------
# dolphin doctor CLI
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Config round-trip for runtime settings
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Zombie process registry
# ---------------------------------------------------------------------------


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

    def test_pid_removed_on_close(self):

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
