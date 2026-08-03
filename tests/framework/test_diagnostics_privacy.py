"""Regression locks for logging, config validation, telemetry and diagnostics privacy."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import types
import zipfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dolphin_desktop import config
from dolphin_desktop._config import get_log_level
from dolphin_desktop._logging import ROOT_LOGGER, get_logger, setup_logging

_PYTHON = sys.executable


@pytest.fixture()
def dolphin_logger() -> Iterator[logging.Logger]:
    """Yield the dolphin root logger and restore its state afterwards."""
    logger = logging.getLogger(ROOT_LOGGER)
    saved = (list(logger.handlers), logger.level, logger.propagate)
    saved_level = get_log_level()
    logger.handlers.clear()
    logger.propagate = True
    try:
        yield logger
    finally:
        logger.handlers[:] = saved[0]
        logger.setLevel(saved[1])
        logger.propagate = saved[2]
        config(log_level=saved_level)


def _run_python(code: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for name in ("DOLPHIN_LOG_LEVEL", "DOLPHIN_DESKTOP_LOG_LEVEL"):
        env.pop(name, None)
    env.update(env_overrides)
    return subprocess.run(
        [_PYTHON, "-c", code], env=env, capture_output=True, text=True, timeout=120
    )


# config(log_level=…) must actually reach the logger


class TestConfigAppliesLogLevel:
    def test_level_is_applied_immediately(self, dolphin_logger: logging.Logger):
        config(log_level="ERROR")
        assert dolphin_logger.level == logging.ERROR
        config(log_level="DEBUG")
        assert dolphin_logger.level == logging.DEBUG

    def test_child_loggers_follow(self, dolphin_logger: logging.Logger):
        config(log_level="ERROR")
        assert not get_logger("selfheal").isEnabledFor(logging.INFO)

    def test_no_handler_is_installed(self, dolphin_logger: logging.Logger):
        """A dolphin handler under pytest would duplicate output and kill caplog."""
        config(log_level="DEBUG")
        assert dolphin_logger.handlers == []
        assert dolphin_logger.propagate is True

    def test_existing_handlers_are_relevelled(self, dolphin_logger: logging.Logger):
        handler = logging.StreamHandler()
        handler.setLevel(logging.CRITICAL)
        dolphin_logger.addHandler(handler)
        config(log_level="DEBUG")
        assert handler.level == logging.DEBUG


# Redaction must survive handlers dolphin does not own


class TestRedactionIsHandlerIndependent:
    def test_secret_never_reaches_pytest_log_capture(self, caplog, dolphin_logger):
        """pytest owns the handlers here — a redacting formatter would be bypassed."""
        caplog.set_level(logging.DEBUG, logger=ROOT_LOGGER)
        get_logger("selfheal").error("connecting with password=hunter2xyz")
        assert "hunter2xyz" not in caplog.text
        assert "***" in caplog.text

    def test_a_never_seen_child_logger_is_redacted(self, caplog, dolphin_logger):
        """A filter on the parent does not run for a child's records."""
        caplog.set_level(logging.DEBUG, logger=ROOT_LOGGER)
        get_logger("fresh_child_for_redaction").error("password=hunter2xyz")
        assert "hunter2xyz" not in caplog.text

    def test_child_logger_obtained_via_stdlib_is_redacted(self, caplog, dolphin_logger):
        from dolphin_desktop._logging import install_redaction

        logging.getLogger("dolphin_desktop.plugin")
        install_redaction()
        caplog.set_level(logging.DEBUG, logger=ROOT_LOGGER)
        logging.getLogger("dolphin_desktop.plugin").warning("token=abcdef123456")
        assert "abcdef123456" not in caplog.text

    def test_lazy_percent_args_are_redacted(self, caplog, dolphin_logger):
        caplog.set_level(logging.DEBUG, logger=ROOT_LOGGER)
        get_logger("selfheal").error("using %s", "api_key=sk-9f3a0011")
        assert "sk-9f3a0011" not in caplog.text

    def test_plain_message_is_untouched(self, caplog, dolphin_logger):
        caplog.set_level(logging.DEBUG, logger=ROOT_LOGGER)
        get_logger("selfheal").error("clicking button OK")
        assert "clicking button OK" in caplog.text

    def test_a_bare_stdlib_child_is_redacted_without_any_sweep(self, caplog, dolphin_logger):
        """``objects``/``_recorder``/``_trace`` reach for logging.getLogger directly."""
        caplog.set_level(logging.DEBUG, logger=ROOT_LOGGER)
        logging.getLogger(f"{ROOT_LOGGER}.never_swept_child").error("password=hunter2xyz")
        assert "hunter2xyz" not in caplog.text
        assert "***" in caplog.text

    def test_module_children_are_redacted_after_a_plain_import(self):
        """A library consumer that never calls setup_logging() is still covered."""
        code = (
            "import logging, sys, dolphin_desktop;"
            "root = logging.getLogger('dolphin_desktop');"
            "root.setLevel(logging.DEBUG);"
            "root.addHandler(logging.StreamHandler(sys.stdout));"
            "logging.getLogger('dolphin_desktop.trace').error('token=abcdef123456');"
            "logging.getLogger('dolphin_desktop.recorder').error('secret=zyxwvu987654')"
        )
        result = _run_python(code)
        assert result.returncode == 0, result.stderr
        assert "abcdef123456" not in result.stdout
        assert "zyxwvu987654" not in result.stdout
        assert result.stdout.count("***") == 2

    def test_a_non_dolphin_logger_is_left_alone(self, caplog):
        """The record-factory hook must not rewrite other libraries' records."""
        caplog.set_level(logging.DEBUG, logger="some_other_library")
        logging.getLogger("some_other_library").error("password=hunter2xyz")
        assert "hunter2xyz" in caplog.text

    def test_malformed_format_string_stays_the_handlers_problem(self):
        """A raising filter would surface in the caller of log(); it must not."""
        from dolphin_desktop._logging import _RedactingFilter

        record = logging.LogRecord(
            ROOT_LOGGER, logging.ERROR, __file__, 1, "%d items", ("nope",), None
        )
        assert _RedactingFilter().filter(record) is True


