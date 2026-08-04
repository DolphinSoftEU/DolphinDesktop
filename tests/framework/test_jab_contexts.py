"""AccessibleContext ownership — the JAB tree walk must not leak JNI refs."""

from __future__ import annotations

import ctypes
import threading
from unittest.mock import MagicMock

import pytest

from dolphin_desktop._exceptions import ElementNotFoundError
from dolphin_desktop._java import (
    _AA_TO_DO,
    _ACI,
    _MAX_SET_TEXT,
    _TEXT_RANGE_CHUNK,
    _VALUE_BUFFER,
    JABLocator,
    _AAInfo,
    _JABResolvedLocator,
    _JABSession,
)

#: AccessBridgePackages.h — the wire packets are sized off this.
_MAX_BUFFER_SIZE = 10240


class _FakeTree:
    """In-memory accessible tree that tracks unreleased contexts.

    ``nodes`` maps an AccessibleContext id to ``(role, name, children)``.
    ``live`` holds every context handed out by :meth:`get_child` that has
    not been released — the JVM-side leak this guards against.

    :meth:`release` is a strict ledger, not a ``discard``: releasing a
    context that is not currently live is the double-``releaseJavaObject``
    the real bridge answers with ``DeleteGlobalRef on invalid reference``,
    so it fails the test rather than passing silently.
    """

    def __init__(self, nodes: dict[int, tuple[str, str, list[int]]]) -> None:
        self._nodes = nodes
        self.live: set[int] = set()
        self.info_reads: list[int] = []
        self.releases: list[int] = []

    def get_info(self, vm_id: int, ac: int) -> _ACI | None:
        self.info_reads.append(ac)
        if ac not in self._nodes:
            return None
        role, name, children = self._nodes[ac]
        info = _ACI()
        info.role_en_US = role
        info.name = name
        info.childrenCount = len(children)
        return info

    def get_child(self, vm_id: int, ac: int, index: int) -> int:
        child = self._nodes[ac][2][index]
        self.live.add(child)
        return child

    def get_root_context(self, hwnd: int) -> tuple[int, int]:
        # getAccessibleContextFromHWND hands out an owned reference too.
        self.live.add(1)
        return 1, 1

    def release(self, vm_id: int, ac: int) -> None:
        self.releases.append(ac)
        if ac not in self.live:
            raise AssertionError(f"releaseJavaObject called twice for AccessibleContext {ac}")
        self.live.remove(ac)


_TREE = {
    1: ("frame", "root", [2, 3]),
    2: ("panel", "content", [4, 5]),
    3: ("label", "StatusLine", []),
    4: ("push button", "Save", []),
    5: ("push button", "Cancel", []),
}


def _locator(monkeypatch, tree: _FakeTree, **criteria) -> JABLocator:
    monkeypatch.setattr(JABLocator, "_session", lambda self: tree)
    return JABLocator(0, **criteria)


def test_search_releases_every_context_except_the_match(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, control_type="label", title="StatusLine")
    result = loc._search(1, 1)
    assert result is not None
    assert result[0] == 3
    assert tree.live == {3}


def test_search_releases_everything_when_nothing_matches(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, title="Nope")
    assert loc._search(1, 1) is None
    assert tree.live == set()


def test_collect_retains_only_the_matched_contexts(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, control_type="button")
    results: list[JABLocator] = []
    vm_id, root_ac = tree.get_root_context(0)
    retained = loc._collect(vm_id, root_ac, results, 20)
    assert retained is False  # the root itself is not a push button
    assert len(results) == 2
    # The two matched contexts now belong to the returned locators; the
    # root, which _collect consumes, is already released.
    assert tree.live == {4, 5}


def test_collect_reports_a_retained_root(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, control_type="frame")
    results: list[JABLocator] = []
    vm_id, root_ac = tree.get_root_context(0)
    assert loc._collect(vm_id, root_ac, results, 20) is True
    assert len(results) == 1
    assert tree.live == {1}


def test_combo_text_index_releases_the_whole_walk(monkeypatch) -> None:
    tree = _FakeTree(
        {
            1: ("combo box", "c", [2]),
            2: ("list", "l", [3, 4]),
            3: ("list item", "Alpha", []),
            4: ("list item", "Beta", []),
        }
    )
    loc = _locator(monkeypatch, tree, control_type="combobox")
    assert loc._combo_text_index(1, 1, "Beta") == 1
    assert tree.live == set()


