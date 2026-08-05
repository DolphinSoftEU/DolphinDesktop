"""Headless tests for the Oracle Forms keymap and JAB role resolution."""

from __future__ import annotations

import pytest

from dolphin_desktop import OracleFormsKey
from dolphin_desktop._exceptions import ElementNotFoundError
from dolphin_desktop._java import _ACI, JABLocator
from dolphin_desktop._oracle_forms import _post_key_to_window, _translate_key

# Forms runtime key bindings


def test_duplicate_and_delete_record_are_distinct() -> None:
    """Forms binds Shift+F6 to Delete Record — duplicating must not delete."""
    assert OracleFormsKey.DUPLICATE_RECORD != OracleFormsKey.DELETE_RECORD


def test_record_level_bindings_match_the_forms_runtime_table() -> None:
    assert OracleFormsKey.DUPLICATE_RECORD == "F4"
    assert OracleFormsKey.DELETE_RECORD == "Shift+F6"
    assert OracleFormsKey.INSERT_RECORD == "F6"
    assert OracleFormsKey.DUPLICATE_ITEM == "F3"


def test_clear_bindings_match_the_forms_runtime_table() -> None:
    """Each clear key destroys more than the one above it — off-by-one wipes data."""
    assert OracleFormsKey.CLEAR_RECORD == "Shift+F4"
    assert OracleFormsKey.CLEAR_BLOCK == "Shift+F5"
    assert OracleFormsKey.CLEAR_FORM == "Shift+F7"
    assert OracleFormsKey.CLEAR_ITEM == "Ctrl+U"


def test_clear_bindings_are_all_distinct() -> None:
    clears = {
        OracleFormsKey.CLEAR_ITEM,
        OracleFormsKey.CLEAR_RECORD,
        OracleFormsKey.CLEAR_BLOCK,
        OracleFormsKey.CLEAR_FORM,
    }
    assert len(clears) == 4


def test_clear_block_is_not_the_clear_form_binding() -> None:
    assert OracleFormsKey.CLEAR_BLOCK != OracleFormsKey.CLEAR_FORM


def test_block_navigation_uses_shift_not_ctrl() -> None:
    assert OracleFormsKey.NEXT_BLOCK == "Shift+Page_Down"
    assert OracleFormsKey.PREVIOUS_BLOCK == "Shift+Page_Up"


def test_exit_leaves_query_mode_via_ctrl_q() -> None:
    assert OracleFormsKey.EXIT == "Ctrl+Q"
    assert OracleFormsKey.CANCEL_QUERY == "Ctrl+Q"


def test_record_navigation_stays_in_the_pc_style_family() -> None:
    """Bare Down/Up is the stock-fmrweb convention — mixing families is the bug.

    The runtime loads exactly one resource file, so a single entry borrowed
    from the other family silently maps to a different Forms function.
    """
    assert OracleFormsKey.NEXT_RECORD == "Shift+Down"
    assert OracleFormsKey.PREVIOUS_RECORD == "Shift+Up"


def test_no_bindings_for_functions_forms_does_not_have() -> None:
    """Forms has no First/Last Record function; F11/F12 mean something else."""
    assert not hasattr(OracleFormsKey, "FIRST_RECORD")
    assert not hasattr(OracleFormsKey, "LAST_RECORD")

    from dolphin_desktop._oracle_forms import OracleFormsApp

    assert not hasattr(OracleFormsApp, "first_record")
    assert not hasattr(OracleFormsApp, "last_record")


def test_only_intentional_aliases_share_a_binding() -> None:
    """Two names on one key must be a documented alias, never an accident."""
    bindings: dict[str, list[str]] = {}
    for name, value in vars(OracleFormsKey).items():
        if not name.startswith("_") and isinstance(value, str):
            bindings.setdefault(value, []).append(name)
    shared = {k: sorted(v) for k, v in bindings.items() if len(v) > 1}
    assert shared == {
        "Ctrl+Q": ["CANCEL_QUERY", "EXIT"],
        "F10": ["COMMIT", "SAVE"],
    }, f"unexpected shared bindings: {shared}"


def test_query_bindings_match_the_forms_runtime_table() -> None:
    assert OracleFormsKey.ENTER_QUERY == "F7"
    assert OracleFormsKey.EXECUTE_QUERY == "F8"
    assert OracleFormsKey.LIST_OF_VALUES == "F9"
    assert OracleFormsKey.SAVE == "F10"


def test_unverified_bindings_carry_their_marker() -> None:
    """The class docstring points at per-entry *unverified* markers; stripped,
    every binding reads as equally corroborated by a published resource file."""
    import pathlib
    import re

    src = pathlib.Path(_oracle_forms_file()).read_text(encoding="utf-8")
    block = src.split("class OracleFormsKey")[1].split("\n\n\n")[0]
    marked = set(re.findall(r"^    ([A-Z_]+) = .*#\s*unverified", block, re.M))
    assert marked == {
        "COUNT_QUERY",
        "NEXT_BLOCK",
        "PREVIOUS_BLOCK",
        "CLEAR_BLOCK",
        "CLEAR_FORM",
        "DUPLICATE_ITEM",
    }


# The form window has a title


def test_forms_window_title_reads_the_window() -> None:
    """``Window`` exposes ``title()``; the ``window_text()`` call raised
    AttributeError, which the blanket handler turned into "" on every call."""
    from dolphin_desktop._oracle_forms import OracleFormsApp, OracleFormsWindow

    class _Window:
        def title(self) -> str:
            return "Oracle Applications - PROD"

    class _Application:
        def window(self) -> _Window:
            return _Window()

    app = OracleFormsApp.__new__(OracleFormsApp)
    app._application = _Application()  # type: ignore[assignment]
    assert OracleFormsWindow(app).title() == "Oracle Applications - PROD"