# One log level, one source of truth


class TestLogLevelEnvVarsAgree:
    @pytest.mark.parametrize("var", ["DOLPHIN_LOG_LEVEL", "DOLPHIN_DESKTOP_LOG_LEVEL"])
    def test_either_env_var_drives_setup_logging(self, var: str):
        code = (
            "import logging, dolphin_desktop;"
            "dolphin_desktop.setup_logging();"
            "print(logging.getLogger('dolphin_desktop').level)"
        )
        result = _run_python(code, **{var: "ERROR"})
        assert result.stdout.strip() == str(logging.ERROR), result.stderr

    def test_dolphin_desktop_prefixed_var_wins(self):
        code = (
            "import logging, dolphin_desktop;"
            "dolphin_desktop.setup_logging();"
            "print(logging.getLogger('dolphin_desktop').level)"
        )
        result = _run_python(code, DOLPHIN_LOG_LEVEL="DEBUG", DOLPHIN_DESKTOP_LOG_LEVEL="ERROR")
        assert result.stdout.strip() == str(logging.ERROR), result.stderr

    def test_setup_logging_default_follows_config(self, dolphin_logger: logging.Logger):
        config(log_level="ERROR")
        setup_logging()
        assert dolphin_logger.level == logging.ERROR


# Config validation


class TestConfigValidation:
    def test_import_survives_malformed_env_numbers(self):
        """A typo in the environment must not make `import dolphin_desktop` explode."""
        result = _run_python(
            "import dolphin_desktop",
            DOLPHIN_VIDEO_FPS="abc",
            DOLPHIN_TIMEOUT="soon",
            DOLPHIN_RETRY="lots",
        )
        assert result.returncode == 0, result.stderr

    def test_malformed_env_number_falls_back_with_a_warning(self, monkeypatch):
        from dolphin_desktop._config import _env_number

        monkeypatch.setenv("DOLPHIN_VIDEO_FPS", "abc")
        with pytest.warns(UserWarning, match="DOLPHIN_VIDEO_FPS"):
            assert _env_number("DOLPHIN_VIDEO_FPS", 10, int) == 10

    def test_valid_env_number_is_used(self, monkeypatch):
        from dolphin_desktop._config import _env_number

        monkeypatch.setenv("DOLPHIN_VIDEO_FPS", "24")
        assert _env_number("DOLPHIN_VIDEO_FPS", 10, int) == 24

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"timeout": -1.0}, "timeout"),
            ({"poll_interval": -0.5}, "poll_interval"),
            ({"video_fps": 0}, "video_fps"),
            ({"video_fps": 31}, "video_fps"),
            ({"retry_count": -2}, "retry_count"),
        ],
    )
    def test_out_of_range_values_are_rejected(self, kwargs: dict, match: str):
        with pytest.raises(ValueError, match=match):
            config(**kwargs)

    def test_rejected_value_does_not_mutate_the_store(self):
        from dolphin_desktop._config import get_timeout

        before = get_timeout()
        with pytest.raises(ValueError):
            config(timeout=-1.0)
        assert get_timeout() == before