def test_resolved_locator_keeps_its_own_context() -> None:
    info = _ACI()
    loc = _JABResolvedLocator(0, vm_id=1, ac=42, info=info, timeout=1.0)
    session = MagicMock()
    loc._session = lambda: session  # type: ignore[method-assign]
    loc._release(1, 42)
    session.release.assert_not_called()


# Retained contexts must be released deterministically


def test_all_hands_back_closable_locators(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    items = _locator(monkeypatch, tree, control_type="button").all()
    assert len(items) == 2
    assert tree.live == {4, 5}
    for item in items:
        item.close()
    assert tree.live == set()


def test_close_is_idempotent(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    (item,) = _locator(monkeypatch, tree, control_type="label").all()
    item.close()
    tree.live.add(3)  # a later, unrelated walk re-acquires the same context
    item.close()
    assert tree.live == {3}


def test_count_releases_every_context_it_walked(monkeypatch) -> None:
    """count() is len(all()) — without a release it leaks one ref per match."""
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, control_type="button")
    assert loc.count() == 2
    assert tree.live == set()
    assert loc.count() == 2
    assert tree.live == set()


def test_nth_releases_the_contexts_it_does_not_return(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    item = _locator(monkeypatch, tree, control_type="button").nth(1)
    assert tree.live == {5}
    item.close()
    assert tree.live == set()


def test_nth_out_of_range_releases_the_whole_walk(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, control_type="button")
    with pytest.raises(ElementNotFoundError):
        loc.nth(7)
    assert tree.live == set()


def test_all_releases_everything_when_the_walk_raises(monkeypatch) -> None:
    """A crash mid-walk must not strand the root or the partial matches."""
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, control_type="button")
    real_get_child = tree.get_child
    calls = {"n": 0}

    def _explode(vm_id: int, ac: int, index: int) -> int:
        calls["n"] += 1
        if calls["n"] > 2:
            raise OSError("JVM went away mid-walk")
        return real_get_child(vm_id, ac, index)

    tree.get_child = _explode  # type: ignore[method-assign]
    assert loc.all() == []
    assert tree.live == set()


def test_all_never_releases_a_matched_context_twice(monkeypatch) -> None:
    """A match that dies while ITS OWN children are walked.

    The locator appended for that node already owns the context, so a walk
    that releases it as well and then lets all()'s handler close the
    locator hands the JVM two releaseJavaObject calls for one JNI global
    reference — DeleteGlobalRef on an invalid reference.
    """
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, control_type="panel")
    real_get_child = tree.get_child

    def _explode(vm_id: int, ac: int, index: int) -> int:
        if ac == 2:  # the matched panel, after its locator was appended
            raise OSError("context went stale")
        return real_get_child(vm_id, ac, index)

    tree.get_child = _explode  # type: ignore[method-assign]
    assert loc.all() == []
    assert tree.live == set()
    assert sorted(tree.releases) == [1, 2]


def test_all_frees_its_walk_on_a_keyboard_interrupt(monkeypatch) -> None:
    """Ctrl-C bypasses ``except Exception``, so the cleanup must sit in a
    ``finally``: every context the walk obtained is released exactly once,
    and no locator left holding an already-released context survives to
    release it a second time from its finalizer."""
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, control_type="panel")
    real_get_child = tree.get_child

    def _interrupt(vm_id: int, ac: int, index: int) -> int:
        if ac == 2:
            raise KeyboardInterrupt
        return real_get_child(vm_id, ac, index)

    tree.get_child = _interrupt  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        loc.all()
    assert tree.live == set()
    assert sorted(tree.releases) == [1, 2]


# A failed search owns the contexts it obtained


def test_find_releases_the_root_when_matching_raises(monkeypatch) -> None:
    """An invalid title_re makes ``_matches`` raise on every probe, and the
    blanket handler must still release root_ac. Two failed ``exists()`` calls
    here have to leave nothing live; otherwise a polling ``exists(timeout=30)``
    leaks one JNI reference per probe."""
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, title_re="[unclosed")
    assert loc.exists() is False
    assert loc.exists() is False
    assert tree.live == set()


def test_find_releases_the_whole_walk_when_it_dies_mid_tree(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, control_type="button")
    real_get_info = tree.get_info

    def _explode(vm_id: int, ac: int):
        if ac == 2:
            raise OSError("context went stale")
        return real_get_info(vm_id, ac)

    tree.get_info = _explode  # type: ignore[method-assign]
    assert loc._find() is None
    assert tree.live == set()


