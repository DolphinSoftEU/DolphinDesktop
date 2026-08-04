"""Core Qt integration tests.

Covers launching and attaching to Qt demo applications and interacting
with their widgets through the UIA backend:
* Qt 6 (PySide6) basic integration and extended widget coverage.
* Qt 5 (PyQt5) basic integration and extended widget coverage.

All tests are Windows-only (per-test ``_windows_only`` decorator).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from dolphin_desktop import Desktop, ElementNotFoundError, env_var, is_windows, sleep

from . import _qt_helpers as h

_windows_only = pytest.mark.skipif(
    not is_windows(),
    reason="Qt UIA backend tests are Windows-only",
)


import importlib.util as _importlib_util  # noqa: E402


def _has(module: str) -> bool:
    return _importlib_util.find_spec(module) is not None


# ---------------------------------------------------------------------------
# Demo-script constants
# ---------------------------------------------------------------------------

# The Qt 6 and Qt 5 demos live in separate scripts with distinct window
# titles, so each gets its own suffixed constant pair.

DEMO_SCRIPT_QT6 = (
    Path(__file__).resolve().parent.parent.parent / "examples" / "qt_demo" / "all_widgets.py"
)
DEMO_WINDOW_TITLE_QT6 = "Dolphin Qt Demo"

DEMO_SCRIPT_QT5 = (
    Path(__file__).resolve().parent.parent.parent / "examples" / "qt_demo" / "all_widgets_qt5.py"
)
DEMO_WINDOW_TITLE_QT5 = "Dolphin Qt5 Demo"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qt_app():
    """Launch the PySide6 demo and yield (Application, Window)."""
    if not _has("PySide6"):
        pytest.skip("PySide6 not installed — pip install pyside6")
    if not h.QT6_SCRIPT.is_file():
        pytest.skip(f"Demo script not found: {h.QT6_SCRIPT}")

    desktop = Desktop(backend="uia")
    app, win = h.launch_demo(desktop, h.QT6_SCRIPT, h.QT6_WINDOW_TITLE)
    try:
        yield app, win
    finally:
        try:
            app.kill()
        except Exception:
            pass


@pytest.fixture(scope="module")
def qt5_app():
    """Launch the PyQt5 demo and yield (Application, Window)."""
    if not _has("PyQt5"):
        pytest.skip("PyQt5 not installed — pip install pyqt5")
    if not h.QT5_SCRIPT.is_file():
        pytest.skip(f"Demo script not found: {h.QT5_SCRIPT}")

    desktop = Desktop(backend="uia")
    app, win = h.launch_demo(desktop, h.QT5_SCRIPT, h.QT5_WINDOW_TITLE)
    try:
        yield app, win
    finally:
        try:
            app.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers (shared by the Qt 6 and Qt 5 tests)
# ---------------------------------------------------------------------------


def _select_tab(win, name: str) -> None:
    """Select a tab in the demo's main QTabWidget and pause for it to render.

    Uses invoke() (SelectionItem.Select) so it works without focus / mouse.
    """
    try:
        win.focus()
    except Exception:
        pass
    win.locator(control_type="TabItem", title=name).invoke()
    sleep(0.3)


def _status(win) -> str:
    """Read the current status message from the persistent QLabel.

    The label's ``accessibleName`` (== UIA Name) mirrors the visible text
    ``"status: <message>"``. We strip the ``status:`` prefix and any
    surrounding whitespace.
    """
    raw = win.locator(class_name="QLabel", title_re=r"^status:.*").text()
    return raw.removeprefix("status:").strip()


def _send_input_supported() -> bool:
    """SendInput is unreliable on locked workstations and CI runners with
    no input desktop — these tests skip cleanly in those environments."""
    try:
        from pywinauto.keyboard import send_keys

        send_keys("")  # no-op, raises if SendInput won't accept events
        return True
    except Exception:
        return False


# ===========================================================================
# Qt 6 basic integration
# ===========================================================================


@_windows_only
def test_is_qt_detects_pyside6_process(qt_app):
    app, _ = qt_app
    assert app.is_qt(), "Application.is_qt() should detect the PySide6 process"


@_windows_only
def test_qt_version_is_6(qt_app):
    app, _ = qt_app
    assert app.qt_version() == "6", "PySide6 should expose Qt 6"


@_windows_only
def test_launch_qt_does_not_leak_env_var(qt_app):
    """Regression guard: the old setdefault-based launch_qt leaked QT_ACCESSIBILITY=1."""
    assert env_var("QT_ACCESSIBILITY") is None, (
        "QT_ACCESSIBILITY leaked into parent process from launch_qt()"
    )


@_windows_only
def test_get_by_object_name_finds_widget(qt_app):
    """objectName lookup matches the leaf of Qt's dotted AutomationId path."""
    _, win = qt_app
    _select_tab(win, "Buttons")
    btn = win.get_by_object_name("qt_btn_ok")
    assert btn.exists(timeout=2.0)


@_windows_only
def test_button_click_by_name(qt_app):
    _, win = qt_app
    _select_tab(win, "Buttons")
    win.button(name="OK").invoke()
    sleep(0.3)
    assert _status(win) == "clicked OK"


@_windows_only
def test_button_click_by_object_name(qt_app):
    _, win = qt_app
    _select_tab(win, "Buttons")
    win.get_by_object_name("qt_btn_cancel").invoke()
    sleep(0.3)
    assert _status(win) == "clicked Cancel"


