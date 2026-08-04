"""Tests for the action recorder's event interpretation."""

from __future__ import annotations

import ast
import re
import sys
import threading
import time
import types

import pytest

from dolphin_desktop import _recorder
from dolphin_desktop._recorder import (
    _PASSWORD_PLACEHOLDER,
    RecordedAction,
    Recorder,
    _action_to_line,
    _is_double_click,
    _selector_to_call,
)

_VK_TAB = 0x09
_VK_ENTER = 0x0D


def _click(x: int = 100, y: int = 200, ts: float = 10.0, kind: str = "click") -> RecordedAction:
    return RecordedAction(
        kind=kind,
        window_title="Notepad",
        window_class="Notepad",
        selector={},
        x=x,
        y=y,
        timestamp=ts,
    )


# WH_MOUSE_LL never delivers WM_LBUTTONDBLCLK, so two presses have to be fused here


class TestDoubleClickSynthesis:
    def test_two_presses_at_the_same_spot_within_interval(self):
        assert _is_double_click(_click(), "click", 100, 200, 10.2, 0.5) is True

    def test_small_movement_is_tolerated(self):
        assert _is_double_click(_click(), "click", 103, 197, 10.2, 0.5) is True

    def test_movement_beyond_threshold_is_not_a_double_click(self):
        assert _is_double_click(_click(), "click", 140, 200, 10.2, 0.5) is False

    def test_press_after_the_interval_is_not_a_double_click(self):
        assert _is_double_click(_click(), "click", 100, 200, 10.8, 0.5) is False

    def test_interval_boundary_is_inclusive(self):
        assert _is_double_click(_click(), "click", 100, 200, 10.5, 0.5) is True

    def test_no_previous_action(self):
        assert _is_double_click(None, "click", 100, 200, 10.2, 0.5) is False

    @pytest.mark.parametrize("prev_kind", ["right_click", "double_click", "type_text"])
    def test_previous_must_be_a_single_click(self, prev_kind):
        prev = _click(kind=prev_kind)
        assert _is_double_click(prev, "click", 100, 200, 10.2, 0.5) is False

    def test_right_click_never_upgrades(self):
        assert _is_double_click(_click(), "right_click", 100, 200, 10.2, 0.5) is False

    def test_out_of_order_timestamps_are_rejected(self):
        assert _is_double_click(_click(ts=10.0), "click", 100, 200, 9.5, 0.5) is False

    def test_triple_click_does_not_chain(self):
        """The second upgrade leaves a double_click, which must not absorb a third press."""
        first = _is_double_click(_click(ts=10.0), "click", 100, 200, 10.2, 0.5)
        assert first is True
        upgraded = _click(kind="double_click", ts=10.2)
        assert _is_double_click(upgraded, "click", 100, 200, 10.4, 0.5) is False


# Password redaction — the recorder writes captured keystrokes to disk in plaintext


class _Focus:
    """Controls what the monkeypatched focus probes report to the recorder.

    Every field reports its *own* selector — a constant one would hide a typed run
    that wrongly inherited the previously focused control's selector.
    """

    def __init__(self) -> None:
        self.is_password = False
        self.field = "user"

    @property
    def selector(self) -> dict[str, str]:
        return {"auto_id": self.field}


@pytest.fixture
def keyboard(monkeypatch) -> tuple[Recorder, _Focus]:
    focus = _Focus()
    win32gui = types.SimpleNamespace(WindowFromPoint=lambda pt: 1, GetForegroundWindow=lambda: 1)
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    monkeypatch.setattr(_recorder, "_get_root_hwnd", lambda hwnd: hwnd)
    monkeypatch.setattr(_recorder, "_get_window_info", lambda hwnd: ("Login", "LoginCls"))
    monkeypatch.setattr(
        _recorder, "_to_unicode", lambda vk, scan, shift, ctrl=False, alt=False: chr(vk).lower()
    )
    monkeypatch.setattr(_recorder, "_focused_is_password", lambda: focus.is_password)
    monkeypatch.setattr(_recorder, "_focused_element_selector", lambda: dict(focus.selector))
    monkeypatch.setattr(_recorder, "_element_at_point", lambda x, y: dict(focus.selector))
    return Recorder(), focus