def test_wait_find_releases_the_root_on_a_failed_poll(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    loc = _locator(monkeypatch, tree, title_re="[unclosed", timeout=0.0)
    with pytest.raises(ElementNotFoundError):
        loc._wait_find()
    assert tree.live == set()


# Resolved locators must read live state, not the collect-time snapshot


def test_resolved_locator_rereads_the_jvm(monkeypatch) -> None:
    tree = _FakeTree(dict(_TREE))
    (item,) = _locator(monkeypatch, tree, control_type="label").all()
    assert item._find() is not None
    tree.info_reads.clear()

    tree._nodes[3] = ("label", "StatusLine changed", [])
    result = item._find()
    assert result is not None
    assert tree.info_reads == [3]
    assert result[2].name == "StatusLine changed"
    item.close()


def test_resolved_locator_reports_a_disposed_component(monkeypatch) -> None:
    tree = _FakeTree(dict(_TREE))
    (item,) = _locator(monkeypatch, tree, control_type="label").all()
    del tree._nodes[3]
    assert item._find() is None
    with pytest.raises(ElementNotFoundError):
        item._wait_find()
    item.close()


def test_resolved_locator_clone_shares_a_single_release(monkeypatch) -> None:
    tree = _FakeTree(_TREE)
    (item,) = _locator(monkeypatch, tree, control_type="label").all()
    clone = item.timeout(5.0)
    clone.close()
    assert tree.live == {3}
    item.close()
    assert tree.live == set()


def test_resolved_locator_clone_dies_with_its_owner(monkeypatch) -> None:
    """The owner holds the only release, so a clone that kept its own copy
    of the context went on driving a JNI reference the JVM had reclaimed."""
    tree = _FakeTree(_TREE)
    (item,) = _locator(monkeypatch, tree, control_type="label").all()
    clone = item.timeout(5.0)
    item.close()
    assert tree.live == set()
    tree.info_reads.clear()
    assert clone._find() is None
    assert tree.info_reads == []
    with pytest.raises(ElementNotFoundError):
        clone._wait_find()


def test_finalizer_does_not_release_from_a_foreign_thread(monkeypatch) -> None:
    """The bridge dispatches through the message loop of the thread that
    called Windows_run; __del__ runs wherever the last reference is dropped."""
    tree = _FakeTree(_TREE)
    (item,) = _locator(monkeypatch, tree, control_type="label").all()
    tree._thread_ident = threading.get_ident() + 1  # type: ignore[attr-defined]
    item.__del__()
    assert tree.live == {3}
    # An explicit close is the caller's own call and is not second-guessed.
    item.close()
    assert tree.live == set()


# Screenshots must be cropped against the virtual desktop


def test_screenshot_grabs_across_every_monitor(monkeypatch) -> None:
    """Without all_screens=True Pillow captures the primary monitor only, so a
    Swing window on a second display returns black and a bbox outside the
    primary monitor is cropped against the wrong framebuffer."""
    calls: list[dict] = []
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda **kwargs: calls.append(kwargs) or "image")

    info = _ACI()
    info.x, info.y, info.width, info.height = 2600, 140, 400, 300
    session = MagicMock()
    session.get_info.return_value = info
    loc = _JABResolvedLocator(0, vm_id=1, ac=42, info=info, timeout=1.0)
    loc._session = lambda: session  # type: ignore[method-assign]

    assert loc.screenshot() == "image"
    assert calls == [{"bbox": (2600, 140, 3000, 440), "all_screens": True}]


# getAccessibleTextRange takes its buffer length in a C short


