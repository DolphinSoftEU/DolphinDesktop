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
* Python **3.11 or newer** (`python --version`)
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
pip install dolphin-desktop pytest
```

Verify the install:

```powershell
dolphin doctor
```

`dolphin doctor` prints a table of every automation subsystem (UIA,
Win32, JAB, CDP, SAP, image OCR) and whether each is functional. For
rows that name an extra (`[cdp]`, `[vision]`), a ❌ tells you which
extra to install (see next step); the other stacks work from the base
install once their external prerequisite is in place.

## 3 — Install the extra for your stack

Pick the row that matches the app you want to test:

| Stack | Install command | See |
|---|---|---|
| Windows GUI (WPF, WinForms, UWP) | *(base install is enough)* | [Quickstart](quickstart.md) |
| **SAP GUI Scripting** | *(base install)* | [SAP guide](guides/sap.md) |
| **Qt 5 / Qt 6** (widgets + QML) | *(base install)* | [Qt guide](guides/qt.md) |
| **Electron / CEF** (VS Code, Steam, Spotify…) | `pip install "dolphin-desktop[cdp]"` + `playwright install chromium` | [Embedded web](guides/embedded-web.md) |
| **Java Swing / Oracle Forms** | *(base install; JAB is auto-enabled)* | [Oracle Forms](guides/oracle-forms.md) |
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

Each snippet is a **runnable pytest file** — save it as
`test_first.py`, then `pytest test_first.py -v`. Every example returns
`assert True` on success so you can confirm dolphin talks to the
platform correctly before wiring your own selectors.

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
        assert "Notepad" in win.title()
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

For local testing without a Forms server, see the working Java Swing
mock in `tests/oracle_forms/`.

### Mainframe 3270 (z/OS TSO, CICS)

Install [wc3270](https://x3270.miraheze.org/wiki/Download) first —
dolphin auto-detects `ws3270.exe` in `%LOCALAPPDATA%\wc3270\` and
`C:\Program Files\wc3270\`.

```python
import pytest
from dolphin_desktop import Desktop, MainframeError, which

def test_3270_reachable():
    if which("ws3270") is None:
        pytest.skip("install wc3270: see docs/guides/mainframe.md")
    try:
        term = Desktop().mainframe(
            host="your.3270.host",
            session_type="3270",
        )
    except MainframeError as exc:
        pytest.skip(f"host unreachable: {exc}")
    try:
        term.wait_ready(timeout=10)
        assert term.screen().rows in (24, 43)
    finally:
        term.disconnect()
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

For component lookups by `TComponent.Name`, see the
[Delphi guide](guides/delphi.md).

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

Pure-Python — no external binary required.

```python
import pytest
from dolphin_desktop import Desktop, tcp_reachable

def test_ibm_i_reachable():
    if not tcp_reachable("pub400.com", 23, timeout=3):
        pytest.skip("no network to pub400.com")
    with Desktop().mainframe(
        host="pub400.com",
        session_type="5250",
        backend="tn5250",
    ) as term:
        term.wait_ready(timeout=10)
        assert "PUB400" in term.text()
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
| `dolphin: command not found` | Not in the venv's PATH | Reactivate the venv, then `pip install --upgrade dolphin-desktop` |
| `RuntimeError: dolphin_desktop[cdp] is not installed` | Playwright missing | `pip install "dolphin-desktop[cdp]" && playwright install chromium` |
| `MainframeError: No ws3270/s3270 binary found` | wc3270 not on PATH | Install wc3270 to `%LOCALAPPDATA%\wc3270\` or pass `ws3270_path=` |
| `Java Access Bridge not enabled` | JAB switch off | Run `jabswitch /enable` in an admin PowerShell, then relaunch the test |
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
the per-stack suites under `tests/` show the pattern.