def test_translate_key_renders_pywinauto_syntax() -> None:
    assert _translate_key("F7") == "{F7}"
    assert _translate_key("Shift+F5") == "+{F5}"
    assert _translate_key("Ctrl+Page_Down") == "^{PGDN}"


# PostMessage key delivery


def test_post_key_declines_modifier_combos() -> None:
    """PostMessage cannot set the target thread's Shift state.

    Claiming success here makes Swing dispatch Shift+F7 as a plain F7 —
    a different Forms command — and suppresses the SendInput fallback.
    """
    assert _post_key_to_window(0, "Shift+F7") is False
    assert _post_key_to_window(0, "Shift+F5") is False
    assert _post_key_to_window(0, "Ctrl+Page_Down") is False


def test_post_key_declines_unknown_base_key() -> None:
    assert _post_key_to_window(0, "Mystery") is False


# JAB role resolution


def _info(role: str, name: str = "") -> _ACI:
    info = _ACI()
    info.role_en_US = role
    info.name = name
    return info


def test_jab_role_accepts_uia_control_types() -> None:
    assert JABLocator(0, control_type="button")._jab_role() == "push button"
    assert JABLocator(0, control_type="edit")._jab_role() == "text"


def test_jab_role_accepts_jab_role_names() -> None:
    assert JABLocator(0, control_type="status bar")._jab_role() == "status bar"
    assert JABLocator(0, control_type="label")._jab_role() == "label"
    assert JABLocator(0, control_type="menu item")._jab_role() == "menu item"


def test_jab_role_names_are_not_shadowed_by_the_uia_alias_table() -> None:
    """``text`` and ``window`` mean different things in the two vocabularies.

    Both are JAB ``role_en_US`` values (JTextField, JWindow) and both used
    to be rewritten by the UIA alias table, so ``role="text"`` searched for
    labels and could never reach an Oracle Forms text item.
    """
    assert JABLocator(0, control_type="text")._jab_role() == "text"
    assert JABLocator(0, control_type="window")._jab_role() == "window"
    assert JABLocator(0, control_type="text")._matches(_info("text")) is True


def test_uia_control_types_documented_by_get_by_role_all_resolve() -> None:
    """``Window.get_by_role`` takes UIA control types on Java windows too."""
    expected = {
        "ToolBar": "tool bar",
        "StatusBar": "status bar",
        "ProgressBar": "progress bar",
        "ScrollBar": "scroll bar",
        "Tab": "page tab list",
        "TabItem": "page tab",
        "TreeItem": "label",
        "Group": "group box",
        "Image": "icon",
        "Custom": "unknown",
    }
    for control_type, role in expected.items():
        assert JABLocator(0, control_type=control_type)._jab_role() == role


def test_unknown_role_is_rejected_not_dropped() -> None:
    """Rejected, but as a DolphinError the surrounding handlers already catch."""
    with pytest.raises(ElementNotFoundError, match="unknown Java role"):
        JABLocator(0, control_type="stats-bar")


def test_role_criterion_actually_filters() -> None:
    loc = JABLocator(0, control_type="status bar")
    assert loc._matches(_info("status bar")) is True
    assert loc._matches(_info("label")) is False


def test_matches_still_requires_at_least_one_criterion() -> None:
    assert JABLocator(0)._matches(_info("label")) is False


def test_exists_accepts_a_timeout_like_its_siblings() -> None:
    import inspect

    from dolphin_desktop._locator import Locator
    from dolphin_desktop._sap import SapLocator

    for cls in (Locator, SapLocator, JABLocator):
        params = inspect.signature(cls.exists).parameters
        assert "timeout" in params, cls


# Key-sequence delivery must not be escaped as literal text


def test_function_key_sends_a_key_sequence_not_literal_text(monkeypatch) -> None:
    """``Keyboard.type`` escapes metacharacters; function keys must bypass it.

    Routing ``"^Q"`` through the escaping path types a literal caret, which
    silently breaks every function key delivered by the foreground fallback.
    """
    from dolphin_desktop import _oracle_forms

    sent: list[str] = []
    monkeypatch.setattr(
        _oracle_forms.Keyboard, "press", staticmethod(lambda keys: sent.append(keys))
    )
    monkeypatch.setattr(
        _oracle_forms.Keyboard,
        "type",
        staticmethod(lambda *a, **k: pytest.fail("function keys must not go through type()")),
    )

    app = _oracle_forms.OracleFormsApp.__new__(_oracle_forms.OracleFormsApp)
    monkeypatch.setattr(type(app), "_primary_hwnd", lambda self: 0)
    monkeypatch.setattr(type(app), "bring_to_foreground", lambda self: None)

    app.function_key(OracleFormsKey.CANCEL_QUERY)
    assert sent == ["^Q"]


def test_no_key_sequence_reaches_the_escaping_type_helper() -> None:
    """Guard the whole module: literal-text escaping must not eat key syntax."""
    import pathlib
    import re

    src = pathlib.Path(_oracle_forms_file()).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in src.splitlines()
        if re.search(r"Keyboard\.type\(", line) and "escape=False" not in line
    ]
    assert offenders == [], (
        "these pass a key sequence to the escaping Keyboard.type(); "
        f"use Keyboard.press() instead: {offenders}"
    )


def _oracle_forms_file() -> str:
    from dolphin_desktop import _oracle_forms

    return _oracle_forms.__file__
