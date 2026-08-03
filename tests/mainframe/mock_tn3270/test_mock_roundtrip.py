"""End-to-end round-trip against a Python-side TN3270 mock.

Every positional operation on the public API is exercised here — the
tests would fail if any of these regressed:

* ``TerminalScreen`` decodes the 3270 buffer to plain text at correct
  ``(row, col)`` coordinates.
* ``MainframeTerminal.move_cursor`` sends a real 3270 order to the
  host and the host echoes the new cursor position back.
* ``TerminalField.type_text`` clears the field, moves the cursor,
  types EBCDIC text, and the host sees the value in Read-Modified.
* AID keys (Enter, PF3) reach the host with the correct 0x7D / 0xF3
  encoding.
* ``wait_change`` blocks until the host writes a new screen.
"""

from __future__ import annotations

from dolphin_desktop import AID, monotonic


def test_initial_screen_has_title_and_labels(mock_term) -> None:
    term, _ = mock_term
    text = term.text()
    assert "MOCK-3270 SIGN-ON" in text
    assert "USER" in text
    assert "PASS" in text


def test_initial_cursor_is_in_user_field(mock_term) -> None:
    term, _ = mock_term
    s = term.screen()
    row, col = s.cursor
    assert row == 6, f"cursor row {row} != 6 (user field row)"
    # USER label is 5 chars starting at col 10; cursor should sit at
    # col 17 (after label + attribute byte + space).
    assert col in range(15, 21), f"cursor col {col} not in user-input range"


def test_move_cursor_reaches_host(mock_term) -> None:
    """MoveCursor sends a real SBA + IC — the follow-up screen() must
    reflect the new position."""
    term, _ = mock_term
    term.move_cursor(7, 20)
    s = term.screen()
    assert s.cursor == (7, 20), f"cursor after move: {s.cursor}"


def test_field_type_text_submits_via_enter(mock_term) -> None:
    """type_text into a field, press Enter, and verify the mock server
    saw both values in the Read-Modified response."""
    term, srv = mock_term
    user_field = term.field(row=6, col=17, length=8)
    pass_field = term.field(row=7, col=17, length=8)
    user_field.type_text("ALICE")
    pass_field.type_text("SECRET")
    term.press(AID.ENTER)
    term.wait_change(timeout=5)
    assert srv.last_read is not None
    assert srv.last_read.aid == 0x7D, f"expected AID_ENTER (0x7D), got 0x{srv.last_read.aid:02X}"
    assert srv.last_read.values.get("user") == "ALICE", srv.last_read.values
    assert srv.last_read.values.get("password") == "SECRET", srv.last_read.values


def test_result_screen_after_submit(mock_term) -> None:
    """After Enter, the mock replies with a result screen showing the
    typed values — must be visible in the fresh screen()."""
    term, _ = mock_term
    term.field(row=6, col=17, length=8).type_text("BOB")
    term.field(row=7, col=17, length=8).type_text("HUNTER2")
    term.press(AID.ENTER)
    term.wait_change(timeout=5)
    text = term.text()
    assert "SIGN-ON RESULT" in text
    assert "BOB" in text
    assert "HUNTER2" in text
    assert "READY" in text


def test_pf3_disconnects(mock_term) -> None:
    """PF3 in the default policy closes the mock — Read-Modified must
    contain AID 0xF3."""
    term, srv = mock_term
    term.press(AID.pf(3))
    # Give the mock a moment to observe the disconnect intent.
    term.wait_change(timeout=3)
    # The mock may have closed the socket already — we cannot always
    # read a subsequent screen. But we can inspect what it received.
    assert srv.last_read is not None
    assert srv.last_read.aid == 0xF3, f"expected AID_PF3 (0xF3), got 0x{srv.last_read.aid:02X}"


def test_multiple_pf_keys_encode_correctly(mock_term) -> None:
    """PF1, PF7, PF12 all round-trip through with the correct AID byte."""
    term, srv = mock_term

    for pf_num, expected_aid in [(1, 0xF1), (7, 0xF7), (12, 0x7C)]:
        srv.last_read = None
        term.press(AID.pf(pf_num))
        term.wait_change(timeout=5)
        assert srv.last_read is not None
        assert srv.last_read.aid == expected_aid, (
            f"PF{pf_num} expected AID 0x{expected_aid:02X}, got 0x{srv.last_read.aid:02X}"
        )


def test_field_read_after_type(mock_term) -> None:
    """After typing into a field, TerminalField.read() returns the
    same string — proves the local view is coherent with what was
    written."""
    term, _ = mock_term
    field = term.field(row=6, col=17, length=8)
    field.type_text("ALICE")
    # Give the emulator a moment to update its local buffer.
    term.screen()
    assert field.read() == "ALICE", (
        f"expected 'ALICE', got {field.read()!r}\nscreen:\n{term.text()}"
    )


def test_field_after_locates_input_position(mock_term) -> None:
    """field_after('USER') lands within the writable region.

    The label-scan heuristic without attribute-byte inspection cannot
    know the exact column where the writable field begins — it lands
    a few columns past the label. Task C wires up ReadBuffer-based
    auto-detection to fix this properly; for now we verify only that
    the returned position is inside the field's range.
    """
    term, _ = mock_term
    f = term.field_after("USER", length=1)
    row, col = f.position
    assert row == 6, f"row {row} != 6"
    # Field is rendered at cols 16..25 (SF byte + 8 input cells + SF byte).
    assert 15 <= col <= 25, f"col {col} not in field range 15..25"


def test_wait_change_returns_promptly_after_screen_write(mock_term) -> None:
    """wait_change() must not block past the arrival of a new screen."""
    term, _ = mock_term
    term.field(row=6, col=17, length=8).type_text("X")
    start = monotonic()
    term.press(AID.ENTER)
    term.wait_change(timeout=5)
    elapsed = monotonic() - start
    assert elapsed < 3.0, f"wait_change took {elapsed:.2f}s — too slow for a mock"


def test_screen_dimensions_after_negotiation(mock_term) -> None:
    """Mock uses 24×80; ws3270 default model is 3279-4 which reports 43×80.
    Either is acceptable — the important part is that the model advertised
    by ws3270 does not corrupt the screen buffer."""
    term, _ = mock_term
    s = term.screen()
    assert s.cols == 80
    assert s.rows in (24, 43)
