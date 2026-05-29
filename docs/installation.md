# Installation

## Requirements

- Windows 10 or Windows 11.
- Python 3.11 or newer.
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
| `vision` | `opencv-python`, `pytesseract` | You need image matching or OCR |
| `fast` | `mss` | You want faster screen capture |
| `video` | `mss` | You want test video capture |
| `pytest` | `pytest`, `allure-pytest` | You want pytest plus Allure integration |
| `telemetry` | `sentry-sdk` | You explicitly enable telemetry |
| `docs` | `mkdocs-material`, `mkdocstrings[python]`, `mike` | You build this documentation site |
| `dev` | test, lint, type-check, and pre-commit tools | You work on Dolphin itself |

Examples:

```bash
pip install "dolphin-desktop[vision]"
pip install "dolphin-desktop[video,pytest]"
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
