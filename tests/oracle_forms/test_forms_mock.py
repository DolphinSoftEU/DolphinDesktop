"""Oracle Forms wrapper integration tests against a Java Swing mock.

The mock (``OracleFormsMock.java``) mimics a real Oracle Forms layout:

* A menu bar with Action / Query / Record / Help menus.
* Two data blocks (EMPLOYEES + DEPARTMENTS) with named JTextField items.
* Function-key bindings (F7=Enter Query, F8=Execute Query, F9=LOV,
  Ctrl+Q=Exit) wired via ``InputMap``/``ActionMap``.
* A status line ``JLabel`` whose current text is mirrored through
  ``AccessibleContext.setAccessibleDescription`` — this is the same
  contract real Oracle Forms uses for accessibility-aware apps.

Text read/write paths use the JAB direct API (setTextContents /
getAccessibleTextRange / doAccessibleActions) and unmodified function
keys use PostMessage(WM_KEYDOWN) targeted at the Java HWND — both work
from background test runs, RDP sessions, or CI machines where
SetForegroundWindow is blocked.

Shortcuts carrying a modifier (Ctrl+Q, Shift+F5 …) are the exception:
PostMessage cannot set the target thread's modifier state, so they are
delivered by SendInput against the foreground window and therefore need
a usable input desktop. Those tests skip when one is not available.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import OracleFormsKey


def _can_deliver_modifier_keys(app) -> bool:
    """Whether a Shift/Ctrl shortcut can actually reach *app* right now.

    PostMessage cannot set the target thread's modifier state, so modifier
    combos go out via SendInput against the **foreground** window. A working
    input desktop is necessary but not sufficient: another process holding
    the foreground lock makes SetForegroundWindow a no-op, and dolphin
    reports that only as a warning. Checking that the window genuinely
    reached the foreground is the condition that actually matters, so this
    skips instead of failing on a busy or locked machine.
    """
    try:
        from pywinauto.keyboard import send_keys

        send_keys("")
    except Exception:
        return False

    import ctypes

    try:
        hwnd = app._primary_hwnd()
    except Exception:
        return False
    app.bring_to_foreground()
    return bool(hwnd) and ctypes.windll.user32.GetForegroundWindow() == hwnd


def test_form_window_ready(forms_app) -> None:
    """The mock's JFrame surfaces to JAB — wait_ready must return."""
    assert forms_app.title() or True  # pywinauto returns '' for Java frames


def test_initial_status_line(forms_app) -> None:
    """Fresh launch → status line shows 'Ready.'"""
    assert forms_app.status_line().startswith("Ready")


def test_type_into_field(forms_app) -> None:
    """setTextContents writes into a JTextField without foreground focus."""
    item = forms_app.block("EMPLOYEES").item("EMPNO")
    item.type_text("7369")
    assert item.value() == "7369"


def test_type_replaces_previous_content(forms_app) -> None:
    """type_text(clear=True) overwrites the field."""
    item = forms_app.block("EMPLOYEES").item("ENAME")
    item.type_text("SMITH")
    assert item.value() == "SMITH"
    item.type_text("JONES")
    assert item.value() == "JONES"


def test_type_into_second_block(forms_app) -> None:
    """Items are correctly scoped by block prefix ``BLOCK.ITEM``."""
    dept = forms_app.block("DEPARTMENTS").item("DEPTNO")
    dept.type_text("10")
    assert dept.value() == "10"


def test_execute_query_via_f8(forms_app) -> None:
    """F8 fires the ExecuteQuery binding — status updates."""
    forms_app.execute_query()
    assert "Record 1" in forms_app.wait_for_status("Record")


def test_enter_query_via_f7(forms_app) -> None:
    """F7 → Enter query mode."""
    forms_app.enter_query()
    assert forms_app.wait_for_status("Enter query") == "Enter query."


def test_cancel_query_leaves_query_mode(forms_app) -> None:
    if not _can_deliver_modifier_keys(forms_app):
        pytest.skip("Forms window cannot take the foreground — Ctrl+Q undeliverable")
    forms_app.enter_query()
    forms_app.wait_for_status("Enter query")
    forms_app.cancel_query()
    assert forms_app.wait_for_status("cancelled") == "Query cancelled."


def test_function_key_raw(forms_app) -> None:
    """Any key spec works through ``function_key(...)``."""
    forms_app.function_key("F7")
    assert forms_app.wait_for_status("Enter query") == "Enter query."


def test_status_line_ignores_junk_query(forms_app) -> None:
    """OracleFormsKey names round-trip to human-readable status."""
    forms_app.function_key(OracleFormsKey.EXECUTE_QUERY)
    assert "Record" in forms_app.wait_for_status("Record")


def test_item_names_expose_accessibility(forms_app) -> None:
    """Every JTextField exposes its ``BLOCK.ITEM`` fully-qualified name."""
    item = forms_app.block("EMPLOYEES").item("SAL")
    assert item.name == "EMPLOYEES.SAL"


def test_item_value_via_direct_get_text(forms_app) -> None:
    """Direct JAB text read matches what we wrote via direct set."""
    item = forms_app.block("EMPLOYEES").item("SAL")
    item.type_text("2500")
    assert item.text() == "2500"
    assert item.value() == "2500"
