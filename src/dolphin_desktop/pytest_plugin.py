"""pytest plugin — auto-registered via the project.entry-points."pytest11" entry point."""

from __future__ import annotations

import math
import os
import re
import time
import uuid
import zipfile
from argparse import ArgumentTypeError
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ._desktop import Desktop
from ._logging import _redact
from ._logging import get_logger as _get_logger

_TRACE_SESSION_KEY: pytest.StashKey[Any] = pytest.StashKey()
_VIDEO_RECORDER_KEY: pytest.StashKey[Any] = pytest.StashKey()
_RETRY_ATTEMPT_KEY: pytest.StashKey[int] = pytest.StashKey()
_RETRY_MAX_KEY: pytest.StashKey[int] = pytest.StashKey()
_DOLPHIN_TRANSIENT_ATTR = "_dolphin_transient_failure"

_session_reports: list[dict[str, Any]] = []


def _cli_timeout(value: str) -> float:
    """Parse a finite, non-negative timeout for pytest's CLI parser."""
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ArgumentTypeError(
            f"timeout must be a finite non-negative number; got {value!r}"
        ) from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ArgumentTypeError(f"timeout must be a finite non-negative number; got {value!r}")
    return parsed


def _cli_retry(value: str) -> int:
    """Parse a non-negative retry count for pytest's CLI parser."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ArgumentTypeError(f"retry must be a non-negative integer; got {value!r}") from exc
    if parsed < 0:
        raise ArgumentTypeError(f"retry must be a non-negative integer; got {value!r}")
    return parsed


def pytest_sessionstart(session: pytest.Session) -> None:
    _session_reports.clear()


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """Kill any zombie processes left over from a crashed or improperly cleaned-up test."""
    from . import _application

    pids = list(_application._live_pids)
    if not pids:
        return

    log = _get_logger("plugin")
    for pid in pids:
        anchored_handle = getattr(_application, "_owned_process_handles", {}).get(pid)
        if anchored_handle is not None:
            try:
                import win32api  # type: ignore[import]

                # The handle is bound to the original kernel process object;
                # never reopen the numeric PID, which may have been reused.
                win32api.TerminateProcess(anchored_handle, 1)
                win32api.CloseHandle(anchored_handle)
                getattr(_application, "_owned_process_handles", {}).pop(pid, None)
                getattr(_application, "_unanchored_pids", set()).discard(pid)
                _application._live_pids.discard(pid)
                log.debug("Killed zombie process PID=%d after test %s", pid, item.nodeid)
            except Exception as exc:
                # Keep both the handle and PID registered. The session
                # finalizer or a later explicit cleanup can retry and the
                # failure remains visible in diagnostics.
                log.warning(
                    "Could not clean up anchored process PID=%d after test %s: %s",
                    pid,
                    item.nodeid,
                    exc,
                )
            continue
        if pid in getattr(_application, "_unanchored_pids", set()):
            log.warning(
                "Skipping PID-only cleanup for unanchored process PID=%d after test %s",
                pid,
                item.nodeid,
            )
            continue
        try:
            import win32api  # type: ignore[import]
            import win32con  # type: ignore[import]

            handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
            win32api.TerminateProcess(handle, 1)
            win32api.CloseHandle(handle)
            log.debug("Killed zombie process PID=%d after test %s", pid, item.nodeid)
        except Exception:
            pass
        finally:
            _application._live_pids.discard(pid)


def _is_transient_failure(report: pytest.TestReport | None) -> bool:
    """True when ``report`` is a failed report caused by a transient dolphin error."""
    return bool(report and report.failed and getattr(report, _DOLPHIN_TRANSIENT_ATTR, False))


def _attempt_will_retry(item: pytest.Item, report: pytest.TestReport | None) -> bool:
    """True if the current attempt failed transiently and another retry remains.

    Read by ``pytest_runtest_makereport`` so it can suppress the artifacts and report
    accumulation of an attempt whose reports will never be published.
    """
    attempt = item.stash.get(_RETRY_ATTEMPT_KEY, None)
    if attempt is None:
        return False
    if attempt >= item.stash.get(_RETRY_MAX_KEY, 0):
        return False
    return _is_transient_failure(report)


def _effective_retry_count(config: pytest.Config) -> int:
    """Resolve the retry count with ``CLI > env > default`` precedence.

    ``--dolphin-retry`` is normally pushed into the global config by the session-scoped
    ``_dolphin_apply_session_config`` fixture, but that fixture runs *inside* the test
    protocol — too late for ``pytest_runtest_protocol`` to see it. So read the CLI option
    directly here, falling back to the env-or-default value tracked by ``_config``.
    """
    from . import _config

    cli_retry: int | None = config.getoption("--dolphin-retry", default=None)
    if cli_retry is not None:
        return int(cli_retry)
    return _config.get_retry_count()


def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> bool | None:
    """Retry tests on transient dolphin errors when ``--dolphin-retry N`` > 0.

    Each attempt runs with ``log=False`` so the reports of an attempt that is about to
    be retried never reach pytest's reporters (terminal/JUnit) and never increment
    ``Session.testsfailed`` (which would fail the session and could trip ``--maxfail``).
    Only the final attempt — a pass, a non-transient failure, or the last retry — has
    its reports published.
    """
    max_retries = _effective_retry_count(item.config)
    if max_retries <= 0:
        return None  # let pytest handle normally

    log = _get_logger("plugin")

    from _pytest.runner import runtestprotocol  # type: ignore[import]

    ihook = item.ihook
    ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)

    item.stash[_RETRY_MAX_KEY] = max_retries
    for attempt in range(max_retries + 1):
        item.stash[_RETRY_ATTEMPT_KEY] = attempt
        reports = runtestprotocol(item, log=False, nextitem=nextitem)
        call_report = next((r for r in reports if r.when == "call"), None)

        if attempt < max_retries and _is_transient_failure(call_report):
            log.info(
                "Retrying test %s (attempt %d/%d) after transient error",
                item.nodeid,
                attempt + 1,
                max_retries,
            )
            continue

        # Final attempt — publish its reports so they are counted and rendered normally.
        for rep in reports:
            ihook.pytest_runtest_logreport(report=rep)
        break

    ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True  # signal that we handled the protocol


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "dolphin(timeout=..., video_mode=..., headless=...): per-test dolphin config overrides",
    )
    config.addinivalue_line(
        "markers",
        "dolphin_headless: mark test as requiring headless desktop mode "
        "(skip with pytest -m 'not dolphin_headless' when running interactively)",
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("dolphin", "Dolphin desktop testing")
    group.addoption(
        "--dolphin-backend",
        default="uia",
        choices=["uia", "win32"],
        help="pywinauto backend to use (default: uia)",
    )
    group.addoption(
        "--dolphin-timeout",
        type=_cli_timeout,
        default=None,
        help="Default element wait timeout in seconds (default: 10; also: DOLPHIN_TIMEOUT env var)",
    )
    group.addoption(
        "--dolphin-screenshot-on-fail",
        action="store_true",
        default=False,
        help="Capture a screenshot of the active window on test failure",
    )
    group.addoption(
        "--dolphin-headless",
        action="store_true",
        default=False,
        help=(
            "Run AUT processes on a hidden desktop (DolphinHidden). "
            "For full UIA support, invoke tests via 'dolphin-run pytest tests/' "
            "which also moves the test runner itself onto the hidden desktop."
        ),
    )
    group.addoption(
        "--dolphin-trace",
        default=None,
        choices=["off", "on-failure", "always"],
        metavar="{off,on-failure,always}",
        help=(
            "Trace capture mode: 'off' disables tracing, 'on-failure' (default) "
            "saves trace only for failed tests, 'always' keeps all traces."
        ),
    )
    group.addoption(
        "--dolphin-trace-dir",
        default="dolphin-traces",
        metavar="PATH",
        help="Directory for trace files (default: dolphin-traces)",
    )
    group.addoption(
        "--dolphin-video",
        default=None,
        choices=["off", "keepfailedonly", "keepall"],
        metavar="{off,keepfailedonly,keepall}",
        help=(
            "Video recording mode: 'off' disables recording, 'keepfailedonly' (default) "
            "saves MP4 only for failed tests, 'keepall' keeps every test video."
        ),
    )
    group.addoption(
        "--dolphin-video-dir",
        default="dolphin-videos",
        metavar="PATH",
        help="Directory for video files (default: dolphin-videos)",
    )
    group.addoption(
        "--dolphin-html",
        default=None,
        metavar="PATH",
        help=(
            "Write an HTML summary report to PATH. "
            "Generated automatically as dolphin-report.html when allure-pytest is absent."
        ),
    )
    group.addoption(
        "--dolphin-desktop-log-level",
        default=None,
        choices=["DEBUG", "INFO", "ERROR"],
        metavar="{DEBUG,INFO,ERROR}",
        help=(
            "Dolphin log verbosity (default: INFO; also: DOLPHIN_LOG_LEVEL env var). "
            "DEBUG shows per-action detail; ERROR suppresses INFO messages."
        ),
    )
    group.addoption(
        "--dolphin-retry",
        type=_cli_retry,
        default=None,
        metavar="N",
        help=(
            "Retry a test up to N times when it fails with a transient dolphin error "
            "(ElementNotFoundError / WaitTimeoutError). Default: 0 (no retry). "
            "Also: DOLPHIN_RETRY env var."
        ),
    )


# Session-scoped fixtures


@pytest.fixture(scope="session")
def dolphin_backend(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--dolphin-backend")  # type: ignore[return-value]


@pytest.fixture(scope="session")
def dolphin_timeout(request: pytest.FixtureRequest) -> float:
    """Effective global timeout: CLI > DOLPHIN_TIMEOUT env var > built-in default."""
    from ._config import _env_number

    cli_val: float | None = request.config.getoption("--dolphin-timeout")
    if cli_val is not None:
        if not math.isfinite(cli_val):
            raise ValueError("timeout must be finite")
        if cli_val < 0:
            raise ValueError("timeout must be non-negative")
        return cli_val
    return float(_env_number("DOLPHIN_TIMEOUT", 10.0, float, minimum=0))


@pytest.fixture(scope="session", autouse=True)
def _dolphin_apply_session_config(dolphin_timeout: float, request: pytest.FixtureRequest) -> None:
    """Push CLI/env settings into dolphin global config and set up logging/telemetry."""
    from . import _config as _cfg
    from ._logging import apply_log_level, install_redaction
    from ._telemetry import init as _init_telemetry

    _cfg.config(timeout=dolphin_timeout)

    cli_trace: str | None = request.config.getoption("--dolphin-trace", default=None)
    if cli_trace is not None:
        _cfg._defaults["trace_mode"] = cli_trace

    cli_video: str | None = request.config.getoption("--dolphin-video", default=None)
    if cli_video is not None:
        _cfg._defaults["video_mode"] = cli_video

    cli_log_level: str | None = request.config.getoption(
        "--dolphin-desktop-log-level", default=None
    )
    if cli_log_level is not None:
        _cfg._defaults["log_level"] = cli_log_level

    cli_retry: int | None = request.config.getoption("--dolphin-retry", default=None)
    if cli_retry is not None:
        _cfg._defaults["retry_count"] = cli_retry

    # Under pytest the logging plugin owns the handlers, so dolphin installs none —
    # it sets the level and attaches the redacting filter to the loggers themselves,
    # which is the only placement that survives propagation to caplog / the terminal.
    # Do NOT set propagate=False here: caplog fixtures in tests rely on propagation.
    apply_log_level()
    install_redaction()

    _init_telemetry()


@pytest.fixture(scope="session")
def dolphin_headless(request: pytest.FixtureRequest) -> bool:
    """``True`` when headless mode is active (``--dolphin-headless`` or ``DOLPHIN_HEADLESS=1``)."""
    return bool(
        request.config.getoption("--dolphin-headless", default=False)
        or os.environ.get("DOLPHIN_HEADLESS") == "1"
    )


@pytest.fixture(scope="session")
def desktop(dolphin_backend: str, dolphin_headless: bool) -> Desktop:
    """Session-scoped Desktop instance — cheap to create, safe to share."""
    return Desktop(backend=dolphin_backend, hidden=True if dolphin_headless else None)


# Per-test fixtures


@pytest.fixture(autouse=True)
def _dolphin_marker_config(request: pytest.FixtureRequest) -> Iterator[None]:
    """Apply per-test config from ``@pytest.mark.dolphin(timeout=X, headless=True, ...)``.

    * ``timeout`` — overrides the global wait timeout for the duration of the test.
    * ``video_mode`` — overrides the video recording mode for this test.
    * ``headless=True`` — skips the test when the session is not running headlessly
      (i.e. when neither ``--dolphin-headless`` nor ``DOLPHIN_HEADLESS=1`` is set).
    """
    marker = request.node.get_closest_marker("dolphin")
    if marker is None:
        yield
        return

    from . import _config as _cfg

    marker_kwargs = marker.kwargs
    timeout = marker_kwargs.get("timeout")
    if timeout is not None:
        try:
            timeout = _cli_timeout(str(timeout))
        except ArgumentTypeError as exc:
            raise pytest.UsageError(
                f"Invalid @pytest.mark.dolphin timeout={marker_kwargs['timeout']!r}: {exc}"
            ) from exc

    video_mode = marker_kwargs.get("video_mode")
    if video_mode is not None:
        valid_video_modes = _cfg._video_modes()
        if not isinstance(video_mode, str) or video_mode not in valid_video_modes:
            raise pytest.UsageError(
                "Invalid @pytest.mark.dolphin "
                f"video_mode={video_mode!r}: expected one of {valid_video_modes}."
            )

    if "headless" in marker_kwargs and not isinstance(marker_kwargs["headless"], bool):
        raise pytest.UsageError(
            "Invalid @pytest.mark.dolphin "
            f"headless={marker_kwargs['headless']!r}: expected a boolean."
        )

    # headless guard — evaluated before any state mutation so nothing to restore on skip
    headless = marker_kwargs.get("headless")
    if headless is True:
        is_headless = bool(
            request.config.getoption("--dolphin-headless", default=False)
            or os.environ.get("DOLPHIN_HEADLESS") == "1"
        )
        if not is_headless:
            pytest.skip("requires headless mode (--dolphin-headless / DOLPHIN_HEADLESS=1)")

    old_timeout = _cfg._defaults.get("timeout")
    old_video_mode = _cfg._defaults.get("video_mode")

    if timeout is not None:
        _cfg._defaults["timeout"] = timeout

    if video_mode is not None:
        _cfg._defaults["video_mode"] = video_mode

    try:
        yield
    finally:
        if old_timeout is not None:
            _cfg._defaults["timeout"] = old_timeout
        if old_video_mode is not None:
            _cfg._defaults["video_mode"] = old_video_mode


def _trace_run_dir_name(nodeid: str) -> str:
    """Build a collision-free trace run directory name for *nodeid*.

    The timestamp only has 1-second resolution, which is not enough to separate
    retry attempts of one test (they are 0.5s apart) or two xdist workers running
    parametrisations whose truncated nodeids are identical — two TraceSessions
    sharing one trace.db corrupt each other's run selection and rmtree.
    """
    safe = re.sub(r"[^\w._-]", "_", nodeid)[:60]
    return f"{safe}_{int(time.time())}_{os.getpid()}_{uuid.uuid4().hex[:6]}"


def _artifact_file_stem(nodeid: str) -> str:
    """Build a collision-free artifact file name (no extension) for *nodeid*.

    Truncating the sanitised nodeid alone is not unique: two parametrisations
    sharing their first 60 characters, a retry attempt, or two xdist workers all
    produce the same stem, so one overwrites the other's ``.png`` / ``.mp4`` and
    both report rows end up pointing at the survivor.
    """
    safe = re.sub(r"[^\w._-]", "_", nodeid)[:60]
    return f"{safe}_{os.getpid()}_{uuid.uuid4().hex[:6]}"


@pytest.fixture(autouse=True)
def _dolphin_trace(request: pytest.FixtureRequest) -> Iterator[None]:
    """Per-test trace session lifecycle."""
    from . import _config as _cfg
    from . import _trace

    mode = _cfg.get_trace_mode()
    if mode == "off":
        yield
        return

    trace_root = Path(str(request.config.rootpath)) / request.config.getoption(
        "--dolphin-trace-dir", default="dolphin-traces"
    )
    run_dir = trace_root / _trace_run_dir_name(request.node.nodeid)

    try:
        session = _trace.TraceSession(
            test_nodeid=request.node.nodeid,
            run_dir=run_dir,
            mode=mode,
        )
    except Exception as exc:
        # Tracing is observational — record_step() and finish() go out of their
        # way never to raise, and construction must hold the same contract. It
        # creates directories and opens a sqlite file, so a read-only workspace,
        # an AV lock on trace.db or a run_dir that crosses MAX_PATH would
        # otherwise turn every single test into a setup ERROR with nothing in
        # the message pointing at tracing as the cause.
        _get_logger("plugin").warning(
            "tracing disabled for this test — could not open the trace store at %s: %s",
            run_dir,
            exc,
        )
        yield
        return
    _trace.set_current_session(session)
    request.node.stash[_TRACE_SESSION_KEY] = session

    try:
        yield
    finally:
        _trace.set_current_session(None)
        # Safety net for paths where pytest_runtest_makereport never finalised the
        # session (interrupted run) — an open sqlite handle would otherwise survive
        # the whole session and block cleanup of the trace dir on Windows.
        session.close_without_finish()


@pytest.fixture(autouse=True)
def _dolphin_video(request: pytest.FixtureRequest) -> Iterator[None]:
    """Per-test video recording lifecycle."""
    from . import _config as _cfg
    from . import _video

    mode = _cfg.get_video_mode()
    if mode == "off":
        yield
        return

    recorder = _video.VideoRecorder(fps=_cfg.get_video_fps())
    try:
        recorder.start()
    except (RuntimeError, OSError) as exc:
        # A missing ffmpeg is a configuration, not a fault; a refusal to capture
        # (session 0, locked workstation, dropped RDP) is the diagnostic the
        # recorder went to the trouble of collecting, so it must not be dropped.
        # OSError covers a DOLPHIN_FFMPEG that points at something unspawnable —
        # video is an accessory, and must never turn a whole run into setup errors.
        log = _get_logger("plugin")
        if _video.find_ffmpeg() is None:
            log.debug("video recording unavailable: %s", exc)
        else:
            log.warning("video recording unavailable: %s", exc)
        yield
        return

    request.node.stash[_VIDEO_RECORDER_KEY] = recorder
    try:
        yield
    finally:
        # Fallback cleanup if pytest_runtest_makereport didn't run (e.g. setup error)
        if request.node.stash.get(_VIDEO_RECORDER_KEY, None) is not None:
            recorder.stop()
            recorder.discard()
            try:
                del request.node.stash[_VIDEO_RECORDER_KEY]
            except KeyError:
                pass


@pytest.fixture
def launch(desktop: Desktop):
    """Function-scoped helper that launches an app and tears it down after the test.

    Usage::

        def test_something(launch):
            app = launch("notepad.exe")
            ...
    """
    launched: list = []

    def _launch(cmd: str, **kwargs):
        app = desktop.launch(cmd, **kwargs)
        launched.append(app)
        return app

    yield _launch

    # Try to kill every launched app — an exception on one must not
    # skip the others, otherwise a single failing teardown leaks every
    # subsequently-launched process in the same test.
    for app in launched:
        try:
            app.kill()
        except Exception:
            pass


# Allure helpers


def _is_allure_available() -> bool:
    try:
        import allure  # noqa: F401

        return True
    except ImportError:
        return False


def _attach_allure_screenshot(path: Path) -> None:
    try:
        import allure

        with path.open("rb") as f:
            allure.attach(
                f.read(),
                name="Screenshot",
                attachment_type=allure.attachment_type.PNG,
            )
    except Exception:
        pass


def _attach_allure_trace(run_dir: Path) -> None:
    """Attach trace HTML viewer (preferred) or a ZIP archive to the Allure report."""
    try:
        import allure

        if not run_dir.exists():
            return

        html_files = sorted(run_dir.glob("*.html"))
        if html_files:
            with html_files[0].open("rb") as f:
                allure.attach(
                    f.read(),
                    name="Trace",
                    attachment_type=allure.attachment_type.HTML,
                )
            return

        zip_path = run_dir.parent / f"{run_dir.name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in run_dir.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(run_dir.parent))
        with zip_path.open("rb") as f:
            allure.attach(f.read(), name="Trace", attachment_type=allure.attachment_type.ZIP)
    except Exception:
        pass


def _attach_allure_text(content: str, name: str) -> None:
    try:
        import allure

        allure.attach(content, name=name, attachment_type=allure.attachment_type.TEXT)
    except Exception:
        pass


def _attach_allure_video(path: Path) -> None:
    try:
        import allure

        allure.attach.file(
            str(path),
            name="Video",
            attachment_type=allure.attachment_type.MP4,
        )
    except Exception:
        pass


# Video helper


def _handle_video(item: pytest.Item, report: pytest.TestReport, phase: str) -> Path | None:
    """Stop the recorder, encode or discard based on outcome and mode.

    *phase* is the report phase the artifact is attached to — ``"setup"`` for a
    test whose outcome was decided by a fixture error, otherwise ``"call"``.

    Returns the saved video path or ``None``.
    """
    from . import _config as _cfg
    from . import _video

    recorder: _video.VideoRecorder | None = item.stash.get(_VIDEO_RECORDER_KEY, None)
    if recorder is None:
        return None

    # The stash entry is what tells the fixture teardown it still has to clean up, so
    # it is only dropped once stop() has actually succeeded — clearing it first would
    # leak the temp frames and a live ffmpeg process whenever stop() raises.
    recorder.stop()
    try:
        del item.stash[_VIDEO_RECORDER_KEY]
    except KeyError:
        pass

    mode = _cfg.get_video_mode()
    should_keep = report.failed or mode == "keepall"

    if not should_keep:
        recorder.discard()
        return None

    video_dir = Path(str(item.config.rootpath)) / item.config.getoption(
        "--dolphin-video-dir", default="dolphin-videos"
    )
    output_path = video_dir / f"{_artifact_file_stem(item.nodeid)}.mp4"

    try:
        recorder.encode(output_path)
        item.add_report_section(phase, "dolphin video", str(output_path))
        _attach_allure_video(output_path)
        return output_path
    except RuntimeError as exc:
        item.add_report_section(phase, "dolphin video (skipped)", str(exc))
        _get_logger("plugin").warning("video for %s was not saved: %s", item.nodeid, exc)
        return None
    finally:
        recorder.discard()


# Screenshot helper


def _capture_failure_screenshot(item: pytest.Item, phase: str = "call") -> Path | None:
    """Save a full-screen PNG; attach to Allure if available. Returns the path or None.

    ``ImageGrab.grab()`` raises on a hidden desktop or a locked session, and the
    node id is truncated because an untruncated parametrised id overflows MAX_PATH.
    Both are capture problems, never test problems, so nothing escapes here.

    ``all_screens=True`` is mandatory: without it Pillow captures the primary monitor
    only, so a failure on a secondary display is photographed as the wrong desktop.
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        return None

    try:
        screenshot_dir = item.config.rootpath / "dolphin-screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        path = screenshot_dir / f"{_artifact_file_stem(item.nodeid)}.png"

        img = ImageGrab.grab(all_screens=True)
        img.save(path)
        item.add_report_section(phase, "dolphin screenshot", str(path))
        _attach_allure_screenshot(path)
        return path
    except Exception as exc:
        _get_logger("plugin").warning(
            "Failure screenshot not captured for %s: %s", item.nodeid, exc
        )
        return None


