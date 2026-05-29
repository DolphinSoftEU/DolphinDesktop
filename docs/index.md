# Dolphin Desktop

Dolphin Desktop is a Python library for testing Windows desktop applications. It gives pytest tests a small, readable API for launching apps, finding windows, interacting with controls, and collecting artifacts when a test fails.

It is designed around Windows desktop automation through UIA, Win32, image matching, and selected integrations such as Java Access Bridge and Office COM.

```bash
pip install dolphin-desktop
```

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch("notepad.exe")
win = app.window(class_name="Notepad")

editor = win.get_by_role("Document")
editor.click()
editor.type_text("Hello, Dolphin!")

assert "Hello, Dolphin!" in editor.text()

app.kill()
```

## Start Here

- [Installation](installation.md): install Dolphin and check your environment.
- [Quickstart](quickstart.md): create a generated test project and run the sample.
- [Tutorial: First Test](tutorials/first-test.md): write a Notepad test from scratch.
- [API Reference](reference/index.md): generated signatures and public objects.

## Implemented Capabilities

| Area | What Dolphin provides |
| --- | --- |
| Native Windows UI | UIA and Win32 automation through pywinauto |
| Locators | Lazy element lookup by role, title, AutomationId, class name, XPath subset, and arbitrary criteria |
| pytest | `desktop` and `launch` fixtures, CLI options, traces, screenshots, videos, retries, and fallback HTML reports |
| Image fallback | Template matching and OCR helpers with `dolphin-desktop[vision]` |
| App-specific helpers | Electron/CEF launch flags, WebView2 semantic launcher, Java Access Bridge, Excel and Word COM helpers |
| CLI | `dolphin init`, `doctor`, `spy`, `record`, `trace`, `selfheal-stats`, `info backends`, and `dolphin-run` |

## Supported Platform

Dolphin Desktop targets Windows 10/11 with Python 3.11 or newer. The package contains placeholder backend classes for other platforms, but the documented and tested product path is Windows desktop automation.
