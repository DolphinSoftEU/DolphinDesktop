"""dolphin CLI entry point."""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

# Seconds `dolphin init` gives its external helpers before giving up — without a
# bound, a stalled network or a credential prompt hangs the command forever.
_PIP_INSTALL_TIMEOUT = 600.0
_GIT_INIT_TIMEOUT = 60.0

# init — embedded project templates

_T_PYPROJECT_MINIMAL = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "0.1.0"
requires-python = ">={py_version}"
dependencies = [
    "dolphin-desktop",
    "pytest>=8.3",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
timeout = 60
pythonpath = ["."]
markers = [
    "integration: marks tests requiring a real app process",
]
"""

_T_PYPROJECT_STANDARD = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "0.1.0"
requires-python = ">={py_version}"
dependencies = [
    "dolphin-desktop",
    "pytest>=8.3",
    "ruff>=0.8",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
timeout = 60
pythonpath = ["."]
markers = [
    "integration: marks tests requiring a real app process",
]

[tool.ruff]
target-version = "py311"
line-length = 100
"""

_T_PYPROJECT_ENTERPRISE = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "0.1.0"
requires-python = ">={py_version}"
dependencies = [
    "dolphin-desktop",
    "pytest>=8.3",
    "allure-pytest>=2.13",
    "ruff>=0.8",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
timeout = 60
pythonpath = ["."]
addopts = "--alluredir=allure-results"
markers = [
    "integration: marks tests requiring a real app process",
]

[tool.ruff]
target-version = "py311"
line-length = 100
"""

_T_CONFTEST = '''\
"""Project-level pytest configuration.

The ``desktop`` and ``launch`` fixtures are provided automatically by
dolphin's pytest plugin (registered via the dolphin package entry point).
Add project-specific fixtures below.
"""
import pytest
'''

_T_TEST_MINIMAL = '''\
"""Sample integration test — verifies that dolphin can automate Notepad."""
import pytest

pytestmark = pytest.mark.integration


def test_notepad_type_and_read(launch):
    """Open Notepad, type text, verify it can be read back."""
    app = launch("notepad.exe")
    win = app.window(class_name="Notepad")
    editor = win.get_by_role("Document")

    editor.click()
    editor.type_text("Hello, Dolphin!")

    assert "Hello, Dolphin!" in editor.text()
'''

_T_TEST_STANDARD = '''\
"""Sample integration test — verifies that dolphin can automate Notepad."""
import pytest

from objects.notepad_page import NotepadPage

pytestmark = pytest.mark.integration


def test_notepad_with_page_object(launch):
    """Open Notepad via page object; type text, then verify it can be read back."""
    app = launch("notepad.exe")
    win = app.window(class_name="Notepad")
    page = NotepadPage(win)

    page.type_text("Hello, Dolphin!")
    assert "Hello, Dolphin!" in page.read_text()
'''

_T_NOTEPAD_PAGE = '''\
"""Page Object for Windows Notepad."""
from __future__ import annotations

from dolphin_desktop import Window


class NotepadPage:
    def __init__(self, window: Window) -> None:
        self._win = window
        self._editor = window.get_by_role("Document")

    def type_text(self, text: str) -> "NotepadPage":
        self._editor.click()
        self._editor.type_text(text)
        return self

    def clear(self) -> "NotepadPage":
        self._editor.clear()
        return self

    def read_text(self) -> str:
        return self._editor.text()
'''

_T_GITIGNORE = """\
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
dolphin-traces/
dolphin-videos/
dolphin-screenshots/
allure-results/
allure-report/
.venv/
venv/
*.egg-info/
dist/
build/
.ruff_cache/
"""

_T_PRE_COMMIT = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
"""

_T_GH_CI = """\
name: Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "{py_version}"

      - name: Install dependencies
        run: pip install dolphin-desktop pytest allure-pytest

      - name: Run tests
        run: pytest tests/ -v --dolphin-backend=uia
"""

# Stack-specific templates

_T_TEST_SAP = '''\
"""SAP GUI Scripting sample — connect + read the first session's system info."""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop


@pytest.fixture(scope="module")
def sap():
    try:
        yield Desktop().sap(timeout=5)
    except Exception as exc:
        pytest.skip(
            f"SAP GUI not running or scripting disabled: {exc}"
        )


def test_sap_reachable(sap):
    """SAP GUI must be running with Scripting enabled (client + server)."""
    assert sap.connections()


def test_first_session_info(sap):
    if not sap.connections():
        pytest.skip("SAP GUI is running but no connection is open")

    info = sap.session().system_info()
    if not info["user"]:
        pytest.skip("a connection is open but nobody is logged on")
    assert info["language"]
'''

_T_TEST_QT = '''\
"""Qt application sample — launch + wait for main window."""

from __future__ import annotations

from dolphin_desktop import Desktop


# Point this at your Qt app before running.
QT_APP_PATH = r"C:\\path\\to\\your\\qt_app.exe"


def test_qt_app_launches():
    with Desktop().launch_qt(QT_APP_PATH) as app:
        win = app.window(title_re=".*")
        win.wait_until_ready(timeout=10)
        assert win.title()  # any non-empty caption
'''

_T_TEST_ELECTRON = '''\
"""Electron via CDP sample — connect to VS Code and read the workbench."""

from __future__ import annotations

import os

import pytest

from dolphin_desktop import Desktop, cdp_install_hint, is_cdp_available


def _find_vscode() -> str | None:
    for path in (
        rf"{os.environ.get('LOCALAPPDATA', '')}\\Programs\\Microsoft VS Code\\Code.exe",
        r"C:\\Program Files\\Microsoft VS Code\\Code.exe",
    ):
        if os.path.isfile(path):
            return path
    return None


VSCODE = _find_vscode()


def test_vscode_via_cdp():
    if not is_cdp_available():
        pytest.skip(cdp_install_hint())
    if VSCODE is None:
        pytest.skip("VS Code not installed at any known path")

    app, cdp = Desktop().launch_electron_cdp(
        # launch() spawns without a shell, so %TEMP% would stay literal
        f\'"{VSCODE}" --user-data-dir={os.environ["TEMP"]}\\\\dolphin-vscode --no-sandbox\',
        debug_port=9223,
    )
    try:
        cdp.locator(".monaco-workbench").wait_for(state="visible", timeout=30)
        assert cdp.locator(".monaco-workbench").is_visible()
    finally:
        cdp.close()
        app.kill()
'''

_T_TEST_MAINFRAME = '''\
"""Mainframe TN3270/5250 sample — connect + read initial screen."""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, MainframeError, tcp_reachable, which


HOST = "pub400.com"  # replace with your host
PORT = 23
SESSION_TYPE = "5250"  # or "3270" for z/OS TSO / CICS


def test_mainframe_reachable():
    if not tcp_reachable(HOST, PORT, timeout=3):
        pytest.skip(f"cannot reach {HOST}:{PORT}")

    backend = "tn5250" if SESSION_TYPE == "5250" else "s3270"
    if backend == "s3270" and which("ws3270") is None:
        pytest.skip("install wc3270 — see docs/guides/mainframe.md")

    try:
        with Desktop().mainframe(
            host=HOST,
            port=PORT,
            session_type=SESSION_TYPE,
            backend=backend,
        ) as term:
            term.wait_ready(timeout=10)
            text = term.text()
            assert text  # any non-empty screen means the client parsed the WTD stream
    except MainframeError as exc:
        pytest.skip(f"host handshake failed: {exc}")
'''

_T_TEST_ORACLE_FORMS = '''\
"""Oracle Forms sample — launch via JNLP + read status line."""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, OracleFormsError


# Replace with your Forms deployment URL or a local JAR.
FORMS_JNLP = "http://forms.example.com/forms/frmservlet?config=hr"


def test_forms_launches():
    try:
        app = Desktop().launch_oracle_forms(jnlp=FORMS_JNLP, timeout=30)
    except OracleFormsError as exc:
        pytest.skip(f"Forms client failed to start: {exc}")

    try:
        app.form().wait_ready(timeout=30)
        assert app.form().title()
    finally:
        app.close()
'''

_T_TEST_DELPHI = '''\
"""Delphi / VCL sample — launch + read form title."""

from __future__ import annotations

from dolphin_desktop import Desktop


# Point this at your Delphi / Lazarus exe before running.
DELPHI_APP_PATH = r"C:\\path\\to\\your\\vcl_app.exe"


def test_delphi_app_launches():
    with Desktop().launch_delphi(DELPHI_APP_PATH) as app:
        form = app.form(title_re=".*")
        form.wait_ready(timeout=10)
        assert form.title()  # any non-empty caption


def test_lookup_by_component_name():
    """Resolve a component by TComponent.Name from the Object Inspector."""
    with Desktop().launch_delphi(DELPHI_APP_PATH) as app:
        form = app.form(title_re=".*")
        form.wait_ready(timeout=10)
        # Replace 'Button1' with the real Object Inspector name.
        # btn = form.component(name="Button1", cls="TButton")
        # btn.click()
        assert form.components()  # at least one child component visible
'''


_STACK_TEMPLATES = {
    "sap": _T_TEST_SAP,
    "qt": _T_TEST_QT,
    "electron": _T_TEST_ELECTRON,
    "mainframe": _T_TEST_MAINFRAME,
    "oracle-forms": _T_TEST_ORACLE_FORMS,
    "delphi": _T_TEST_DELPHI,
}


_DEFAULT_TRACE_DIR = "dolphin-traces"


# init helpers


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _scaffold(target: Path, name: str, template: str, py_version: str) -> None:
    target.mkdir(parents=True)
    (target / "tests").mkdir()

    ctx = {"name": name, "py_version": py_version}

    if template == "minimal":
        _write(target / "pyproject.toml", _T_PYPROJECT_MINIMAL.format(**ctx))
        _write(target / "conftest.py", _T_CONFTEST)
        _write(target / "tests" / "test_sample.py", _T_TEST_MINIMAL)
    elif template == "standard":
        _write(target / "pyproject.toml", _T_PYPROJECT_STANDARD.format(**ctx))
        _write(target / "conftest.py", _T_CONFTEST)
        (target / "objects").mkdir()
        _write(target / "objects" / "__init__.py", "")
        _write(target / "objects" / "notepad_page.py", _T_NOTEPAD_PAGE)
        _write(target / "tests" / "__init__.py", "")
        _write(target / "tests" / "test_sample.py", _T_TEST_STANDARD)
    elif template == "enterprise":
        _write(target / "pyproject.toml", _T_PYPROJECT_ENTERPRISE.format(**ctx))
        _write(target / "conftest.py", _T_CONFTEST)
        (target / "objects").mkdir()
        _write(target / "objects" / "__init__.py", "")
        _write(target / "objects" / "notepad_page.py", _T_NOTEPAD_PAGE)
        _write(target / "tests" / "__init__.py", "")
        _write(target / "tests" / "test_sample.py", _T_TEST_STANDARD)
        _write(target / ".gitignore", _T_GITIGNORE)
        _write(target / ".pre-commit-config.yaml", _T_PRE_COMMIT)
        gh_dir = target / ".github" / "workflows"
        gh_dir.mkdir(parents=True)
        _write(gh_dir / "ci.yml", _T_GH_CI.format(**ctx))
        (target / "allure-results").mkdir()
        _write(target / "allure-results" / ".gitkeep", "")
    elif template in _STACK_TEMPLATES:
        # Stack-specific scaffold — minimal pyproject + a working
        # first-test template for that stack.
        _write(target / "pyproject.toml", _T_PYPROJECT_MINIMAL.format(**ctx))
        _write(target / "conftest.py", _T_CONFTEST)
        _write(target / "tests" / "__init__.py", "")
        test_name = f"test_{template.replace('-', '_')}.py"
        _write(target / "tests" / test_name, _STACK_TEMPLATES[template])
    else:
        raise ValueError(f"Unknown template: {template!r}")


def _init_cmd(args: argparse.Namespace) -> None:
    """Bootstrap a new dolphin test project."""
    yes: bool = args.yes
    name: str = args.name or ""
    template: str = args.template or ""

    # --- project name ---
    if not name:
        if yes:
            name = "my-dolphin-tests"
        else:
            name = input("Project name [my-dolphin-tests]: ").strip() or "my-dolphin-tests"

    # --- template ---
    valid = ("minimal", "standard", "enterprise", *_STACK_TEMPLATES)
    if template not in valid:
        if yes:
            template = "standard"
        else:
            print("Available templates:")
            print("  1) minimal      — single test file + conftest + pyproject.toml")
            print("  2) standard     — tests/, objects/ (page objects), conftest, pyproject")
            print("  3) enterprise   — standard + Allure, GitHub Actions CI, pre-commit")
            print("  4) sap          — SAP GUI Scripting starter (connect + session probe)")
            print("  5) qt           — Qt widget app starter (launch_qt + wait for window)")
            print("  6) electron     — Electron via CDP starter (VS Code example)")
            print("  7) mainframe    — TN3270/5250 starter (pub400 or your host)")
            print("  8) oracle-forms — Oracle Forms starter via JAB (JNLP launch)")
            print("  9) delphi       — Delphi / Lazarus VCL starter (launch_delphi + lookup)")
            choice = input("Template [standard]: ").strip()
            _map = {
                "": "standard",
                "1": "minimal",
                "2": "standard",
                "3": "enterprise",
                "4": "sap",
                "5": "qt",
                "6": "electron",
                "7": "mainframe",
                "8": "oracle-forms",
                "9": "delphi",
                "minimal": "minimal",
                "standard": "standard",
                "enterprise": "enterprise",
                "sap": "sap",
                "qt": "qt",
                "electron": "electron",
                "mainframe": "mainframe",
                "oracle-forms": "oracle-forms",
                "delphi": "delphi",
            }
            template = _map.get(choice, "standard")

    py_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    target = Path(name)

    if target.exists():
        print(f"Error: '{name}' already exists.")
        sys.exit(1)

    print(f"\nBootstrapping '{name}' (template: {template}, Python: {py_version}) ...")
    _scaffold(target, name, template, py_version)

    print("\nCreated:")
    for p in sorted(target.rglob("*")):
        rel = p.relative_to(target)
        indent = "  " + "    " * (len(rel.parts) - 1)
        suffix = "/" if p.is_dir() else ""
        print(f"{indent}{rel.parts[-1]}{suffix}")

    # --- install ---
    do_install: bool = args.install
    if not yes and not do_install:
        deps = "dolphin-desktop pytest" + (" allure-pytest" if template == "enterprise" else "")
        ans = input(f"\nInstall dependencies ({deps})? [y/N]: ").strip().lower()
        do_install = ans == "y"

    install_failed = False
    if do_install:
        deps_list = ["dolphin-desktop", "pytest"]
        if template == "enterprise":
            deps_list.append("allure-pytest")
        print("\nInstalling dependencies ...")
        try:
            pip_res = subprocess.run(
                [sys.executable, "-m", "pip", "install", *deps_list],
                check=False,
                timeout=_PIP_INSTALL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            pip_res = subprocess.CompletedProcess(args=deps_list, returncode=1)
            print(f"\n  (pip install timed out after {_PIP_INSTALL_TIMEOUT:.0f}s)")
        if pip_res.returncode != 0:
            # pip surfaces its own error on stderr — flag the failure so the
            # closing "Done!" doesn't paper over it, and re-print the pip
            # command line so the user can retry manually.
            install_failed = True
            print(
                f"\n  (pip install exited with code {pip_res.returncode}; "
                f"dependencies were NOT installed — retry with: "
                f"pip install {' '.join(deps_list)})"
            )

    # --- git init ---
    do_git: bool = args.git
    if not yes and not do_git:
        ans = input("Initialize git repository? [y/N]: ").strip().lower()
        do_git = ans == "y"

    if do_git:
        try:
            result = subprocess.run(
                ["git", "init", str(target)],
                capture_output=True,
                text=True,
                timeout=_GIT_INIT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            print(f"  (git init skipped: timed out after {_GIT_INIT_TIMEOUT:.0f}s)")
        except OSError as exc:
            # No git in PATH — the project is already scaffolded, so this is a note,
            # not a traceback.
            print(f"  (git init skipped: {exc})")
        else:
            if result.returncode == 0:
                print(f"Initialized git repository in {target.resolve()}")
            else:
                print(f"  (git init skipped: {result.stderr.strip()})")

    # --- next steps ---
    print("\nDone! Next steps:")
    print(f"  cd {name}")
    if not do_install or install_failed:
        extra = " allure-pytest" if template == "enterprise" else ""
        print(f"  pip install dolphin-desktop pytest{extra}")
    print("  pytest tests/ -v")


# doctor


def _doctor_cmd(_args: argparse.Namespace) -> None:
    """Print dolphin environment diagnostics."""
    import importlib
    import platform

    _enc = sys.stdout.encoding or "utf-8"
    try:
        "✓✗".encode(_enc)
        ok, fail, skip = "✓", "✗", "-"
    except (UnicodeEncodeError, LookupError):
        ok, fail, skip = "[ok]", "[FAIL]", "[-]"

    try:
        from dolphin_desktop import __version__
    except Exception:
        __version__ = "unknown"

    print(f"dolphin  {__version__}")
    print(f"Python   {sys.version.split()[0]}  ({sys.executable})")
    print(f"Platform {platform.platform()}")
    print()

    print("Required dependencies:")
    for label, module in [
        ("pywinauto", "pywinauto"),
        ("comtypes", "comtypes"),
        ("Pillow (PIL)", "PIL"),
        ("pywin32 (win32api)", "win32api"),
        ("PyYAML", "yaml"),
    ]:
        try:
            mod = importlib.import_module(module)
            ver = getattr(mod, "__version__", "installed")
            print(f"  {ok}  {label}: {ver}")
        except ImportError:
            print(f"  {fail}  {label}: NOT INSTALLED")

    print()
    print("Optional dependencies:")
    for label, module in [
        ("opencv-python  [vision]", "cv2"),
        ("pytesseract    [vision]", "pytesseract"),
        ("mss            [fast/video]", "mss"),
        ("sentry-sdk     [telemetry]", "sentry_sdk"),
    ]:
        try:
            mod = importlib.import_module(module)
            ver = getattr(mod, "__version__", "installed")
            print(f"  {ok}  {label}: {ver}")
        except ImportError:
            print(f"  {skip}  {label}: not installed")

    print()
    print("UIA access:")
    try:
        import pywinauto

        wins = pywinauto.Desktop(backend="uia").windows()
        print(f"  {ok}  accessible ({len(wins)} top-level window(s) visible)")
    except Exception as exc:
        print(f"  {fail}  FAILED — {exc}")

    print()
    print("Environment variables:")
    for var, default in [
        ("DOLPHIN_TIMEOUT", "10.0"),
        ("DOLPHIN_LOG_LEVEL", "INFO"),
        ("DOLPHIN_TRACE", "on-failure"),
        ("DOLPHIN_VIDEO", "keepfailedonly"),
        ("DOLPHIN_VIDEO_FPS", "10"),
        ("DOLPHIN_HEADLESS", "0"),
        ("DOLPHIN_RETRY", "0"),
        ("DOLPHIN_TELEMETRY", "off"),
    ]:
        val = os.environ.get(var)
        if val is not None:
            print(f"  {var}={val}")
        else:
            print(f"  {var} (not set, default: {default})")


# selfheal-stats


def _selfheal_stats_cmd(args: argparse.Namespace) -> None:
    from dolphin_desktop._selfheal import selfheal_stats

    file = Path(args.file) if args.file else None
    records = selfheal_stats(n=args.last, file=file)

    if not records:
        print("No self-healing events recorded.")
        return

    print(f"Last {len(records)} self-healing event(s):\n")
    for r in records:
        ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        test = r.get("test") or "(unknown)"
        primary = r.get("primary", {})
        fallback = r.get("fallback", {})
        print(f"  [{ts}]  {test}")
        print(f"    primary:  {primary}")
        print(f"    fallback: {fallback}")
        print()


# trace view


def _trace_view_cmd(args: argparse.Namespace) -> None:
    import sqlite3

    from dolphin_desktop._trace import generate_html, list_runs

    trace_dir = Path(args.dir)

    if args.last:
        runs = list_runs(trace_dir)
        if not runs:
            print(f"No traces found in {trace_dir}")
            sys.exit(1)
        run_dir = Path(runs[0]["run_dir"])
    else:
        run_id: str = args.run_id
        candidate = Path(run_id)
        if candidate.is_dir():
            run_dir = candidate
        else:
            run_dir = trace_dir / run_id
            if not run_dir.is_dir():
                print(f"Trace not found: {run_id!r}\nSearched: {run_dir}")
                sys.exit(1)

    try:
        html_path = generate_html(run_dir)
    except (FileNotFoundError, sqlite3.DatabaseError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    url = html_path.resolve().as_uri()
    webbrowser.open(url)
    print(f"Opened: {html_path}")


# trace list


def _trace_list_cmd(args: argparse.Namespace) -> None:
    from dolphin_desktop._trace import list_runs

    runs = list_runs(Path(args.dir))
    if not runs:
        print(f"No traces found in {args.dir}")
        return

    n = min(args.last, len(runs))
    print(f"{'STATUS':<10} {'DURATION':>9}  {'DATE':>19}  TEST")
    print("-" * 70)
    for run in runs[:n]:
        status = (run.get("status") or "?").upper()[:8]
        started = datetime.datetime.fromtimestamp(run["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
        if run.get("finished_at"):
            dur = f"{run['finished_at'] - run['started_at']:.1f}s"
        else:
            dur = "—"
        nodeid = run.get("test_nodeid", "")
        print(f"{status:<10} {dur:>9}  {started}  {nodeid}")


# record


def _record_cmd(args: argparse.Namespace) -> None:
    """Record mouse/keyboard interactions and emit a dolphin Python test."""
    from dolphin_desktop._recorder import Recorder

    output = Path(args.output)
    app: str | None = args.app or None
    backend: str = args.backend
    func_name: str = args.func_name

    rec = Recorder(app=app, backend=backend)
    rec.start()

    if app:
        app_hint = f'  app filter: "{app}"'
        scope = f'keyboard and mouse input in windows matching "{app}"'
    else:
        app_hint = "  app filter: (all windows)"
        scope = "EVERY keystroke and click on this desktop, in every application"
    print("dolphin recorder started")
    print(app_hint)
    print(f"  WARNING: this records {scope}.")
    print("  Typed text is written to the generated script in plain text.")
    print("  Password fields are redacted on a best-effort basis only —")
    print("  review the generated file before committing it.")
    print("  Press Ctrl+F12 to stop recording")
    print()

    try:
        rec.wait()
    except KeyboardInterrupt:
        rec.stop()

    actions = rec.actions()
    if not actions:
        print("No actions recorded.")
        return

    code = rec.generate_code(output, func_name=func_name)
    print(f"Recorded {len(actions)} action(s).")
    print(f"Written to: {output}")
    print()
    print(code)


# spy


def _spy_sap_cmd(args: argparse.Namespace) -> None:
    """Inspect a SAP GUI session tree or pick a SAP control interactively."""
    from dolphin_desktop._spy import (
        _sap_selector_to_code,
        format_sap_tree,
        sap_inspect,
        sap_pick,
    )

    if args.pick:
        try:
            result = sap_pick()
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        sel = result["selector"]
        if sel:
            print("\nPicked SAP control:")
            for k, v in sel.items():
                print(f"  {k}: {v!r}")
            print(f"\nLocator call:  session.{_sap_selector_to_code(sel)}")
        return

    kw: dict = {"connection": args.connection, "session": args.session}
    if args.depth is not None:
        kw["depth"] = args.depth

    try:
        tree = sap_inspect(**kw)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        import json

        print(json.dumps(tree, indent=2, ensure_ascii=False))
    else:
        print(format_sap_tree(tree["root"], color=None))


def _spy_jab_cmd(args: argparse.Namespace) -> None:
    """Dump the Java Access Bridge accessibility tree for a Swing window.

    Requires `dolphin spy --jab --pid <java-pid>`. Walks the JAB tree and
    prints each accessible context as role, name, optional description and
    bounds. Role and name are what JABLocator matches on.
    """
    if not args.pid:
        print("Error: --jab requires --pid <java-process-id>")
        sys.exit(2)
    from dolphin_desktop._java import JavaAccessBridge, _JABSession

    if not JavaAccessBridge.is_enabled():
        print("Java Access Bridge is not enabled. Run: jabswitch /enable")
        sys.exit(2)

    session = _JABSession.get_or_create()
    # Find the first Java window belonging to the pid.
    import ctypes

    user32 = ctypes.windll.user32
    matched: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
    def enum_proc(hwnd: int, _: int) -> bool:
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == args.pid and session.is_java_window(hwnd):
            matched.append(hwnd)
        return True

    user32.EnumWindows(enum_proc, 0)
    if not matched:
        print(f"No Java window found for pid={args.pid}")
        sys.exit(1)

    for hwnd in matched:
        ctx = session.get_root_context(hwnd)
        if ctx is None:
            continue
        vm_id, root = ctx
        print(f"--- hwnd={hwnd} vm_id={vm_id} ---")
        _dump_jab(session, vm_id, root, depth=0, max_depth=6)


def _dump_jab(session: Any, vm_id: int, ac: int, depth: int, max_depth: int) -> None:
    if depth > max_depth:
        return
    info = session.get_info(vm_id, ac)
    if info is None:
        return
    indent = "  " * depth
    print(
        f"{indent}[{info.role_en_US}] "
        f"name={info.name!r}"
        + (f" desc={info.description!r}" if info.description else "")
        + f"  @({info.x},{info.y})  {info.width}x{info.height}"
    )
    for i in range(min(info.childrenCount, 200)):
        child = session.get_child(vm_id, ac, i)
        if child:
            _dump_jab(session, vm_id, child, depth + 1, max_depth)


def _spy_cdp_cmd(args: argparse.Namespace) -> None:
    """List every page at a CDP endpoint with its title and visible text.

    Prints the URL, ``document.title`` and the first 500 characters of
    ``body.innerText`` per page — enough to tell pages apart when picking
    one to attach to. It is not a DOM dump.
    """
    from dolphin_desktop import CDPSession, cdp_install_hint, is_cdp_available

    if not is_cdp_available():
        print(cdp_install_hint())
        sys.exit(2)
    endpoint = args.cdp
    print(f"connecting to {endpoint}...")
    cdp = CDPSession.connect(endpoint, timeout=10)
    try:
        pages = cdp.pages()
        print(f"{len(pages)} page(s) — dumping <title> + first 500 chars of body\n")
        for i, page in enumerate(pages):
            print(f"--- [{i}] url={page.url[:100]!r} ---")
            cdp.switch_to_page(i)
            try:
                title = cdp.evaluate("() => document.title")
                snippet = cdp.evaluate(
                    "() => (document.body ? document.body.innerText : '').slice(0, 500)"
                )
                print(f"  title: {title!r}")
                for line in (snippet or "").splitlines()[:12]:
                    print(f"  {line}")
            except Exception as exc:
                print(f"  ERROR: {exc}")
    finally:
        cdp.close()


def _spy_mainframe_cmd(args: argparse.Namespace) -> None:
    """Dump the current screen buffer + field list of a mainframe session."""
    from dolphin_desktop import Desktop, MainframeError

    hp = str(args.mainframe)
    if ":" in hp:
        host, port_s = hp.rsplit(":", 1)
        port = int(port_s)
    else:
        host = hp
        port = 23
    session_type = args.session_type
    backend = "tn5250" if session_type == "5250" else "s3270"
    try:
        with Desktop().mainframe(
            host=host,
            port=port,
            session_type=session_type,
            backend=backend,
            timeout=15,
        ) as term:
            term.wait_ready(timeout=10)
            s = term.screen()
            print(f"--- {host}:{port} ({session_type})  {s.rows}x{s.cols}  cursor={s.cursor} ---")
            for r in range(1, s.rows + 1):
                line = s.line(r).rstrip()
                if line:
                    print(f"  [{r:2d}] {line}")
            fields = term.fields()
            print(f"\n--- {len(fields)} field(s) ---")
            for f in fields:
                print(f"  {f}")
    except MainframeError as exc:
        print(f"connect failed: {exc}")
        sys.exit(1)


def _spy_delphi_cmd(args: argparse.Namespace) -> None:
    """Enumerate a Delphi / VCL app's forms and TComponent tree.

    Requires `--pid <delphi-pid>`. Walks the UIA tree via pywinauto,
    printing each control's AutomationId (which maps to TComponent.Name
    on Delphi 10.4+), class name, caption, and rectangle.
    """
    if not args.pid:
        print("Error: --delphi requires --pid <delphi-process-id>")
        sys.exit(2)
    from dolphin_desktop import Desktop

    try:
        app = Desktop().connect(process=args.pid, timeout=5)
    except Exception as exc:
        print(f"attach failed: {exc}")
        sys.exit(1)

    try:
        wins = app._app.windows()
    except Exception:
        wins = []
    if not wins:
        print(f"no top-level windows for pid={args.pid}")
        sys.exit(1)

    for w in wins:
        try:
            info = w.element_info
            cls = getattr(info, "class_name", "") or ""
            aid = getattr(info, "automation_id", "") or ""
            caption = w.window_text() or ""
        except Exception:
            continue
        print(f"--- form  class={cls!r}  automation_id={aid!r}  caption={caption!r} ---")
        _dump_delphi_children(w, depth=1, max_depth=int(args.depth) if args.depth else 5)


def _dump_delphi_children(win: Any, depth: int, max_depth: int) -> None:
    if depth > max_depth:
        return
    indent = "  " * depth
    try:
        children = win.children()
    except Exception:
        return
    for c in children:
        try:
            info = c.element_info
            cls = getattr(info, "class_name", "") or ""
            aid = getattr(info, "automation_id", "") or ""
            caption = c.window_text() or ""
            rect = c.rectangle()
        except Exception:
            continue
        print(
            f"{indent}[{cls}] "
            f"name={aid!r} caption={caption[:50]!r} "
            f"@({rect.left},{rect.top}) {rect.width()}x{rect.height()}"
        )
        _dump_delphi_children(c, depth + 1, max_depth)


def _spy_cmd(args: argparse.Namespace) -> None:
    """Inspect a window's UIA tree or pick an element interactively."""
    from dolphin_desktop._spy import _chain_to_code, format_tree, inspect, pick

    if args.sap:
        _spy_sap_cmd(args)
        return
    if getattr(args, "jab", False):
        _spy_jab_cmd(args)
        return
    if getattr(args, "cdp", None):
        _spy_cdp_cmd(args)
        return
    if getattr(args, "mainframe", None):
        _spy_mainframe_cmd(args)
        return
    if getattr(args, "delphi", False):
        _spy_delphi_cmd(args)
        return

    backend: str = args.backend

    if args.image_pick:
        from dolphin_desktop._spy import image_pick

        image_pick(output_dir=args.output_dir, backend=backend)
        return

    if args.pick:
        result = pick(backend=backend)
        chain = result["chain"]
        if result["message"]:
            print(f"  {result['message']}")
        if chain:
            print("\nPicked element:")
            for k, v in chain[-1].items():
                print(f"  {k}: {v!r}")
            if len(chain) > 1:
                print("\nAncestor chain (outermost → leaf):")
                for i, sel in enumerate(chain):
                    marker = "→" if i == len(chain) - 1 else " "
                    parts = ", ".join(f"{k}={v!r}" for k, v in sel.items())
                    print(f"  {marker} {parts}")
            print(f"\nLocator call:  window.{_chain_to_code(chain)}")
        return

    # --- tree mode ---
    kw: dict = {"backend": backend}
    if args.window:
        # --window is a plain substring to the user, so metacharacters in it must not
        # reach the regex engine.
        kw["title_re"] = f".*{re.escape(args.window)}.*"
    elif args.exact:
        kw["title"] = args.exact
    elif args.cls:
        kw["class_name"] = args.cls
    elif args.pid:
        kw["pid"] = args.pid
    else:
        print("error: specify --window, --exact, --class, --pid, or --pick", file=sys.stderr)
        sys.exit(1)

    if args.depth is not None:
        kw["depth"] = args.depth

    try:
        tree = inspect(**kw)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        import json

        print(json.dumps(tree, indent=2, ensure_ascii=False))
    else:
        root = tree["root"]
        print(format_tree(root, color=None))


# info backends


def _info_backends_cmd(_args: argparse.Namespace) -> None:
    """List all registered dolphin backends."""
    from dolphin_desktop._backend import list_backends

    backends = list_backends()

    def _safe(s: str, width: int) -> str:
        s = s.encode(sys.stdout.encoding or "ascii", errors="replace").decode(
            sys.stdout.encoding or "ascii", errors="replace"
        )
        return s[:width]

    print(f"{'ID':<10} {'PLATFORM':<10} {'AVAIL':<7} {'SOURCE':<10} DESCRIPTION")
    print("-" * 72)
    for b in backends:
        avail = "yes" if b["available"] else "no"
        desc = _safe(b["description"], 38) if b["description"] else ""
        print(f"{b['id']:<10} {b['platform']:<10} {avail:<7} {b['source']:<10} {desc}")

    print()
    print(
        f"Total: {len(backends)} backend(s)  "
        f"({sum(1 for b in backends if b['available'])} available on this system)"
    )
    print()
    print("To add a custom backend, implement dolphin_desktop._backend.Backend and register it")
    print("via the 'dolphin_desktop.backends' entry-point group in your pyproject.toml.")


# main


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dolphin",
        description="Dolphin desktop-testing toolkit",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # --- init ---
    init_p = sub.add_parser("init", help="Bootstrap a new dolphin test project")
    init_p.add_argument(
        "name",
        nargs="?",
        metavar="NAME",
        help="Project name / directory to create (prompted if omitted)",
    )
    init_p.add_argument(
        "--template",
        choices=[
            "minimal",
            "standard",
            "enterprise",
            "sap",
            "qt",
            "electron",
            "mainframe",
            "oracle-forms",
            "delphi",
        ],
        default=None,
        metavar="TEMPLATE",
        help=(
            "Project template. Generic: minimal / standard (default) / "
            "enterprise. Stack-specific starters: sap / qt / electron / "
            "mainframe / oracle-forms / delphi — each ships a runnable "
            "first test for that stack."
        ),
    )
    init_p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive: accept all defaults (name=my-dolphin-tests, template=standard)",
    )
    init_p.add_argument(
        "--install",
        action="store_true",
        default=False,
        help="Run pip install after scaffolding",
    )
    init_p.add_argument(
        "--git",
        action="store_true",
        default=False,
        help="Run git init in the new project directory",
    )

    # --- doctor ---
    sub.add_parser("doctor", help="Check dolphin environment and dependencies")

    # --- selfheal-stats ---
    stats_p = sub.add_parser("selfheal-stats", help="Show self-healing fallback usage")
    stats_p.add_argument(
        "--last",
        type=int,
        default=10,
        metavar="N",
        help="Number of recent events to show (default: 10)",
    )
    stats_p.add_argument(
        "--file",
        default=None,
        metavar="PATH",
        help="Path to telemetry log (default: .dolphin-selfheal.jsonl)",
    )

    # --- trace ---
    trace_p = sub.add_parser("trace", help="Trace management commands")
    trace_sub = trace_p.add_subparsers(dest="trace_command", metavar="SUBCOMMAND")

    trace_view = trace_sub.add_parser("view", help="Generate and open a trace HTML viewer")
    trace_view.add_argument(
        "run_id",
        nargs="?",
        metavar="RUN_ID",
        help="Run directory name or path (omit with --last)",
    )
    trace_view.add_argument(
        "--last",
        action="store_true",
        help="Open the most recent trace",
    )
    trace_view.add_argument(
        "--dir",
        default=_DEFAULT_TRACE_DIR,
        metavar="PATH",
        help=f"Traces directory (default: {_DEFAULT_TRACE_DIR})",
    )

    trace_list = trace_sub.add_parser("list", help="List recent traces")
    trace_list.add_argument(
        "--last",
        type=int,
        default=20,
        metavar="N",
        help="Number of recent runs to show (default: 20)",
    )
    trace_list.add_argument(
        "--dir",
        default=_DEFAULT_TRACE_DIR,
        metavar="PATH",
        help=f"Traces directory (default: {_DEFAULT_TRACE_DIR})",
    )

    # --- record ---
    record_p = sub.add_parser(
        "record",
        help="Record interactions and generate a Python test",
        description=(
            "Record interactions and generate a Python test. "
            "Recording installs a low-level keyboard hook: without --app it "
            "captures every keystroke on the desktop, including text typed "
            "into other applications, and writes it to the generated script "
            "in plain text. Password fields are redacted best-effort only. "
            "Review generated scripts before committing them."
        ),
    )
    record_p.add_argument(
        "--output",
        "-o",
        required=True,
        metavar="FILE",
        help="Output .py file path",
    )
    record_p.add_argument(
        "--app",
        "-a",
        default=None,
        metavar="TITLE",
        help="Filter: record only events in windows whose title contains TITLE",
    )
    record_p.add_argument(
        "--backend",
        choices=["uia", "win32"],
        default="uia",
        help="pywinauto backend used in generated code (default: uia)",
    )
    record_p.add_argument(
        "--func-name",
        default="test_recorded",
        metavar="NAME",
        help="Name of the generated test function (default: test_recorded)",
    )

    # --- info ---
    info_p = sub.add_parser("info", help="Show dolphin system information")
    info_sub = info_p.add_subparsers(dest="info_command", metavar="SUBCOMMAND")
    info_sub.add_parser("backends", help="List all registered automation backends")

    # --- spy ---
    spy_p = sub.add_parser("spy", help="Inspect UIA tree or pick an element interactively")
    spy_p.add_argument(
        "--window",
        "-w",
        metavar="TITLE",
        default=None,
        help="Window to inspect (partial title match via title_re)",
    )
    spy_p.add_argument(
        "--exact",
        metavar="TITLE",
        default=None,
        help="Window to inspect (exact title match)",
    )
    spy_p.add_argument(
        "--class",
        dest="cls",
        metavar="CLASS",
        default=None,
        help="Window to inspect (Win32 class name)",
    )
    spy_p.add_argument(
        "--pid",
        type=int,
        default=None,
        metavar="PID",
        help="Window to inspect (process ID)",
    )
    spy_p.add_argument(
        "--depth",
        type=int,
        default=None,
        metavar="N",
        help="Maximum tree depth to traverse (default: unlimited)",
    )
    spy_p.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of a coloured tree",
    )
    spy_p.add_argument(
        "--pick",
        action="store_true",
        help="Interactive pick mode: Ctrl+Click an element to get its selector",
    )
    spy_p.add_argument(
        "--image-pick",
        action="store_true",
        dest="image_pick",
        help="Image capture mode: Ctrl+Click an element -- save template PNG",
    )
    spy_p.add_argument(
        "--output-dir",
        default=".",
        metavar="DIR",
        dest="output_dir",
        help="Directory for captured template PNGs (default: current directory)",
    )
    spy_p.add_argument(
        "--backend",
        choices=["uia", "win32"],
        default="uia",
        help="pywinauto backend (default: uia)",
    )
    spy_p.add_argument(
        "--sap",
        action="store_true",
        help="Inspect SAP GUI via SAP GUI Scripting (emits find_by_id locators)",
    )
    spy_p.add_argument(
        "--jab",
        action="store_true",
        help="Inspect a Java Swing / Oracle Forms window via the Java Access Bridge",
    )
    spy_p.add_argument(
        "--cdp",
        metavar="URL",
        help="Inspect an Electron / CEF app's DOM through the given CDP endpoint (http://host:port)",
    )
    spy_p.add_argument(
        "--mainframe",
        metavar="HOST:PORT",
        help="Inspect a mainframe screen (host:port). Use with --session-type 3270|5250",
    )
    spy_p.add_argument(
        "--session-type",
        default="3270",
        choices=["3270", "5250"],
        help="Mainframe session type for --mainframe (default: 3270)",
    )
    spy_p.add_argument(
        "--delphi",
        action="store_true",
        help="Enumerate a Delphi / VCL (Lazarus) app's forms and TComponent tree via UIA",
    )
    spy_p.add_argument(
        "--connection",
        type=int,
        default=0,
        metavar="N",
        help="SAP connection index for --sap (default: 0)",
    )
    spy_p.add_argument(
        "--session",
        type=int,
        default=0,
        metavar="N",
        help="SAP session index for --sap (default: 0)",
    )

    args = parser.parse_args()

    if args.command == "init":
        _init_cmd(args)
    elif args.command == "doctor":
        _doctor_cmd(args)
    elif args.command == "selfheal-stats":
        _selfheal_stats_cmd(args)
    elif args.command == "trace":
        if args.trace_command == "view":
            if not args.last and not args.run_id:
                trace_view.print_help()
                sys.exit(1)
            _trace_view_cmd(args)
        elif args.trace_command == "list":
            _trace_list_cmd(args)
        else:
            trace_p.print_help()
            sys.exit(1)
    elif args.command == "info":
        if args.info_command == "backends":
            _info_backends_cmd(args)
        else:
            info_p.print_help()
            sys.exit(1)
    elif args.command == "record":
        _record_cmd(args)
    elif args.command == "spy":
        _spy_cmd(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
