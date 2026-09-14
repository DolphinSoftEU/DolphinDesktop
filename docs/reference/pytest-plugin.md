# pytest Plugin

Dolphin registers a pytest plugin automatically through the `pytest11` package entry point. Install `dolphin-desktop` in the same environment as pytest; no import is required in `conftest.py`.

## Fixtures

### `desktop`

```python
def test_example(desktop):
    app = desktop.launch("notepad.exe")
    ...
```

Session-scoped `Desktop` instance. The backend comes from `--dolphin-backend`.

### `launch`

```python
def test_example(launch):
    app = launch("notepad.exe")
    win = app.window(class_name="Notepad")
    ...
```

Function-scoped helper. It calls `Desktop.launch(...)`, records the returned `Application`, and calls `Application.kill()` during teardown.

`launch` accepts the same keyword arguments as `Desktop.launch`:

```python
def test_with_args(launch):
    app = launch("myapp.exe", timeout=20, startup_delay=1.0)
    win = app.window(title="My App")
```

### `dolphin_backend`

```python
def test_backend_name(dolphin_backend):
    assert dolphin_backend in {"uia", "win32"}
```

Session-scoped value of `--dolphin-backend`.

### `dolphin_timeout`

```python
def test_timeout_value(dolphin_timeout):
    assert dolphin_timeout >= 0
```

Session-scoped effective timeout from CLI, environment, or the default.

### `dolphin_headless`

```python
def test_headless_flag(dolphin_headless):
    print(dolphin_headless)
```

Session-scoped flag set by `--dolphin-headless` or `DOLPHIN_HEADLESS=1`.

## CLI Options

| Option | Default | Description |
| --- | --- | --- |
| `--dolphin-backend {uia,win32}` | `uia` | pywinauto backend |
| `--dolphin-timeout FLOAT` | `10.0` | Default element wait timeout in seconds |
| `--dolphin-screenshot-on-fail` | off | Capture a full-screen PNG on test failure |
| `--dolphin-headless` | off | Create `Desktop(hidden=True)` through the fixture |
| `--dolphin-trace {off,on-failure,always}` | `on-failure` | Trace capture mode |
| `--dolphin-trace-dir PATH` | `dolphin-traces` | Trace output directory |
| `--dolphin-video {off,keepfailedonly,keepall}` | `keepfailedonly` | Video capture mode. The default records automatically and keeps MP4s only for failed tests. Requires an external `ffmpeg` binary on `PATH` (or `DOLPHIN_FFMPEG`); without it, recording is silently skipped. No extra enables it: the `fast` extra installs `mss`, which speeds up trace screenshots, not video encoding. |
| `--dolphin-video-dir PATH` | `dolphin-videos` | Video output directory |
| `--dolphin-html PATH` | automatic | Fallback HTML report path |
| `--dolphin-desktop-log-level {DEBUG,INFO,ERROR}` | `INFO` | Dolphin log verbosity |
| `--dolphin-retry N` | `0` | Retry transient `ElementNotFoundError` and `WaitTimeoutError` failures |

Examples:

```bash
pytest tests/ -v --dolphin-backend=win32
pytest tests/ -v --dolphin-timeout=20
pytest tests/ -v --dolphin-screenshot-on-fail
pytest tests/ -v --dolphin-trace=always
pytest tests/ -v --dolphin-video=keepfailedonly
```

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `DOLPHIN_TIMEOUT` | `10.0` | Default element wait timeout |
| `DOLPHIN_TRACE` | `on-failure` | Trace mode: `always`, `on-failure`, `off` |
| `DOLPHIN_VIDEO` | `keepfailedonly` | Video mode: `keepall`, `keepfailedonly`, `off` |
| `DOLPHIN_VIDEO_FPS` | `10` | Video frame rate |
| `DOLPHIN_HEADLESS` | `0` | Headless desktop flag |
| `DOLPHIN_LOG_LEVEL` | `INFO` | Logging level |
| `DOLPHIN_RETRY` | `0` | Retry count for transient Dolphin errors |
| `DOLPHIN_TELEMETRY` | `off` | Telemetry opt-in flag |

## Per-Test Marker

Use `@pytest.mark.dolphin(...)` for per-test overrides:

```python
import pytest


@pytest.mark.dolphin(timeout=30, video_mode="keepall")
def test_slow_flow(launch):
    app = launch("myapp.exe")
```

`@pytest.mark.dolphin(headless=True)` skips the test unless headless mode is active.

## Generated Artifacts

| Artifact | Default path |
| --- | --- |
| Screenshots on failure | `dolphin-screenshots/` |
| Traces | `dolphin-traces/` |
| Videos | `dolphin-videos/` |
| Fallback HTML report | `dolphin-report.html` when Allure is not installed |

## 9.3 — Warstwa stabilności

### Logging

Dolphin logging accepts only `DEBUG`, `INFO`, and `ERROR`; the default is
`INFO`. `--dolphin-desktop-log-level` is validated by pytest, while
`config(log_level=...)` raises `ValueError` for any other value without
changing the configured defaults or logger state. The same three values are
accepted case-insensitively by the Python API.

For environment configuration, `DOLPHIN_DESKTOP_LOG_LEVEL` takes precedence
over `DOLPHIN_LOG_LEVEL`. The selected value is validated after precedence is
resolved; an invalid value emits a warning and falls back safely to `INFO`,
even when the lower-priority variable is valid.

In standalone use, `setup_logging()` owns one console handler on the
`dolphin_desktop` logger and repeated calls reuse it. Under pytest, pytest
owns the handlers: Dolphin installs none on its logger, leaves propagation
enabled, and applies the level/redaction configuration so messages reach
pytest's handlers without duplicate output.

`--dolphin-screenshot-on-fail` is diagnostic support and must not change the
test result. If the screenshot backend cannot capture an image (for example,
because the desktop is locked or `ImageGrab` raises), Dolphin logs a warning
and preserves the original test exception and exit status. It does not emit a
pytest `INTERNALERROR` caused by the failed capture.

The JUnit/XML, fallback HTML, pytest stdout/stderr and diagnostic log remain
available for investigating the failure. A successful capture is written under
`dolphin-screenshots/` and its path is included in the test report; a failed
capture does not create a misleading or partial PNG.
