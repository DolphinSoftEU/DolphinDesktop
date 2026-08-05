# Core Concepts

## Lazy Locators

`Window` methods such as `get_by_role`, `get_by_automation_id`, and `locator` return `Locator` objects. A locator stores search criteria. It does not query Windows until an action or query is called.

```python
save = win.get_by_automation_id("btnSave")  # no search yet
save.click()                                # resolves, waits, then clicks
```

Because locators are lazy, the same locator can survive UI redraws. Each action resolves the current element again.

## Auto-Waiting

Actions and query helpers wait for elements instead of failing immediately. The default timeout is 10 seconds.

Configure it through:

```bash
pytest tests/ -v --dolphin-timeout=20
set DOLPHIN_TIMEOUT=20
```

or in Python:

```python
from dolphin_desktop import config

config(timeout=20)
button = win.get_by_title("Save").timeout(5)
```

## Backends

| Backend | Use for | Notes |
| --- | --- | --- |
| `uia` | Modern Windows applications | Default backend, based on Microsoft UI Automation |
| `win32` | Older Win32, MFC, Delphi, or legacy controls | Use `Desktop(backend="win32")` or `Desktop.for_legacy_apps()` |
| `image` | Visual fallback | Used through `ImageLocator`, `Screen`, or `window.image(...)`; install `dolphin-desktop[vision]` |

The package also exposes placeholder backend classes for future or plugin use. The documented runtime path is Windows.

## How Dolphin Locates Your Application

Dolphin does not know where **your** application is installed — you
tell it. No path in `dolphin_desktop`'s source refers to a
user-specific location (`C:\Users\...`, `D:\Program Files\...`).
Three patterns cover every supported stack:

| Pattern | You provide | Library uses | Stacks |
| --- | --- | --- | --- |
| **Launch** | Path to `.exe` or command line | Starts the process | Delphi (`launch_delphi`), Qt (`launch_qt`), Electron / CEF (`launch_electron_cdp` / `launch_cef_cdp`), Java (`launch_java`), PowerBuilder (`launch_powerbuilder`), generic (`launch`) |
| **Connect via COM** | Nothing — app must already be running | System COM registry (`SAPGUI`, `Excel.Application`, `Word.Application`) | SAP GUI (`SapGui.connect()`), Excel (`ExcelApp.connect()`), Word (`WordApp.connect()`) |
| **Attach via window criteria** | `title_re=`, `class_name=`, or `process=` — app must be running | pywinauto `connect(...)` | Oracle Forms (`attach_oracle_forms`), generic (`Desktop().connect(...)`) |

### Auto-detected paths (well-known install dirs)

One stack today ships default install locations:

- **Mainframe TN3270** — `ws3270.exe` is discovered by
  (1) `PATH`, (2) `%LOCALAPPDATA%\wc3270\`,
  (3) `C:\Program Files\wc3270\`,
  (4) `C:\Program Files (x86)\wc3270\`.
  These are wc3270's installer's default locations. Override by
  passing `ws3270_path=` explicitly.

Every other stack: if a path is needed, you supply it.

### Parametrising paths for CI

Hardcoding paths inside test files works locally but breaks in CI
where the app lives elsewhere. Recommended pattern in `conftest.py`:

```python
import os
import pytest

QT_APP = os.environ.get("QT_APP_PATH", r"C:\devbuild\my_app.exe")
VCL_APP = os.environ.get("VCL_APP_PATH", r"C:\devbuild\my_vcl.exe")

@pytest.fixture
def qt_app(desktop):
    with desktop.launch_qt(QT_APP) as app:
        yield app
```

Then in CI: `QT_APP_PATH=D:/agent/artifacts/app.exe pytest tests/ -v`.

## pytest Fixtures

The pytest plugin is loaded automatically when the package is installed.

| Fixture | Scope | Purpose |
| --- | --- | --- |
| `desktop` | session | Shared `Desktop` instance |
| `launch` | function | Launch an app and kill it after the test |
| `dolphin_backend` | session | Value of `--dolphin-backend` |
| `dolphin_timeout` | session | Effective default timeout |
| `dolphin_headless` | session | Whether headless mode is active |

The `launch` fixture is the recommended default for tests that own the app process.

## Screenshots, Traces, And Videos

Useful pytest options:

```bash
pytest tests/ -v --dolphin-screenshot-on-fail
pytest tests/ -v --dolphin-trace=always
pytest tests/ -v --dolphin-video=keepfailedonly
```

Trace files are written to `dolphin-traces` by default. Video files are written to `dolphin-videos` when video recording is enabled and `ffmpeg` is available.

Open the latest trace:

```bash
dolphin trace view --last
```

## Headless Mode

`dolphin-run` creates a hidden Windows desktop and runs the whole command on it:

```bash
dolphin-run pytest tests/ -v
```

This is useful for some CI agents, but it is not a replacement for an interactive Windows session in every case. Store apps, UWP apps, and some mouse-heavy workflows can still require a visible desktop.

See [Headless Mode](tutorials/headless-mode.md) for limitations and workarounds.