# Telemetry — flush at exit, no user source


class TestTelemetryIntegrations:
    def test_atexit_integration_is_kept(self):
        """default_integrations=False also drops AtexitIntegration, losing queued events."""
        from dolphin_desktop._telemetry import _integrations

        sentinel = object()
        module = types.ModuleType("sentry_sdk.integrations.atexit")
        module.AtexitIntegration = lambda: sentinel  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"sentry_sdk.integrations.atexit": module}):
            assert _integrations() == [sentinel]

    def test_missing_sentry_yields_no_integrations(self):
        from dolphin_desktop._telemetry import _integrations

        with patch.dict(sys.modules, {"sentry_sdk.integrations.atexit": None}):
            assert _integrations() == []

    def test_init_passes_the_explicit_integration_list(self):
        import dolphin_desktop._telemetry as tel

        sentinel = object()
        fake = MagicMock()
        tel._initialized = False
        try:
            with (
                patch.dict(sys.modules, {"sentry_sdk": fake}),
                patch.dict(os.environ, {"DOLPHIN_TELEMETRY": "on"}),
                patch.object(tel, "_DOLPHIN_DSN", "https://key@example.invalid/1"),
                patch.object(tel, "_integrations", return_value=[sentinel]),
            ):
                tel.init()
        finally:
            tel._initialized = False
        assert fake.init.call_args.kwargs["integrations"] == [sentinel]


class TestTelemetryStripsUserSource:
    def _event(self) -> dict:
        return {
            "exception": {
                "values": [
                    {
                        "stacktrace": {
                            "frames": [
                                {
                                    "module": "mytests.test_login",
                                    "context_line": '    Keyboard.type("hunter2")',
                                    "pre_context": ["def test_login():"],
                                    "post_context": ["    assert logged_in()"],
                                    "vars": {"pwd": "hunter2"},
                                },
                                {
                                    "module": "dolphin_desktop._locator",
                                    "context_line": "    raise ElementNotFoundError(msg)",
                                },
                            ]
                        }
                    }
                ]
            }
        }

    def _send(self, event: dict) -> dict | None:
        from dolphin_desktop._exceptions import ElementNotFoundError
        from dolphin_desktop._telemetry import _before_send

        exc = ElementNotFoundError("el")
        return _before_send(event, {"exc_info": (type(exc), exc, None)})

    def test_user_frame_loses_every_source_key(self):
        sent = self._send(self._event())
        assert sent is not None
        user_frame = sent["exception"]["values"][0]["stacktrace"]["frames"][0]
        for key in ("context_line", "pre_context", "post_context", "vars"):
            assert key not in user_frame

    def test_no_line_of_user_source_survives(self):
        sent = self._send(self._event())
        assert "hunter2" not in json.dumps(sent)

    def test_dolphin_frame_keeps_its_source(self):
        sent = self._send(self._event())
        assert sent is not None
        dolphin_frame = sent["exception"]["values"][0]["stacktrace"]["frames"][1]
        assert "ElementNotFoundError" in dolphin_frame["context_line"]

    def test_dropped_events_are_still_dropped(self):
        from dolphin_desktop._telemetry import _before_send

        event = self._event()
        event["exception"]["values"][0]["stacktrace"]["frames"].reverse()
        hint = {"exc_info": (ValueError, ValueError("oops"), None)}
        assert _before_send(event, hint) is None


# Self-healing telemetry file


