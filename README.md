# DolphinDesktop

[![PyPI](https://img.shields.io/pypi/v/dolphin-desktop.svg)](https://pypi.org/project/dolphin-desktop/)
[![Python](https://img.shields.io/pypi/pyversions/dolphin-desktop.svg)](https://pypi.org/project/dolphin-desktop/)
[![Platform](https://img.shields.io/badge/platform-Windows-informational.svg)](https://github.com/DolphinSoftEU/DolphinDesktop)
[![Tests](https://github.com/DolphinSoftEU/DolphinDesktop/actions/workflows/ci.yml/badge.svg)](https://github.com/DolphinSoftEU/DolphinDesktop/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/DolphinSoftEU/DolphinDesktop/blob/master/LICENSE)

Python-native test automation for every UI stack a Windows enterprise
hits — SAP GUI, Qt 5/6, Electron/CEF, WPF/WinForms/UWP, Java Swing,
Oracle Forms, Delphi/VCL, PowerBuilder, mainframe 3270/5250, and
Office COM — under one API. Lazy locators, auto-waiting, pytest-first.

## Requirements

- **Windows 10 or Windows 11** — the library drives Windows accessibility
  APIs and is not portable to Linux or macOS
- **Python 3.11, 3.12 or 3.13** — every release is tested on all three
- 64-bit Python recommended

Individual stacks add their own prerequisites, which are not Python
packages and cannot be installed with `pip`: SAP GUI for Windows with
scripting enabled, a JDK carrying the Java Access Bridge for Swing and
Oracle Forms, `ws3270.exe` or a terminal emulator for the mainframe
backends, and an `ffmpeg` binary for video recording. Each stack guide
states what it needs.

```bash
pip install dolphin-desktop
```

## Why DolphinDesktop?

One Python API across every desktop UI technology listed below, so a
suite spanning SAP, a Qt client and an Electron app is written, run and
reported as one thing rather than three.

- Lazy locators that resolve at the moment of use, not at declaration
- Auto-waiting built into every action, with a per-call `timeout=`
- Backend selection behind a common abstraction — the same
  `get_by_role(...).click()` drives UIA, JAB, CDP and SAP Scripting
- Programmatic actions that raise `UnsupportedPatternError` instead of
  silently degrading to a blind mouse click
- pytest-native: fixtures, markers, traces, screenshots and video come
  from the plugin, not from glue code you maintain
- Legacy stacks treated as first-class, not as an afterthought

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

## What It Provides

| Stack                                       | Under the hood                                          | Extra needed                |
|---------------------------------------------|--------------------------------------------------------|-----------------------------|
| Windows GUI (WPF, WinForms, UWP, MFC)       | UIA + Win32 via pywinauto                              | *(base)*                    |
| **SAP GUI Scripting**                       | COM Scripting binding                                  | *(base)*                    |
| **Qt 5 / Qt 6** (widgets, QML, QGraphicsView)| UIA + injected agent DLL                              | *(base)*                    |
| **Electron / CEF** (VS Code, Steam, Spotify)| Chrome DevTools Protocol via Playwright                | `[cdp]`                     |
| **WebView2** (Edge Chromium embedded)       | Same CDP surface                                       | `[cdp]`                     |
| **Java Swing / Oracle Forms**               | Direct JAB API                                         | *(base + JDK 8)*            |
| **Mainframe 3270** (z/OS TSO, CICS)         | ws3270 subprocess wrap                                 | *(base + wc3270)*           |
| **Mainframe 5250** (IBM i, AS/400)          | Pure-Python native TN5250 client                       | *(base)*                    |
| **HLLAPI** (PCOMM, Attachmate, Rocket)      | ctypes binding to `EHLAPI32.DLL`                       | *(base + emulator)*         |
| **Delphi / VCL** (RAD Studio, Lazarus)      | UIA + Delphi-aware locator with 5 strategies           | *(base)*                    |
| **PowerBuilder** (2019+ Appeon)             | UIA + OCR fallback for DataWindow                      | *(base; `[vision]` for OCR)* |
| Office (Excel / Word)                       | COM Automation via pywin32                             | *(base + Office)*           |
| Image-based fallback                        | OpenCV template match + Tesseract OCR                  | `[vision]`                  |

Every backend shares the same design: **lazy locators, auto-waiting,
readable assertions.**

- Lazy locators that resolve only when an action or query runs.
- Auto-waiting on every action with per-call `timeout=`.
- pytest plugin with `desktop` and `launch` fixtures.
- Trace / screenshot / video / recorder / spy / project scaffold CLI.
- **Autonomous library contract** — user tests import only from `dolphin_desktop`.

## Verification status

Three statuses, so "implemented" can never be read as "proven":

- **Verified — real** — exercised against the actual software, either by
  the suite in this repository or by hand where a licence prevents the
  suite from installing it.
- **Verified — simulated** — exercised against a mock that reproduces the
  protocol or API faithfully, but not against the vendor's own product.
- **Projected** — implemented, never run against anything. No row is in
  this state today; it exists so a future one can say so honestly.

Everything reproducible runs from `pytest tests/`.

| Stack                            | Status               | How it is exercised                   |
|----------------------------------|----------------------|---------------------------------------|
| Native Windows (UIA / Win32)     | Verified — real      | headless suite + real apps            |
| Qt 5 / Qt 6 widgets              | Verified — real      | real Qt apps built by the suite       |
| Delphi / Lazarus LCL             | Verified — real      | real Lazarus sample app               |
| Electron / CEF (VS Code, Steam)  | Verified — real      | real applications over CDP            |
| PowerBuilder (Appeon runtime)    | Verified — real      | live PB 2025 demo: UIA + OCR fallback |
| SAP GUI                          | Verified — real      | live ABAP system over GUI Scripting   |
| Mainframe TN5250 (IBM i)         | Verified — real      | pub400, a public IBM i host           |
| Delphi VCL (RAD Studio)          | Verified — real      | licensed RAD Studio app               |
| Oracle Forms 12c                 | Verified — real      | live Forms 12c client                 |
| HLLAPI (emulator)                | Verified — real      | commercial emulator                   |
| Mainframe TN3270                 | Verified — simulated | in-repo 3270 protocol server          |
| Oracle Forms / Swing over JAB    | Verified — simulated | Java Swing mock exercising the JAB API|
| HLLAPI call encoding             | Verified — simulated | injected `EHLAPI32.DLL` stub          |

The SAP suite in `tests/sap/` runs against a real ABAP system and is
**credential-free**: it reads user, password and client from environment
variables and skips cleanly when they are absent, so it ships safely and
is a no-op on a machine without SAP. It needs SAP GUI Scripting enabled
on both the client and the server (`sapgui/user_scripting = TRUE`).
dolphin stores no SAP credentials of its own: you attach to a session you
have already logged into, or drive the logon screen with credentials your
test supplies. See [docs/guides/sap.md](https://dolphinsoft.pl/latest/guides/sap/).

## Quickstart

```bash
pip install dolphin-desktop pytest
dolphin doctor
dolphin init my-tests --yes
cd my-tests
pytest tests/ -v
```

The default scaffold creates a Notepad test and a small Page Object under `objects/`.

## Documentation

Full docs live under `docs/` and are rendered as an MkDocs site
(`uv run mkdocs serve` to preview locally, or browse the sources
directly on GitHub).

Entry points:

- **[Getting started](https://dolphinsoft.pl/latest/getting-started/)** — the shortest path
  from a clean venv to a passing test per stack (SAP / Qt / Electron /
  Java / Mainframe / Delphi / etc.).
- **[Installation](https://dolphinsoft.pl/latest/installation/)** — base install + per-stack
  extras. SAP, Qt, Delphi, mainframe and Oracle Forms need none — they
  are in the base install; `[cdp]`, `[vision]`, `[fast]`, `[pytest]`,
  `[telemetry]` and `[all]` are the ones that exist.
- **[Quickstart](https://dolphinsoft.pl/latest/quickstart/)** — generate a Notepad project
  from the CLI in under a minute.
- **[Core concepts](https://dolphinsoft.pl/latest/core-concepts/)** — lazy locators,
  auto-waiting, backends, pytest fixtures, headless mode, how dolphin
  locates your app.

Per-stack guides:
[Native Windows](https://dolphinsoft.pl/latest/guides/native/) ·
[SAP](https://dolphinsoft.pl/latest/guides/sap/) ·
[Qt](https://dolphinsoft.pl/latest/guides/qt/) ·
[Java Swing / AWT](https://dolphinsoft.pl/latest/guides/java/) ·
[Delphi / VCL](https://dolphinsoft.pl/latest/guides/delphi/) ·
[Mainframe (3270 / 5250 / HLLAPI)](https://dolphinsoft.pl/latest/guides/mainframe/) ·
[Oracle Forms](https://dolphinsoft.pl/latest/guides/oracle-forms/) ·
[PowerBuilder](https://dolphinsoft.pl/latest/guides/powerbuilder/) ·
[WebView2](https://dolphinsoft.pl/latest/guides/webview2/) ·
[Office](https://dolphinsoft.pl/latest/guides/office/) ·
[Image-based](https://dolphinsoft.pl/latest/guides/image-based/) ·
[Embedded web (Electron / CEF)](https://dolphinsoft.pl/latest/guides/embedded-web/).

Reference:
[API index](https://dolphinsoft.pl/latest/reference/) ·
[Support matrix](https://dolphinsoft.pl/latest/support-matrix/) — stack × backend × mode ·
[Backend capabilities](https://dolphinsoft.pl/latest/backend-capabilities/) — the
`Capability` vocabulary ·
[CLI](https://dolphinsoft.pl/latest/reference/cli/) ·
[Migration guide](https://dolphinsoft.pl/latest/migration/).

## Development

See [CONTRIBUTING.md](https://github.com/DolphinSoftEU/DolphinDesktop/blob/master/CONTRIBUTING.md) for setup and
[RELEASING.md](https://github.com/DolphinSoftEU/DolphinDesktop/blob/master/RELEASING.md) for the publish checklist.

## Contact

Maintained by DolphinSoft Kamil Głuszek.

- Website: <https://dolphinsoft.pl>
- Email: <kontakt@dolphinsoft.pl>

## License

[Apache License 2.0](https://github.com/DolphinSoftEU/DolphinDesktop/blob/master/LICENSE)
