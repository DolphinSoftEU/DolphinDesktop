"""Actions that did not happen must raise, never report success."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dolphin_desktop._delphi import DelphiComponent, DelphiError
from dolphin_desktop._java import _JABSession

# Delphi / VCL


def _dead_wrapper() -> MagicMock:
    wrapper = MagicMock()
    for method in ("invoke", "click", "click_input", "double_click_input", "right_click_input"):
        getattr(wrapper, method).side_effect = RuntimeError("no such element")
    return wrapper


def test_click_raises_when_every_strategy_fails() -> None:
    component = DelphiComponent(_dead_wrapper(), name="BtnSave", cls="TButton")
    with pytest.raises(DelphiError, match=r"click\(\) failed"):
        component.click()


def test_double_click_raises_when_every_strategy_fails() -> None:
    component = DelphiComponent(_dead_wrapper(), name="Grid", cls="TStringGrid")
    with pytest.raises(DelphiError):
        component.double_click()


def test_right_click_raises_when_every_strategy_fails() -> None:
    component = DelphiComponent(_dead_wrapper(), name="Grid", cls="TStringGrid")
    with pytest.raises(DelphiError):
        component.right_click()


def _edit_wrapper() -> MagicMock:
    """Wrapper with no working programmatic setter, forcing the keystroke path."""
    wrapper = MagicMock()
    wrapper.set_edit_text.side_effect = RuntimeError("no EditWrapper")
    wrapper.set_text.side_effect = RuntimeError("no ValuePattern")
    return wrapper


def _typed(wrapper: MagicMock) -> list[str]:
    return [call.args[0] for call in wrapper.type_keys.call_args_list]


def test_set_text_escapes_type_keys_metacharacters() -> None:
    """Unescaped, "P@ss+1" sends Shift+1 and the field ends up holding "P@ss!"."""
    wrapper = _edit_wrapper()
    DelphiComponent(wrapper, name="EdtPassword", cls="TEdit").set_text("P@ss+1")
    assert _typed(wrapper)[-1] == "P@ss{+}1"


def test_set_text_escapes_braces_and_parentheses() -> None:
    """These raise inside pywinauto rather than mistyping, so the failure was
    swallowed by the _safe() wrapper and set_text reported success."""
    wrapper = _edit_wrapper()
    DelphiComponent(wrapper, name="EdtCfg", cls="TEdit").set_text("cfg{a}(b)")
    assert _typed(wrapper)[-1] == "cfg{{}a{}}{(}b{)}"


def test_set_text_raises_when_the_keystroke_path_fails() -> None:
    """A DelphiError like click()/double_click()/right_click(), not the raw
    pywinauto exception the last-resort path happened to hit."""
    wrapper = _edit_wrapper()
    wrapper.type_keys.side_effect = [None, RuntimeError("cannot send keys")]
    with pytest.raises(DelphiError, match=r"set_text\(\) failed"):
        DelphiComponent(wrapper, name="EdtCfg", cls="TEdit").set_text("value")


def test_type_text_without_clear_escapes_caller_data() -> None:
    wrapper = MagicMock()
    DelphiComponent(wrapper, name="Memo", cls="TMemo").type_text("100% (net)", clear=False)
    wrapper.type_keys.assert_called_once_with(
        "100{%} {(}net{)}", with_spaces=True, with_tabs=True, with_newlines=True
    )


def test_press_key_still_delivers_a_key_sequence() -> None:
    """press_key takes pywinauto key syntax — escaping it would type a caret."""
    wrapper = MagicMock()
    DelphiComponent(wrapper, name="EdtCfg", cls="TEdit").press_key("^s")
    wrapper.type_keys.assert_called_once_with("^s", with_spaces=True)


def test_click_returns_self_when_a_strategy_succeeds() -> None:
    wrapper = MagicMock()
    wrapper.invoke.side_effect = RuntimeError("no InvokePattern")
    component = DelphiComponent(wrapper, name="BtnSave", cls="TButton")
    assert component.click() is component
    wrapper.click.assert_called_once()


# Java Access Bridge


class _FakeWab:
    """Minimal stand-in for windowsaccessbridge-64.dll's action API."""

    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.dispatched: list[str] = []

    def getAccessibleActions(self, vm_id, ac, ref) -> bool:  # noqa: N802
        info = ref._obj
        info.actionsCount = len(self._names)
        for i, name in enumerate(self._names):
            info.actionInfo[i].name = name
        return True

    def doAccessibleActions(self, vm_id, ac, ref, failure_ref) -> bool:  # noqa: N802
        todo = ref._obj
        self.dispatched.append(todo.actions[0].name)
        return True


def _session(names: list[str]) -> tuple[_JABSession, _FakeWab]:
    session = object.__new__(_JABSession)
    wab = _FakeWab(names)
    session._wab = wab
    session._has_action_api = True
    return session, wab


def test_do_action_dispatches_the_requested_action() -> None:
    session, wab = _session(["click"])
    assert session.do_action(1, 2, "click") is True
    assert wab.dispatched == ["click"]


def test_do_action_never_substitutes_a_different_action() -> None:
    """expand() on a button that only exposes click must NOT click it."""
    session, wab = _session(["click"])
    assert session.do_action(1, 2, "expand") is False
    assert wab.dispatched == []


def test_do_action_matches_case_insensitively() -> None:
    session, wab = _session(["Toggle"])
    assert session.do_action(1, 2, "toggle") is True
    assert wab.dispatched == ["Toggle"]