class TestSelfhealTelemetryFile:
    @pytest.fixture()
    def stats_path(self, tmp_path: Path, monkeypatch) -> Path:
        path = tmp_path / "selfheal.jsonl"
        monkeypatch.setenv("DOLPHIN_SELFHEAL_FILE", str(path))
        return path

    def test_env_var_is_honoured_when_set_after_import(self, stats_path: Path):
        from dolphin_desktop._selfheal import record_fallback, selfheal_stats

        record_fallback({"title": "OK"}, {"auto_id": "btnOk"}, test_name="t")
        assert stats_path.exists()
        assert selfheal_stats()[-1]["primary"] == {"title": "OK"}

    def test_secrets_in_criteria_are_redacted(self, stats_path: Path):
        from dolphin_desktop._selfheal import record_fallback

        record_fallback({"title": "password=hunter2xyz"}, {"auto_id": "pwdField"}, test_name="t")
        text = stats_path.read_text(encoding="utf-8")
        assert "hunter2xyz" not in text
        assert json.loads(text.splitlines()[0])["primary"]["title"] == "password=***"

    def test_log_is_rotated_at_the_size_cap(self, stats_path: Path):
        from dolphin_desktop._selfheal import _MAX_BYTES, record_fallback

        stats_path.write_text("x" * (_MAX_BYTES + 1), encoding="utf-8")
        record_fallback({"title": "OK"}, {"auto_id": "btnOk"}, test_name="t")

        assert stats_path.with_name(stats_path.name + ".1").exists()
        assert stats_path.stat().st_size < 1024

    def test_small_log_is_not_rotated(self, stats_path: Path):
        from dolphin_desktop._selfheal import record_fallback

        record_fallback({"title": "OK"}, {"auto_id": "btnOk"}, test_name="t")
        record_fallback({"title": "Cancel"}, {"auto_id": "btnCancel"}, test_name="t")

        assert not stats_path.with_name(stats_path.name + ".1").exists()
        assert len(stats_path.read_text(encoding="utf-8").splitlines()) == 2


# Crash dump


def _window(pid: int, title: str, cls: str = "Win32Class") -> MagicMock:
    win = MagicMock()
    win.process_id.return_value = pid
    win.window_text.return_value = title
    win.class_name.return_value = cls
    win.children.return_value = []
    return win