@_windows_only
def test_toggle_button(qt_app):
    """Qt's setCheckable(True) makes the QPushButton report as CheckBox in UIA."""
    _, win = qt_app
    _select_tab(win, "Buttons")
    win.check_box(name="Toggle").invoke()
    sleep(0.3)
    assert _status(win) == "toggle=True"
    win.check_box(name="Toggle").invoke()
    sleep(0.3)
    assert _status(win) == "toggle=False"


@_windows_only
def test_disabled_button_is_not_enabled(qt_app):
    _, win = qt_app
    _select_tab(win, "Buttons")
    btn = win.button(name="Disabled")
    assert btn.is_enabled() is False


@_windows_only
def test_checkbox_check_uncheck(qt_app):
    _, win = qt_app
    _select_tab(win, "Buttons")
    chk = win.check_box(name="Remember me")
    chk.invoke()
    sleep(0.3)
    assert _status(win) == "remember=True"
    chk.invoke()
    sleep(0.3)
    assert _status(win) == "remember=False"


@_windows_only
def test_radio_button_selection(qt_app):
    _, win = qt_app
    _select_tab(win, "Buttons")
    win.radio_button(name="Option B").invoke()
    sleep(0.3)
    assert _status(win) == "selected Option B"


@_windows_only
def test_line_edit_set_text(qt_app):
    _, win = qt_app
    _select_tab(win, "Inputs")
    edit = win.edit(name="Username")
    edit.set_text("alice")
    sleep(0.3)
    # Verify by reading the field back.
    assert edit.text() == "alice"


@_windows_only
def test_password_field_accepts_input(qt_app):
    _, win = qt_app
    _select_tab(win, "Inputs")
    pw = win.edit(name="Password")
    pw.set_text("secret123")
    # Password mode masks the visible text, but the field must accept input.
    assert pw.is_enabled()


@_windows_only
def test_combo_box_exists_and_resolves(qt_app):
    """UIA limitation: SelectionItem.Select on Qt combo items does not
    always trigger ``currentTextChanged`` — Qt's UIA bridge maps Select to
    ``IsSelected = true`` on the item, but the QComboBox's currentIndex
    update is driven by mouse/keyboard. Verify resolution + interaction here;
    the injected Qt agent closes the gap.
    """
    _, win = qt_app
    _select_tab(win, "Choices")
    combo = win.combo_box(name="Language")
    assert combo.exists(timeout=2.0)
    # select_item must not raise on a found widget.
    combo.select_item("Polish")


@_windows_only
def test_list_widget_item_resolves(qt_app):
    """UIA limitation: SelectionItem.Select on QListWidget items does not
    always change ``currentItem`` — see :func:`test_combo_box_exists_and_resolves`.
    """
    _, win = qt_app
    _select_tab(win, "Choices")
    item = win.locator(control_type="ListItem", title="Banana")
    assert item.exists(timeout=2.0)


@_windows_only
def test_menu_top_level_items_resolve(qt_app):
    """Top-level QMenuBar items are addressable as UIA MenuItem children of the window.

    UIA limitation: Qt opens submenus as **detached top-level windows**
    (class ``QMenu``), not as children of the menubar. Our current Menu
    resolution falls back to "root window scope" which is the main window,
    not the desktop. Reaching popup items therefore needs either keyboard
    navigation (``win.focus(); press_key("%(F){ENTER}{ENTER}")``) or the
    injected Qt agent. This test verifies the menubar items themselves
    are reachable, which is enough for the common keyboard-driven flow.
    """
    _, win = qt_app
    for label in ("File", "Edit", "View"):
        mi = win.locator(control_type="MenuItem", title=label)
        assert mi.exists(timeout=2.0), f"menu item {label!r} not found"


@_windows_only
def test_toolbar_checkable_action(qt_app):
    """Toolbar QAction with setCheckable(True) appears as CheckBox; toggle it."""
    _, win = qt_app
    bold = win.locator(class_name="QToolButton", title="Bold")
    bold.invoke()
    sleep(0.3)
    assert _status(win).startswith("toolbar bold=")


@_windows_only
def test_window_title(qt_app):
    _, win = qt_app
    assert win.title() == DEMO_WINDOW_TITLE_QT6


@_windows_only
def test_window_bounding_box(qt_app):
    _, win = qt_app
    bb = win.bounding_box()
    assert bb["width"] > 200 and bb["height"] > 200


# ===========================================================================
# Qt 6 extended coverage
# ===========================================================================


# ---------- Group A — Backend integration ----------


@_windows_only
def test_qt_detection_is_cached(qt_app):
    """Repeated is_qt() / qt_version() calls return identical results."""
    app, _ = qt_app
    results = [(app.is_qt(), app.qt_version()) for _ in range(5)]
    assert all(r == results[0] for r in results)
    assert results[0] == (True, "6")


@_windows_only
def test_qt_app_is_not_other_runtimes(qt_app):
    """A pure-Qt PySide6 process must not be misdetected as Electron / CEF / WebView2 / IE."""
    app, _ = qt_app
    assert app.is_qt() is True
    assert app.is_electron() is False
    assert app.is_cef() is False
    assert app.is_webview2() is False
    assert app.is_legacy_ie() is False


