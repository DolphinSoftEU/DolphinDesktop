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

- **[Getting Started](getting-started.md)** — the shortest path from a
  clean Python environment to a passing test for **your** stack (SAP,
  Qt, Electron, Java, Mainframe, Oracle Forms).
- [Installation](installation.md) — the base install and dependency table.
- [Quickstart](quickstart.md) — generate a Notepad project from the CLI.
- [Tutorial: First Test](tutorials/first-test.md) — write a Notepad test by hand.
- [Support matrix](support-matrix.md) — which stack maps
  to which backend, supported modes, out-of-scope decisions
  (Flash/Flex, Silverlight), and the capability checklist.
- [API Reference](reference/index.md) — generated signatures and public objects.

## Implemented Capabilities

This table is a **quick index**. For the authoritative
stack × backend × mode support matrix — including per-stack
limitations, supported modes, out-of-scope decisions
(Flash / Silverlight), and the capability checklist — see
[Support matrix](support-matrix.md).

| Area | What Dolphin provides | Guide |
| --- | --- | --- |
| Native Windows UI (WPF, WinForms, UWP) | UIA + Win32 through pywinauto | [Quickstart](quickstart.md) |
| **SAP GUI** | COM Scripting bindings + `SapLocator` | [SAP guide](guides/sap.md) |
| **Qt 5 / Qt 6** | UIA + agent DLL for QML / QGraphicsView | [Qt guide](guides/qt.md) |
| **Electron / CEF** (VS Code, Steam, Spotify) | Chrome DevTools Protocol via Playwright | [Embedded web](guides/embedded-web.md) |
| **Java Swing / Oracle Forms** | Direct JAB API (setTextContents, requestFocus, doAccessibleActions) | [Oracle Forms](guides/oracle-forms.md) |
| **Mainframe 3270** (z/OS, CICS) | ws3270 subprocess + mock TN3270 server | [Mainframe](guides/mainframe.md) |
| **Mainframe 5250** (IBM i, AS/400) | Pure-Python TN5250 (no NVT fallback) | [Mainframe](guides/mainframe.md) |
| **HLLAPI** (PCOMM, Attachmate, Rocket) | ctypes binding to `EHLAPI32.DLL` | [Mainframe](guides/mainframe.md) |
| **Delphi / VCL** (RAD Studio, Lazarus) | UIA + `TComponent.Name → AutomationId` locator | [Delphi](guides/delphi.md) |
| **PowerBuilder** (2019+ Appeon) | UIA + OCR fallback for DataWindow | [PowerBuilder](guides/powerbuilder.md) |
| Image fallback | Template matching + OCR with `dolphin-desktop[vision]` | [Image-based](guides/image-based.md) |
| pytest | `desktop`/`launch` fixtures, CLI options, traces, screenshots, videos, retries | [FAQ](faq.md) |
| CLI | `dolphin init`, `doctor`, `spy`, `record`, `trace`, `selfheal-stats`, `dolphin-run` | — |

## Supported Platform

Dolphin Desktop targets Windows 10/11 with Python 3.11 or newer. The package contains placeholder backend classes for other platforms, but the documented and tested product path is Windows desktop automation.