def _uia_tree(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("uia_tree.txt").decode()


class TestCrashDumpFilename:
    def test_two_dumps_in_the_same_second_do_not_overwrite(self, tmp_path: Path):
        from dolphin_desktop._crash import write_crash_dump

        first = write_crash_dump(output_dir=tmp_path)
        second = write_crash_dump(output_dir=tmp_path)

        assert first != second
        assert len(list(tmp_path.glob("crash-*.zip"))) == 2

    def test_name_still_starts_with_crash(self, tmp_path: Path):
        from dolphin_desktop._crash import write_crash_dump

        assert write_crash_dump(output_dir=tmp_path).name.startswith("crash-")


class TestCrashDumpUiaScope:
    def test_other_processes_windows_are_not_captured(self, tmp_path: Path):
        import pywinauto

        from dolphin_desktop._crash import write_crash_dump

        mine = _window(4242, "Order Entry - AUT")
        theirs = _window(9999, "Inbox: Q3 payroll - Mail")

        with patch.object(pywinauto, "Desktop") as desktop:
            desktop.return_value.windows.return_value = [theirs, mine]
            path = write_crash_dump(output_dir=tmp_path, pid=4242)

        tree = _uia_tree(path)
        assert "Order Entry - AUT" in tree
        assert "payroll" not in tree

    def test_all_windows_opt_in_captures_everything(self, tmp_path: Path):
        import pywinauto

        from dolphin_desktop._crash import write_crash_dump

        with patch.object(pywinauto, "Desktop") as desktop:
            desktop.return_value.windows.return_value = [_window(9999, "Some other app")]
            path = write_crash_dump(output_dir=tmp_path, all_windows=True)

        assert "Some other app" in _uia_tree(path)

    def test_attached_processes_are_in_scope(self, tmp_path: Path):
        """SAP/mainframe/Oracle Forms attach with owns_process=False.

        Those PIDs never enter the launched-process kill lists, so scoping the
        capture to those alone yields the empty-placeholder artifact for
        dolphin's primary workflows.
        """
        import pywinauto

        from dolphin_desktop import _application
        from dolphin_desktop._crash import write_crash_dump

        attached = _window(7777, "SAP Easy Access")
        with (
            patch.object(_application, "_live_pids", set(), create=True),
            patch.object(_application, "_session_pids", set(), create=True),
            patch.object(_application, "_attached_pids", {7777}, create=True),
            patch.object(pywinauto, "Desktop") as desktop,
        ):
            desktop.return_value.windows.return_value = [attached]
            path = write_crash_dump(output_dir=tmp_path)

        assert "SAP Easy Access" in _uia_tree(path)

    def test_a_missing_attached_pid_set_does_not_break_the_dump(self, monkeypatch):
        from dolphin_desktop import _application
        from dolphin_desktop._crash import _target_pids

        monkeypatch.delattr(_application, "_attached_pids", raising=False)
        monkeypatch.setattr(_application, "_live_pids", {11}, raising=False)
        monkeypatch.setattr(_application, "_session_pids", {22}, raising=False)
        assert _target_pids(None) == {11, 22}

    def test_unscoped_capture_is_refused_by_default(self, tmp_path: Path):
        from dolphin_desktop._crash import write_crash_dump

        with patch("dolphin_desktop._crash._target_pids", return_value=set()):
            path = write_crash_dump(output_dir=tmp_path)

        assert "all_windows=True" in _uia_tree(path)

    def test_walk_is_bounded_in_time(self, tmp_path: Path):
        import pywinauto

        from dolphin_desktop._crash import write_crash_dump

        with patch.object(pywinauto, "Desktop") as desktop:
            desktop.return_value.windows.return_value = [_window(4242, f"w{i}") for i in range(50)]
            path = write_crash_dump(output_dir=tmp_path, pid=4242, uia_budget=0.0)

        assert "budget exhausted" in _uia_tree(path)


# Failure text is a sink like any other


class TestRedactionCoversFailureText:
    """``pytest -l`` renders a captured local as ``password = 'hunter2'``.

    That format went through unredacted while the same string passed to
    ``log.info`` was masked, and the pytest ``longrepr`` is persisted to
    trace.db, rendered into trace.html and zipped into the Allure attachment.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "E       assert password = 'hunter2'",
            'password = "hunter2"',
            '{"password":"hunter2"}',
            "token: abcdef123456",
            "api_key=sk-abcdef123456",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9",
        ],
    )
    def test_a_secret_never_survives(self, text: str) -> None:
        from dolphin_desktop._logging import _redact

        redacted = _redact(text)
        assert "***" in redacted
        for secret in ("hunter2", "abcdef123456", "sk-abcdef123456", "eyJhbGciOiJIUzI1NiJ9"):
            assert secret not in redacted

    def test_ordinary_prose_is_left_alone(self) -> None:
        from dolphin_desktop._logging import _redact

        assert _redact("no secrets here at all") == "no secrets here at all"

    def test_the_plugin_redacts_longrepr_before_storing_it(self) -> None:
        """Pins the call site, not just the helper."""
        import inspect

        from dolphin_desktop import pytest_plugin

        source = inspect.getsource(pytest_plugin)
        assert "_redact(str(report.longrepr))" in source


class TestRedactionDoesNotEatOrdinaryText:
    """The pattern runs over pytest's longrepr, where prose is common.

    Without a word boundary after the keyword, "secrets" and "tokens" matched
    as the keyword and masked the following word — destroying the assertion
    diff in the one artifact meant to explain a failure.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "assert 'secret' == 'secrets'",
            "secret santa list",
            "password field detected: QLineEdit",
            "no secrets here at all",
            "the token is fine",
        ],
    )
    def test_prose_is_left_alone(self, text: str) -> None:
        from dolphin_desktop._logging import _redact

        assert _redact(text) == text

    def test_the_json_shape_is_still_redacted(self) -> None:
        """A closing quote before the separator — missing this was a leak."""
        from dolphin_desktop._logging import _redact

        assert _redact('{"password":"hunter2xyz"}') == '{"password":"***"}'

    def test_a_pathological_input_does_not_backtrack(self) -> None:
        import time

        from dolphin_desktop._logging import _redact

        start = time.perf_counter()
        _redact("authorization:" + "=" * 20_000)
        assert time.perf_counter() - start < 1.0