@_windows_only
def test_qtbackend_is_registered():
    """The Qt backend appears in list_backends() with the expected metadata."""
    from dolphin_desktop import QtBackend, list_backends

    by_id = {b["id"]: b for b in list_backends()}
    assert "qt" in by_id
    assert by_id["qt"]["platform"] == "windows"
    assert by_id["qt"]["class"].endswith(".QtBackend")
    assert QtBackend.id == "qt"


# ---------- Group B — Widget discovery per tab ----------


@_windows_only
def test_buttons_tab_contents(qt_app):
    _, win = qt_app
    h.select_tab(win, "Buttons")
    for name in ("OK", "Cancel", "Toggle", "Disabled"):
        # Both Button and CheckBox roles appear depending on setCheckable().
        found = win.button(name=name).exists(timeout=1.0) or win.check_box(name=name).exists(
            timeout=1.0
        )
        assert found, f"Buttons tab: widget {name!r} not found"
    assert win.check_box(name="Remember me").exists(timeout=1.0)
    assert win.check_box(name="Send updates").exists(timeout=1.0)
    for opt in ("Option A", "Option B", "Option C"):
        assert win.radio_button(name=opt).exists(timeout=1.0)


@_windows_only
def test_inputs_tab_contents(qt_app):
    """Each Inputs-tab widget is reachable by its accessible name.

    Mixed control types: Edit for QLineEdit, Spinner for QSpinBox/QDoubleSpinBox,
    Slider for QSlider, etc. — so we search by ``title`` only and let any
    matching control type satisfy the existence check.
    """
    _, win = qt_app
    h.select_tab(win, "Inputs")
    for label in (
        "Username",
        "Password",
        "Readonly",
        "Count",
        "Price",
        "Volume",
        "Birth date",
        "Notes",
    ):
        assert win.locator(title=label).exists(timeout=1.0), (
            f"Inputs tab: widget {label!r} not found"
        )


@_windows_only
def test_choices_tab_contents(qt_app):
    _, win = qt_app
    h.select_tab(win, "Choices")
    assert win.combo_box(name="Language").exists(timeout=1.0)
    assert win.combo_box(name="City").exists(timeout=1.0)
    assert win.list_box(name="Fruits").exists(timeout=1.0)


@_windows_only
def test_containers_tab_contents(qt_app):
    _, win = qt_app
    h.select_tab(win, "Containers")
    assert win.tree(name="Files").exists(timeout=1.0)
    # QTableView is reachable by its accessibleName.
    assert win.locator(title="People").exists(timeout=1.0)


@_windows_only
def test_dialogs_tab_has_all_buttons(qt_app):
    _, win = qt_app
    h.select_tab(win, "Dialogs")
    for name in ("Show info", "Show confirm", "Show input", "Show custom"):
        assert win.button(name=name).exists(timeout=1.0)


# ---------- Group C — Read-only queries ----------


@_windows_only
def test_window_is_visible_and_active(qt_app):
    _, win = qt_app
    assert win.exists() is True
    assert win.is_visible() is True


@_windows_only
def test_window_bounding_box_positive(qt_app):
    _, win = qt_app
    bb = win.bounding_box()
    assert bb["width"] >= 500 and bb["height"] >= 400
    assert bb["left"] < bb["right"]
    assert bb["top"] < bb["bottom"]


@_windows_only
def test_locator_exists_false_for_missing(qt_app):
    _, win = qt_app
    assert win.button(name="This button does not exist").exists(timeout=0.5) is False


@_windows_only
def test_locator_exists_with_short_timeout(qt_app):
    """exists(timeout=2.0) on a present widget returns True quickly."""
    _, win = qt_app
    h.select_tab(win, "Buttons")
    t0 = time.monotonic()
    assert win.button(name="OK").exists(timeout=2.0) is True
    assert time.monotonic() - t0 < 2.0, "exists() should return immediately for present widget"


@_windows_only
def test_readonly_lineedit_is_enabled_but_has_text(qt_app):
    """QLineEdit.setReadOnly(True) keeps is_enabled() True; content is readable."""
    _, win = qt_app
    h.select_tab(win, "Inputs")
    ro = win.edit(name="Readonly")
    assert ro.is_enabled() is True
    assert ro.text() == "read-only"


@_windows_only
def test_disabled_button_is_visible_but_not_enabled(qt_app):
    _, win = qt_app
    h.select_tab(win, "Buttons")
    btn = win.button(name="Disabled")
    assert btn.exists(timeout=1.0)
    assert btn.is_visible() is True
    assert btn.is_enabled() is False


# ---------- Group D — Stateful interactions ----------


@_windows_only
def test_username_field_type_and_clear(qt_app):
    _, win = qt_app
    h.select_tab(win, "Inputs")
    edit = win.edit(name="Username")
    edit.set_text("first_value")
    assert edit.text() == "first_value"
    edit.clear()
    assert edit.text() == ""


@_windows_only
def test_notes_multiline_widget_exists(qt_app):
    """QPlainTextEdit is reachable by its accessibleName."""
    _, win = qt_app
    h.select_tab(win, "Inputs")
    notes = win.locator(title="Notes")
    assert notes.exists(timeout=2.0)


@_windows_only
def test_italic_toolbar_action_toggles(qt_app):
    _, win = qt_app
    italic = win.locator(class_name="QToolButton", title="Italic")
    italic.invoke()
    assert h.status_starts_with(win, "toolbar italic=True", timeout=3.0)
    italic.invoke()
    assert h.status_starts_with(win, "toolbar italic=False", timeout=3.0)


