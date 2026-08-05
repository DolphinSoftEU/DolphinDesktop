"""Shared helpers for Qt 5 / Qt 6 integration tests.

Both ``all_widgets.py`` (PySide6) and ``all_widgets_qt5.py`` (PyQt5) expose an
identical UI structure — the only differences are the binding-specific
``objectName`` prefix (``qt_`` vs ``qt5_``) and the window title.  All helper
functions below abstract over both.

These helpers are imported by both the UIA and Qt-agent test files and use
**only** the public ``dolphin_desktop`` API — no ``time``/``sys``/``os``
stdlib reach-throughs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dolphin_desktop import sleep

# ---------------------------------------------------------------------------
# Demo binding constants
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_DIR = REPO_ROOT / "examples" / "qt_demo"

QT6_SCRIPT = DEMO_DIR / "all_widgets.py"
QT6_WINDOW_TITLE = "Dolphin Qt Demo"
QT6_PREFIX = "qt"  # objectName prefix

QT5_SCRIPT = DEMO_DIR / "all_widgets_qt5.py"
QT5_WINDOW_TITLE = "Dolphin Qt5 Demo"
QT5_PREFIX = "qt5"

QML_SCRIPT = DEMO_DIR / "qml_widgets.py"
QML_WINDOW_TITLE = "Dolphin QML Demo"

CHARTS_SCRIPT = DEMO_DIR / "charts_demo.py"
CHARTS_WINDOW_TITLE = "Dolphin Charts Demo"

GRAPHICS_SCRIPT = DEMO_DIR / "graphics_demo.py"
GRAPHICS_WINDOW_TITLE = "Dolphin Graphics Demo"

CM_SCRIPT = DEMO_DIR / "contact_manager.py"
CM_WINDOW_TITLE = "Dolphin Contact Manager"


# ---------------------------------------------------------------------------
# Status / tab helpers
# ---------------------------------------------------------------------------


def select_tab(win: Any, name: str) -> None:
    """Select a tab in the demo's main QTabWidget (works on both Qt 5 and Qt 6)."""
    try:
        win.focus()
    except Exception:
        pass
    win.locator(control_type="TabItem", title=name).invoke()
    sleep(0.3)


def read_status(win: Any) -> str:
    """Read the persistent status label and return the message after ``status:``."""
    raw = win.locator(class_name="QLabel", title_re=r"^status:.*").text()
    return raw.removeprefix("status:").strip()


def wait_for_status(win: Any, expected: str, timeout: float = 5.0) -> bool:
    """Poll the status label until it equals *expected* or *timeout* elapses."""
    from dolphin_desktop import monotonic

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            if read_status(win) == expected:
                return True
        except Exception:
            pass
        sleep(0.1)
    return False


def status_starts_with(win: Any, prefix: str, timeout: float = 5.0) -> bool:
    """Poll until the status label starts with *prefix*."""
    from dolphin_desktop import monotonic

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            if read_status(win).startswith(prefix):
                return True
        except Exception:
            pass
        sleep(0.1)
    return False


# ---------------------------------------------------------------------------
# Fixture builders (used by per-binding test files)
# ---------------------------------------------------------------------------


def _stale_demo_pids(title: str) -> list[int]:
    """PIDs of demo windows already on the desktop before we launch ours."""
    import win32gui  # type: ignore[import-untyped]
    import win32process  # type: ignore[import-untyped]

    found: list[int] = []

    def _cb(hwnd: int, _: None) -> None:
        try:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
                found.append(win32process.GetWindowThreadProcessId(hwnd)[1])
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return []
    return found


def launch_demo(desktop: Any, script: Path, title: str):
    """Launch a Qt demo script and yield (app, win).

    Uses ``Desktop.launch_qt_python_script`` so the test never has to import
    ``sys``. ``Application.detach()`` removes the launcher PID from
    dolphin's auto-kill list once the real Python child has been resolved
    via ``app.window(...)``.
    """
    # detach() is what lets an interrupted run leave the demo running, and the
    # next run then matches that window instead of its own. dolphin refuses to
    # bind a stranger's window — correctly — but the resulting "belongs to pid
    # N, which is neither this application nor a hand-off of it" says nothing
    # about the real cause, and it repeats for every test in the file.
    # Polled rather than sampled once: these fixtures are module-scoped, so the
    # previous module's app.kill() may still be tearing down when the next one
    # starts and its window can outlive the call by a moment. Only a window
    # that is still there after the grace period is a genuine orphan.
    stale: list[int] = []
    for _ in range(15):
        stale = _stale_demo_pids(title)
        if not stale:
            break
        sleep(0.2)
    if stale:
        raise RuntimeError(
            f"a {title!r} window is already open (pid(s) {stale}) — a previous "
            f"Qt run was interrupted and left the demo behind. Kill those "
            f"processes and re-run; leaving them makes every Qt test in this "
            f"file error out on a window it does not own."
        )

    app = desktop.launch_qt_python_script(str(script), timeout=20, startup_delay=2.5)

    try:
        win = app.window(title=title, timeout=15)
        win.wait_until_ready(timeout=10)
        app.detach()
        try:
            win.focus()
        except Exception:
            pass
        sleep(0.3)
        return app, win
    except Exception:
        try:
            app.kill()
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Multi-step workflows (used by both bindings)
# ---------------------------------------------------------------------------


def fill_login_form(win: Any, username: str, password: str) -> None:
    """Type *username* and *password* into the Inputs tab fields."""
    select_tab(win, "Inputs")
    win.edit(name="Username").set_text(username)
    sleep(0.2)
    win.edit(name="Password").set_text(password)
    sleep(0.2)


def navigate_all_tabs(win: Any) -> list[str]:
    """Cycle through every demo tab and return the list of titles visited."""
    visited: list[str] = []
    for tab in ("Buttons", "Inputs", "Choices", "Containers", "Dialogs"):
        select_tab(win, tab)
        visited.append(tab)
    select_tab(win, "Buttons")
    return visited
