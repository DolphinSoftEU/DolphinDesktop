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
