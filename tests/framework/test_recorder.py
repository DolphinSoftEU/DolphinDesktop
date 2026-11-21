"""Tests for the action recorder's event interpretation."""

from __future__ import annotations

import ast
import ctypes
import queue
import re
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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
    rec = Recorder(global_recording=True)
    original_key = rec._handle_key
    original_mouse = rec._handle_mouse

    def snapshot():
        return _recorder._InputContext(
            1,
            100,
            2,
            "Login",
            "LoginCls",
            focus.is_password,
            tuple(focus.selector.items()),
        )

    def key(*args, **kwargs):
        original_key(*args, **kwargs, context=snapshot())

    def mouse(*args, **kwargs):
        original_mouse(*args, **kwargs, context=snapshot())
        rec._pending_selector = dict(focus.selector)

    monkeypatch.setattr(rec, "_handle_key", key)
    monkeypatch.setattr(rec, "_handle_mouse", mouse)
    return rec, focus


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


class _CallRecorder:
    """Stands in for the ``win`` the generated source is written for."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return _call


def _run(source: str) -> _CallRecorder:
    """Compile and evaluate a generated line, returning what the calls received."""
    win = _CallRecorder()
    eval(compile(source, "<gen>", "eval"), {"win": win})
    return win


class TestGeneratedCodeIsValidPython:
    @pytest.mark.parametrize(
        "value",
        [r"C:\Users\foo", 'Say "hi"', "it's", "line\nbreak", "back\\slash"],
    )
    def test_selector_values_survive_escaping(self, value):
        call = _selector_to_call({"title": value})
        win = _run(f"win.{call}")
        assert win.calls == [("get_by_title", (value,), {})]

    @pytest.mark.parametrize("value", [r"C:\Users\foo", 'Say "hi"'])
    def test_typed_text_survives_escaping(self, value):
        action = RecordedAction(
            kind="type_text",
            window_title="W",
            window_class="C",
            selector={"title": value},
            data=value,
        )
        win = _run(_action_to_line(action, "win").strip())
        assert win.calls == [("get_by_title", (value,), {}), ("type_text", (value,), {})]

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
        code = rec.generate_code()
        compile(code, "<gen>", "exec")
        calls = {
            node.func.attr: node
            for node in ast.walk(ast.parse(code))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        # The backslashes survive both the repr and the regex escaping: the
        # generated patterns still match the paths they were recorded from.
        assert re.fullmatch(
            ast.literal_eval(calls["connect"].keywords[0].value), r"C:\Program Files\App"
        )
        assert re.fullmatch(ast.literal_eval(calls["window"].keywords[0].value), r"C:\Users\foo")
        role = calls["get_by_role"]
        assert ast.literal_eval(role.args[0]) == "Button"
        assert ast.literal_eval(role.keywords[0].value) == 'Say "hi"'

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
        rec._started = True  # This helper models an already started recording.

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

    def test_join_rejects_reads_while_still_recording(self):
        rec = Recorder()
        thread = self._slow_processor(rec)
        with pytest.raises(RuntimeError, match="Stop recording before"):
            rec._join_processor()
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

    def test_a_wedged_processor_raises_incomplete_recording(self, caplog):
        rec = Recorder()
        blocked = threading.Event()
        thread = threading.Thread(target=blocked.wait, daemon=True)
        rec._proc_thread = thread
        thread.start()
        rec._evt_queue.put(("mouse", "click", 0, 0, 0.0))
        rec._stopped.set()

        try:
            with caplog.at_level("WARNING", logger="dolphin_desktop.recorder"):
                with pytest.raises(TimeoutError, match="recording is incomplete"):
                    rec._join_processor(timeout=0.05)
        finally:
            blocked.set()
            thread.join(2)
        assert "recording is incomplete" in caplog.text

    def test_an_idle_live_processor_is_not_treated_as_completed(self):
        """An empty queue is insufficient: the worker must consume its sentinel."""
        rec = Recorder()
        blocked = threading.Event()
        thread = threading.Thread(target=blocked.wait, daemon=True)
        rec._proc_thread = thread
        thread.start()
        rec._proc_idle.set()  # parked in get(), holding no event
        rec._stopped.set()

        try:
            with pytest.raises(TimeoutError, match="recording is incomplete"):
                rec._join_processor(timeout=0.05)
        finally:
            blocked.set()
            thread.join(2)

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

        try:
            with caplog.at_level("WARNING", logger="dolphin_desktop.recorder"):
                with pytest.raises(TimeoutError, match="recording is incomplete"):
                    rec._join_processor(timeout=0.05)
        finally:
            blocked.set()
            thread.join(2)
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

        rec = Recorder(global_recording=True)
        rec._handle_key(
            0x53,
            31,
            False,
            True,
            True,
            _time.time(),
            ralt=False,
            context=_recorder._InputContext(1, 100, 2, "IDE", "IDECls", False),
        )
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
        rec, focus = keyboard
        _click_field(rec)
        _press(rec, _VK_TAB)
        focus.field = "next"
        _type(rec, "ab")

        typed = [a for a in rec.actions() if a.kind == "type_text"]
        assert [a.selector for a in typed] == [{"auto_id": "next"}]

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


def _action(
    kind: str = "click",
    *,
    title: str = "Window",
    window_class: str = "WindowClass",
    selector: dict[str, str] | None = None,
    data: str = "",
    x: int = 10,
    y: int = 20,
    timestamp: float = 1.0,
) -> RecordedAction:
    return RecordedAction(
        kind=kind,
        window_title=title,
        window_class=window_class,
        selector={} if selector is None else selector,
        data=data,
        x=x,
        y=y,
        timestamp=timestamp,
    )


def _install_win32gui(monkeypatch, *, title: str = "Target", cls: str = "TargetClass"):
    gui = SimpleNamespace(
        WindowFromPoint=lambda point: 11,
        GetForegroundWindow=lambda: 11,
        GetWindowText=lambda hwnd: title,
        GetClassName=lambda hwnd: cls,
    )
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setattr(_recorder, "_get_root_hwnd", lambda hwnd: hwnd)
    return gui


def test_double_click_interval_falls_back_when_user32_fails(monkeypatch) -> None:
    monkeypatch.setattr(_recorder._user32, "GetDoubleClickTime", Mock(side_effect=OSError()))
    assert _recorder._double_click_interval() == 0.5


def test_to_unicode_populates_modifier_state_and_returns_one_character(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def translate(vk, scan, state, buffer, length, flags):
        observed.update(
            vk=vk,
            scan=scan,
            shift=state[_recorder._VK_SHIFT],
            ctrl=state[_recorder._VK_CONTROL],
            alt=state[_recorder._VK_MENU],
            length=length,
            flags=flags,
        )
        buffer.value = "�"
        return 1

    monkeypatch.setattr(_recorder._user32, "ToUnicode", translate)
    assert _recorder._to_unicode(0x41, 30, True, ctrl=True, alt=True) == "�"
    assert observed == {
        "vk": 0x41,
        "scan": 30,
        "shift": 0x80,
        "ctrl": 0x80,
        "alt": 0x80,
        "length": 8,
        "flags": 0,
    }


@pytest.mark.parametrize("result", [0, 2])
def test_to_unicode_returns_empty_for_non_single_character_result(monkeypatch, result) -> None:
    monkeypatch.setattr(_recorder._user32, "ToUnicode", Mock(return_value=result))
    assert _recorder._to_unicode(0x41, 30, False) == ""


def test_to_unicode_returns_empty_when_user32_raises(monkeypatch) -> None:
    monkeypatch.setattr(_recorder._user32, "ToUnicode", Mock(side_effect=RuntimeError("layout")))
    assert _recorder._to_unicode(0x41, 30, False) == ""


def test_vk_translation_ignores_modifiers_and_unknown_non_printable_keys(monkeypatch) -> None:
    monkeypatch.setattr(_recorder, "_to_unicode", lambda *args, **kwargs: "")
    assert _recorder._vk_to_sendkeys(_recorder._VK_SHIFT, 0, False, False, False) is None
    assert _recorder._vk_to_sendkeys(0xFF, 0, False, False, False) is None
    assert _recorder._vk_to_sendkeys(0xFF, 0, False, True, False) is None


def test_selector_and_action_rendering_cover_special_and_fallback_forms() -> None:
    assert _recorder._selector_to_call({"auto_id": "save"}) == "get_by_automation_id('save')"
    assert _recorder._selector_to_call({"class_name": "Edit"}) == "get_by_class('Edit')"
    assert _recorder._selector_to_call({"title": "A", "class_name": "B"}) == (
        "locator(title='A', class_name='B')"
    )

    unknown = _recorder._action_to_line(_action("drag", selector={"auto_id": "x"}), "win")
    assert unknown == "    # drag: ''"


def test_root_window_and_element_helpers_cover_success_none_and_failure(monkeypatch) -> None:
    gui = SimpleNamespace(
        GetAncestor=lambda hwnd, flag: 99,
        GetWindowText=lambda hwnd: None,
        GetClassName=lambda hwnd: "Dialog",
    )
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(GA_ROOT=3))
    assert _recorder._get_root_hwnd(7) == 99
    assert _recorder._get_window_info(7) == ("", "Dialog")

    monkeypatch.setattr(gui, "GetAncestor", lambda hwnd, flag: 0)
    assert _recorder._get_root_hwnd(7) == 7

    info = SimpleNamespace(automation_id=None, name="Save", class_name="Button", control_type=None)
    spy = SimpleNamespace(
        _element_info_from_point=Mock(return_value=info),
        _suggest_selector=Mock(return_value={"title": "Save"}),
    )
    monkeypatch.setitem(sys.modules, "dolphin_desktop._spy", spy)
    assert _recorder._element_at_point(1, 2) == {"title": "Save"}
    spy._suggest_selector.assert_called_once_with("", "Save", "Button", "")

    spy._element_info_from_point.return_value = None
    assert _recorder._element_at_point(1, 2) == {}
    spy._element_info_from_point.side_effect = RuntimeError("uia")
    assert _recorder._element_at_point(1, 2) == {}

    monkeypatch.setitem(sys.modules, "win32gui", None)
    monkeypatch.setitem(sys.modules, "win32con", None)
    assert _recorder._get_root_hwnd(7) == 7
    assert _recorder._get_window_info(7) == ("", "")


def test_focus_helpers_cover_uia_success_empty_and_failures(monkeypatch) -> None:
    element = SimpleNamespace(CurrentIsPassword=1)
    uia = SimpleNamespace(GetFocusedElement=Mock(return_value=element))
    uia_defines = SimpleNamespace(IUIA=lambda: SimpleNamespace(iuia=uia))
    monkeypatch.setitem(sys.modules, "pywinauto.uia_defines", uia_defines)
    assert _recorder._focused_password_flag() is True

    uia.GetFocusedElement.return_value = None
    assert _recorder._focused_password_flag() is None
    uia.GetFocusedElement.side_effect = RuntimeError("COM")
    assert _recorder._focused_password_flag() is None

    focused = SimpleNamespace(
        AutomationId="saveId", Name="Save", ClassName="Button", ControlType="Button"
    )
    uia.GetFocusedElement.side_effect = None
    uia.GetFocusedElement.return_value = focused
    element_info = SimpleNamespace(
        automation_id="saveId", name="Save", class_name="Button", control_type="Button"
    )
    element_info_class = Mock(return_value=element_info)
    selector = Mock(return_value={"auto_id": "saveId"})
    monkeypatch.setitem(
        sys.modules,
        "pywinauto.uia_element_info",
        SimpleNamespace(UIAElementInfo=element_info_class),
    )
    monkeypatch.setitem(
        sys.modules, "dolphin_desktop._spy", SimpleNamespace(_suggest_selector=selector)
    )
    assert _recorder._focused_element_selector() == {"auto_id": "saveId"}
    element_info_class.assert_called_once_with(focused)
    selector.assert_called_once_with("saveId", "Save", "Button", "Button")

    uia.GetFocusedElement.return_value = None
    assert _recorder._focused_element_selector() == {}
    uia.GetFocusedElement.side_effect = RuntimeError("COM")
    assert _recorder._focused_element_selector() == {}


@pytest.mark.parametrize(
    ("app", "expected"),
    [(None, "EVERY"), ("Notepad", "PID 100")],
)
def test_start_logs_scope_and_starts_both_daemon_threads(monkeypatch, app, expected) -> None:
    made = []

    class FakeThread:
        def __init__(self, *, target, daemon, name):
            made.append((target, daemon, name))

        def start(self):
            rec._hook_ready.set()

    monkeypatch.setattr(threading, "Thread", FakeThread)
    warning = Mock()
    monkeypatch.setattr(_recorder._log, "warning", warning)
    monkeypatch.setattr(_recorder, "_resolve_app_pid", lambda app: 100)
    rec = Recorder(app=app, global_recording=app is None)
    rec._stopped.set()
    rec.start()

    assert expected in warning.call_args.args[1]
    assert [(daemon, name) for _, daemon, name in made] == [
        (True, "dolphin-rec-hook"),
        (True, "dolphin-rec-proc"),
    ]
    assert made[0][0] == rec._hook_loop
    assert made[1][0] == rec._process_loop
    assert not rec._stopped.is_set()
    if app:
        code = Recorder(app=app).generate_code()
        assert f"desktop.connect(title_re={_recorder._title_re_literal(app)})" in code
        assert "process=100" not in code


def test_stop_without_hook_thread_is_idempotent_and_ignores_stale_thread_id(monkeypatch) -> None:
    rec = Recorder()
    rec._started = True
    rec._hook_thread_id = 42
    post = Mock()
    monkeypatch.setattr(_recorder._user32, "PostThreadMessageW", post)

    rec.stop()
    rec.stop()

    post.assert_not_called()
    assert rec._evt_queue.get_nowait() is None
    assert rec._evt_queue.empty()


@pytest.mark.parametrize("failures_before_success", [1, 3])
def test_stop_retries_failed_quit_message_and_preserves_diagnostic(
    monkeypatch, failures_before_success
):
    rec = Recorder(pid=100)
    entered = threading.Event()
    quit_requested = threading.Event()
    api = _HookAPI({})

    def get_message(*args):
        entered.set()
        assert quit_requested.wait(2)
        return 0

    def post_quit(*args):
        if api.PostThreadMessageW.call_count <= failures_before_success:
            return 0
        quit_requested.set()
        return 1

    api.GetMessageW = get_message
    api.PostThreadMessageW = Mock(side_effect=post_quit)
    monkeypatch.setattr(_recorder, "_user32", api)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    join_processor = rec._join_processor
    monkeypatch.setattr(rec, "_join_processor", lambda: join_processor(timeout=0.1))
    try:
        rec.start()
        assert entered.wait(2)
        if failures_before_success == _recorder._STOP_POST_ATTEMPTS:
            with pytest.raises(TimeoutError, match="hook thread did not stop") as caught:
                rec.stop()
            assert caught.value.__cause__ is rec._stop_request_error
            assert rec._stopped.is_set()
            assert rec._hook_thread.is_alive()
            assert not rec._quit_posted
            assert api.PostThreadMessageW.call_count == _recorder._STOP_POST_ATTEMPTS
            # Exhausted automatic retries still allow another explicit stop.
            rec.stop()
        else:
            rec.stop()  # A transient failure is recovered within this call.
        diagnostic = rec._stop_request_error
        assert diagnostic.winerror == 5
        assert rec._stopped.is_set()
        assert rec._quit_posted
        assert not rec._hook_thread.is_alive()
        assert api.unhooked == [101, 202]
        rec.stop()
        assert rec.wait(0)
        assert rec.actions() == []
        assert api.PostThreadMessageW.call_count == failures_before_success + 1
        api.PostThreadMessageW.assert_called_with(rec._hook_thread_id, _recorder._WM_QUIT, 0, 0)
    finally:
        quit_requested.set()
        if rec._hook_thread is not None:
            rec._hook_thread.join(2)
        if rec._proc_thread is not None:
            rec._proc_thread.join(2)


def test_wait_timeout_and_join_from_processor_thread_are_noops() -> None:
    rec = Recorder()
    assert rec.wait(0) is False

    rec._stopped.set()
    rec._proc_thread = threading.current_thread()
    rec._join_processor()
    assert rec._proc_thread is threading.current_thread()


def test_join_grants_one_extra_pass_when_an_event_arrives_during_idle_wait(caplog) -> None:
    class AliveThread:
        def join(self, timeout):
            return None

        def is_alive(self):
            return True

    rec = Recorder()
    rec._proc_thread = AliveThread()  # type: ignore[assignment]
    rec._proc_idle.set()
    rec._evt_queue.put(("key", 0x41, 0, False, False, False, False, 0.0))
    rec._stopped.set()

    with caplog.at_level("WARNING", logger="dolphin_desktop.recorder"):
        with pytest.raises(TimeoutError, match="recording is incomplete"):
            rec._join_processor(timeout=0)

    assert "recording is incomplete" in caplog.text


def test_generate_code_writes_requested_output_file(tmp_path: Path) -> None:
    rec = Recorder(app="Demo", backend="win32")
    output = tmp_path / "recorded.py"
    code = rec.generate_code(output, func_name="test_demo")
    assert output.read_text(encoding="utf-8") == code
    assert "def test_demo()" in code


def test_process_loop_handles_mouse_key_unknown_and_empty_queue(monkeypatch) -> None:
    rec = Recorder()
    flush = Mock()
    mouse = Mock()
    key = Mock()
    monkeypatch.setattr(rec, "_flush_text", flush)
    monkeypatch.setattr(rec, "_handle_mouse", mouse)
    monkeypatch.setattr(rec, "_handle_key", key)
    rec._evt_queue.put(("mouse", "click", 1, 2, 3.0, _recorder._InputContext()))
    rec._evt_queue.put(
        ("key", 65, 30, True, False, False, True, 4.0, _recorder._InputContext(hwnd=99))
    )
    rec._evt_queue.put(("other", "ignored"))
    rec._evt_queue.put(None)

    rec._process_loop()

    flush.assert_called_once_with()
    mouse.assert_called_once_with("click", 1, 2, 3.0, context=_recorder._InputContext())
    key.assert_called_once_with(
        65, 30, True, False, False, 4.0, ralt=True, context=_recorder._InputContext(hwnd=99)
    )
    assert rec._processed == 3


def test_process_loop_retries_after_an_empty_queue(monkeypatch) -> None:
    class EmptyThenStopQueue:
        def __init__(self):
            self.calls = 0

        def get(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise queue.Empty
            return None

    event_queue = EmptyThenStopQueue()
    rec = Recorder()
    rec._evt_queue = event_queue  # type: ignore[assignment]
    rec._process_loop()
    assert event_queue.calls == 2


@pytest.mark.parametrize("handler", ["_handle_key", "_handle_mouse", "_flush_text"])
def test_processor_failure_blocks_wait_and_partial_script(monkeypatch, tmp_path, caplog, handler):
    rec = Recorder(pid=100)
    failure = ValueError("synthetic-sensitive-payload")
    flush = Mock()
    monkeypatch.setattr(rec, "_flush_text", flush)
    monkeypatch.setattr(rec, handler, Mock(side_effect=failure))
    rec._pending_text.append("partial")
    context = _recorder._InputContext(1, 100, 2, "Owned", "Dialog", False)
    if handler == "_handle_key":
        event = ("key", 65, 30, False, False, False, False, 1.0, context)
    else:
        event = ("mouse", "click", 1, 2, 1.0, context)
    rec._evt_queue.put(event)
    rec._evt_queue.put(event)  # This event must remain unprocessed after the failure.
    processor = threading.Thread(target=rec._process_loop, daemon=True)
    rec._proc_thread = processor
    processor.start()
    processor.join(2)
    assert not processor.is_alive()
    assert rec._processor_error is failure
    assert rec._stopped.is_set()
    assert rec._processed == 0
    assert rec._evt_queue.get_nowait() == event

    output = tmp_path / "recorded.py"
    output.write_text("keep existing script", encoding="utf-8")
    for operation in (lambda: rec.wait(0), rec.actions, lambda: rec.generate_code(output)):
        with pytest.raises(RuntimeError, match="recording is incomplete") as caught:
            operation()
        assert caught.value.__cause__ is failure
    assert rec._proc_thread is None
    assert output.read_text(encoding="utf-8") == "keep existing script"
    assert rec._pending_text == ["partial"]
    assert "synthetic-sensitive-payload" not in caplog.text


def test_processor_failure_requests_hook_shutdown_and_wakes_wait(monkeypatch):
    quit_requested = threading.Event()
    api = _HookAPI({})

    def get_message(*args):
        assert quit_requested.wait(2)
        return 0

    def post_quit(*args):
        quit_requested.set()
        return 1

    api.GetMessageW = get_message
    api.PostThreadMessageW = Mock(side_effect=post_quit)
    monkeypatch.setattr(_recorder, "_user32", api)
    rec = Recorder(pid=100)
    failure = ValueError("controlled key handler failure")
    monkeypatch.setattr(rec, "_handle_key", Mock(side_effect=failure))
    try:
        rec.start()
        rec._evt_queue.put(("key", 65, 30, False, False, False, False, 1.0, None))
        with pytest.raises(RuntimeError, match="event processor failed") as caught:
            rec.wait(2)
        assert caught.value.__cause__ is failure
        assert rec._proc_thread is None
        assert not rec._hook_thread.is_alive()
        assert api.unhooked == [101, 202]
        api.PostThreadMessageW.assert_called_once_with(
            rec._hook_thread_id, _recorder._WM_QUIT, 0, 0
        )
    finally:
        quit_requested.set()
        if rec._hook_thread is not None:
            rec._hook_thread.join(2)
        if rec._proc_thread is not None:
            rec._proc_thread.join(2)


def test_handle_mouse_covers_window_lookup_failure_and_app_filter(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "win32gui", None)
    monkeypatch.setattr(_recorder, "_element_at_point", Mock(return_value={}))
    rec = Recorder()
    rec._handle_mouse("right_click", 1, 2, 3.0)
    assert rec.actions() == []

    _install_win32gui(monkeypatch, title="Other")
    element = Mock(return_value={"auto_id": "x"})
    monkeypatch.setattr(_recorder, "_element_at_point", element)
    filtered = Recorder(app="Target")
    filtered._handle_mouse("click", 1, 2, 3.0)
    assert filtered.actions() == []
    element.assert_not_called()


def test_handle_mouse_upgrades_second_left_press_to_double_click(monkeypatch) -> None:
    _install_win32gui(monkeypatch, title="Target", cls="Dialog")
    selectors = iter([{"auto_id": "first"}, {}])
    monkeypatch.setattr(_recorder, "_element_at_point", lambda x, y: next(selectors))
    rec = Recorder()
    rec._dbl_click_interval = 0.5

    rec.global_recording = True
    context = _recorder._InputContext(1, 100, 2, "Target", "Dialog")
    rec._handle_mouse("click", 10, 20, 1.0, context=context)
    rec._handle_mouse("click", 13, 17, 1.2, context=context)

    actions = rec.actions()
    assert len(actions) == 1
    assert actions[0].kind == "double_click"
    assert actions[0].selector == {}
    assert (actions[0].x, actions[0].y, actions[0].timestamp) == (13, 17, 1.2)
    assert rec._pending_selector == {}


def test_handle_key_covers_window_lookup_failure_filter_and_modifier_only(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "win32gui", None)
    monkeypatch.setattr(_recorder, "_focused_is_password", lambda: False)
    monkeypatch.setattr(_recorder, "_focused_element_selector", lambda: {"auto_id": "edit"})
    monkeypatch.setattr(_recorder, "_vk_to_sendkeys", lambda *args, **kwargs: "a")
    rec = Recorder()
    rec._handle_key(0x41, 30, False, False, False, 1.0)
    assert rec.actions() == []

    _install_win32gui(monkeypatch, title="Other")
    filtered = Recorder(app="Target")
    filtered._pending_text = ["before"]
    filtered._pending_selector = {"auto_id": "old"}
    filtered._pending_win_title = "Other"
    filtered._pending_win_class = "OtherClass"
    filtered._handle_key(0x41, 30, False, False, False, 2.0)
    assert [action.data for action in filtered.actions()] == ["before"]

    _install_win32gui(monkeypatch, title="Target")
    monkeypatch.setattr(_recorder, "_vk_to_sendkeys", lambda *args, **kwargs: None)
    ignored = Recorder()
    ignored._handle_key(_recorder._VK_SHIFT, 0, False, False, False, 3.0)
    assert ignored.actions() == []


def test_handle_key_uses_the_hwnd_captured_at_hook_time_not_a_live_requery(
    monkeypatch,
) -> None:
    """KAN-576: classification must use the window at keystroke time, not

    whatever window happens to be foreground when the processor thread
    catches up — GetForegroundWindow() is never re-queried when a captured
    hwnd is available.
    """
    windows = {11: ("Target", "TargetClass"), 22: ("Other", "OtherClass")}
    gui = SimpleNamespace(
        GetForegroundWindow=lambda: 22,  # a different window is foreground *now*
        GetWindowText=lambda hwnd: windows[hwnd][0],
        GetClassName=lambda hwnd: windows[hwnd][1],
    )
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setattr(_recorder, "_get_root_hwnd", lambda hwnd: hwnd)
    monkeypatch.setattr(_recorder, "_focused_is_password", lambda: False)
    monkeypatch.setattr(_recorder, "_focused_element_selector", lambda: {})
    monkeypatch.setattr(_recorder, "_vk_to_sendkeys", lambda *args, **kwargs: "a")

    rec = Recorder(app="Target", pid=100)
    context = _recorder._InputContext(11, 100, 12, "Target", "TargetClass", False)
    rec._handle_key(0x41, 30, False, False, False, 1.0, context=context)
    rec._flush_text()

    assert [a.window_title for a in rec.actions()] == ["Target"]


@pytest.mark.parametrize(
    ("actions", "expected_connection", "expected_window"),
    [
        ([_action(window_class="Class")], "class_name='Class'", "class_name='Class'"),
        ([_action(title="Window", window_class="")], "title_re", "title_re"),
        ([_action(title="", window_class="")], 'title_re=".*"', "top_window()"),
    ],
)
def test_build_code_covers_unfiltered_connection_variants(
    actions, expected_connection: str, expected_window: str
) -> None:
    rec = Recorder()
    code = "\n".join(rec._build_code(actions, "test_recorded"))
    assert expected_connection in code
    assert expected_window in code
    ast.parse(code)


def test_build_code_declares_multiple_windows_and_passes_when_empty() -> None:
    rec = Recorder()
    actions = [
        _action(title="First", window_class="FirstClass"),
        _action(title="Second", window_class=""),
        _action(title="", window_class=""),
        _action(title="First", window_class="FirstClass"),
    ]
    code = "\n".join(rec._build_code(actions, "test_multi"))
    assert "win = app.window(class_name='FirstClass')" in code
    assert "win2 = app.window(title_re='.*Second.*')" in code
    assert "win3 = app.top_window()" in code
    assert "win3.locator().click()" in code

    empty = "\n".join(rec._build_code([], "test_empty"))
    assert 'app = desktop.connect(title_re=".*")  # TODO: specify app' in empty
    assert "pass  # no actions recorded" in empty


class _HookAPI:
    def __init__(self, callbacks, handles=(101, 202), dispatch=None, message_returns=(1,)):
        self.callbacks = callbacks
        self.handles = iter(handles)
        self.message_returns = iter(message_returns)
        self.dispatch = dispatch
        self.message_calls = 0
        self.translated = 0
        self.dispatched = 0
        self.unhooked = []
        self.quit_calls = []

    def GetCurrentThreadId(self):  # noqa: N802
        return 77

    def SetWindowsHookExW(self, hook_kind, callback, module, thread_id):  # noqa: N802
        self.callbacks[hook_kind] = callback
        return next(self.handles)

    def GetMessageW(self, message, hwnd, minimum, maximum):  # noqa: N802
        self.message_calls += 1
        return next(self.message_returns)

    def TranslateMessage(self, message):  # noqa: N802
        self.translated += 1

    def DispatchMessageW(self, message):  # noqa: N802
        self.dispatched += 1
        if self.dispatch:
            self.dispatch()

    def UnhookWindowsHookEx(self, handle):  # noqa: N802
        self.unhooked.append(handle)
        return 1

    def PeekMessageW(self, *args):  # noqa: N802
        return 0

    def CallNextHookEx(self, hook, n_code, w_param, l_param):  # noqa: N802
        return 123

    def GetAsyncKeyState(self, vk):  # noqa: N802
        return 0x8000 if vk in {_recorder._VK_SHIFT, _recorder._VK_CONTROL} else 0

    def GetForegroundWindow(self):  # noqa: N802
        return 11

    def PostQuitMessage(self, code):  # noqa: N802
        self.quit_calls.append(code)

    def PostThreadMessageW(self, *args):  # noqa: N802
        return 1


def test_hook_loop_queues_mouse_and_keyboard_events_and_stops_on_ctrl_stop_key(monkeypatch) -> None:
    callbacks: dict[int, object] = {}
    structs: list[ctypes.Structure] = []
    api = _HookAPI(callbacks)
    rec = Recorder(stop_key=0x7B)
    captured = _recorder._InputContext(11, 100, 12, "Login", "Dialog", True)
    monkeypatch.setattr(_recorder, "_capture_context", lambda: captured)
    monkeypatch.setattr(_recorder, "_capture_mouse_context", lambda x, y: captured)

    def dispatch():
        mouse = _recorder._MSLLHOOKSTRUCT()
        mouse.pt.x, mouse.pt.y = 12, 34
        structs.append(mouse)
        assert (
            rec._mouse_cb(  # type: ignore[operator]
                _recorder._HC_ACTION, _recorder._WM_LBUTTONDOWN, ctypes.addressof(mouse)
            )
            == 123
        )
        assert rec._mouse_cb(1, _recorder._WM_LBUTTONDOWN, 0) == 123
        assert rec._mouse_cb(_recorder._HC_ACTION, 0x9999, 0) == 123

        key = _recorder._KBDLLHOOKSTRUCT()
        key.vkCode, key.scanCode = 0x41, 30
        structs.append(key)
        assert (
            rec._kbd_cb(  # type: ignore[operator]
                _recorder._HC_ACTION, _recorder._WM_KEYDOWN, ctypes.addressof(key)
            )
            == 123
        )
        assert rec._kbd_cb(1, _recorder._WM_KEYDOWN, 0) == 123
        assert rec._kbd_cb(_recorder._HC_ACTION, 0x9999, 0) == 123

        stop = _recorder._KBDLLHOOKSTRUCT()
        stop.vkCode = 0x7B
        structs.append(stop)
        assert (
            rec._kbd_cb(  # type: ignore[operator]
                _recorder._HC_ACTION, _recorder._WM_SYSKEYDOWN, ctypes.addressof(stop)
            )
            == 123
        )

    api.dispatch = dispatch
    monkeypatch.setattr(_recorder, "_HOOKPROC", lambda function: function)
    monkeypatch.setattr(ctypes.windll, "kernel32", SimpleNamespace(GetCurrentThreadId=lambda: 77))
    monkeypatch.setattr(_recorder, "_user32", api)

    rec._hook_loop()

    assert rec._hook_thread_id == 77
    assert api.translated == 1
    assert api.dispatched == 1
    assert api.unhooked == [101, 202]
    assert api.quit_calls == [0]
    events = []
    while True:
        event = rec._evt_queue.get_nowait()
        if event is None:
            break
        events.append(event)
    assert events[0][:4] == ("mouse", "click", 12, 34)
    assert events[1][:2] == ("key", 0x41)
    assert events[1][-1] is captured
    assert events[0][-1] is captured


@pytest.mark.parametrize("stop_source", ["request", "hotkey"])
def test_stopped_callbacks_forward_without_capturing_even_when_quit_post_fails(
    monkeypatch, stop_source
):
    rec = Recorder(pid=100)
    rec._hook_thread = threading.current_thread()
    api = _HookAPI({})
    api.GetAsyncKeyState = Mock(return_value=0x8000)
    api.PostThreadMessageW = Mock(return_value=0)
    captured = _recorder._InputContext(11, 100, 12, "Owned", "Dialog", False)
    keyboard_probe = Mock(return_value=captured)
    mouse_probe = Mock(return_value=captured)
    monkeypatch.setattr(_recorder, "_capture_context", keyboard_probe)
    monkeypatch.setattr(_recorder, "_capture_mouse_context", mouse_probe)
    monkeypatch.setattr(_recorder, "_HOOKPROC", lambda function: function)
    monkeypatch.setattr(_recorder, "_user32", api)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)

    def dispatch():
        before_stop = _recorder._KBDLLHOOKSTRUCT()
        before_stop.vkCode = ord("A")
        rec._kbd_cb(_recorder._HC_ACTION, _recorder._WM_KEYDOWN, ctypes.addressof(before_stop))
        if stop_source == "request":
            rec._request_stop()
            assert api.PostThreadMessageW.call_count == _recorder._STOP_POST_ATTEMPTS
            assert rec._stop_request_error.winerror == 5
        else:
            stop = _recorder._KBDLLHOOKSTRUCT()
            stop.vkCode = rec._stop_vk
            rec._kbd_cb(_recorder._HC_ACTION, _recorder._WM_KEYDOWN, ctypes.addressof(stop))
        assert rec._stopped.is_set()
        # Invalid pointers prove stopped callbacks do not even read input data.
        for message in (_recorder._WM_KEYDOWN, _recorder._WM_SYSKEYDOWN):
            assert rec._kbd_cb(_recorder._HC_ACTION, message, 0) == 123
        for message in (_recorder._WM_LBUTTONDOWN, _recorder._WM_RBUTTONDOWN):
            assert rec._mouse_cb(_recorder._HC_ACTION, message, 0) == 123

    api.dispatch = dispatch
    rec._hook_loop()
    keyboard_probe.assert_called_once_with()
    mouse_probe.assert_not_called()
    assert len(api.quit_calls) >= 4  # Callbacks request exit on their own thread.
    assert api.unhooked == [101, 202]
    event = rec._evt_queue.get_nowait()
    assert event[:2] == ("key", ord("A"))
    assert event[-1] is captured
    assert rec._evt_queue.get_nowait() is None
    assert rec._evt_queue.empty()


def test_hook_loop_handles_full_queue_and_message_loop_exit(monkeypatch) -> None:
    class FullQueue:
        def __init__(self):
            self.events = []

        def put_nowait(self, event):
            raise queue.Full

        def put(self, event):
            self.events.append(event)

    callbacks: dict[int, object] = {}
    api = _HookAPI(callbacks, message_returns=(1, 0))
    rec = Recorder()
    full_queue = FullQueue()
    rec._evt_queue = full_queue  # type: ignore[assignment]

    structs: list[ctypes.Structure] = []

    def dispatch():
        mouse = _recorder._MSLLHOOKSTRUCT()
        mouse.pt.x, mouse.pt.y = 1, 2
        structs.append(mouse)
        rec._mouse_cb(  # type: ignore[operator]
            _recorder._HC_ACTION, _recorder._WM_LBUTTONDOWN, ctypes.addressof(mouse)
        )
        key = _recorder._KBDLLHOOKSTRUCT()
        key.vkCode, key.scanCode = 0x41, 30
        structs.append(key)
        rec._kbd_cb(  # type: ignore[operator]
            _recorder._HC_ACTION, _recorder._WM_KEYDOWN, ctypes.addressof(key)
        )

    api.dispatch = dispatch

    monkeypatch.setattr(_recorder, "_HOOKPROC", lambda function: function)
    monkeypatch.setattr(ctypes.windll, "kernel32", SimpleNamespace(GetCurrentThreadId=lambda: 88))
    monkeypatch.setattr(_recorder, "_user32", api)

    rec._hook_loop()

    assert rec._hook_thread_id == 88
    assert api.message_calls == 2
    assert api.translated == 1
    assert api.dispatched == 1
    assert api.unhooked == [101, 202]
    assert full_queue.events == [None]


@pytest.mark.parametrize("handles,unhooked", [((0,), []), ((101, 0), [101])])
def test_hook_installation_failure_cleans_up_and_signals_waiters(monkeypatch, handles, unhooked):
    api = _HookAPI({}, handles=handles)
    monkeypatch.setattr(_recorder, "_user32", api)
    rec = Recorder(pid=100)
    rec._hook_loop()
    assert isinstance(rec._hook_error, OSError)
    assert rec._hook_ready.is_set()
    assert rec._stopped.is_set()
    assert api.unhooked == unhooked
    assert api.message_calls == 0
    assert rec._evt_queue.get_nowait() is None


def test_start_reports_hook_installation_failure(monkeypatch):
    rec = Recorder(pid=100)
    monkeypatch.setattr(_recorder, "_user32", _HookAPI({}, handles=(101, 0)))
    with pytest.raises(OSError, match="Could not install recorder hooks") as caught:
        rec.start()
    rec._hook_thread.join(2)
    assert isinstance(caught.value.__cause__, OSError)
    assert rec._proc_thread is None


def test_failed_start_cannot_restart_but_fresh_recorder_can_record_and_stop(monkeypatch):
    failed = Recorder(pid=100)
    monkeypatch.setattr(_recorder, "_user32", _HookAPI({}, handles=(0,)))
    with pytest.raises(OSError, match="Could not install recorder hooks"):
        failed.start()
    failed._hook_thread.join(2)
    original_error = failed._hook_error
    original_thread = failed._hook_thread
    assert failed._evt_queue.qsize() == 1  # Sentinel left by failed hook installation.

    quit_requested = threading.Event()
    api = _HookAPI({})

    def get_message(*args):
        assert quit_requested.wait(2)
        return 0

    def post_quit(*args):
        quit_requested.set()
        return 1

    api.GetMessageW = get_message
    api.PostThreadMessageW = post_quit
    install = Mock(wraps=api.SetWindowsHookExW)
    api.SetWindowsHookExW = install
    monkeypatch.setattr(_recorder, "_user32", api)
    monkeypatch.setattr(_recorder, "_vk_to_sendkeys", lambda *args, **kwargs: "a")
    with pytest.raises(RuntimeError, match="single-use"):
        failed.start()
    install.assert_not_called()
    assert failed._hook_error is original_error
    assert failed._hook_thread is original_thread
    assert failed._proc_thread is None
    assert failed._evt_queue.qsize() == 1
    assert failed._stopped.is_set()

    rec = Recorder(pid=100)
    try:
        rec.start()
        hook = rec._hook_thread
        processor = rec._proc_thread
        with pytest.raises(RuntimeError, match="single-use"):
            rec.start()  # Reject double start without disturbing an active recording.
        assert rec._hook_thread is hook
        assert rec._proc_thread is processor
        assert not rec._stopped.is_set()
        context = _recorder._InputContext(1, 100, 2, "Owned", "Dialog", False)
        rec._evt_queue.put(("key", 65, 30, False, False, False, False, 1.0, context))
        rec.stop()
        assert rec.wait(0)
        assert rec._processed == 1
        assert rec._evt_queue.empty()
        assert [action.data for action in rec.actions()] == ["a"]
        with pytest.raises(RuntimeError, match="single-use"):
            rec.start()  # A completed recording remains available, but cannot restart.
        assert [action.data for action in rec.actions()] == ["a"]
        assert install.call_count == 2
    finally:
        quit_requested.set()
        rec.stop()


@pytest.mark.parametrize("reader", ["actions", "generate_code"])
def test_stop_before_start_is_noop_and_active_read_preserves_pending_text(
    monkeypatch, tmp_path, reader
):
    quit_requested = threading.Event()
    entered = threading.Event()
    release = threading.Event()
    api = _HookAPI({})

    def get_message(*args):
        assert quit_requested.wait(2)
        return 0

    def post_quit(*args):
        quit_requested.set()
        return 1

    api.GetMessageW = get_message
    api.PostThreadMessageW = post_quit
    monkeypatch.setattr(_recorder, "_user32", api)
    monkeypatch.setattr(_recorder, "_vk_to_sendkeys", Mock(side_effect=["a", "b"]))
    rec = Recorder(pid=100)
    event_queue = rec._evt_queue
    rec.stop()
    rec.stop()
    assert rec._evt_queue is event_queue
    assert event_queue.empty()
    assert not rec._stopped.is_set()
    assert not rec._started
    original_key = rec._handle_key
    calls = 0

    def blocked_key(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert release.wait(2)
        original_key(*args, **kwargs)

    monkeypatch.setattr(rec, "_handle_key", blocked_key)
    output = tmp_path / "recorded.py"
    output.write_text("keep existing script", encoding="utf-8")
    try:
        rec.start()
        context = _recorder._InputContext(1, 100, 2, "Owned", "Dialog", False)
        event = ("key", 65, 30, False, False, False, False, 1.0, context)
        rec._evt_queue.put(event)
        rec._evt_queue.put(event)
        assert entered.wait(2)
        assert rec._processed == 1
        assert rec._pending_text == ["a"]
        with pytest.raises(RuntimeError, match="Stop recording before"):
            if reader == "actions":
                rec.actions()
            else:
                rec.generate_code(output)
        assert rec._pending_text == ["a"]
        assert rec._actions == []
        assert output.read_text(encoding="utf-8") == "keep existing script"
        release.set()
        rec.stop()
        assert rec.wait(0)
        assert rec._processed == 2
        assert [action.data for action in rec.actions()] == ["ab"]
        assert "'ab'" in rec.generate_code(output)
        assert rec._evt_queue.empty()
    finally:
        release.set()
        quit_requested.set()
        rec.stop()


@pytest.mark.parametrize("thread_name", ["dolphin-rec-hook", "dolphin-rec-proc"])
def test_thread_start_failure_stops_hooks_and_rejects_recording(monkeypatch, tmp_path, thread_name):
    rec = Recorder(pid=100)
    quit_requested = threading.Event()
    api = _HookAPI({})

    def get_message(*args):
        assert quit_requested.wait(2)
        return 0

    api.GetMessageW = get_message
    api.PostThreadMessageW = lambda *args: quit_requested.set() or 1
    monkeypatch.setattr(_recorder, "_user32", api)
    original_start = threading.Thread.start
    failure = RuntimeError("cannot create thread")

    def start(thread):
        if thread.name == thread_name:
            raise failure
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    try:
        with pytest.raises(RuntimeError) as caught:
            rec.start()
        assert caught.value is failure
        assert rec._stopped.is_set()
        assert rec._proc_thread is None
        if thread_name == "dolphin-rec-proc":
            assert quit_requested.is_set()
            assert not rec._hook_thread.is_alive()
            assert api.unhooked == [101, 202]
        else:
            assert rec._hook_thread is None
            assert api.unhooked == []
        output = tmp_path / "recorded.py"
        for operation in (
            rec.stop,
            lambda: rec.wait(0),
            rec.actions,
            lambda: rec.generate_code(output),
        ):
            with pytest.raises(RuntimeError, match="initialization failed") as caught:
                operation()
            assert caught.value.__cause__ is failure
        assert not output.exists()
    finally:
        quit_requested.set()
        if rec._hook_thread is not None:
            rec._hook_thread.join(2)


@pytest.mark.parametrize(
    "options", [{}, {"pid": 100, "global_recording": True}, {"app": "missing"}]
)
def test_initialization_failure_rejects_completion_and_preserves_output(
    monkeypatch, tmp_path, options
):
    failure = ValueError("app not found")
    monkeypatch.setattr(_recorder, "_resolve_app_pid", Mock(side_effect=failure))
    rec = Recorder(**options)
    output = tmp_path / "recorded.py"
    output.write_text("existing script", encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        rec.start()
    original_error = caught.value
    for operation in (
        rec.stop,
        lambda: rec.wait(0),
        rec.actions,
        lambda: rec.generate_code(output),
    ):
        with pytest.raises(RuntimeError, match="initialization failed") as caught:
            operation()
        assert caught.value.__cause__ is original_error
    assert output.read_text(encoding="utf-8") == "existing script"
    with pytest.raises(RuntimeError, match="single-use"):
        rec.start()


def test_wait_synchronizes_with_processor_start_after_hook_exits(monkeypatch):
    rec = Recorder(pid=100)
    processor_starting = threading.Event()
    release_start = threading.Event()
    waiter_locking = threading.Event()
    wait_finished = threading.Event()
    lock = threading.Lock()
    errors = []
    results = []

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == "recorder-waiter":
                waiter_locking.set()
            lock.acquire()

        def __exit__(self, *args):
            lock.release()

    rec._start_lock = ObservedLock()
    monkeypatch.setattr(_recorder, "_user32", _HookAPI({}, message_returns=(0,)))
    original_start = threading.Thread.start

    def start(thread):
        if thread.name == "dolphin-rec-proc":
            processor_starting.set()
            assert release_start.wait(2)
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)

    def start_recording():
        try:
            rec.start()
        except Exception as exc:
            errors.append(exc)

    def wait_recording():
        try:
            results.append(rec.wait(2))
        except Exception as exc:
            errors.append(exc)
        finally:
            wait_finished.set()

    starter = threading.Thread(target=start_recording)
    waiter = threading.Thread(target=wait_recording, name="recorder-waiter")
    try:
        starter.start()
        assert processor_starting.wait(2)
        assert rec._stopped.wait(2)
        waiter.start()
        assert waiter_locking.wait(2)
        assert not wait_finished.is_set()
        release_start.set()
        starter.join(2)
        waiter.join(2)
        assert not starter.is_alive()
        assert not waiter.is_alive()
        assert errors == []
        assert results == [True]
        assert rec.actions() == []
    finally:
        release_start.set()
        starter.join(2)
        if waiter.ident is not None:
            waiter.join(2)
        rec.stop()


def test_start_timeout_stops_capture_without_starting_processor(monkeypatch):
    rec = Recorder(pid=100)
    hook_thread = Mock(spec=threading.Thread)
    hook_thread.is_alive.return_value = False
    make_thread = Mock(return_value=hook_thread)
    ready_wait = Mock(return_value=False)
    monkeypatch.setattr(threading, "Thread", make_thread)
    monkeypatch.setattr(rec._hook_ready, "wait", ready_wait)

    with pytest.raises(TimeoutError, match="Recorder hooks did not initialize"):
        rec.start()

    ready_wait.assert_called_once_with(5.0)
    hook_thread.start.assert_called_once_with()
    assert make_thread.call_count == 1
    assert rec._proc_thread is None
    assert rec._stopped.is_set()
    hook_thread.join.assert_called_once_with(_recorder._PROC_JOIN_TIMEOUT)
    assert rec._evt_queue.empty()


@pytest.mark.parametrize("logger_fails", [False, True])
def test_unhook_failure_still_removes_remaining_hook_and_signals_completion(
    monkeypatch, logger_fails
):
    api = _HookAPI({}, message_returns=(0,))
    unhook = Mock(side_effect=[0, 1])
    api.UnhookWindowsHookEx = unhook
    error = Mock(side_effect=RuntimeError("logging handler failed") if logger_fails else None)
    monkeypatch.setattr(_recorder, "_user32", api)
    monkeypatch.setattr(_recorder._log, "error", error)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    rec = Recorder(pid=100)

    rec._hook_loop()

    assert [call.args for call in unhook.call_args_list] == [(101,), (202,)]
    error.assert_called_once_with("Could not remove recorder hook: %s", rec._hook_error)
    assert rec._hook_error.winerror == 5
    assert rec._stopped.is_set()
    assert rec._hook_ready.is_set()
    assert rec._evt_queue.get_nowait() is None
    assert rec._evt_queue.empty()
    with pytest.raises(OSError, match="failed during recording") as caught:
        rec.wait(0)
    assert caught.value.__cause__ is rec._hook_error


@pytest.mark.parametrize("reader", ["wait", "stop"])
def test_cleanup_logging_failure_does_not_strand_recorder_threads(monkeypatch, reader):
    release = threading.Event()
    api = _HookAPI({})
    unhook = Mock(side_effect=[0, 1])
    api.UnhookWindowsHookEx = unhook

    def get_message(*args):
        assert release.wait(2), "Test did not release the hook loop"
        return 0

    api.GetMessageW = get_message
    monkeypatch.setattr(_recorder, "_user32", api)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    monkeypatch.setattr(
        _recorder._log, "error", Mock(side_effect=RuntimeError("logging handler failed"))
    )
    rec = Recorder(pid=100)
    try:
        rec.start()
        processor = rec._proc_thread
        hook = rec._hook_thread
        release.set()
        with pytest.raises(OSError, match="failed during recording") as caught:
            if reader == "wait":
                rec.wait(2)
            else:
                rec.stop()
        assert caught.value.__cause__ is rec._hook_error
        assert rec._hook_error.winerror == 5
        assert [call.args for call in unhook.call_args_list] == [(101,), (202,)]
        assert not hook.is_alive()
        assert not processor.is_alive()
        assert rec._evt_queue.empty()  # Processor consumed the sentinel.
        assert rec._stopped.is_set()
        assert rec._hook_ready.is_set()
    finally:
        release.set()
        if rec._hook_thread is not None:
            rec._hook_thread.join(3)
        if rec._proc_thread is not None:
            rec._proc_thread.join(3)


@pytest.mark.parametrize("logger_fails", [False, True])
def test_cleanup_failures_preserve_original_hook_error(monkeypatch, logger_fails):
    api = _HookAPI({}, message_returns=(-1,))
    api.UnhookWindowsHookEx = Mock(return_value=0)
    monkeypatch.setattr(_recorder, "_user32", api)
    monkeypatch.setattr(ctypes, "get_last_error", Mock(side_effect=[6, 5, 87]))
    if logger_fails:
        monkeypatch.setattr(
            _recorder._log, "error", Mock(side_effect=RuntimeError("logging handler failed"))
        )
    rec = Recorder(pid=100)
    rec._hook_loop()
    assert rec._hook_error.winerror == 6
    assert len(rec._hook_error.__notes__) == 2
    assert api.UnhookWindowsHookEx.call_count == 2
    with pytest.raises(OSError) as caught:
        rec.wait(0)
    assert caught.value.__cause__ is rec._hook_error


@pytest.mark.parametrize("reader", ["stop", "wait", "actions", "generate_code"])
@pytest.mark.parametrize("state", ["queued", "mid_event", "idle"])
def test_stalled_drain_cannot_report_success_or_generate_partial_code(
    monkeypatch, tmp_path, reader, state
):
    rec = Recorder(pid=100)
    rec._started = True  # Model a live session whose worker later stalls.
    entered = threading.Event()
    release = threading.Event()
    rec._evt_queue.put(("key",))
    rec._pending_text.append("unfinished")

    def blocked_processor():
        if state != "queued":
            rec._evt_queue.get_nowait()
        if state == "idle":
            rec._proc_idle.set()
        entered.set()
        release.wait(2)

    processor = threading.Thread(target=blocked_processor, daemon=True)
    rec._proc_thread = processor
    processor.start()
    assert entered.wait(2)
    rec._stopped.set()
    monkeypatch.setattr(rec, "_join_processor", lambda: Recorder._join_processor(rec, timeout=0.01))
    output = tmp_path / "incomplete.py"
    try:
        with pytest.raises(TimeoutError, match="recording is incomplete"):
            if reader == "generate_code":
                rec.generate_code(output)
            else:
                getattr(rec, reader)()
        assert not output.exists()
        assert rec._pending_text == ["unfinished"]
        assert rec._actions == []
    finally:
        release.set()
        processor.join(2)


def test_wait_does_not_drain_until_hook_producer_has_finished():
    rec = Recorder(pid=100)
    rec._stopped.set()
    rec._hook_thread = Mock(spec=threading.Thread)
    rec._hook_thread.is_alive.return_value = True
    rec._proc_thread = Mock(spec=threading.Thread)
    with pytest.raises(TimeoutError, match="hook thread did not stop"):
        rec.wait(0)
    rec._proc_thread.join.assert_not_called()
    assert rec._evt_queue.empty()


def test_drain_cannot_wait_for_its_own_hook_thread():
    rec = Recorder(pid=100)
    rec._stopped.set()
    rec._hook_thread = threading.current_thread()
    with pytest.raises(RuntimeError, match="from the hook thread"):
        rec._join_processor()


@pytest.mark.parametrize("event_kind", ["key", "mouse"])
def test_stop_discards_in_flight_callback_and_drains_already_queued_events(monkeypatch, event_kind):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    errors = []
    rec = Recorder(pid=100)
    api = _HookAPI({})
    api.GetAsyncKeyState = Mock(return_value=0)

    def capture_context(*args):
        entered.set()
        assert release.wait(2)
        return _recorder._InputContext(1, 100, 2, "Owned", "Dialog", False)

    def dispatch():
        # An event queued before stop must survive; the blocked callback must not.
        rec._evt_queue.put(
            (
                "key",
                ord("A"),
                0,
                False,
                False,
                False,
                False,
                1.0,
                _recorder._InputContext(1, 100, 2, "Owned", "Dialog", False),
            )
        )
        if event_kind == "mouse":
            mouse = _recorder._MSLLHOOKSTRUCT()
            rec._mouse_cb(_recorder._HC_ACTION, _recorder._WM_LBUTTONDOWN, ctypes.addressof(mouse))
            return
        key = _recorder._KBDLLHOOKSTRUCT()
        key.vkCode = ord("A")
        rec._kbd_cb(_recorder._HC_ACTION, _recorder._WM_KEYDOWN, ctypes.addressof(key))

    def stop_recording():
        try:
            rec.stop()
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    api.dispatch = dispatch
    monkeypatch.setattr(_recorder, "_user32", api)
    monkeypatch.setattr(_recorder, "_HOOKPROC", lambda function: function)
    monkeypatch.setattr(_recorder, "_capture_context", capture_context)
    monkeypatch.setattr(_recorder, "_capture_mouse_context", capture_context)
    monkeypatch.setattr(_recorder, "_vk_to_sendkeys", lambda *args, **kwargs: "a")
    stopper = threading.Thread(target=stop_recording, daemon=True)
    try:
        rec.start()
        assert entered.wait(2)
        stopper.start()
        assert rec._stopped.wait(2)
        assert not finished.is_set()
        release.set()
        assert finished.wait(2)
        stopper.join(2)
        assert errors == []
        assert rec.wait(0)
        assert rec._processed == 1
        assert [action.data for action in rec.actions()] == ["a"]
        assert rec._evt_queue.empty()
        assert api.unhooked == [101, 202]
    finally:
        release.set()
        if stopper.ident is not None:
            stopper.join(2)
        rec.stop()


@pytest.mark.parametrize("event_kind", ["mouse", "key"])
@pytest.mark.parametrize("failure_site", ["context", "queue", "shutdown"])
@pytest.mark.parametrize("logger_fails", [False, True])
def test_callback_failure_still_forwards_input_and_rejects_recording(
    monkeypatch, caplog, event_kind, failure_site, logger_fails
):
    rec = Recorder(pid=100)
    api = _HookAPI({})
    failure = RuntimeError("sensitive captured input")
    next_hook = Mock(return_value=123)
    api.CallNextHookEx = next_hook
    mouse = _recorder._MSLLHOOKSTRUCT()
    key = _recorder._KBDLLHOOKSTRUCT(vkCode=ord("A"))
    data = mouse if event_kind == "mouse" else key
    message = _recorder._WM_LBUTTONDOWN if event_kind == "mouse" else _recorder._WM_KEYDOWN
    pointer = ctypes.addressof(data)
    context = _recorder._InputContext(11, 100, 12, "Owned", "Dialog", False)
    probe = Mock(return_value=context, side_effect=failure if failure_site == "context" else None)
    monkeypatch.setattr(_recorder, "_capture_context", probe)
    monkeypatch.setattr(_recorder, "_capture_mouse_context", probe)
    if failure_site == "queue":
        monkeypatch.setattr(rec._evt_queue, "put_nowait", Mock(side_effect=failure))
    elif failure_site == "shutdown":
        api.PostQuitMessage = Mock(side_effect=failure)
    if logger_fails:
        log_error = Mock(side_effect=RuntimeError("logging handler failed"))
        monkeypatch.setattr(_recorder._log, "error", log_error)

    def dispatch():
        if failure_site == "shutdown":
            rec._stopped.set()
        callback = rec._mouse_cb if event_kind == "mouse" else rec._kbd_cb
        assert callback(_recorder._HC_ACTION, message, pointer) == 123

    api.dispatch = dispatch
    monkeypatch.setattr(_recorder, "_user32", api)
    # Keep the real ctypes callback boundary: uncaught exceptions would be
    # swallowed by ctypes and reported as unraisable, losing the hook result.
    rec._hook_loop()

    next_hook.assert_called_once_with(None, _recorder._HC_ACTION, message, pointer)
    assert api.unhooked == [101, 202]
    assert rec._hook_error is failure
    with pytest.raises(OSError, match="hook loop failed") as caught:
        rec.wait(0)
    assert caught.value.__cause__ is failure
    assert rec._evt_queue.get_nowait() is None
    assert rec._evt_queue.empty()
    assert "sensitive captured input" not in caplog.text
    if logger_fails:
        log_error.assert_called_once_with("Recorder input callback failed; recording is incomplete")
    if failure_site == "shutdown":
        assert api.PostQuitMessage.call_count == 2
    else:
        assert api.quit_calls == [0]


@pytest.mark.parametrize("event_kind", ["mouse", "key"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_message_loop_failure_preserves_callback_error(monkeypatch, event_kind, cleanup_fails):
    rec = Recorder(pid=100)
    api = _HookAPI({})
    original = RuntimeError("sensitive captured input")
    secondary = OSError("message loop failure")
    next_hook = Mock(return_value=123)
    api.CallNextHookEx = next_hook
    data = (
        _recorder._MSLLHOOKSTRUCT()
        if event_kind == "mouse"
        else _recorder._KBDLLHOOKSTRUCT(vkCode=ord("A"))
    )
    message = _recorder._WM_LBUTTONDOWN if event_kind == "mouse" else _recorder._WM_KEYDOWN
    pointer = ctypes.addressof(data)
    probe = Mock(side_effect=original)
    monkeypatch.setattr(_recorder, "_capture_context", probe)
    monkeypatch.setattr(_recorder, "_capture_mouse_context", probe)
    monkeypatch.setattr(_recorder, "_user32", api)
    if cleanup_fails:
        api.UnhookWindowsHookEx = Mock(side_effect=[0, 1])
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)

    def get_message(*args):
        # Windows dispatches low-level callbacks while GetMessageW is pending.
        callback = rec._mouse_cb if event_kind == "mouse" else rec._kbd_cb
        assert callback(_recorder._HC_ACTION, message, pointer) == 123
        raise secondary

    api.GetMessageW = get_message
    rec._hook_loop()

    next_hook.assert_called_once_with(None, _recorder._HC_ACTION, message, pointer)
    assert rec._hook_error is original
    assert original.__notes__[0] == "Additional hook loop failure: message loop failure"
    assert len(original.__notes__) == (2 if cleanup_fails else 1)
    if cleanup_fails:
        assert original.__notes__[1].startswith("Additional hook cleanup failure:")
        assert [call.args for call in api.UnhookWindowsHookEx.call_args_list] == [(101,), (202,)]
    else:
        assert api.unhooked == [101, 202]
    assert rec._hook_ready.is_set()
    assert rec._evt_queue.get_nowait() is None
    assert rec._evt_queue.empty()
    with pytest.raises(OSError, match="hook loop failed") as caught:
        rec.wait(0)
    assert caught.value.__cause__ is original


@pytest.mark.parametrize("event_kind", ["mouse", "key"])
@pytest.mark.parametrize("sign", [-1, 1])
def test_actual_hook_callbacks_preserve_pointer_sized_arguments_and_result(
    monkeypatch, event_kind, sign
):
    rec = Recorder(pid=100)
    api = _HookAPI({}, message_returns=(0,))
    monkeypatch.setattr(_recorder, "_user32", api)
    rec._hook_loop()
    magnitude = 1 << (ctypes.sizeof(ctypes.c_void_p) * 8 - 2)
    result = sign * (magnitude + 123)
    next_hook = Mock(return_value=result)
    api.CallNextHookEx = next_hook
    probe = Mock(side_effect=AssertionError("negative nCode must not inspect input"))
    monkeypatch.setattr(_recorder, "_capture_context", probe)
    monkeypatch.setattr(_recorder, "_capture_mouse_context", probe)
    rec._stopped.clear()
    callback = rec._mouse_cb if event_kind == "mouse" else rec._kbd_cb

    assert callback(-1, magnitude + 1, magnitude + 2) == result
    next_hook.assert_called_once_with(None, -1, magnitude + 1, magnitude + 2)
    assert rec._hook_error is None
    probe.assert_not_called()


def test_hook_callback_forwards_pointer_sized_lparam_to_real_winapi():
    # Negative nCode forwards without dereferencing lParam; no installed hook or
    # injected input is needed to exercise the actual ctypes boundary.
    callback = _recorder._HOOKPROC(
        lambda code, wp, lp: _recorder._user32.CallNextHookEx(None, code, wp, lp)
    )
    failures = []
    previous = sys.unraisablehook
    magnitude = 1 << (ctypes.sizeof(ctypes.c_void_p) * 8 - 2)
    try:
        sys.unraisablehook = failures.append
        callback(-1, magnitude + 321, magnitude + 123)
    finally:
        sys.unraisablehook = previous
    assert not failures
    assert ctypes.sizeof(_recorder._HOOKPROC._restype_) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(_recorder._user32.CallNextHookEx.restype) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(_recorder._user32.SetWindowsHookExW.restype) == ctypes.sizeof(
        ctypes.c_void_p
    )


@pytest.mark.parametrize("reader", ["stop", "wait", "actions", "generate_code"])
def test_all_completion_methods_report_message_loop_failure_after_start(
    monkeypatch, tmp_path, reader
):
    release = threading.Event()
    entered = threading.Event()
    api = _HookAPI({})

    def fail_after_start(*args):
        entered.set()
        assert release.wait(2)
        return -1

    api.GetMessageW = fail_after_start
    monkeypatch.setattr(_recorder, "_user32", api)
    rec = Recorder(pid=100)
    processor = None
    output = tmp_path / "recorded.py"
    output.write_text("keep existing script", encoding="utf-8")
    try:
        rec.start()
        processor = rec._proc_thread
        assert entered.wait(2)
        assert rec.wait(0) is False
        rec._pending_text.append("partial")
        release.set()
        assert rec._stopped.wait(2)
        operations = {
            "stop": rec.stop,
            "wait": lambda: rec.wait(2),
            "actions": rec.actions,
            "generate_code": lambda: rec.generate_code(output),
        }
        with pytest.raises(OSError, match="failed during recording") as caught:
            operations[reader]()
        assert caught.value.__cause__ is rec._hook_error
        assert not rec._hook_thread.is_alive()
        assert not processor.is_alive()
        assert rec._proc_thread is None
        assert rec._evt_queue.empty()
        # Once the worker reference is cleared, subsequent readers must still
        # reject the failed recording, including stop() -> generate_code().
        for operation in operations.values():
            with pytest.raises(OSError, match="failed during recording") as repeated:
                operation()
            assert repeated.value.__cause__ is rec._hook_error
        assert rec._pending_text == ["partial"]
        assert output.read_text(encoding="utf-8") == "keep existing script"
    finally:
        release.set()
        rec._hook_thread.join(2)
        if processor is not None:
            processor.join(2)
    assert api.unhooked == [101, 202]


def test_recorder_detects_double_clicks_and_escapes_window_titles() -> None:
    from dolphin_desktop._recorder import RecordedAction, _is_double_click, _title_re_literal

    previous = RecordedAction("click", "Demo", "Window", {}, x=10, y=10, timestamp=1.0)
    assert _is_double_click(previous, "click", 13, 12, 1.2, 0.5) is True
    assert _is_double_click(previous, "click", 20, 10, 1.2, 0.5) is False
    assert _title_re_literal("A.B") == repr(".*A\\.B.*")


def test_recorder_renders_selector_and_action_code() -> None:
    from dolphin_desktop._recorder import RecordedAction, _action_to_line, _selector_to_call

    assert _selector_to_call({"title": "Save"}) == "get_by_title('Save')"
    action = RecordedAction(
        kind="click",
        selector={"title": "Save"},
        timestamp=0.0,
        window_title="Demo",
        window_class="Window",
    )
    assert ".click()" in _action_to_line(action, "window")


@pytest.mark.parametrize("flags", [(True, False), (False, True), (None, False), (False, None)])
def test_delayed_focus_changes_use_event_classification(monkeypatch, flags):
    rec = Recorder(pid=100)
    monkeypatch.setattr(_recorder, "_vk_to_sendkeys", lambda *a, **kw: "s")
    live = Mock(side_effect=AssertionError("queued input must not consult live focus"))
    monkeypatch.setattr(_recorder, "_focused_password_flag", live)
    monkeypatch.setattr(_recorder, "_focused_element_selector", live)
    monkeypatch.setattr(_recorder, "_capture_context", live)
    # Queue both events before processing: the real focus has already moved.
    for focus, password in enumerate(flags, 2):
        context = _recorder._InputContext(1, 100, focus, "Login", "Dialog", password)
        rec._evt_queue.put(("key", 0x53, 0, False, False, False, False, 1.0, context))
    rec._evt_queue.put(None)
    rec._process_loop()
    assert [a.data for a in rec.actions()] == [
        "s" if flag is False else _PASSWORD_PLACEHOLDER for flag in flags
    ]
    assert "type_text('ss')" not in rec.generate_code()
    live.assert_not_called()


def test_scope_checks_pid_even_when_another_process_has_the_same_title(monkeypatch):
    rec = Recorder(app="Login", pid=100)
    translate = Mock(return_value="s")
    monkeypatch.setattr(_recorder, "_vk_to_sendkeys", translate)
    rec._handle_key(
        0x53,
        0,
        False,
        False,
        False,
        1.0,
        context=_recorder._InputContext(1, 200, 2, "Login", "Dialog", False),
    )
    assert rec.actions() == []
    translate.assert_not_called()
    # A changed title in the pinned process still belongs to the recording.
    rec._handle_key(
        0x53,
        0,
        False,
        False,
        False,
        2.0,
        context=_recorder._InputContext(1, 100, 2, "Changed", "Dialog", False),
    )
    assert rec.actions()[0].window_title == "Changed"


def test_password_characters_are_not_retained_in_pending_buffer(monkeypatch):
    rec = Recorder(pid=100)
    monkeypatch.setattr(_recorder, "_vk_to_sendkeys", lambda *a, **kw: "secret")
    rec._handle_key(
        0x53,
        0,
        False,
        False,
        False,
        1.0,
        context=_recorder._InputContext(1, 100, 2, "Login", "Dialog", True),
    )
    assert "secret" not in repr(rec._pending_text)
    assert "secret" not in rec.generate_code()


def test_start_requires_explicit_recording_scope():
    with pytest.raises(ValueError, match="explicit global_recording"):
        Recorder().start()
    with pytest.raises(ValueError, match="cannot be combined"):
        Recorder(pid=100, global_recording=True).start()


@pytest.mark.parametrize("pids", [[], [100, 200], [0]])
def test_app_resolution_refuses_missing_or_ambiguous_processes(monkeypatch, pids):
    gui = SimpleNamespace(
        EnumWindows=lambda callback, extra: [callback(i, extra) for i in range(len(pids))],
        IsWindowVisible=lambda hwnd: True,
        GetWindowText=lambda hwnd: "Login",
    )
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (1, pids[hwnd])),
    )
    with pytest.raises(ValueError, match="exactly one"):
        _recorder._resolve_app_pid("Login")


def test_app_resolution_accepts_multiple_windows_of_one_process(monkeypatch):
    gui = SimpleNamespace(
        EnumWindows=lambda callback, extra: [callback(i, extra) for i in (1, 2)],
        IsWindowVisible=lambda hwnd: True,
        GetWindowText=lambda hwnd: "Login",
    )
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(
        sys.modules, "win32process", SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (1, 100))
    )
    assert _recorder._resolve_app_pid("Login") == 100


@pytest.mark.parametrize(
    ("style", "cls", "second_focus", "expected"),
    [
        (0, "Edit", 2, False),
        (0x20, "Edit", 2, True),
        (0, "CustomUIA", 2, None),
        (0, "Edit", 3, None),
    ],
)
@pytest.mark.parametrize("api_result", [tuple, list])
@pytest.mark.parametrize("second_identity", [(10, 100), (11, 100), (10, 101)])
def test_native_event_classification_is_stable_or_unknown(
    monkeypatch, style, cls, second_focus, expected, api_result, second_identity
):
    gui = SimpleNamespace(
        GetForegroundWindow=lambda: 1,
        GetClassName=lambda hwnd: cls,
        GetWindowLong=lambda hwnd, index: style,
    )
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    root_reads = 0

    def identity(hwnd):
        nonlocal root_reads
        if hwnd == 1:
            root_reads += 1
            if root_reads > 1:
                return api_result(second_identity)
        return api_result((10, 100))

    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=identity),
    )
    monkeypatch.setattr(_recorder, "_get_root_hwnd", lambda hwnd: 1)
    monkeypatch.setattr(_recorder, "_get_window_info", lambda hwnd: ("Login", "Dialog"))
    focuses = iter([(1, 2), (1, second_focus)])
    monkeypatch.setattr(_recorder, "_native_focus", lambda thread: next(focuses))
    context = _recorder._capture_context()
    assert (context.hwnd, context.pid, context.focus) == (1, 100, 2)
    assert context.password is (expected if second_identity == (10, 100) else None)


def test_native_query_failure_discards_unverifiable_event(monkeypatch):
    monkeypatch.setitem(sys.modules, "win32gui", None)
    assert _recorder._capture_context() == _recorder._InputContext()


@pytest.mark.parametrize("arguments", [[], ["--app", "Login", "--global"]])
def test_cli_requires_one_explicit_scope(monkeypatch, arguments):
    from dolphin_desktop import _cli

    monkeypatch.setattr(sys, "argv", ["dolphin", "record", "-o", "out.py", *arguments])
    with pytest.raises(SystemExit) as error:
        _cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize("arguments", [["--pid", "100"], ["--global"], ["--app", "Login"]])
def test_cli_passes_explicit_scope_to_recorder(monkeypatch, arguments):
    from dolphin_desktop import _cli

    recorder = Mock()
    recorder.pid = 100 if arguments[0] != "--global" else None
    recorder.actions.return_value = []
    factory = Mock(return_value=recorder)
    monkeypatch.setattr(_recorder, "Recorder", factory)
    monkeypatch.setattr(sys, "argv", ["dolphin", "record", "-o", "out.py", *arguments])
    _cli.main()
    factory.assert_called_once_with(
        app="Login" if arguments[0] == "--app" else None,
        backend="uia",
        pid=100 if arguments[0] == "--pid" else None,
        global_recording=arguments[0] == "--global",
    )
    recorder.start.assert_called_once()


@pytest.mark.parametrize("success", [True, False])
def test_native_focus_uses_pointer_sized_handles_and_checks_api_failure(monkeypatch, success):
    def query(thread, pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(_recorder._GUITHREADINFO)).contents
        assert thread == 10
        assert info.cbSize == ctypes.sizeof(_recorder._GUITHREADINFO)
        info.hwndActive = 0x100000001
        info.hwndFocus = 0x100000002
        return success

    monkeypatch.setattr(_recorder, "_user32", SimpleNamespace(GetGUIThreadInfo=query))
    if success:
        assert _recorder._native_focus(10) == (0x100000001, 0x100000002)
    else:
        with pytest.raises(OSError, match="GetGUIThreadInfo failed"):
            _recorder._native_focus(10)


def test_mouse_selector_is_discarded_if_context_changes_during_hit_test(monkeypatch):
    original = _recorder._InputContext(1, 100, 2, "Login", "Dialog", target=2)
    changed = _recorder._InputContext(3, 200, 4, "Other", "Dialog", target=4)
    contexts = iter([original, changed])
    monkeypatch.setattr(_recorder, "_capture_mouse_context", lambda x, y: next(contexts))
    monkeypatch.setattr(_recorder, "_element_at_point", lambda x, y: {"auto_id": "wrong"})
    assert _recorder._mouse_selector(original, 10, 20) == {}


def test_clicks_in_different_processes_are_not_merged(monkeypatch):
    monkeypatch.setattr(_recorder, "_mouse_selector", lambda *args: {})
    rec = Recorder(global_recording=True)
    rec._dbl_click_interval = 0.5
    for pid, ts in [(100, 1.0), (200, 1.1)]:
        rec._handle_mouse(
            "click", 10, 20, ts, context=_recorder._InputContext(pid, pid, 0, "Same", "Dialog")
        )
    assert [a.kind for a in rec.actions()] == ["click", "click"]


def test_explicit_pid_is_supplied_at_replay_time() -> None:
    rec = Recorder(pid=12345)
    code = rec.generate_code()
    assert "12345" not in code
    namespace = {}
    exec(compile(code, "recorded.py", "exec"), namespace)
    desktop = Mock()
    namespace["Desktop"] = Mock(return_value=desktop)
    namespace["test_recorded"](54321)
    desktop.connect.assert_called_once_with(process=54321)


@pytest.fixture
def native_capture(monkeypatch):
    """Exercise real snapshot/selector code with controlled native boundaries."""
    from dolphin_desktop import _spy

    state = SimpleNamespace(focus=3, target=2, pid=100, password=False, field_class="Edit")
    gui = SimpleNamespace(
        WindowFromPoint=lambda point: state.target,
        GetForegroundWindow=lambda: 1,
        GetWindowText=lambda hwnd: "Login",
        GetClassName=lambda hwnd: "Dialog" if hwnd == 1 else state.field_class,
        GetWindowLong=lambda hwnd, index: 0x20 if state.password else 0,
    )
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (10, state.pid)),
    )
    monkeypatch.setattr(_recorder, "_get_root_hwnd", lambda hwnd: 1)
    monkeypatch.setattr(_recorder, "_native_focus", lambda thread: (1, state.focus))
    monkeypatch.setattr(
        _recorder,
        "_to_unicode",
        lambda vk, scan, shift, ctrl=False, alt=False: chr(vk).lower(),
    )
    monkeypatch.setattr(
        _spy,
        "_element_info_from_point",
        lambda x, y: SimpleNamespace(
            automation_id=f"field{state.target}", name="", class_name="Edit", control_type="Edit"
        ),
    )
    return state


@pytest.mark.parametrize("backspace", [False, True])
def test_native_capture_click_then_typing_preserves_verified_selector(native_capture, backspace):
    state = native_capture
    rec = Recorder(pid=100)
    click = _recorder._capture_mouse_context(10, 20)
    assert click.focus == 3 and click.target == 2
    state.focus = 2  # The click moves focus before the worker processes it.
    key = _recorder._capture_context()
    assert not key.selector_items  # Production snapshots do not contain UIA selectors.
    rec._evt_queue.put(("mouse", "click", 10, 20, 1.0, click))
    keys = [0x41, 0x08, 0x42] if backspace else [0x41, 0x42]
    for vk in keys:
        rec._evt_queue.put(("key", vk, 0, False, False, False, False, 2.0, key))
    rec._evt_queue.put(None)
    rec._process_loop()
    actions = rec.actions()
    assert actions[0].selector == {"auto_id": "field2"}
    assert all(action.selector == {"auto_id": "field2"} for action in actions)
    assert "get_by_automation_id('field2').type_text(" in rec.generate_code()


@pytest.mark.parametrize("change", ["focus", "pid", "unknown", "tab"])
def test_native_capture_never_reuses_selector_after_unverified_focus(native_capture, change):
    state = native_capture
    state.focus = 2
    rec = Recorder(pid=100)
    rec._handle_mouse("click", 10, 20, 1.0, context=_recorder._capture_mouse_context(10, 20))
    if change == "focus":
        state.focus = 3
    elif change == "pid":
        state.pid = 200
    elif change == "unknown":
        state.field_class = "CustomUIA"
    else:
        rec._handle_key(0x09, 0, False, False, False, 2.0, context=_recorder._capture_context())
    rec._handle_key(0x41, 0, False, False, False, 3.0, context=_recorder._capture_context())
    typed = [action for action in rec.actions() if action.kind == "type_text"]
    if change == "pid":
        assert typed == []
    else:
        assert typed[0].selector == {}
        assert typed[0].data == (_PASSWORD_PLACEHOLDER if change == "unknown" else "a")


@pytest.mark.parametrize("change", ["target", "pid"])
def test_native_mouse_hit_test_rejects_target_changes(native_capture, monkeypatch, change):
    from dolphin_desktop import _spy

    state = native_capture
    context = _recorder._capture_mouse_context(10, 20)
    original = _spy._element_info_from_point

    def hit_test(x, y):
        info = original(x, y)
        if change == "target":
            state.target = 3
        else:
            state.pid = 200
        return info

    monkeypatch.setattr(_spy, "_element_info_from_point", hit_test)
    assert _recorder._mouse_selector(context, 10, 20) == {}


@pytest.mark.parametrize("pid", [0, -1, True, False])
def test_recorder_refuses_invalid_process_ids(pid):
    with pytest.raises(ValueError, match="pid must be a positive process ID"):
        Recorder(pid=pid)


def test_mouse_capture_failure_discards_unverifiable_event(native_capture, monkeypatch):
    monkeypatch.setattr(
        sys.modules["win32gui"], "WindowFromPoint", Mock(side_effect=OSError("hit test failed"))
    )
    context = _recorder._capture_mouse_context(10, 20)
    assert context == _recorder._InputContext()
    rec = Recorder(pid=100)
    rec._handle_mouse("click", 10, 20, 1.0, context=context)
    assert rec.actions() == []


@pytest.mark.parametrize("change", ["root", "pid"])
def test_native_capture_discards_inconsistent_mouse_target(native_capture, monkeypatch, change):
    if change == "pid":
        monkeypatch.setattr(
            sys.modules["win32process"],
            "GetWindowThreadProcessId",
            lambda hwnd: (10, 200 if hwnd == 2 else 100),
        )
    else:
        target_roots = iter([1, 4])
        monkeypatch.setattr(
            _recorder, "_get_root_hwnd", lambda hwnd: next(target_roots) if hwnd == 2 else 1
        )
    assert _recorder._capture_mouse_context(10, 20) == _recorder._InputContext()


def test_native_modifier_only_key_keeps_verified_click_selector(native_capture):
    native_capture.focus = 2
    rec = Recorder(pid=100)
    rec._handle_mouse("click", 10, 20, 1.0, context=_recorder._capture_mouse_context(10, 20))
    rec._handle_key(
        _recorder._VK_SHIFT, 0, False, False, False, 2.0, context=_recorder._capture_context()
    )
    assert [action.kind for action in rec.actions()] == ["click"]
    rec._handle_key(0x41, 0, False, False, False, 3.0, context=_recorder._capture_context())
    typed = rec.actions()[-1]
    assert (typed.kind, typed.selector, typed.data) == ("type_text", {"auto_id": "field2"}, "a")