# Main report hook


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(  # type: ignore[misc]
    item: pytest.Item, call: pytest.CallInfo
) -> None:
    outcome = yield
    report = outcome.get_result()
    if report.failed and getattr(report, "longrepr", None):
        # JUnit and terminal reporters consume the same TestReport after this
        # hook.  Sanitise the report itself so those sinks cannot serialize the
        # original exception text before the trace-specific copy is redacted.
        report.longrepr = _redact(str(report.longrepr))
    if call.when == "call":
        from ._exceptions import ElementNotFoundError, WaitTimeoutError

        excinfo = getattr(call, "excinfo", None)
        is_transient = excinfo is not None and isinstance(
            excinfo.value, (ElementNotFoundError, WaitTimeoutError)
        )
        setattr(report, _DOLPHIN_TRANSIENT_ATTR, is_transient)
    try:
        _collect_artifacts(item, call, report)
    except Exception as exc:
        # An exception raised after the yield of a hookwrapper reaches pytest as an
        # INTERNALERROR and masks the very failure this hook is reporting on.
        _get_logger("plugin").warning(
            "dolphin artifact collection failed for %s: %s", item.nodeid, exc
        )


def _collect_artifacts(item: pytest.Item, call: pytest.CallInfo, report: pytest.TestReport) -> None:
    if call.when == "call" and _attempt_will_retry(item, report):
        # This attempt's reports will never be published (a retry follows), so emit
        # nothing: skip screenshot, trace finalization, video keep, and accumulation.
        retry_session = item.stash.get(_TRACE_SESSION_KEY, None)
        if retry_session is not None:
            retry_session.close_without_finish()
        # The video recorder, if any, is stopped & discarded by the _dolphin_video
        # fixture's teardown, which runs after this hook for the same attempt.
        return

    # A setup that does not pass decides the test's outcome on its own — ``call``
    # never happens — so its artifacts have to be finalised here too, otherwise the
    # trace session stays open and unfinished for the rest of the run.
    decides_outcome = call.when == "call" or (call.when == "setup" and report.outcome != "passed")
    if not decides_outcome:
        if call.when == "teardown" and report.failed:
            _record_teardown_failure(item, report)
        return

    phase = call.when

    screenshot_path: Path | None = None
    if report.failed and item.config.getoption("--dolphin-screenshot-on-fail", default=False):
        screenshot_path = _capture_failure_screenshot(item, phase)

    from . import _trace

    session: _trace.TraceSession | None = item.stash.get(_TRACE_SESSION_KEY, None)
    trace_dir: Path | None = None

    if session is not None:
        if report.failed:
            # Redacted like every other sink. longrepr is persisted to
            # trace.db, rendered into trace.html and zipped into the Allure
            # attachment, and under `pytest -l` it carries the failing frame's
            # locals — so an unredacted password in scope was published even
            # though logging the same string would have masked it.
            error_msg = _redact(str(report.longrepr)) if report.longrepr else None
            session.finish("failed", error_message=error_msg)
            trace_dir = session.run_dir
            item.add_report_section(phase, "dolphin trace", str(trace_dir))
            _attach_allure_trace(trace_dir)
        else:
            session.finish("passed")

    video_path = _handle_video(item, report, phase)

    # attach captured stdout/stderr to Allure on failure
    if report.failed:
        capstdout: str = getattr(report, "capstdout", "") or ""
        capstderr: str = getattr(report, "capstderr", "") or ""
        if capstdout:
            _attach_allure_text(capstdout, "stdout")
        if capstderr:
            _attach_allure_text(capstderr, "stderr")

    # enrich JUnit XML <properties> with artifact paths
    if screenshot_path:
        report.user_properties.append(("dolphin_screenshot", str(screenshot_path)))
    if trace_dir:
        report.user_properties.append(("dolphin_trace", str(trace_dir)))
    if video_path:
        report.user_properties.append(("dolphin_video", str(video_path)))

    # accumulate for HTML fallback report
    _session_reports.append(
        {
            "nodeid": item.nodeid,
            "outcome": report.outcome,
            "duration": getattr(report, "duration", 0.0),
            "screenshot": str(screenshot_path) if screenshot_path else None,
            "trace": str(trace_dir) if trace_dir else None,
            "video": str(video_path) if video_path else None,
        }
    )