def _type(rec: Recorder, text: str) -> None:
    for char in text:
        rec._handle_key(ord(char.upper()), 0, False, False, False, time.time())


def _press(rec: Recorder, vk: int, *, ctrl: bool = False) -> None:
    rec._handle_key(vk, 0, False, ctrl, False, time.time())


def _click_field(rec: Recorder) -> None:
    """Click the control the focus stub currently reports."""
    rec._handle_mouse("click", 10, 20, time.time())


class TestPasswordRedaction:
    def test_tab_between_fields_keeps_the_password_out_of_the_script(self, keyboard):
        """type user → Tab → type password → Enter, the flow redaction exists for."""
        rec, focus = keyboard
        _type(rec, "alice")
        _press(rec, _VK_TAB)
        focus.field = "password"
        focus.is_password = True
        _type(rec, "hunter2")
        _press(rec, _VK_ENTER)

        actions = rec.actions()
        assert not any("hunter2" in a.data for a in actions)
        assert [(a.kind, a.data) for a in actions] == [
            ("type_text", "alice"),
            ("press_key", "{TAB}"),
            ("type_text", _PASSWORD_PLACEHOLDER),
            ("press_key", "{ENTER}"),
        ]

    def test_username_before_the_tab_is_still_readable(self, keyboard):
        rec, focus = keyboard
        _type(rec, "alice")
        _press(rec, _VK_TAB)
        focus.field = "password"
        focus.is_password = True
        _type(rec, "secret")
        assert rec.actions()[0].data == "alice"

    def test_flag_flipping_mid_run_redacts_the_whole_run(self, keyboard):
        """Focus is sampled a queue hop late, so a run reaching a password field is unsafe."""
        rec, focus = keyboard
        _type(rec, "ab")
        focus.is_password = True
        _type(rec, "cd")
        assert [a.data for a in rec.actions()] == [_PASSWORD_PLACEHOLDER, _PASSWORD_PLACEHOLDER]

    def test_leaving_a_password_field_starts_a_readable_run(self, keyboard):
        rec, focus = keyboard
        focus.is_password = True
        _type(rec, "secret")
        focus.is_password = False
        _type(rec, "note")
        assert [a.data for a in rec.actions()] == [_PASSWORD_PLACEHOLDER, "note"]

    def test_typing_after_a_bare_tab_targets_the_new_control(self, keyboard):
        """Click, Tab away without typing, then type: the run belongs to the new field."""
        rec, focus = keyboard
        _click_field(rec)
        _press(rec, _VK_TAB)
        focus.field = "password"
        focus.is_password = True
        _type(rec, "hunter2")

        typed = [a for a in rec.actions() if a.kind == "type_text"]
        assert [a.selector for a in typed] == [{"auto_id": "password"}]

    def test_typing_after_a_bare_enter_targets_the_new_control(self, keyboard):
        rec, focus = keyboard
        _click_field(rec)
        _press(rec, _VK_ENTER)
        focus.field = "search"
        _type(rec, "ab")

        typed = [a for a in rec.actions() if a.kind == "type_text"]
        assert [a.selector for a in typed] == [{"auto_id": "search"}]

    def test_typing_after_a_bare_shortcut_targets_the_focused_control(self, keyboard):
        """Ctrl+Tab moves focus too, and the shortcut branch must not pin the selector."""
        rec, focus = keyboard
        _click_field(rec)
        _press(rec, _VK_TAB, ctrl=True)
        focus.field = "password"
        _type(rec, "ab")

        typed = [a for a in rec.actions() if a.kind == "type_text"]
        assert [a.selector for a in typed] == [{"auto_id": "password"}]

    def test_the_key_itself_is_still_sent_to_the_clicked_control(self, keyboard):
        rec, _focus = keyboard
        _click_field(rec)
        _press(rec, _VK_TAB)
        pressed = [a for a in rec.actions() if a.kind == "press_key"]
        assert [a.selector for a in pressed] == [{"auto_id": "user"}]

    def test_altgr_characters_stay_inside_the_typed_run(self, keyboard, monkeypatch):
        """AltGr reaches the hook as Ctrl+Alt; 'ą' must not become the shortcut ^a."""
        rec, _focus = keyboard
        monkeypatch.setattr(
            _recorder,
            "_to_unicode",
            lambda vk, scan, shift, ctrl=False, alt=False: (
                "ą" if (ctrl and alt) else chr(vk).lower()
            ),
        )
        _type(rec, "ma")
        rec._handle_key(0x41, 30, False, True, True, time.time())  # AltGr+A
        _type(rec, "ka")

        assert [(a.kind, a.data) for a in rec.actions()] == [("type_text", "maąka")]

    def test_unclassifiable_focus_fails_closed(self, monkeypatch):
        monkeypatch.setattr(_recorder, "_focused_password_flag", lambda: None)
        assert _recorder._focused_is_password() is True

    def test_classified_plain_field_is_not_redacted(self, monkeypatch):
        monkeypatch.setattr(_recorder, "_focused_password_flag", lambda: False)
        assert _recorder._focused_is_password() is False


