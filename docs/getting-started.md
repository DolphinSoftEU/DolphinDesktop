# Getting Started

The shortest path from a clean Python environment to a passing test
for **your** application stack.

> **Windows only.** dolphin_desktop drives Microsoft UI Automation,
> Java Access Bridge, and Chrome DevTools Protocol from a Windows
> process. macOS and Linux are not supported for the automation
> targets — pull requests welcome, but not part of the current release.

## 1 — Environment check

Requirements:

* Windows 10 (build 19041+) or Windows 11
* Python **3.11, 3.12, or 3.13** (`python --version`)
* A shell running as the same Windows user as the app under test

Recommended: create a dedicated virtual environment so dolphin's
optional dependencies don't leak into your other Python projects.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## 2 — Install

Base install — always required:

```powershell
python -m pip install "dolphin-desktop[pytest] @ git+https://github.com/DolphinSoftEU/DolphinDesktop.git@testpypi-preview"
```

Verify the install:

```powershell
dolphin doctor
```

`dolphin doctor` reports Python, package dependencies and local accessibility
prerequisites. A successful probe does not establish compatibility with your
application or a remote service. Install the optional extra and external
runtime required by your stack, then run a focused test against that target.

## 3 — Install the extra for your stack

Pick the row that matches the app you want to test:

| Stack | Install command | See |
|---|---|---|
| Windows GUI (WPF, WinForms, UWP) | *(base install is enough)* | [Quickstart](quickstart.md) |
| **SAP GUI Scripting** | *(base install)* | [SAP guide](guides/sap.md) |
| **Qt 5 / Qt 6** (widgets + QML) | *(base install)* | [Qt guide](guides/qt.md) |
| **Electron / CEF** (VS Code, Steam, Spotify…) | Add `[cdp]`; enable remote debugging on the target app | [Embedded web](guides/embedded-web.md) |
| **Java Swing / Oracle Forms** | *(base install; configure an absolute `JAVA_HOME` with JAB)* | [Oracle Forms](guides/oracle-forms.md) |
| **Mainframe 3270** (z/OS, CICS) | *(base install; needs wc3270)* | [Mainframe](guides/mainframe.md) |
| **Mainframe 5250** (IBM i, AS/400) | *(base install; pure-Python)* | [Mainframe](guides/mainframe.md) |
| **Delphi / VCL** (RAD Studio, Lazarus) | *(base install)* | [Delphi](guides/delphi.md) |
| **PowerBuilder** (2019+ Appeon) | *(base install)* | [PowerBuilder](guides/powerbuilder.md) |
| Image-based fallback | `pip install "dolphin-desktop[vision]"` | [Image-based](guides/image-based.md) |

You can install multiple extras at once:

```powershell
pip install "dolphin-desktop[cdp,vision]"
```

## 4 — Your first test, per stack

Each snippet is a minimal pytest pattern for a prepared application. Replace
example paths, hosts and selectors with your own settings before running it.
For local demos that need no business-system credentials, see `examples/` in
the source checkout. A skipped test means its target was not verified.