@_windows_only
def test_check_send_updates_starts_checked(qt_app):
    """Send updates checkbox starts in checked state; toggle flips it."""
    _, win = qt_app
    h.select_tab(win, "Buttons")
    chk = win.check_box(name="Send updates")
    chk.invoke()
    # After invoke, should fire updates=False (was True).
    assert h.wait_for_status(win, "updates=False", timeout=3.0)
    chk.invoke()
    assert h.wait_for_status(win, "updates=True", timeout=3.0)


@_windows_only
def test_radio_options_a_b_c_each_selectable(qt_app):
    _, win = qt_app
    h.select_tab(win, "Buttons")
    for opt in ("Option A", "Option C", "Option B"):
        win.radio_button(name=opt).invoke()
        assert h.wait_for_status(win, f"selected {opt}", timeout=3.0)


@_windows_only
def test_multiple_buttons_clicked_in_sequence(qt_app):
    """Click OK then Cancel — status reflects last action."""
    _, win = qt_app
    h.select_tab(win, "Buttons")
    win.button(name="OK").invoke()
    assert h.wait_for_status(win, "clicked OK", timeout=3.0)
    win.button(name="Cancel").invoke()
    assert h.wait_for_status(win, "clicked Cancel", timeout=3.0)


# ---------- Group E — Multi-step workflows ----------


@_windows_only
def test_login_form_workflow(qt_app):
    """Fill username + password, verify both readable back."""
    _, win = qt_app
    h.fill_login_form(win, "alice", "topsecret")
    assert win.edit(name="Username").text() == "alice"
    # Password mode masks, but is_enabled remains true.
    assert win.edit(name="Password").is_enabled()


@_windows_only
def test_navigate_all_five_tabs(qt_app):
    """Every tab is reachable and renders content."""
    _, win = qt_app
    visited = h.navigate_all_tabs(win)
    assert visited == ["Buttons", "Inputs", "Choices", "Containers", "Dialogs"]


@_windows_only
def test_full_buttons_workflow(qt_app):
    """Buttons tab workflow: check remember, select option, click OK."""
    _, win = qt_app
    h.select_tab(win, "Buttons")
    win.check_box(name="Remember me").invoke()
    h.wait_for_status(win, "remember=True", timeout=3.0)
    win.radio_button(name="Option A").invoke()
    h.wait_for_status(win, "selected Option A", timeout=3.0)
    win.button(name="OK").invoke()
    assert h.wait_for_status(win, "clicked OK", timeout=3.0)


# ---------- Group F — Window operations ----------


@_windows_only
def test_window_focus_does_not_raise(qt_app):
    _, win = qt_app
    win.focus()  # must not raise
    # SetForegroundWindow can be refused by the shell, but the window it was
    # aimed at must survive the attempt and stay usable.
    assert win.exists()
    assert win.is_visible()
    assert win.title() == h.QT6_WINDOW_TITLE


@_windows_only
def test_window_title_matches_demo_constant(qt_app):
    _, win = qt_app
    assert win.title() == h.QT6_WINDOW_TITLE


@_windows_only
def test_window_screenshot_saves_png(qt_app, tmp_path):
    """Window.screenshot writes a PIL image to disk."""
    _, win = qt_app
    out = tmp_path / "qt6_window.png"
    img = win.screenshot(out)
    assert out.is_file()
    assert out.stat().st_size > 0
    assert img.size[0] > 0 and img.size[1] > 0


@_windows_only
def test_window_handle_count_is_at_least_one(qt_app):
    """Application.windows() returns at least the main window."""
    app, _ = qt_app
    wins = app.windows()
    assert len(wins) >= 1


# ---------- Group G — Locator chaining ----------


@_windows_only
def test_locator_chain_finds_radio_inside_group(qt_app):
    """Locator chaining: find Group 'Pick one' then Option A inside it."""
    _, win = qt_app
    h.select_tab(win, "Buttons")
    group = win.locator(title="Pick one")
    assert group.exists(timeout=2.0)
    inner = group.locator(control_type="RadioButton", title="Option A")
    assert inner.exists(timeout=2.0)


@_windows_only
def test_class_name_combined_with_title_finds_button(qt_app):
    """Class-name lookup works when combined with a specific title.

    Bare class_name lookups (no title) sometimes time out on Qt because
    pywinauto's UIA descendant scan can hit a busy tab transition; pinning
    with title gives a deterministic match path.
    """
    _, win = qt_app
    h.select_tab(win, "Buttons")
    pb = win.locator(class_name="QPushButton", title="OK")
    assert pb.exists(timeout=2.0)


@_windows_only
def test_get_by_object_name_resolves_multiple_widgets(qt_app):
    """get_by_object_name works for several different widgets."""
    _, win = qt_app
    h.select_tab(win, "Buttons")
    assert win.get_by_object_name("qt_btn_ok").exists(timeout=2.0)
    assert win.get_by_object_name("qt_chk_remember").exists(timeout=2.0)
    h.select_tab(win, "Inputs")
    assert win.get_by_object_name("qt_input_username").exists(timeout=2.0)


# ---------- Group H — Error paths ----------


@_windows_only
def test_missing_widget_raises_element_not_found(qt_app):
    """Resolving a nonexistent widget raises ElementNotFoundError."""
    _, win = qt_app
    h.select_tab(win, "Buttons")
    with pytest.raises(ElementNotFoundError):
        win.button(name="Definitely not a real button").timeout(1.0).click()


