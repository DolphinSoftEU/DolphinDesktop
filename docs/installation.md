# Installation

## Requirements

- Windows 10 or Windows 11.
- Python 3.11, 3.12, or 3.13 for the 0.2.0 release.
- A terminal running as the same Windows user as the application under test.

Dolphin uses Microsoft UI Automation, Win32 APIs, pywin32, and COM. macOS and Linux are not supported runtime targets for the documented desktop automation flow.

## Basic Install

Install the package:

```bash
pip install dolphin-desktop
```

For a pytest project, install pytest in the same environment:

```bash
pip install dolphin-desktop pytest
```

The base package installs:

| Dependency | Used for |
| --- | --- |
| `pywinauto` | UIA and Win32 automation |
| `Pillow` | Screenshots and image handling |
| `comtypes` | COM bindings used by UIA |
| `pywin32` | Win32, COM, clipboard, and process helpers |
| `pyyaml` | Object repository YAML files |

## Optional Extras

Extras are defined in `pyproject.toml`.

| Extra | Installs | Use when |
| --- | --- | --- |
| `cdp` | `playwright` | You automate Electron, CEF, or WebView2 apps over the Chrome DevTools Protocol |
| `vision` | `opencv-python`, `numpy`, `pytesseract` | You need image matching or OCR |
| `fast` | `mss` | You want faster full-desktop screenshots for trace steps. Note this does **not** enable video: MP4 recording needs an external `ffmpeg` binary, which no extra can install. |
| `pytest` | `pytest`, `allure-pytest` | You want pytest plus Allure integration |
| `telemetry` | `sentry-sdk` | You explicitly enable telemetry |
| `all` | everything in `cdp`, `fast`, `pytest` and `vision` | You want every runtime extra in one install. Excludes `telemetry`, which reports off the machine and is opted into by name. |
| `docs` | `mkdocs-material`, `mkdocstrings[python]`, `mike` | You build this documentation site |

Working on Dolphin itself needs the development toolchain, which is a
dependency group rather than an extra: `uv sync --group dev`.

## Stacks that need no extra

SAP, Qt, IBM mainframe and Oracle Forms are supported by the base package —
there is nothing to add with `pip`, because what they need is not a Python
wheel:

* **SAP GUI** — automation goes through the SAP GUI Scripting COM interface,
  published by SAP GUI itself. It must also be enabled server-side
  (`sapgui/user_scripting = TRUE`, transaction `RZ11`); see the
  [SAP guide](guides/sap.md).
* **Qt** — the Qt agent DLLs ship inside the package.
* **IBM mainframe** — the TN5250 client is pure Python. The `s3270` backend
  drives `ws3270.exe`, and HLLAPI binds to the emulator's own DLL; both are
  installed outside `pip`.
* **Oracle Forms / Java Swing** — the Java Access Bridge ships with the
  Adoptium or Oracle JDK and is enabled with `jabswitch /enable`.

Examples:

```bash
pip install "dolphin-desktop[vision]"
pip install "dolphin-desktop[fast,pytest]"
pip install "dolphin-desktop[docs]"
```

`pytesseract` is a Python wrapper. OCR also requires the Tesseract executable to be installed separately and available to your test process.

## Verify The Environment

Run:

```bash
dolphin doctor
```

`doctor` prints the Dolphin version, Python executable, Windows platform, required dependencies, optional dependencies, UIA access, and relevant `DOLPHIN_*` environment variables.

Required dependencies should be reported as installed. If the UIA access check fails, run the command from an interactive Windows session as the same user that owns the desktop.

## Create A Test Project

The project scaffold command is:

```bash
dolphin init my-tests
```

If prompted for a template, press Enter to accept `standard`. For non-interactive setup, use:

```bash
dolphin init my-tests --yes
```

Available templates:

| Template | Contents |
| --- | --- |
| `minimal` | `pyproject.toml`, `conftest.py`, and one sample test |
| `standard` | Minimal files plus `objects/notepad_page.py` and package markers |
| `enterprise` | Standard files plus Allure config, GitHub Actions, `.gitignore`, and pre-commit config |

## Common Setup Problems

`pywin32` import errors:

```bash
pip install pywin32
python -m pywin32_postinstall -install
```

UIA cannot see the application:

Run the test process in the same interactive user session as the app. UIA cannot automate another user's desktop, the secure UAC desktop, or most Session 0 service desktops.

The application runs as Administrator:

Run the test process as Administrator too. Windows integrity levels prevent a non-elevated process from driving elevated windows.

Image matching fails:

Install the `vision` extra and make sure your template image matches the target DPI and theme.