# AltGr — Windows reports it as Ctrl+Alt, and this repo's primary locale is Polish


class TestAltGrTranslation:
    def _layout(self, monkeypatch, altgr: str) -> None:
        """Stub a layout whose third level yields *altgr* for any key.

        ``_to_unicode`` is replaced by a stand-in that ignores the vk and
        returns *altgr* whenever both Ctrl and Alt are set, and ``"a"``
        otherwise — so callers pick whichever vk suits the case they describe.
        """
        monkeypatch.setattr(
            _recorder,
            "_to_unicode",
            lambda vk, scan, shift, ctrl=False, alt=False: altgr if (ctrl and alt) else "a",
        )

    def test_a_third_level_character_wins_over_the_ctrl_branch(self, monkeypatch):
        self._layout(monkeypatch, "ą")
        assert _recorder._vk_to_sendkeys(0x41, 30, False, True, True) == "ą"

    def test_a_third_level_brace_is_a_character_not_a_key_spec(self, monkeypatch):
        """German AltGr+7 is '{' — a literal that must not be read back as send_keys."""
        self._layout(monkeypatch, "{")
        assert _recorder._vk_to_sendkeys(0x37, 8, False, True, True) == "{"

    def test_ctrl_alone_is_still_a_shortcut(self, monkeypatch):
        self._layout(monkeypatch, "ą")
        assert _recorder._vk_to_sendkeys(0x41, 30, False, True, False) == "^a"

    def test_ctrl_alt_without_a_layout_character_is_still_a_shortcut(self, monkeypatch):
        self._layout(monkeypatch, "")
        # Both modifiers survive. Emitting "^a" here dropped the Alt and made
        # Ctrl+Alt+Q replay as Ctrl+Q — Quit in most editors — and it
        # contradicted the special-key path, which already kept both.
        assert _recorder._vk_to_sendkeys(0x41, 30, False, True, True) == "^%a"

    def test_a_special_key_is_never_taken_for_altgr(self, monkeypatch):
        self._layout(monkeypatch, "ą")
        assert _recorder._vk_to_sendkeys(_VK_ENTER, 28, False, True, True) == "^%{ENTER}"


# Generated code must parse