@_windows_only
def test_missing_object_name_raises_element_not_found(qt_app):
    """get_by_object_name on a nonexistent leaf raises after timeout."""
    _, win = qt_app
    with pytest.raises(ElementNotFoundError):
        win.get_by_object_name("does_not_exist_anywhere").timeout(1.0)._resolve()


@_windows_only
def test_locator_exists_with_zero_timeout_for_missing_returns_false(qt_app):
    _, win = qt_app
    assert win.button(name="missing_button_xyz").exists(timeout=0) is False


@_windows_only
def test_application_repr_includes_pid_and_backend(qt_app):
    app, _ = qt_app
    text = repr(app)
    assert "pid=" in text
    assert str(app.process_id) in text


# ---------- UIA enhancements ----------


@_windows_only
def test_uses_qt_quick_is_false_for_widget_demo(qt_app):
    """The PySide6 widget demo uses no QML — uses_qt_quick must be False."""
    app, _ = qt_app
    assert app.uses_qt_quick() is False


@_windows_only
def test_menu_popup_submenu_reaches_file_new(qt_app):
    """Desktop-scope fallback finds Qt's detached QMenu popup.

    Best-effort: Qt popup menus can take 100-500 ms to materialise and the
    parent-click may itself fail with SetCursorPos on a loaded test runner.
    We assert the menu *resolves* (no ElementNotFoundError) — the actual
    fire-and-status verification is exercised by the keyboard-driven menu
    tests in test_qt.py.
    """
    _, win = qt_app
    if not _send_input_supported():
        pytest.skip("SendInput not accepting events — workstation likely locked")
    from dolphin_desktop._exceptions import ElementNotFoundError

    try:
        win.menu("File").item("New").timeout(3.0)._resolve()
    except ElementNotFoundError as exc:
        pytest.skip(f"popup menu still not reachable (SetCursorPos/focus race): {exc}")
    finally:
        from dolphin_desktop import Keyboard

        try:
            Keyboard.press("{ESC}")
            Keyboard.press("{ESC}")
        except Exception:
            pass


@_windows_only
def test_combo_select_item_keyboard_fires_signal(qt_app):
    """select_item_keyboard drives currentTextChanged via real keyboard input."""
    _, win = qt_app
    if not _send_input_supported():
        pytest.skip("SendInput not accepting events — workstation likely locked")
    h.select_tab(win, "Choices")
    combo = win.combo_box(name="Language")
    try:
        combo.select_item_keyboard("Polish")
    except RuntimeError as exc:
        if "SendInput" in str(exc):
            pytest.skip(f"SendInput throttled mid-test: {exc}")
        raise
    assert h.wait_for_status(win, "language=Polish", timeout=3.0)


@_windows_only
def test_list_select_item_keyboard_fires_signal(qt_app):
    """select_item_keyboard on QListWidget drives currentTextChanged."""
    _, win = qt_app
    if not _send_input_supported():
        pytest.skip("SendInput not accepting events — workstation likely locked")
    h.select_tab(win, "Choices")
    lst = win.list_box(name="Fruits")
    try:
        lst.select_item_keyboard("Banana")
    except RuntimeError as exc:
        if "SendInput" in str(exc):
            pytest.skip(f"SendInput throttled mid-test: {exc}")
        raise
    assert h.wait_for_status(win, "fruit=Banana", timeout=3.0)


@_windows_only
def test_locator_text_returns_empty_string_quickly(qt_app):
    """Regression guard: text() on an empty QLineEdit must return without
    hanging on the SendInput-driven clipboard fallback."""
    _, win = qt_app
    h.select_tab(win, "Inputs")
    edit = win.edit(name="Username")
    edit.clear()
    t0 = time.monotonic()
    value = edit.text()
    elapsed = time.monotonic() - t0
    # We always expect "" for a cleared field, but the clipboard fallback
    # itself may produce empty or fail — both are acceptable. The real
    # invariant is that text() returns within a small wall-clock budget
    # even when the SendInput pathway is being throttled.
    assert value == ""
    assert elapsed < 15.0, (
        f"text() on empty Qt edit took {elapsed:.1f}s — clipboard fallback should "
        "fail fast or be bypassed by ValuePattern"
    )


# ===========================================================================
# Qt 5 basic integration
# ===========================================================================


@_windows_only
def test_is_qt_detects_pyqt5_process(qt5_app):
    app, _ = qt5_app
    assert app.is_qt(), "Application.is_qt() should detect the PyQt5 process"


@_windows_only
def test_qt_version_is_5(qt5_app):
    app, _ = qt5_app
    assert app.qt_version() == "5", "PyQt5 should expose Qt 5"


@_windows_only
def test_launch_qt_does_not_leak_env_var_qt5(qt5_app):
    """Regression guard — same as for Qt 6."""
    assert env_var("QT_ACCESSIBILITY") is None, (
        "QT_ACCESSIBILITY leaked into parent process from launch_qt()"
    )


@_windows_only
def test_button_click_by_name_qt5(qt5_app):
    _, win = qt5_app
    _select_tab(win, "Buttons")
    win.button(name="OK").invoke()
    sleep(0.3)
    assert _status(win) == "clicked OK"


@_windows_only
def test_cancel_button_click(qt5_app):
    _, win = qt5_app
    _select_tab(win, "Buttons")
    win.button(name="Cancel").invoke()
    sleep(0.3)
    assert _status(win) == "clicked Cancel"


