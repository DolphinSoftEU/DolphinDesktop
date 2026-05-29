"""pytest plugin — auto-registered via the project.entry-points."pytest11" entry point."""

from __future__ import annotations

import os
import re
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ._desktop import Desktop

_TRACE_SESSION_KEY: pytest.StashKey[Any] = pytest.StashKey()
_VIDEO_RECORDER_KEY: pytest.StashKey[Any] = pytest.StashKey()
_RETRY_ATTEMPT_KEY: pytest.StashKey[int] = pytest.StashKey()
_RETRY_MAX_KEY: pytest.StashKey[int] = pytest.StashKey()

_session_reports: list[dict[str, Any]] = []


def pytest_sessionstart(session: pytest.Session) -> None:
    _session_reports.clear()


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """Kill any zombie processes left over from a crashed or improperly cleaned-up test."""
    from . import _application

    pids = list(_application._live_pids)
    if not pids:
        return

    import logging

    log = logging.getLogger("dolphin_desktop.plugin")
    for pid in pids:
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
    if report is None or not report.failed:
        return False

    from ._exceptions import ElementNotFoundError, WaitTimeoutError

    longrepr = getattr(report, "longrepr", None)
    if longrepr is None:
        return False
    text = str(longrepr)
    return any(name in text for name in (ElementNotFoundError.__name__, WaitTimeoutError.__name__))


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

    import logging

    log = logging.getLogger("dolphin_desktop.plugin")

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
            time.sleep(0.5)
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
        type=float,
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
        "--dolphin-log-level",
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
        type=int,
        default=None,
        metavar="N",
        help=(
            "Retry a test up to N times when it fails with a transient dolphin error "
            "(ElementNotFoundError / WaitTimeoutError). Default: 0 (no retry). "
            "Also: DOLPHIN_RETRY env var."
        ),
    )


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def dolphin_backend(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--dolphin-backend")  # type: ignore[return-value]


@pytest.fixture(scope="session")
def dolphin_timeout(request: pytest.FixtureRequest) -> float:
    """Effective global timeout: CLI > DOLPHIN_TIMEOUT env var > built-in default."""
    cli_val: float | None = request.config.getoption("--dolphin-timeout")
    if cli_val is not None:
        return cli_val
    env_val = os.environ.get("DOLPHIN_TIMEOUT")
    if env_val is not None:
        return float(env_val)
    return 10.0


@pytest.fixture(scope="session", autouse=True)
def _dolphin_apply_session_config(dolphin_timeout: float, request: pytest.FixtureRequest) -> None:
    """Push CLI/env settings into dolphin global config and set up logging/telemetry."""
    import logging as _stdlib_logging

    from . import _config as _cfg
    from ._telemetry import init as _init_telemetry

    _cfg._defaults["timeout"] = dolphin_timeout

    cli_trace: str | None = request.config.getoption("--dolphin-trace", default=None)
    if cli_trace is not None:
        _cfg._defaults["trace_mode"] = cli_trace

    cli_video: str | None = request.config.getoption("--dolphin-video", default=None)
    if cli_video is not None:
        _cfg._defaults["video_mode"] = cli_video

    cli_log_level: str | None = request.config.getoption("--dolphin-log-level", default=None)
    if cli_log_level is not None:
        _cfg._defaults["log_level"] = cli_log_level

    cli_retry: int | None = request.config.getoption("--dolphin-retry", default=None)
    if cli_retry is not None:
        _cfg._defaults["retry_count"] = cli_retry

    # Under pytest, the logging plugin manages output — just set the level so that
    # child loggers (dolphin_desktop.selfheal, dolphin_desktop.plugin, …) are filtered correctly.
    # Do NOT set propagate=False here: caplog fixtures in tests rely on propagation.
    level_int = getattr(_stdlib_logging, _cfg.get_log_level(), _stdlib_logging.INFO)
    _stdlib_logging.getLogger("dolphin").setLevel(level_int)

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


# ---------------------------------------------------------------------------
# Per-test fixtures
# ---------------------------------------------------------------------------


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

    # headless guard — evaluated before any state mutation so nothing to restore on skip
    headless = marker.kwargs.get("headless")
    if headless is True:
        is_headless = bool(
            request.config.getoption("--dolphin-headless", default=False)
            or os.environ.get("DOLPHIN_HEADLESS") == "1"
        )
        if not is_headless:
            pytest.skip("requires headless mode (--dolphin-headless / DOLPHIN_HEADLESS=1)")

    old_timeout = _cfg._defaults.get("timeout")
    old_video_mode = _cfg._defaults.get("video_mode")

    timeout = marker.kwargs.get("timeout")
    if timeout is not None:
        _cfg._defaults["timeout"] = float(timeout)

    video_mode = marker.kwargs.get("video_mode")
    if video_mode is not None:
        _cfg._defaults["video_mode"] = video_mode

    try:
        yield
    finally:
        if old_timeout is not None:
            _cfg._defaults["timeout"] = old_timeout
        if old_video_mode is not None:
            _cfg._defaults["video_mode"] = old_video_mode


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
    safe = re.sub(r"[^\w._-]", "_", request.node.nodeid)[:80]
    run_dir = trace_root / f"{safe}_{int(time.time())}"

    session = _trace.TraceSession(
        test_nodeid=request.node.nodeid,
        run_dir=run_dir,
        mode=mode,
    )
    _trace.set_current_session(session)
    request.node.stash[_TRACE_SESSION_KEY] = session

    yield

    _trace.set_current_session(None)


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
    except RuntimeError:
        # ffmpeg not installed — skip recording silently
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

    for app in launched:
        app.kill()


# ---------------------------------------------------------------------------
# Allure helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Video helper
# ---------------------------------------------------------------------------


def _handle_video(item: pytest.Item, report: pytest.TestReport) -> Path | None:
    """Stop the recorder, encode or discard based on outcome and mode.

    Returns the saved video path or ``None``.
    """
    from . import _config as _cfg
    from . import _video

    recorder: _video.VideoRecorder | None = item.stash.get(_VIDEO_RECORDER_KEY, None)
    if recorder is None:
        return None

    try:
        del item.stash[_VIDEO_RECORDER_KEY]
    except KeyError:
        pass

    recorder.stop()

    mode = _cfg.get_video_mode()
    should_keep = report.failed or mode == "keepall"

    if not should_keep:
        recorder.discard()
        return None

    video_dir = Path(str(item.config.rootpath)) / item.config.getoption(
        "--dolphin-video-dir", default="dolphin-videos"
    )
    safe = re.sub(r"[^\w._-]", "_", item.nodeid)[:80]
    output_path = video_dir / f"{safe}.mp4"

    try:
        recorder.encode(output_path)
        item.add_report_section("call", "dolphin video", str(output_path))
        _attach_allure_video(output_path)
        return output_path
    except RuntimeError as exc:
        item.add_report_section("call", "dolphin video (skipped)", str(exc))
        return None
    finally:
        recorder.discard()


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------


def _capture_failure_screenshot(item: pytest.Item) -> Path | None:
    """Save a full-screen PNG; attach to Allure if available. Returns the path or None."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return None

    screenshot_dir = item.config.rootpath / "dolphin-screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    safe_name = item.nodeid.replace("/", "_").replace("::", "__")
    path = screenshot_dir / f"{safe_name}.png"

    img = ImageGrab.grab()
    img.save(path)
    item.add_report_section("call", "dolphin screenshot", str(path))
    _attach_allure_screenshot(path)
    return path


# ---------------------------------------------------------------------------
# Main report hook
# ---------------------------------------------------------------------------


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(  # type: ignore[misc]
    item: pytest.Item, call: pytest.CallInfo
) -> None:
    outcome = yield
    report = outcome.get_result()

    if call.when == "call" and _attempt_will_retry(item, report):
        # This attempt's reports will never be published (a retry follows), so emit
        # nothing: skip screenshot, trace finalization, video keep, and accumulation.
        retry_session = item.stash.get(_TRACE_SESSION_KEY, None)
        if retry_session is not None:
            retry_session.close_without_finish()
        # The video recorder, if any, is stopped & discarded by the _dolphin_video
        # fixture's teardown, which runs after this hook for the same attempt.
        return

    screenshot_path: Path | None = None
    if (
        report.failed
        and call.when == "call"
        and item.config.getoption("--dolphin-screenshot-on-fail", default=False)
    ):
        screenshot_path = _capture_failure_screenshot(item)

    if call.when != "call":
        return

    from . import _trace

    session: _trace.TraceSession | None = item.stash.get(_TRACE_SESSION_KEY, None)
    trace_dir: Path | None = None

    if session is not None:
        if report.failed or report.outcome == "error":
            error_msg = str(report.longrepr) if report.longrepr else None
            session.finish("failed", error_message=error_msg)
            trace_dir = session.run_dir
            item.add_report_section("call", "dolphin trace", str(trace_dir))
            _attach_allure_trace(trace_dir)
        else:
            session.finish("passed")

    video_path = _handle_video(item, report)

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


# ---------------------------------------------------------------------------
# Session finish — HTML fallback report
# ---------------------------------------------------------------------------


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _session_reports:
        return
    explicit: str | None = session.config.getoption("--dolphin-html", default=None)
    if explicit is None and _is_allure_available():
        return
    report_path = (
        Path(explicit) if explicit else Path(str(session.config.rootpath)) / "dolphin-report.html"
    )
    _generate_html_report(report_path)


def _generate_html_report(output: Path) -> None:
    passed = [r for r in _session_reports if r["outcome"] == "passed"]
    failed = [r for r in _session_reports if r["outcome"] == "failed"]
    skipped = [r for r in _session_reports if r["outcome"] == "skipped"]

    rows: list[str] = []
    for r in _session_reports:
        color = {
            "passed": "#4caf50",
            "failed": "#f44336",
            "skipped": "#ff9800",
        }.get(r["outcome"], "#9e9e9e")
        ss_html = (
            f'<a href="{r["screenshot"]}"><img src="{r["screenshot"]}" style="max-width:160px"></a>'
            if r["screenshot"]
            else ""
        )
        trace_html = f'<a href="{r["trace"]}">trace</a>' if r["trace"] else ""
        video_html = f'<a href="{r["video"]}">video</a>' if r["video"] else ""
        rows.append(
            "<tr>"
            f'<td style="color:{color};font-weight:bold">{r["outcome"].upper()}</td>'
            f"<td><code>{r['nodeid']}</code></td>"
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