class TestGeneratedCodeIsValidPython:
    @pytest.mark.parametrize(
        "value",
        [r"C:\Users\foo", 'Say "hi"', "it's", "line\nbreak", "back\\slash"],
    )
    def test_selector_values_survive_escaping(self, value):
        call = _selector_to_call({"title": value})
        compile(f"win.{call}", "<gen>", "eval")

    @pytest.mark.parametrize("value", [r"C:\Users\foo", 'Say "hi"'])
    def test_typed_text_survives_escaping(self, value):
        action = RecordedAction(
            kind="type_text",
            window_title="W",
            window_class="C",
            selector={"title": value},
            data=value,
        )
        compile(_action_to_line(action, "win").strip(), "<gen>", "eval")

    def test_full_script_with_a_backslash_title_compiles(self):
        rec = Recorder(app=r"C:\Program Files\App")
        rec._actions.append(
            RecordedAction(
                kind="click",
                window_title=r"C:\Users\foo",
                window_class="",
                selector={"title": 'Say "hi"', "control_type": "Button"},
            )
        )
        compile(rec.generate_code(), "<gen>", "exec")

    @pytest.mark.parametrize(
        "kind", ["click", "double_click", "right_click", "type_text", "press_key"]
    )
    def test_an_action_without_a_selector_is_still_a_statement(self, kind):
        """A note about the missing selector must not comment the action out."""
        action = RecordedAction(
            kind=kind, window_title="W", window_class="C", selector={}, data="secret text"
        )
        call = ast.parse(_action_to_line(action, "win").strip(), mode="eval").body
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Attribute)
        assert call.func.attr == kind

    def test_a_selectorless_action_survives_code_generation(self):
        rec = Recorder(app="Notepad")
        rec._actions.append(
            RecordedAction(
                kind="type_text",
                window_title="Notepad",
                window_class="",
                selector={},
                data="secret text",
            )
        )
        code = rec.generate_code()
        typed = [
            node
            for node in ast.walk(ast.parse(code))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "type_text"
        ]
        assert len(typed) == 1
        assert "TODO" in code  # the missing selector still has to be flagged

    def test_window_title_is_matched_literally_not_as_a_regex(self):
        """An unescaped '(' in a window title would make the emitted title_re invalid."""
        pattern = ast.literal_eval(_recorder._title_re_literal("a(b"))
        assert re.match(pattern, "x a(b y")


# Shutdown ordering