def _record_teardown_failure(item: pytest.Item, report: pytest.TestReport) -> None:
    """Correct the report row for a test whose teardown failed.

    pytest exits non-zero and prints ``1 error`` for a finalizer that raises,
    but the ``call`` phase had already committed an outcome of ``passed`` —
    so ``dolphin-report.html`` showed the test green while the run was red.
    The row is amended here instead.

    Artifacts cannot be recovered at this point: the ``call`` phase finished
    the trace session, which removes the run directory under the default
    ``on-failure`` mode, and discards the video. Re-run with
    ``--dolphin-trace=always`` to keep them for a teardown that fails.
    """
    # Redacted like the call-phase longrepr: this one reaches the terminal
    # report section and the JUnit XML, and a fixture finalizer fails with the
    # same locals in scope that made the call-phase text sensitive.
    error_msg = _redact(str(report.longrepr)) if report.longrepr else "teardown failed"
    item.add_report_section("teardown", "dolphin", error_msg)
    for entry in reversed(_session_reports):
        if entry.get("nodeid") == item.nodeid:
            entry["outcome"] = "error"
            entry["teardown_error"] = error_msg
            return
    _session_reports.append(
        {
            "nodeid": item.nodeid,
            "outcome": "error",
            "duration": getattr(report, "duration", 0.0),
            "screenshot": None,
            "trace": None,
            "video": None,
            "teardown_error": error_msg,
        }
    )