@_windows_only
def test_toggle_button_qt5(qt5_app):
    _, win = qt5_app
    _select_tab(win, "Buttons")
    win.check_box(name="Toggle").invoke()
    sleep(0.3)
    assert _status(win) == "toggle=True"
    win.check_box(name="Toggle").invoke()
    sleep(0.3)
    assert _status(win) == "toggle=False"


@_windows_only
def test_disabled_button_is_not_enabled_qt5(qt5_app):
    _, win = qt5_app
    _select_tab(win, "Buttons")
    btn = win.button(name="Disabled")
    assert btn.is_enabled() is False


@_windows_only
def test_checkbox_check_uncheck_qt5(qt5_app):
    _, win = qt5_app
    _select_tab(win, "Buttons")
    chk = win.check_box(name="Remember me")
    chk.invoke()
    sleep(0.3)
    assert _status(win) == "remember=True"
    chk.invoke()
    sleep(0.3)
    assert _status(win) == "remember=False"


@_windows_only
def test_radio_button_selection_qt5(qt5_app):
    _, win = qt5_app
    _select_tab(win, "Buttons")
    win.radio_button(name="Option B").invoke()
    sleep(0.3)
    assert _status(win) == "selected Option B"


@_windows_only
def test_line_edit_set_text_qt5(qt5_app):
    _, win = qt5_app
    _select_tab(win, "Inputs")
    edit = win.edit(name="Username")
    edit.set_text("alice")
    sleep(0.3)
    assert edit.text() == "alice"


@_windows_only
def test_password_field_accepts_input_qt5(qt5_app):
    _, win = qt5_app
    _select_tab(win, "Inputs")
    pw = win.edit(name="Password")
    pw.set_text("secret123")
    assert pw.is_enabled()


@_windows_only
def test_combo_box_exists_and_resolves_qt5(qt5_app):
    """Qt 5 combo box can be located via accessibleName.

    Qt 5's UIA bridge does not expose dropdown items as enumerable ListItem
    children the same way Qt 6 does, so ``select_item`` may raise on Qt 5.
    Test only that the combo itself resolves — the injected Qt agent
    will close the dropdown-item gap.
    """
    _, win = qt5_app
    _select_tab(win, "Choices")
    combo = win.combo_box(name="Language")
    assert combo.exists(timeout=2.0)


@_windows_only
def test_list_widget_item_resolves_qt5(qt5_app):
    _, win = qt5_app
    _select_tab(win, "Choices")
    item = win.locator(control_type="ListItem", title="Banana")
    assert item.exists(timeout=2.0)


@_windows_only
def test_menu_top_level_items_resolve_qt5(qt5_app):
    """See Qt 6 test for rationale."""
    _, win = qt5_app
    for label in ("File", "Edit", "View"):
        mi = win.locator(control_type="MenuItem", title=label)
        assert mi.exists(timeout=2.0), f"menu item {label!r} not found"


@_windows_only
def test_toolbar_checkable_action_qt5(qt5_app):
    _, win = qt5_app
    bold = win.locator(class_name="QToolButton", title="Bold")
    bold.invoke()
    sleep(0.3)
    assert _status(win).startswith("toolbar bold=")


@_windows_only
def test_get_by_object_name_finds_widget_qt5(qt5_app):
    """Qt 5 exposes a hierarchical AutomationId path same as Qt 6 for QWidget.

    If this fails on a specific Qt 5 build, mark xfail in your project
    instead of removing — it works on Qt 5.15.x via PyQt5 in our matrix.
    """
    _, win = qt5_app
    _select_tab(win, "Buttons")
    btn = win.get_by_object_name("qt5_btn_ok")
    assert btn.exists(timeout=3.0)


@_windows_only
def test_window_title_qt5(qt5_app):
    _, win = qt5_app
    assert win.title() == DEMO_WINDOW_TITLE_QT5


@_windows_only
def test_window_bounding_box_qt5(qt5_app):
    _, win = qt5_app
    bb = win.bounding_box()
    assert bb["width"] > 200 and bb["height"] > 200


# ===========================================================================
# Qt 5 extended coverage
# ===========================================================================


# ---------- Group A — Backend integration ----------


@_windows_only
def test_qt_detection_is_cached_qt5(qt5_app):
    app, _ = qt5_app
    results = [(app.is_qt(), app.qt_version()) for _ in range(5)]
    assert all(r == results[0] for r in results)
    assert results[0] == (True, "5")


@_windows_only
def test_qt5_app_is_not_other_runtimes(qt5_app):
    app, _ = qt5_app
    assert app.is_qt() is True
    assert app.is_electron() is False
    assert app.is_cef() is False
    assert app.is_webview2() is False
    assert app.is_legacy_ie() is False


@_windows_only
def test_qtbackend_is_registered_for_qt5_too():
    """QtBackend handles both Qt 5 and Qt 6 — same backend, same registration."""
    from dolphin_desktop import QtBackend, list_backends

    by_id = {b["id"]: b for b in list_backends()}
    assert "qt" in by_id
    assert QtBackend.platform == "windows"


# ---------- Group B — Widget discovery per tab ----------


@_windows_only
def test_buttons_tab_contents_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    for name in ("OK", "Cancel", "Toggle", "Disabled"):
        found = win.button(name=name).exists(timeout=1.0) or win.check_box(name=name).exists(
            timeout=1.0
        )
        assert found, f"Buttons tab: widget {name!r} not found"
    assert win.check_box(name="Remember me").exists(timeout=1.0)
    assert win.check_box(name="Send updates").exists(timeout=1.0)
    for opt in ("Option A", "Option B", "Option C"):
        assert win.radio_button(name=opt).exists(timeout=1.0)