class TestStopDrainsTheQueue:
    def _slow_processor(self, rec: Recorder) -> threading.Thread:
        def _loop() -> None:
            rec._evt_queue.get()
            time.sleep(0.05)
            rec._actions.append(
                RecordedAction(kind="click", window_title="W", window_class="C", selector={})
            )

        thread = threading.Thread(target=_loop, daemon=True)
        rec._proc_thread = thread
        thread.start()
        return thread

    def test_stop_waits_for_queued_events(self):
        rec = Recorder()
        self._slow_processor(rec)
        rec.stop()
        assert len(rec.actions()) == 1

    def test_wait_waits_for_queued_events(self):
        rec = Recorder()
        self._slow_processor(rec)
        rec._evt_queue.put(None)
        rec._stopped.set()
        assert rec.wait(1.0) is True
        assert len(rec.actions()) == 1

    def test_join_is_a_noop_while_still_recording(self):
        rec = Recorder()
        thread = self._slow_processor(rec)
        rec._join_processor()  # not stopped yet — must not block on the running thread
        assert thread.is_alive()
        rec.stop()

    def _draining_processor(self, rec: Recorder, per_event: float) -> threading.Thread:
        """A processor that keeps making progress, one event per *per_event* seconds."""

        def _loop() -> None:
            while True:
                evt = rec._evt_queue.get()
                if evt is None:
                    return
                time.sleep(per_event)
                rec._actions.append(
                    RecordedAction(kind="click", window_title="W", window_class="C", selector={})
                )
                rec._processed += 1

        thread = threading.Thread(target=_loop, daemon=True)
        rec._proc_thread = thread
        thread.start()
        return thread

    def test_a_drain_slower_than_the_timeout_is_not_abandoned(self):
        """The cap bounds a stalled drain, not a long one — the tail must not be lost."""
        rec = Recorder()
        for _ in range(6):
            rec._evt_queue.put(("mouse", "click", 0, 0, 0.0))
        self._draining_processor(rec, per_event=0.04)
        rec._evt_queue.put(None)
        rec._stopped.set()

        rec._join_processor(timeout=0.1)
        assert len(rec._actions) == 6

    def test_a_wedged_processor_is_abandoned_with_a_warning(self, caplog):
        rec = Recorder()
        blocked = threading.Event()
        thread = threading.Thread(target=blocked.wait, daemon=True)
        rec._proc_thread = thread
        thread.start()
        rec._evt_queue.put(("mouse", "click", 0, 0, 0.0))
        rec._stopped.set()

        with caplog.at_level("WARNING", logger="dolphin_desktop.recorder"):
            rec._join_processor(timeout=0.05)
        blocked.set()
        assert "recording is incomplete" in caplog.text

    def test_an_idle_processor_is_not_reported_as_stalled(self, caplog):
        """wait() can return before the hook thread posts its sentinel."""
        rec = Recorder()
        blocked = threading.Event()
        thread = threading.Thread(target=blocked.wait, daemon=True)
        rec._proc_thread = thread
        thread.start()
        rec._proc_idle.set()  # parked in get(), holding no event
        rec._stopped.set()

        with caplog.at_level("WARNING", logger="dolphin_desktop.recorder"):
            rec._join_processor(timeout=0.05)
        blocked.set()
        assert caplog.text == ""

    def test_a_processor_wedged_mid_event_is_reported(self, caplog):
        """An event leaves the queue before it is interpreted, so emptiness proves nothing.

        A processor blocked in the focus probe of the keystroke it just dequeued must
        not be mistaken for an idle one — actions() would then flush alongside it.
        """
        rec = Recorder()
        blocked = threading.Event()
        dequeued = threading.Event()

        def _loop() -> None:
            rec._evt_queue.get()
            dequeued.set()
            blocked.wait()

        thread = threading.Thread(target=_loop, daemon=True)
        rec._proc_thread = thread
        thread.start()
        rec._evt_queue.put(("key", 0x41, 0, False, False, False, 0.0))
        assert dequeued.wait(2.0)
        rec._stopped.set()

        with caplog.at_level("WARNING", logger="dolphin_desktop.recorder"):
            rec._join_processor(timeout=0.05)
        blocked.set()
        assert rec._evt_queue.empty()
        assert "recording is incomplete" in caplog.text

    def test_the_processor_publishes_its_idle_state(self):
        """The drain reads that flag — the real loop has to raise it."""
        rec = Recorder()
        thread = threading.Thread(target=rec._process_loop, daemon=True)
        rec._proc_thread = thread
        thread.start()
        try:
            assert rec._proc_idle.wait(2.0)
        finally:
            rec._evt_queue.put(None)
            thread.join(2.0)


# A recorded keystroke must replay as the keystroke, not as its spelling


class TestSpecialKeysAreNeverTypedAsText:
    """``{BACKSPACE}`` in a ``type_text`` run replays as eleven characters.

    ``type_text`` escapes braces before handing the string to send_keys, so a
    braced token folded into a text run comes out literal. Every key with a
    braced spelling — not just Tab and Enter — must therefore end the run.
    """

    @pytest.mark.parametrize(
        ("vk", "expected"),
        [
            (0x08, "{BACKSPACE}"),
            (0x2E, "{DELETE}"),
            (0x26, "{UP}"),
            (0x24, "{HOME}"),
            (0x21, "{PGUP}"),
            (0x74, "{F5}"),
            (0x09, "{TAB}"),
            (0x0D, "{ENTER}"),
        ],
    )
    def test_a_special_key_is_a_braced_token(self, vk, expected):
        assert _recorder._vk_to_sendkeys(vk, 0, False, False, False) == expected

    @pytest.mark.parametrize("vk", [0x08, 0x2E, 0x26, 0x24, 0x21, 0x74, 0x09, 0x0D])
    def test_every_special_key_ends_a_text_run(self, vk):
        # The guard _handle_key uses to choose press_key over type_text. Every
        # key whose spelling is braced must satisfy it, or the token is
        # accumulated as if it were a character.
        assert vk in _recorder._SPECIAL_KEY_MAP

    def test_a_braced_token_would_not_survive_type_text(self):
        # Pins why the above matters, so the guard is not "simplified" back.
        from dolphin_desktop._helpers import _escape_keys

        assert _escape_keys("Helo{BACKSPACE}lo") == "Helo{{}BACKSPACE{}}lo"


