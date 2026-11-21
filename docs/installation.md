# Installation

## Requirements

- Windows 10 or Windows 11.
- Python 3.11, 3.12, or 3.13 for the 0.2.0 release.
- A terminal running as the same Windows user as the application under test.

Dolphin uses Microsoft UI Automation, Win32 APIs, pywin32, and COM. macOS and Linux are not supported runtime targets for the documented desktop automation flow.

## Preview installation

This documentation describes the upcoming **0.2.0** release. The published
[PyPI release](https://pypi.org/project/dolphin-desktop/) is **0.1.0**, whose
API and integrations differ. For the API shown here, install the development
branch in a dedicated environment; Git must be on PATH:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install "dolphin-desktop[pytest] @ git+https://github.com/DolphinSoftEU/DolphinDesktop.git@testpypi-preview"
```

This installs pytest and Allure support with Dolphin's compatible pytest range.
For repeatable CI, replace `testpypi-preview` in the requirement with a reviewed
commit SHA, or install an approved wheel from the corresponding preview build.
Install that preview explicitly again when creating a new environment: generated
project metadata names `dolphin-desktop` without pinning a particular preview.

## Published PyPI release

To install the currently published release rather than the preview:

```powershell
python -m pip install dolphin-desktop
```

The remaining pages describe 0.2.0. See the release's own documentation and
changelog when using 0.1.0.

## Base dependencies

On Windows, the base package installs:

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
| `all` | `cdp`, `fast`, `pytest` and `vision` | Install these four extras together. `docs` and `telemetry` are separate choices. |
| `docs` | `mkdocs-material`, `mkdocstrings[python]`, `mike` | You build this documentation site |

In a source checkout, development tools are a dependency group rather than
a package extra: `uv sync --locked --group dev`. See `CONTRIBUTING.md` in the source checkout for component prerequisites.

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

After installing the preview above, add optional extras in the same environment:

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
| `sap`, `qt`, `electron`, `mainframe`, `oracle-forms`, `delphi` | Stack-specific starter tests and prerequisites; see `dolphin init --help` |

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


## Build these docs from source

From a checkout containing `pyproject.toml` and `uv.lock`:

```powershell
uv sync --locked --group dev --extra docs
uv run --no-sync mkdocs serve
```

Use `uv run --no-sync mkdocs build --strict` to check a documentation change.
The navigation, public base URL, preview notice and API rendering are configured
in `mkdocs.yml`. API pages read signatures from `src/`.