class TestRedactionShapesThatLeakedBefore:
    """Locks the shapes two earlier versions of the pattern let through.

    Both regressions came from tuning against false positives without a
    counter-corpus: raising the minimum value length printed short PINs in
    full, and excluding ``,``/``;`` from the value meant a password
    containing one leaked entirely, because the part before it was too short
    to match. Every value here is deliberately short or punctuated.
    """

    @pytest.mark.parametrize(
        ("text", "secret"),
        [
            ("token=abcd", "abcd"),
            ("password=12345", "12345"),
            ("secret=abcde", "abcde"),
            ("Password=P@ss,w0rd", "P@ss,w0rd"),
            ("password=my;pass", "my;pass"),
            ("DB_PASSWORD=tiger123", "tiger123"),
            ("?token=abcd1234&next=1", "abcd1234"),
            ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
            ("API_KEY=SK-ABCDEF", "SK-ABCDEF"),
            ("apiKey: abcd1234", "abcd1234"),
            ("api-key=abcd1234", "abcd1234"),
            ("password:\thunter2tab", "hunter2tab"),
            ("password: hunter2\n  next: line", "hunter2"),
        ],
    )
    def test_the_value_never_survives(self, text: str, secret: str) -> None:
        from dolphin_desktop._logging import _redact

        out = _redact(text)
        assert secret not in out, f"{secret!r} survived in {out!r}"
        assert "***" in out

    @pytest.mark.parametrize(
        "text",
        [
            # A separator must not cross a newline: a log line ending in a
            # keyword used to mask the first word of the next line.
            "click on Password\nWARNING dolphin: auth failed",
            "Enter your password\nTraceback (most recent call last):",
            "auth failed for host example.com",
            "token expired 3 minutes ago",
            "authorization denied by policy",
        ],
    )
    def test_prose_and_line_boundaries_are_untouched(self, text: str) -> None:
        from dolphin_desktop._logging import _redact

        assert _redact(text) == text

    def test_the_query_string_keeps_its_other_parameters(self) -> None:
        from dolphin_desktop._logging import _redact

        assert _redact("?token=abcd1234&next=1") == "?token=***&next=1"