class TestModifiersSurviveTranslation:
    @pytest.mark.parametrize(
        ("vk", "shift", "ctrl", "alt", "expected"),
        [
            (0x51, False, True, False, "^q"),
            (0x51, False, True, True, "^%q"),
            (0x46, False, False, True, "%f"),
            (0x58, True, True, False, "^+x"),
            (0x24, True, False, False, "+{HOME}"),
            (0x24, True, True, False, "^+{HOME}"),
            (0x31, False, True, False, "^1"),
        ],
    )
    def test_shortcut_spelling(self, vk, shift, ctrl, alt, expected):
        assert _recorder._vk_to_sendkeys(vk, 0, shift, ctrl, alt) == expected

    def test_plain_alt_is_recorded_not_dropped(self):
        # Alt+F, O is the classic File -> Open path. Returning None here made
        # _handle_key discard the Alt+F as a modifier-only key and record the
        # following "o" as text, so the script typed a stray letter and never
        # opened the menu.
        assert _recorder._vk_to_sendkeys(0x46, 0, False, False, True) is not None

    @pytest.mark.parametrize("spelling", ["{BACKSPACE}", "%f", "^%q", "^+x", "+{HOME}"])
    def test_pywinauto_parses_it_as_real_virtual_keys(self, spelling):
        from pywinauto.keyboard import parse_keys

        actions = [str(a) for a in parse_keys(spelling)]
        assert actions
        # A Unicode packet renders as <charname> with no VK_ / KEsc marker; an
        # accelerator table cannot match one.
        assert any("VK_" in a or "KEsc" in a for a in actions)


class TestAltGrNeedsTheRightAlt:
    """AltGr is physically the right Alt; the left one is always a shortcut."""

    def _layout(self, monkeypatch, produces: str) -> None:
        monkeypatch.setattr(
            _recorder,
            "_to_unicode",
            lambda vk, scan, shift, ctrl=False, alt=False: produces,
        )

    def test_right_alt_on_a_third_level_layout_is_a_character(self, monkeypatch):
        self._layout(monkeypatch, "ś")
        assert _recorder._altgr_char(0x53, 31, False, True, True, ralt=True) == "ś"

    def test_left_alt_is_a_shortcut_even_on_that_layout(self, monkeypatch):
        # Ctrl+Alt+S is Settings in several IDEs. Without the side of the Alt
        # key a Polish or German user recorded type_text("ś") instead.
        self._layout(monkeypatch, "ś")
        assert _recorder._altgr_char(0x53, 31, False, True, True, ralt=False) == ""


