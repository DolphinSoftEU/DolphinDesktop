"""Rich wait primitives — cursor / field / custom predicate."""

from __future__ import annotations

import pytest

from dolphin_desktop import MainframeError, start_thread


def test_wait_for_cursor_matches_initial_position(mock_term) -> None:
    """Cursor lands at the mock's cursor_field position after connect —
    wait_for_cursor with those coordinates must return immediately."""
    term, _ = mock_term
    row, col = term.screen().cursor
    term.wait_for_cursor(row, col, timeout=1.0)


def test_wait_for_cursor_after_move(mock_term) -> None:
    """After move_cursor, wait_for_cursor sees the new position without
    having to poll manually."""
    term, _ = mock_term
    term.move_cursor(7, 20)
    term.wait_for_cursor(7, 20, timeout=2.0)


def test_wait_for_cursor_timeout_raises(mock_term) -> None:
    term, _ = mock_term
    with pytest.raises(MainframeError, match="cursor at"):
        term.wait_for_cursor(20, 40, timeout=0.5)


def test_wait_for_field_finds_writable_slot(mock_term) -> None:
    """User field is writable at (6, 17). wait_for_field must return
    its FieldInfo."""
    term, _ = mock_term
    info = term.wait_for_field(6, 17, timeout=2.0)
    assert info.row == 6 and info.col == 17
    assert info.writable
    assert info.length == 8


def test_wait_for_field_writable_only_ignores_protected(mock_term) -> None:
    """The title bar has a protected field at (1, 2). Waiting with
    writable=True must time out; writable=False must find it."""
    term, _ = mock_term
    # Find any protected field to test the writable=False branch.
    protected_positions = [(f.row, f.col) for f in term.fields() if f.protected]
    assert protected_positions, "expected at least one protected field on the mock"
    r, c = protected_positions[0]
    info = term.wait_for_field(r, c, writable=False, timeout=1.0)
    assert info.protected
    with pytest.raises(MainframeError, match="writable field"):
        term.wait_for_field(r, c, writable=True, timeout=0.4)


def test_wait_for_custom_predicate(mock_term) -> None:
    """wait_for(predicate) blocks until the callable returns True."""
    term, _ = mock_term
    calls = {"n": 0}

    def _pred(t) -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    term.wait_for(_pred, timeout=2.0, poll_interval=0.05)
    assert calls["n"] >= 3


def test_wait_for_predicate_timeout(mock_term) -> None:
    term, _ = mock_term
    with pytest.raises(MainframeError, match="never happens"):
        term.wait_for(lambda t: False, timeout=0.3, description="never happens")


def test_wait_for_text_still_works(mock_term) -> None:
    """The pre-existing wait_for_text is refactored on top of _poll_until
    — must keep its old behaviour."""
    term, _ = mock_term
    term.wait_for_text("MOCK-3270", timeout=2.0)


def test_wait_for_cursor_survives_async_host_write(mock_term) -> None:
    """Simulate a host that repaints the screen from a background
    thread mid-wait — the cursor eventually lands at the target."""
    term, _ = mock_term

    def _mover() -> None:
        # Give the wait a moment to start polling, then move the cursor.
        import time as _t

        _t.sleep(0.3)
        term.move_cursor(15, 40)

    t = start_thread(_mover)
    term.wait_for_cursor(15, 40, timeout=3.0)
    t.join(timeout=1.0)