class TestRedactionAdversarialCorpus:
    """Shapes an adversarial review generated against earlier versions.

    Four iterations of this pattern each leaked a different family. The two
    lists below are kept together on purpose: every previous regression came
    from tuning against one direction without a corpus for the other.
    """

    @pytest.mark.parametrize(
        ("text", "secret"),
        [
            # Compound identifiers — a word boundary after the keyword missed
            # all of these, including the most canonical secret env var there is.
            ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG", "wJalrXUtnFEMI/K7MDENG"),
            ("secret_key=AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
            ("private_key=MIIEvQIBADANBgkq", "MIIEvQIBADANBgkq"),
            ("password_hash=$2b$12$abcdefgh", "$2b$12$abcdefgh"),
            ("api_key_id=AKIA123456789", "AKIA123456789"),
            ("passwords=Sup3rS3cret", "Sup3rS3cret"),
            ("access_token=ya29.AHES6ZR", "ya29.AHES6ZR"),
            ("refresh_token=1//0gabc", "1//0gabc"),
            ("client_secret=GOCSPX-abc123", "GOCSPX-abc123"),
            # Keyword spellings that carry credentials in the wild.
            ("pwd=Sup3rS3cret", "Sup3rS3cret"),
            ("passphrase=correcthorsebattery", "correcthorsebattery"),
            ("credential=Sup3rS3cret", "Sup3rS3cret"),
            ("signature=aGVsbG93b3JsZA", "aGVsbG93b3JsZA"),
            ("Set-Cookie: sessionid=38afes7a8; HttpOnly", "38afes7a8"),
            ("UID=sa;PWD=Sup3rS3cret;Trust=yes", "Sup3rS3cret"),
            # Auth schemes beyond Bearer/Basic. Masking the scheme and printing
            # the credential after it looks redacted and is not.
            ("Authorization: NTLM TlRMTVNTUAABAAAAB4IIogAAAAA", "TlRMTVNTUAABAAAAB4IIogAAAAA"),
            ("Authorization: Negotiate YIIZkAYGKwYBBQUCoIIZ", "YIIZkAYGKwYBBQUCoIIZ"),
            ("Authorization: Token abcd1234efgh", "abcd1234efgh"),
            ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9", "eyJhbGciOiJIUzI1NiJ9"),
            ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
            # Quoted values run to the closing quote, spaces and commas included.
            ('password="correct horse battery staple"', "correct horse battery staple"),
            ("password = 'correct horse battery staple'", "correct horse battery staple"),
            ("Password=P@ss,w0rd", "P@ss,w0rd"),
            ("password=Sup3r&Secret", "Sup3r&Secret"),
        ],
    )
    def test_the_credential_never_survives(self, text: str, secret: str) -> None:
        from dolphin_desktop._logging import _redact

        out = _redact(text)
        assert secret not in out, f"{secret!r} survived in {out!r}"
        assert "***" in out

    @pytest.mark.parametrize(
        "text",
        [
            # Assertion diffs must stay readable — masking a Python literal
            # turned both sides of a comparison into the same string.
            "E       assert {'auth': False} == {'auth': True}",
            "assert resp.token == 'expected'",
            # "author" is not "auth".
            "author=Jan Kowalski",
        ],
    )
    def test_failure_text_is_not_corrupted(self, text: str) -> None:
        from dolphin_desktop._logging import _redact

        assert _redact(text) == text

    def test_a_quoted_value_keeps_its_quotes(self) -> None:
        """The mask replaces the value, not the delimiters around it."""
        from dolphin_desktop._logging import _redact

        assert _redact('password="hunter2"') == 'password="***"'
        assert _redact("password='hunter2'") == "password='***'"


class TestRedactionStaysLinear:
    """Redaction runs on every log line, so a slow pattern is a real defect.

    An earlier rebuild made the cost quadratic in the length of a run of
    identifier characters — 44 KB took 1.7 s and a 2 MB log line hung for
    minutes. The pattern is a security filter over untrusted text, so this is
    a denial-of-service surface, not just a slow test.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "plain-run",
            "keyword-then-spaces",
            "keyword-then-value-run",
            "repeated-assignment",
            # The shape that was quadratic: a long unbroken identifier run, as
            # a base64 blob or minified source in a traceback would be.
            "identifier-run",
        ],
    )
    def test_a_large_input_is_processed_promptly(self, name: str) -> None:
        import time

        from dolphin_desktop._logging import _redact

        probe = {
            "plain-run": "x" * 200_000,
            "keyword-then-spaces": "auth" + " " * 20_000 + "x",
            "keyword-then-value-run": "token:" + "a" * 100_000,
            "repeated-assignment": "password=a," * 4_000,
            "identifier-run": "AWS_SECRET_ACCESS_KEY_" * 2_000,
        }[name]

        start = time.perf_counter()
        _redact(probe)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"{name}: {len(probe)} chars took {elapsed:.1f}s"


class TestRedactionKeepsTracebacksReadable:
    """A trace exists to explain a failure; masking its coordinates defeats it.

    Matching the keyword inside an identifier is what reaches
    ``AWS_SECRET_ACCESS_KEY`` — and it also reaches a *filename*, so a test in
    ``test_sap_auth.py`` had the failing line number replaced by the mask.
    """

    @pytest.mark.parametrize(
        "line",
        [
            "tests/test_sap_auth.py:42: AssertionError",
            "src/dolphin_desktop/_secret.py:118: in _load",
            "tests/framework/test_auth_flow.py::TestLogin::test_ok",
            "config/auth.json:12: parse error",
            "settings/token.yaml:7: bad key",
        ],
    )
    def test_a_source_location_is_untouched(self, line: str) -> None:
        from dolphin_desktop._logging import _redact

        assert _redact(line) == line

    @pytest.mark.parametrize(
        "text", ["credentials=None", "credentials=None, retries=3", "token=True"]
    )
    def test_a_python_literal_is_untouched_even_at_end_of_string(self, text: str) -> None:
        """A log record has no trailing newline, so the exclusion cannot rely
        on a delimiter following the literal."""
        from dolphin_desktop._logging import _redact

        assert _redact(text) == text

    @pytest.mark.parametrize(
        ("text", "secret"),
        [
            ("password=Nonexistent9", "Nonexistent9"),
            ("secret=Falsehood1", "Falsehood1"),
            ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI", "wJalrXUtnFEMI"),
        ],
    )
    def test_the_exclusions_do_not_open_a_hole(self, text: str, secret: str) -> None:
        from dolphin_desktop._logging import _redact

        assert secret not in _redact(text)


class TestSelfhealJournalStaysParseable:
    """The journal is JSONL; redacting the serialised text rewrote the document.

    ``selfheal_stats()`` swallows ``JSONDecodeError``, so one corrupt line
    silently truncated the whole history from that point — an invisible
    failure mode.
    """

    def test_a_value_with_a_quote_round_trips(self, tmp_path, monkeypatch) -> None:
        import json

        from dolphin_desktop import _selfheal

        journal = tmp_path / "selfheal.jsonl"
        monkeypatch.setattr(_selfheal, "_stats_file", lambda: journal)
        _selfheal.record_fallback(
            primary='password="he said \\"hi\\""', fallback_used="token=42", test_name="t"
        )
        line = journal.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)  # would raise before the fix
        assert "42" not in parsed["fallback"]
        assert _selfheal.selfheal_stats(file=journal)