class TestAltGrSuppressionSurvivesComposition:
    """The side of the Alt key must hold all the way to the emitted string.

    Testing ``_altgr_char`` alone hid a real bypass: ``_vk_to_sendkeys`` called
    it a second time without ``ralt``, taking the permissive default, so a
    suppression the caller had already applied was silently reinstated.
    """

    def _layout(self, monkeypatch, produces: str) -> None:
        monkeypatch.setattr(
            _recorder,
            "_to_unicode",
            lambda vk, scan, shift, ctrl=False, alt=False: produces,
        )

    def test_left_alt_reaches_the_emitted_string_as_a_shortcut(self, monkeypatch):
        self._layout(monkeypatch, "ś")
        assert _recorder._vk_to_sendkeys(0x53, 31, False, True, True, ralt=False) == "^%s"

    def test_right_alt_still_produces_the_layout_character(self, monkeypatch):
        self._layout(monkeypatch, "ś")
        assert _recorder._vk_to_sendkeys(0x53, 31, False, True, True, ralt=True) == "ś"

    def test_handle_key_records_a_left_alt_combination_as_a_shortcut(self, monkeypatch):
        """End to end through the branch the recorder actually takes."""
        import time as _time
        import types

        self._layout(monkeypatch, "ś")
        win32gui = types.SimpleNamespace(
            WindowFromPoint=lambda pt: 1, GetForegroundWindow=lambda: 1
        )
        monkeypatch.setitem(sys.modules, "win32gui", win32gui)
        monkeypatch.setattr(_recorder, "_get_root_hwnd", lambda hwnd: hwnd)
        monkeypatch.setattr(_recorder, "_get_window_info", lambda hwnd: ("IDE", "IDECls"))
        monkeypatch.setattr(_recorder, "_focused_is_password", lambda: False)
        monkeypatch.setattr(_recorder, "_focused_element_selector", lambda: {"auto_id": "edit"})

        rec = Recorder()
        rec._handle_key(0x53, 31, False, True, True, _time.time(), ralt=False)
        actions = rec.actions()
        assert [(a.kind, a.data) for a in actions] == [("press_key", "^%s")]


class TestSelectorSurvivesNonFocusChangingKeys:
    """Ending a text run and forgetting the control are different decisions.

    Widening the press_key branch to every special key made Backspace drop the
    pending selector too, so the run after a corrected typo re-resolved through
    ``_focused_element_selector()`` — which answers ``{}`` on any failure.
    """

    @pytest.mark.parametrize("vk", [0x08, 0x26, 0x74, 0x75])  # BACKSPACE, UP, F5, F6
    def test_a_key_before_any_typing_keeps_the_clicked_selector(self, keyboard, monkeypatch, vk):
        """Click, press a non-focus-changing key, then type.

        No text preceded the key, so ``_flush_text()`` clears nothing and the
        pending selector is whatever this branch decides. With the focus probe
        failing — the Qt/JAB case with weak UIA focus reporting — the clicked
        element's selector is the only good one left, and dropping it put a
        bare ``locator()`` in the generated script.
        """
        rec, _focus = keyboard
        _click_field(rec)
        monkeypatch.setattr(_recorder, "_focused_element_selector", dict)
        _press(rec, vk)
        _type(rec, "ab")

        typed = [a for a in rec.actions() if a.kind == "type_text"]
        assert [a.selector for a in typed] == [{"auto_id": "user"}]

    def test_a_focus_changing_key_before_any_typing_still_forgets_it(self, keyboard, monkeypatch):
        """The counterpart: Tab really did move focus, so the old selector is
        wrong and must not be inherited even though nothing was typed yet."""
        rec, _focus = keyboard
        _click_field(rec)
        monkeypatch.setattr(_recorder, "_focused_element_selector", dict)
        _press(rec, _VK_TAB)
        _type(rec, "ab")

        typed = [a for a in rec.actions() if a.kind == "type_text"]
        assert [a.selector for a in typed] == [{}]

    def test_backspace_is_still_emitted_as_a_keystroke(self, keyboard):
        rec, _focus = keyboard
        _type(rec, "helo")
        _press(rec, 0x08)
        _type(rec, "lo")
        assert [(a.kind, a.data) for a in rec.actions()] == [
            ("type_text", "helo"),
            ("press_key", "{BACKSPACE}"),
            ("type_text", "lo"),
        ]

    def test_tab_still_forgets_it(self, keyboard):
        """The focus-changing subset must keep clearing — this is the flow
        that otherwise attributes a password to the username field."""
        rec, focus = keyboard
        _click_field(rec)
        _type(rec, "alice")
        _press(rec, _VK_TAB)
        focus.field = "password"
        _type(rec, "hunter2")

        typed = [a for a in rec.actions() if a.kind == "type_text"]
        assert [a.selector for a in typed] == [{"auto_id": "user"}, {"auto_id": "password"}]