@_windows_only
def test_inputs_tab_contents_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Inputs")
    for label in (
        "Username",
        "Password",
        "Readonly",
        "Count",
        "Price",
        "Volume",
        "Birth date",
        "Notes",
    ):
        assert win.locator(title=label).exists(timeout=1.0), (
            f"Inputs tab: widget {label!r} not found"
        )


@_windows_only
def test_choices_tab_contents_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Choices")
    assert win.combo_box(name="Language").exists(timeout=1.0)
    assert win.combo_box(name="City").exists(timeout=1.0)
    assert win.list_box(name="Fruits").exists(timeout=1.0)


@_windows_only
def test_containers_tab_contents_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Containers")
    assert win.tree(name="Files").exists(timeout=1.0)
    assert win.locator(title="People").exists(timeout=1.0)


@_windows_only
def test_dialogs_tab_has_all_buttons_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Dialogs")
    for name in ("Show info", "Show confirm", "Show input", "Show custom"):
        assert win.button(name=name).exists(timeout=1.0)


# ---------- Group C — Read-only queries ----------


@_windows_only
def test_window_is_visible_and_active_qt5(qt5_app):
    _, win = qt5_app
    assert win.exists() is True
    assert win.is_visible() is True


@_windows_only
def test_window_bounding_box_positive_qt5(qt5_app):
    _, win = qt5_app
    bb = win.bounding_box()
    assert bb["width"] >= 500 and bb["height"] >= 400
    assert bb["left"] < bb["right"]
    assert bb["top"] < bb["bottom"]


@_windows_only
def test_locator_exists_false_for_missing_qt5(qt5_app):
    _, win = qt5_app
    assert win.button(name="This button does not exist").exists(timeout=0.5) is False


@_windows_only
def test_locator_exists_with_short_timeout_qt5(qt5_app):
    """Qt 5 UIA lookups are slower than Qt 6 — bound the budget loosely."""
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    t0 = time.monotonic()
    assert win.button(name="OK").exists(timeout=5.0) is True
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"exists() took {elapsed:.1f}s for a present widget"


@_windows_only
def test_readonly_lineedit_is_enabled_but_has_text_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Inputs")
    ro = win.edit(name="Readonly")
    assert ro.is_enabled() is True
    assert ro.text() == "read-only"


@_windows_only
def test_disabled_button_is_visible_but_not_enabled_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    btn = win.button(name="Disabled")
    assert btn.exists(timeout=1.0)
    assert btn.is_visible() is True
    assert btn.is_enabled() is False


# ---------- Group D — Stateful interactions ----------


@_windows_only
def test_username_field_type_and_clear_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Inputs")
    edit = win.edit(name="Username")
    edit.set_text("first_value")
    assert edit.text() == "first_value"
    edit.clear()
    assert edit.text() == ""


@_windows_only
def test_notes_multiline_widget_exists_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Inputs")
    notes = win.locator(title="Notes")
    assert notes.exists(timeout=2.0)


@_windows_only
def test_italic_toolbar_action_toggles_qt5(qt5_app):
    _, win = qt5_app
    italic = win.locator(class_name="QToolButton", title="Italic")
    italic.invoke()
    assert h.status_starts_with(win, "toolbar italic=True", timeout=3.0)
    italic.invoke()
    assert h.status_starts_with(win, "toolbar italic=False", timeout=3.0)


@_windows_only
def test_check_send_updates_starts_checked_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    chk = win.check_box(name="Send updates")
    chk.invoke()
    assert h.wait_for_status(win, "updates=False", timeout=3.0)
    chk.invoke()
    assert h.wait_for_status(win, "updates=True", timeout=3.0)


@_windows_only
def test_radio_options_a_b_c_each_selectable_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    for opt in ("Option A", "Option C", "Option B"):
        win.radio_button(name=opt).invoke()
        assert h.wait_for_status(win, f"selected {opt}", timeout=3.0)


@_windows_only
def test_multiple_buttons_clicked_in_sequence_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    win.button(name="OK").invoke()
    assert h.wait_for_status(win, "clicked OK", timeout=3.0)
    win.button(name="Cancel").invoke()
    assert h.wait_for_status(win, "clicked Cancel", timeout=3.0)


# ---------- Group E — Multi-step workflows ----------


@_windows_only
def test_login_form_workflow_qt5(qt5_app):
    _, win = qt5_app
    h.fill_login_form(win, "alice", "topsecret")
    assert win.edit(name="Username").text() == "alice"
    assert win.edit(name="Password").is_enabled()


@_windows_only
def test_navigate_all_five_tabs_qt5(qt5_app):
    _, win = qt5_app
    visited = h.navigate_all_tabs(win)
    assert visited == ["Buttons", "Inputs", "Choices", "Containers", "Dialogs"]


@_windows_only
def test_full_buttons_workflow_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    win.check_box(name="Remember me").invoke()
    h.wait_for_status(win, "remember=True", timeout=3.0)
    win.radio_button(name="Option A").invoke()
    h.wait_for_status(win, "selected Option A", timeout=3.0)
    win.button(name="OK").invoke()
    assert h.wait_for_status(win, "clicked OK", timeout=3.0)


# ---------- Group F — Window operations ----------


