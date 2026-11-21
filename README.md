# DolphinDesktop

[![PyPI](https://img.shields.io/pypi/v/dolphin-desktop.svg)](https://pypi.org/project/dolphin-desktop/)
[![Python](https://img.shields.io/badge/python-3.11--3.13-blue.svg)](https://github.com/DolphinSoftEU/DolphinDesktop/blob/v0.2.0/pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Windows-informational.svg)](https://github.com/DolphinSoftEU/DolphinDesktop)
[![CI](https://github.com/DolphinSoftEU/DolphinDesktop/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/DolphinSoftEU/DolphinDesktop/actions/workflows/ci.yml?query=branch%3Amaster)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/DolphinSoftEU/DolphinDesktop/blob/master/LICENSE)

Python test automation for Windows desktop applications: native UIA/Win32,
SAP GUI, Qt, Electron/CEF, Java Swing, Oracle Forms, Delphi, PowerBuilder,
mainframe terminals and Office COM. Lazy locators, automatic waits and pytest
fixtures share a common style, with stack-specific facades where needed.

**Release:** **0.2.0** — **2026-11-21**. See the
[changelog](https://github.com/DolphinSoftEU/DolphinDesktop/blob/v0.2.0/CHANGELOG.md)
for changes from 0.1.0.

## Requirements

- Windows 10 or Windows 11 for desktop automation.
- Python 3.11–3.13; 64-bit Python recommended. Qt agent targets must be x64.
- The same Windows user/session and compatible permissions as the application
  under test. Physical input and pixel capture require an interactive desktop.

Installing or importing the package on Linux/macOS does not enable Windows
desktop automation; some non-desktop helpers can still be used there.
External applications and runtimes are installed separately from Python extras.

## Install and quickstart

Install the latest published release from PyPI:

```powershell
python -m pip install dolphin-desktop
```

Install 0.2.0 with pytest support in a dedicated environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install "dolphin-desktop[pytest]==0.2.0"

dolphin doctor
dolphin init my-tests --yes
cd my-tests
python -m pytest tests/ -v
```

The default scaffold creates a Notepad test and a Page Object under `objects/`.
Run it in the environment where Dolphin was installed. The generated project's
dependency metadata names `dolphin-desktop`.
For locked repository development, follow
[CONTRIBUTING.md](https://github.com/DolphinSoftEU/DolphinDesktop/blob/v0.2.0/CONTRIBUTING.md).

The underlying API can also be used directly:

```python
from dolphin_desktop import Desktop

with Desktop().launch("notepad.exe") as app:
    win = app.window(class_name="Notepad")
    editor = win.get_by_role("Document")
    editor.click()
    editor.type_text("Hello, Dolphin!")
    assert "Hello, Dolphin!" in editor.text()
```

The context manager cleans up the launched application even if an assertion
fails. Selectors depend on the target's accessibility tree; use `dolphin spy`
to inspect controls if your Notepad or application exposes different roles.

## Features

- Lazy locators with automatic waits, explicit state/text waiters and ordered
  selector/image fallbacks.
- Programmatic actions such as `invoke()` and `set_value()` where the target
  exposes the required pattern. Unsupported operations report capability or
  pattern errors rather than silently substituting physical input.
- Backend capability discovery and stack-specific locators for native controls,
  Java, SAP and CDP.
- pytest `desktop` / `launch` fixtures, retries, screenshots, traces, video and
  optional Allure reporting.
- CLI tools for project scaffolding, environment diagnostics, element picking,
  recording, trace viewing and hidden-desktop execution with `dolphin-run`.

Timeout conventions depend on the API: native locators support
`.timeout(seconds)`, selected convenience methods accept `timeout_ms`, and
`playwright_compat` follows Playwright's millisecond convention. See the
[API reference](https://dolphinsoft.pl/docs/reference) for individual signatures.

## Application stacks

| Stack | Automation surface | Prerequisites beyond the base package |
| --- | --- | --- |
| Native Windows: WPF, WinForms, UWP, MFC | UIA / Win32 through pywinauto | Target application with accessible controls |
| SAP GUI | COM Scripting, `SapGui` / `SapSession` / `SapLocator` | SAP GUI; Scripting enabled on client and server |
| Qt 5 / Qt 6 | UIA widgets; bundled agent for QObject, QML and QGraphicsView | Compatible x64 target for agent operations |
| Electron / CEF | CDP through Playwright | `[cdp]`; target started with remote debugging enabled |
| WebView2 | UIA by default; optional CDP attachment | `[cdp]` only for DOM access, with an enabled debug endpoint |
| Java Swing / Oracle Forms | Java Access Bridge | JRE/JDK with JAB and an explicitly configured absolute `JAVA_HOME` |
| Mainframe TN3270 | s3270 subprocess | wc3270 / `ws3270.exe` |
| Mainframe TN5250 | Native pure-Python protocol client | Reachable IBM i host; write-path limitation below |
| HLLAPI | Emulator DLL binding | Compatible terminal emulator and absolute `hllapi_dll_path` |
| Delphi VCL / Lazarus LCL | UIA/MSAA controls and Delphi facade | Accessible controls; Name selectors require a provider |
| PowerBuilder | UIA; image/OCR fallback for opaque DataWindow content | `[vision]` and Tesseract when OCR is needed |
| Excel / Word / PowerPoint / classic Outlook | Public COM facades | Installed desktop Office; configured MAPI profile for Outlook |
| Image fallback | OpenCV template matching / Tesseract OCR | `[vision]`; Tesseract executable for OCR |

Important boundaries:

- Stock VCL does not automatically publish Delphi component names as UIA
  AutomationIds. Name lookup requires an application-supplied provider;
  Dolphin includes no VCL Name provider or RTTI agent.
- Native TN5250 screen reading has live-host coverage; its write/submit path
  remains unverified against live IBM i hosts. Use HLLAPI with an enterprise
  emulator for write-critical flows.
- `Desktop.mainframe(tls=True)` enables verified TLS on supported backends.
  Remote plaintext requires explicit `allow_plaintext=True`; port 992 alone
  does not enable TLS.
- Outlook automation requires classic Outlook, not new Outlook. Office COM
  exposes document/item APIs; ribbons and dialogs use UIA/Win32 separately.
- Hidden mode supports suitable programmatic operations; physical clicks and
  keyboard input still depend on the desktop and control capabilities.

See the [stack guides](https://dolphinsoft.pl/docs/guides) and
[support matrix](https://dolphinsoft.pl/docs/support-matrix) for detailed limits.

## Optional extras

The following extras are available in 0.2.0:

| Extra | Purpose |
| --- | --- |
| `cdp` | Playwright integration for CDP-enabled applications |
| `vision` | OpenCV, NumPy and pytesseract for template matching and OCR |
| `fast` | `mss` for faster screenshots |
| `pytest` | Supported pytest version and Allure integration |
| `telemetry` | Sentry integration, enabled explicitly |
| `all` | `cdp`, `fast`, `pytest` and `vision`; excludes `telemetry` and `docs` |
| `docs` | MkDocs tools for building the documentation |

Install extras together, for example `dolphin-desktop[cdp,vision,pytest]`, from
the same source/version you selected above. MP4 recording needs an external
`ffmpeg` executable; the `fast` extra does not install it. OCR needs a separate
Tesseract installation. There is no `video` or `dev` extra in 0.2.0.

## Tests and verification

CI covers unit tests, controlled Windows components, native Qt agent
reproducibility, dependency/security checks and wheel installation on
Windows/Linux/macOS. Real-application and external-system suites have separate
prerequisites; their presence in `tests/` does not establish a passing run on
every product/version. A skipped test is incomplete verification.

See the [current CI runs](https://github.com/DolphinSoftEU/DolphinDesktop/actions/workflows/ci.yml)
and [contributing guide](https://github.com/DolphinSoftEU/DolphinDesktop/blob/v0.2.0/CONTRIBUTING.md)
for test categories, setup and desktop acceptance commands.

## Documentation and development

- [Documentation](https://dolphinsoft.pl/docs): installation, quickstart,
  guides and API reference.
- [Getting Started](https://dolphinsoft.pl/docs/getting-started) and
  [Quickstart](https://dolphinsoft.pl/docs/quickstart).
- [0.2.0 documentation sources](https://github.com/DolphinSoftEU/DolphinDesktop/tree/v0.2.0/docs)
  for this release; hosted documentation may lag these changes.
- [Contributing](https://github.com/DolphinSoftEU/DolphinDesktop/blob/v0.2.0/CONTRIBUTING.md)
  for repository setup, checks and local documentation builds.

Package releases are published by designated project maintainers.

## Security

See [SECURITY.md](https://github.com/DolphinSoftEU/DolphinDesktop/blob/v0.2.0/SECURITY.md)
for private vulnerability reporting, recorder scope, local control channels
and artifact redaction limits. Screenshots, videos and generated scripts can
contain sensitive application data; review them before sharing.

## Contact and license

Maintained by DolphinSoft: [dolphinsoft.pl](https://dolphinsoft.pl) ·
[kontakt@dolphinsoft.pl](mailto:kontakt@dolphinsoft.pl).

[Apache License 2.0](https://github.com/DolphinSoftEU/DolphinDesktop/blob/master/LICENSE).