class _TextWab:
    """Stand-in bridge that enforces the real wire-packet limit.

    GetAccessibleTextRangePackage.rText is wchar_t[MAX_BUFFER_SIZE],
    so the bridge can never return more than 10240 wchars however many are
    asked for. A fake that honours any length hides the bug this models:
    asking for more and then advancing by the *requested* count leaves a
    hole in the middle of the text with nothing to signal it.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self.requested: list[int] = []

    def getAccessibleTextInfo(self, vm_id, ac, ref, x, y) -> bool:  # noqa: N802
        ref._obj.charCount = len(self._text)
        return True

    def getAccessibleTextRange(self, vm_id, ac, start, end, buf, length) -> bool:  # noqa: N802
        self.requested.append(length)
        if not -32768 <= length <= 32767:
            raise OverflowError("buffer length does not fit a C short")
        served = min(end + 1, start + _MAX_BUFFER_SIZE - 1)
        buf.value = self._text[start:served]
        return True


def _text_session(text: str) -> tuple[_JABSession, _TextWab]:
    session = object.__new__(_JABSession)
    wab = _TextWab(text)
    session._wab = wab
    session._has_text_api = True
    return session, wab


def test_get_text_returns_short_content_in_one_read() -> None:
    session, wab = _text_session("hello")
    assert session.get_text(1, 2) == "hello"
    assert wab.requested == [6]


def test_get_text_never_asks_for_more_than_the_wire_packet_holds() -> None:
    """A 40k-char JTextArea must come back whole, in packet-sized reads."""
    text = "".join(chr(0x41 + (i % 26)) for i in range(40000))
    session, wab = _text_session(text)
    assert session.get_text(1, 2) == text
    assert wab.requested, "a non-empty field must be read at least once"
    assert all(n <= _MAX_BUFFER_SIZE for n in wab.requested), wab.requested
    assert wab.requested[0] == _TEXT_RANGE_CHUNK + 1


def test_get_text_advances_by_what_the_bridge_returned() -> None:
    """A short read must not open a gap.

    The bridge truncates to its packet size, so advancing by the requested
    count instead of the returned length silently drops everything between
    the two — no exception, no signal, just missing characters.
    """
    text = "".join(chr(0x41 + (i % 26)) for i in range(25000))
    session, _wab = _text_session(text)
    assert session.get_text(1, 2) == text


def test_get_text_empty_field_needs_no_read() -> None:
    session, wab = _text_session("")
    assert session.get_text(1, 2) == ""
    assert wab.requested == []


def test_session_release_skips_null_and_missing_api() -> None:
    calls: list[tuple[int, int]] = []

    class _Wab:
        def releaseJavaObject(self, vm_id, ac) -> None:  # noqa: N802
            calls.append((vm_id, ac))

    session = object.__new__(_JABSession)
    session._wab = _Wab()
    session._has_release_api = True
    session.release(7, 99)
    session.release(7, 0)
    assert calls == [(7, 99)]

    session._has_release_api = False
    session.release(7, 100)
    assert calls == [(7, 99)]


def test_setup_prototypes_binds_release_java_object() -> None:
    session = object.__new__(_JABSession)
    session._wab = MagicMock()
    session._setup_prototypes()
    assert session._has_release_api is True
    assert session._wab.releaseJavaObject.argtypes == [ctypes.c_long, ctypes.c_int64]
    assert session._wab.releaseJavaObject.restype is None


# Wire-packet limits from AccessBridgePackages.h


def test_text_chunk_fits_the_wire_packet() -> None:
    """``rText`` is ``wchar_t[MAX_BUFFER_SIZE]`` and the call passes count+1."""
    assert _TEXT_RANGE_CHUNK + 1 <= _MAX_BUFFER_SIZE


def test_set_text_limit_matches_the_documented_maximum() -> None:
    """``SetTextContentsPackage.text`` is ``wchar_t[MAX_STRING_SIZE]`` (1024)."""
    assert _MAX_SET_TEXT == 1023


def test_value_buffer_is_not_larger_than_its_packet_field() -> None:
    assert _VALUE_BUFFER <= _MAX_BUFFER_SIZE


def test_actions_to_do_matches_the_c_struct() -> None:
    """``AccessibleActionsToDo.actions`` is ``[MAX_ACTIONS_TO_DO]`` = 32.

    An oversized Python struct is not a crash, but it means the two sides
    disagree about the packet layout — worth pinning against a silent edit.
    """
    assert _AA_TO_DO.actions.size == ctypes.sizeof(_AAInfo) * 32


class _SetTextWab:
    def __init__(self) -> None:
        self.written: list[str] = []

    def setTextContents(self, vm_id, ac, text) -> bool:  # noqa: N802
        self.written.append(text)
        return True


def _set_text_session() -> tuple[_JABSession, _SetTextWab]:
    session = object.__new__(_JABSession)
    wab = _SetTextWab()
    session._wab = wab
    session._has_set_text_api = True
    return session, wab


def test_set_text_rejects_a_string_the_bridge_cannot_hold() -> None:
    """Handing over more than the packet holds would write a truncated value.

    ``setTextContents`` replaces the whole content, so there is no chunking
    fallback — refusing is the only way not to corrupt the field.
    """
    session, wab = _set_text_session()
    assert session.set_text_contents(1, 2, "x" * (_MAX_SET_TEXT + 1)) is False
    assert wab.written == []


def test_set_text_accepts_a_string_at_the_limit() -> None:
    session, wab = _set_text_session()
    assert session.set_text_contents(1, 2, "x" * _MAX_SET_TEXT) is True
    assert wab.written == ["x" * _MAX_SET_TEXT]


class _ValueWab:
    def __init__(self, value: str | None) -> None:
        self._value = value
        self.lengths: list[int] = []

    def getCurrentAccessibleValueFromContext(self, vm_id, ac, buf, length) -> bool:  # noqa: N802
        self.lengths.append(length)
        if self._value is None:
            return False
        buf.value = self._value
        return True


def test_get_value_uses_the_real_bridge_export() -> None:
    """``getAccessibleValueFromContext`` is not exported; the ``Current`` one is."""
    session = object.__new__(_JABSession)
    wab = _ValueWab("42")
    session._wab = wab
    session._has_value_api = True
    assert session.get_value(1, 2) == "42"
    assert wab.lengths == [_VALUE_BUFFER]


def test_get_value_returns_none_when_the_target_has_no_value() -> None:
    session = object.__new__(_JABSession)
    session._wab = _ValueWab(None)
    session._has_value_api = True
    assert session.get_value(1, 2) is None


# A read must not act, and focusing must not activate


class _RecordingSession:
    """Stands in for _JABSession, recording which bridge calls were made."""

    def __init__(self, *, value: str | None = None, text: str | None = None) -> None:
        self._value = value
        self._text = text
        self.calls: list[str] = []
        self.focus_ok = True
        #: Whether this bridge build exports requestFocus at all. focus()
        #: treats "not exported" and "exported but refused" differently.
        self._has_focus_api = True

    def get_value(self, vm_id: int, ac: int) -> str | None:
        self.calls.append("get_value")
        return self._value

    def get_text(self, vm_id: int, ac: int) -> str | None:
        self.calls.append("get_text")
        return self._text

    def request_focus(self, vm_id: int, ac: int) -> bool:
        self.calls.append("request_focus")
        return self.focus_ok

    def do_action(self, vm_id: int, ac: int, action: str) -> bool:
        self.calls.append(f"do_action:{action}")
        return True


def _locator_on(session: _RecordingSession) -> JABLocator:
    loc = object.__new__(JABLocator)
    loc._session = lambda: session
    loc._wait_find = lambda: (1, 2, _ACI())
    loc._release = lambda vm_id, ac: None
    # Only what __repr__ reads — the error paths below name the locator.
    loc._hwnd = 1
    loc._control_type = "push button"
    loc._title = "SUBMIT"
    return loc


def test_focus_requests_focus_instead_of_pressing_the_button() -> None:
    """focus() was an alias for click(), so focusing a JButton dispatched its
    AccessibleAction — committing the transaction the test meant to fill in."""
    session = _RecordingSession()
    _locator_on(session).focus()
    assert session.calls == ["request_focus"]
    assert not any(c.startswith("do_action") for c in session.calls)


def test_focus_reports_a_component_that_refuses_focus() -> None:
    """The bridge exports requestFocus and the component still says no —
    that is a real problem with the component, so it must not be papered over
    by activating it."""
    from dolphin_desktop._exceptions import UnsupportedPatternError

    session = _RecordingSession()
    session.focus_ok = False
    with pytest.raises(UnsupportedPatternError):
        _locator_on(session).focus()
    assert not any(c.startswith("do_action") for c in session.calls)


def test_focus_falls_back_when_the_bridge_has_no_focus_api() -> None:
    """A build without the export has no other way to move focus.

    Raising here would break ``OracleFormsItem.type_text(text, clear=False)``,
    which focuses before replaying keystrokes, so the click action stands in.
    """
    session = _RecordingSession()
    session.focus_ok = False
    session._has_focus_api = False
    _locator_on(session).focus()
    assert session.calls == ["request_focus", "do_action:click"]


def test_value_reads_accessible_value_and_never_clicks() -> None:
    """text()'s fallback clicks the component's centre, which drags a JSlider
    thumb to the midpoint — so reading the value changed it."""
    session = _RecordingSession(value="42", text="ignored")
    assert _locator_on(session).value() == "42"
    assert session.calls == ["get_value"]


@pytest.mark.parametrize("no_value", [None, ""])
def test_value_falls_back_to_text_when_there_is_no_accessible_value(no_value) -> None:
    """The empty string is not a value.

    The bridge answers TRUE with an empty buffer for a component that has no
    AccessibleValue at all — every JTextField in an Oracle Forms block — so
    treating "" as a reading made ``item.value()`` return "" for a field that
    plainly held text.
    """
    session = _RecordingSession(value=no_value, text="hello")
    assert _locator_on(session).value() == "hello"
    assert session.calls[0] == "get_value"
    assert "get_text" in session.calls