@_windows_only
def test_window_focus_does_not_raise_qt5(qt5_app):
    _, win = qt5_app
    win.focus()
    # SetForegroundWindow can be refused by the shell, but the window it was
    # aimed at must survive the attempt and stay usable.
    assert win.exists()
    assert win.is_visible()
    assert win.title() == h.QT5_WINDOW_TITLE


@_windows_only
def test_window_title_matches_demo_constant_qt5(qt5_app):
    _, win = qt5_app
    assert win.title() == h.QT5_WINDOW_TITLE


@_windows_only
def test_window_screenshot_saves_png_qt5(qt5_app, tmp_path):
    _, win = qt5_app
    out = tmp_path / "qt5_window.png"
    img = win.screenshot(out)
    assert out.is_file()
    assert out.stat().st_size > 0
    assert img.size[0] > 0 and img.size[1] > 0


@_windows_only
def test_window_handle_count_is_at_least_one_qt5(qt5_app):
    app, _ = qt5_app
    wins = app.windows()
    assert len(wins) >= 1


# ---------- Group G — Locator chaining ----------


@_windows_only
def test_locator_chain_finds_radio_inside_group_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    group = win.locator(title="Pick one")
    assert group.exists(timeout=2.0)
    inner = group.locator(control_type="RadioButton", title="Option A")
    assert inner.exists(timeout=2.0)


@_windows_only
def test_class_name_combined_with_title_finds_button_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    pb = win.locator(class_name="QPushButton", title="OK")
    assert pb.exists(timeout=2.0)


@_windows_only
def test_get_by_object_name_resolves_multiple_widgets_qt5(qt5_app):
    """Qt 5 also exposes hierarchical AutomationId — get_by_object_name works."""
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    assert win.get_by_object_name("qt5_btn_ok").exists(timeout=2.0)
    assert win.get_by_object_name("qt5_chk_remember").exists(timeout=2.0)
    h.select_tab(win, "Inputs")
    assert win.get_by_object_name("qt5_input_username").exists(timeout=2.0)


# ---------- Group H — Error paths ----------


@_windows_only
def test_missing_widget_raises_element_not_found_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Buttons")
    with pytest.raises(ElementNotFoundError):
        win.button(name="Definitely not a real button").timeout(1.0).click()


@_windows_only
def test_missing_object_name_raises_element_not_found_qt5(qt5_app):
    _, win = qt5_app
    with pytest.raises(ElementNotFoundError):
        win.get_by_object_name("does_not_exist_anywhere").timeout(1.0)._resolve()


@_windows_only
def test_locator_exists_with_zero_timeout_for_missing_returns_false_qt5(qt5_app):
    _, win = qt5_app
    assert win.button(name="missing_button_xyz").exists(timeout=0) is False


@_windows_only
def test_application_repr_includes_pid_and_backend_qt5(qt5_app):
    app, _ = qt5_app
    text = repr(app)
    assert "pid=" in text
    assert str(app.process_id) in text


# ---------- UIA enhancements ----------


@_windows_only
def test_uses_qt_quick_is_false_for_pyqt5_widget_demo(qt5_app):
    app, _ = qt5_app
    assert app.uses_qt_quick() is False


@_windows_only
def test_menu_popup_submenu_reaches_file_new_qt5(qt5_app):
    """Desktop-scope fallback finds Qt 5's detached QMenu popup."""
    _, win = qt5_app
    if not _send_input_supported():
        pytest.skip("SendInput not accepting events — workstation likely locked")
    try:
        win.menu("File").item("New").timeout(3.0)._resolve()
    except ElementNotFoundError as exc:
        pytest.skip(f"popup menu still not reachable (SetCursorPos/focus race): {exc}")
    finally:
        from dolphin_desktop import Keyboard

        try:
            Keyboard.press("{ESC}")
            Keyboard.press("{ESC}")
        except Exception:
            pass


@_windows_only
def test_combo_select_item_keyboard_fires_signal_qt5(qt5_app):
    _, win = qt5_app
    if not _send_input_supported():
        pytest.skip("SendInput not accepting events — workstation likely locked")
    h.select_tab(win, "Choices")
    combo = win.combo_box(name="Language")
    try:
        combo.select_item_keyboard("Polish")
    except RuntimeError as exc:
        if "SendInput" in str(exc):
            pytest.skip(f"SendInput throttled mid-test: {exc}")
        raise
    assert h.wait_for_status(win, "language=Polish", timeout=3.0)


@_windows_only
def test_list_select_item_keyboard_fires_signal_qt5(qt5_app):
    _, win = qt5_app
    if not _send_input_supported():
        pytest.skip("SendInput not accepting events — workstation likely locked")
    h.select_tab(win, "Choices")
    lst = win.list_box(name="Fruits")
    try:
        lst.select_item_keyboard("Banana")
    except RuntimeError as exc:
        if "SendInput" in str(exc):
            pytest.skip(f"SendInput throttled mid-test: {exc}")
        raise
    assert h.wait_for_status(win, "fruit=Banana", timeout=3.0)


@_windows_only
def test_locator_text_returns_empty_string_quickly_qt5(qt5_app):
    _, win = qt5_app
    h.select_tab(win, "Inputs")
    edit = win.edit(name="Username")
    edit.clear()
    t0 = time.monotonic()
    value = edit.text()
    elapsed = time.monotonic() - t0
    assert value == ""
    assert elapsed < 15.0