> **You supply the path to your app.** dolphin_desktop never
> hardcodes a location for your executable — every `launch_*(...)`
> below takes the path from you. For the full mental model (launch
> vs connect-via-COM vs attach-via-window-criteria) and the CI
> parametrisation pattern, see
> [Core Concepts → How Dolphin Locates Your Application](core-concepts.md#how-dolphin-locates-your-application).

### Windows GUI (Notepad)

```python
from dolphin_desktop import Desktop

def test_notepad_launches():
    with Desktop().launch("notepad.exe") as app:
        win = app.window(class_name="Notepad")
        win.wait_until_ready(timeout=5)
        assert win.is_visible()
        assert win.title()
```

### SAP GUI

```python
import pytest
from dolphin_desktop import Desktop

def test_sap_gui_reachable():
    # SAP GUI must be running with Scripting enabled.
    try:
        sap = Desktop().sap(timeout=5)
    except Exception as exc:
        pytest.skip(f"SAP GUI not running or scripting disabled: {exc}")
    assert sap.connections(), "no SAP connection is open"
```

### Qt widgets (via UIA)

```python
from dolphin_desktop import Desktop

def test_qt_app_launches():
    with Desktop().launch_qt(r"C:\path\to\your_qt_app.exe") as app:
        win = app.window(title_re=".*")
        win.wait_until_ready(timeout=10)
        assert win.title()  # any non-empty title
```

For QML / QGraphicsView, see the [Qt agent guide](guides/qt.md).

### Electron / CEF via CDP

```python
import pytest
from dolphin_desktop import Desktop, is_cdp_available, cdp_install_hint

def test_vscode_via_cdp():
    if not is_cdp_available():
        pytest.skip(cdp_install_hint())
    app, cdp = Desktop().launch_electron_cdp(
        r'"C:\Users\<you>\AppData\Local\Programs\Microsoft VS Code\Code.exe"',
        debug_port=9223,
    )
    try:
        cdp.locator(".monaco-workbench").wait_for(state="visible", timeout=30)
        assert cdp.locator(".monaco-workbench").is_visible()
    finally:
        cdp.close()
        app.kill()
```

For Steam / Spotify / CEF hosts, swap `launch_electron_cdp` →
`launch_cef_cdp`.

### Oracle Forms (Java Swing)

```python
import pytest
from dolphin_desktop import Desktop

def test_forms_reachable():
    try:
        app = Desktop().launch_oracle_forms(
            jnlp="http://forms.example.com/forms/frmservlet",
            timeout=30,
        )
    except Exception as exc:
        pytest.skip(f"Forms server unreachable: {exc}")
    try:
        app.form().wait_ready(timeout=30)
        assert app.form().title()
    finally:
        app.close()
```

For a public Java Access Bridge example, see `examples/java_swing/`.
The Forms-specific Swing mock is maintained with the separate acceptance suite.

### Mainframe 3270 (z/OS TSO, CICS)

Install [wc3270](https://x3270.bgp.nu/) first —
dolphin auto-detects `ws3270.exe` in `%LOCALAPPDATA%\wc3270\` and
`C:\Program Files\wc3270\`.

```python
from dolphin_desktop import Desktop

def test_3270_reachable():
    with Desktop().mainframe(
        host="your.3270.host",
        port=992,
        session_type="3270",
        tls=True,
    ) as term:
        term.wait_ready(timeout=10)
        assert term.screen().rows in (24, 43)
```

### Delphi / VCL (Lazarus, RAD Studio)

```python
from dolphin_desktop import Desktop

def test_delphi_app_launches():
    with Desktop().launch_delphi(r"C:\path\to\your_vcl_app.exe") as app:
        form = app.form(title_re=".*")
        form.wait_ready(timeout=10)
        assert form.title()
```

For UIA selectors and the provider prerequisite for `TComponent.Name` lookup,
see the [Delphi guide](guides/delphi.md). Stock VCL does not automatically
publish component Names as AutomationIds, including on Delphi 12.

### PowerBuilder (Appeon 2019+)

```python
from dolphin_desktop import Desktop

def test_powerbuilder_launches():
    with Desktop().launch_powerbuilder(r"C:\path\to\pb_app.exe") as app:
        win = app.window(title_re=".*")
        win.wait_until_ready(timeout=10)
        assert win.title()
```

For classic pre-Appeon PowerBuilder, see the
[PowerBuilder guide](guides/powerbuilder.md) — DataWindow grids require
image-based fallback in 0.2.0.

### Mainframe 5250 (IBM i)

Pure Python, with no emulator binary required. Supply a prepared IBM i host,
its verified TLS endpoint and expected sign-on text. See the native TN5250
limitations in the [mainframe guide](guides/mainframe.md#native-tn5250-backend-status).

```python
from dolphin_desktop import Desktop

def test_ibm_i_reachable():
    with Desktop().mainframe(
        host="your.ibmi.host",
        port=992,
        session_type="5250",
        backend="tn5250",
        tls=True,
    ) as term:
        term.wait_ready(timeout=10)
        assert "Sign On" in term.text()  # Adapt to your host's sign-on screen.
```

## 5 — Run

```powershell
pytest test_first.py -v
```

If the test passes, you're set up — jump into the per-stack docs
(right column of the table above) to build real tests.

## 6 — Troubleshooting the setup

The most common issues, quick fixes:

| Symptom | Cause | Fix |
|---|---|---|
| `dolphin: command not found` | Not in the venv's PATH | Reactivate the venv, then reinstall the preview from [Installation](installation.md#preview-installation) |
| `RuntimeError: dolphin_desktop[cdp] is not installed` | Playwright missing | Add the `cdp` extra to the installed preview; browser downloads are needed only when launching Playwright's own browser |
| `MainframeError: No ws3270/s3270 binary found` | wc3270 not on PATH | Install wc3270 to `%LOCALAPPDATA%\wc3270\` or pass `ws3270_path=` |
| `Java Access Bridge not enabled` | JAB configuration incomplete | Set a trusted absolute `JAVA_HOME` with the bridge DLL and `jabswitch.exe`; follow the [Java guide](guides/java.md) |
| `SapConnection: could not attach` | SAP GUI scripting disabled | Enable in SAP GUI Options → Accessibility → Scripting → Enable scripting |
| Locator times out on a visible element | UIA / JAB tree not published yet | Increase `timeout=` on `wait_for()` or use `dolphin doctor` to verify UIA is healthy |
| `SetCursorPos: No error message is available` | RDP or background focus | Use the JAB-direct path (`item.type_text` on Forms) or move to `CDPLocator` for web content |

Per-stack troubleshooting lives inside each stack's doc — see the
Troubleshooting section of each guide under `docs/guides/`.

## 7 — Where to go next

* [Core concepts](core-concepts.md) — the mental model dolphin uses.
* [API reference](reference/index.md) — every public method.
* [FAQ](faq.md) — pitfalls, gotchas, and answers to real questions.
* Per-stack guides linked in the install table above.

Contributing a stack that isn't listed here? PRs welcome —
the public demos under `examples/` show the pattern.