# Session finish — HTML fallback report


def _xdist_worker_id(config: pytest.Config) -> str | None:
    """Return the pytest-xdist worker id, or ``None`` outside a worker process.

    ``workerinput`` is injected by xdist into the worker's config only; the
    controller and a plain single-process run never have it.
    """
    workerinput = getattr(config, "workerinput", None)
    if workerinput is None:
        return None
    return str(workerinput.get("workerid", "worker"))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Final safety-net cleanup + HTML fallback report."""
    # ---- 1. Kill any session-tracked PIDs that escaped per-test teardown ----
    #
    # When a fixture calls ``app.detach()`` (to survive module-scope teardown)
    # and pytest later hard-kills the test (timeout, KeyboardInterrupt, etc.)
    # before the fixture's ``finally: app.kill()`` runs, the AUT leaks.
    # ``Application.__init__`` records every PID in ``_session_pids`` and only
    # ``close()``/``kill()`` clear it — so anything remaining here is an orphan.
    from . import _application

    orphans = list(_application._session_pids)
    if orphans:
        log = _get_logger("plugin")
        for pid in orphans:
            anchored_handle = getattr(_application, "_owned_process_handles", {}).get(pid)
            if anchored_handle is not None:
                try:
                    import win32api  # type: ignore[import]

                    # Reuse the anchored handle from launch; opening PID here
                    # would make session cleanup vulnerable to PID reuse.
                    win32api.TerminateProcess(anchored_handle, 1)
                    win32api.CloseHandle(anchored_handle)
                    getattr(_application, "_owned_process_handles", {}).pop(pid, None)
                    getattr(_application, "_unanchored_pids", set()).discard(pid)
                    _application._session_pids.discard(pid)
                    _application._live_pids.discard(pid)
                    log.info("Killed orphan AUT PID=%d at session end", pid)
                except Exception as exc:
                    log.warning(
                        "Could not clean up anchored orphan AUT PID=%d at session end: %s",
                        pid,
                        exc,
                    )
                continue
            if pid in getattr(_application, "_unanchored_pids", set()):
                log.warning(
                    "Skipping PID-only session cleanup for unanchored process PID=%d",
                    pid,
                )
                continue
            try:
                import win32api  # type: ignore[import]
                import win32con  # type: ignore[import]

                handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
                win32api.TerminateProcess(handle, 1)
                win32api.CloseHandle(handle)
                log.info("Killed orphan AUT PID=%d at session end", pid)
            except Exception:
                pass
            finally:
                _application._session_pids.discard(pid)
                _application._live_pids.discard(pid)

    # ---- 2. HTML fallback report (unchanged) ----
    if not _session_reports:
        return
    explicit: str | None = session.config.getoption("--dolphin-html", default=None)
    if explicit is None and _is_allure_available():
        return
    report_path = (
        Path(explicit) if explicit else Path(str(session.config.rootpath)) / "dolphin-report.html"
    )
    worker_id = _xdist_worker_id(session.config)
    if worker_id is not None:
        # ``_session_reports`` is per-process, so a worker only ever holds its own
        # slice of the run. Writing that to the shared filename would race the other
        # workers and leave a partial report indistinguishable from a complete one.
        report_path = report_path.with_name(f"{report_path.stem}-{worker_id}{report_path.suffix}")
    _generate_html_report(report_path)


def _generate_html_report(output: Path) -> None:
    import html as _h

    passed = [r for r in _session_reports if r["outcome"] == "passed"]
    failed = [r for r in _session_reports if r["outcome"] == "failed"]
    skipped = [r for r in _session_reports if r["outcome"] == "skipped"]
    # Counted and shown separately. A teardown failure is recorded as "error",
    # and leaving it out of the headline reproduced the very bug the teardown
    # handling fixes: the run is red while the summary says nothing failed.
    errors = [r for r in _session_reports if r["outcome"] == "error"]

    rows: list[str] = []
    for r in _session_reports:
        color = {
            "passed": "#4caf50",
            "failed": "#f44336",
            "skipped": "#ff9800",
            "error": "#f44336",
        }.get(r["outcome"], "#9e9e9e")
        # Nodeids carry parametrisation values and paths carry whatever the user named
        # a file, so both reach here as arbitrary text and must be escaped.
        screenshot = _h.escape(r["screenshot"] or "", quote=True)
        ss_html = (
            f'<a href="{screenshot}"><img src="{screenshot}" style="max-width:160px"></a>'
            if r["screenshot"]
            else ""
        )
        trace_html = (
            f'<a href="{_h.escape(r["trace"] or "", quote=True)}">trace</a>' if r["trace"] else ""
        )
        video_html = (
            f'<a href="{_h.escape(r["video"] or "", quote=True)}">video</a>' if r["video"] else ""
        )
        rows.append(
            "<tr>"
            f'<td style="color:{color};font-weight:bold">{_h.escape(r["outcome"].upper())}</td>'
            f"<td><code>{_h.escape(r['nodeid'])}</code></td>"
            f"<td>{r.get('duration', 0.0):.2f}s</td>"
            f"<td>{ss_html}</td>"
            f"<td>{trace_html}</td>"
            f"<td>{video_html}</td>"
            "</tr>"
        )

    html = (
        "<!DOCTYPE html>\n"
        "<html lang='en'>\n"
        "<head>\n"
        "  <meta charset='UTF-8'>\n"
        "  <title>Dolphin Test Report</title>\n"
        "  <style>\n"
        "    body{font-family:monospace;margin:24px;background:#1e1e1e;color:#d4d4d4}\n"
        "    h1{color:#569cd6}\n"
        "    table{border-collapse:collapse;width:100%}\n"
        "    th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #333}\n"
        "    th{background:#2d2d2d;color:#9cdcfe}\n"
        "    a{color:#4fc1ff}\n"
        "    img{border:1px solid #555;cursor:pointer}\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <h1>Dolphin Test Report</h1>\n"
        f"  <p><b>Passed:</b> {len(passed)} &nbsp;"
        f" <b>Failed:</b> {len(failed)} &nbsp;"
        f" <b>Errors:</b> {len(errors)} &nbsp;"
        f" <b>Skipped:</b> {len(skipped)}</p>\n"
        "  <table>\n"
        "    <tr>"
        "<th>Status</th><th>Test</th><th>Duration</th>"
        "<th>Screenshot</th><th>Trace</th><th>Video</th>"
        "</tr>\n" + "".join(f"    {row}\n" for row in rows) + "  </table>\n"
        "</body>\n"
        "</html>\n"
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
