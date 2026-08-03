"""Field auto-detection via ReadBuffer.

Verifies that ``MainframeTerminal.fields()`` returns each Start-Field
range with the correct row/col/length and protected/hidden flags, and
that ``field_after(label)`` — with ``length=None`` — picks the writable
input that immediately follows the label.
"""

from __future__ import annotations

from dolphin_desktop import AID, FieldInfo


def test_fields_returns_populated_list(mock_term) -> None:
    term, _ = mock_term
    fields = term.fields()
    assert len(fields) >= 3, f"expected at least title + 2 input fields, got {len(fields)}"
    for f in fields:
        assert isinstance(f, FieldInfo)
        assert f.row >= 1
        assert f.col >= 1
        assert f.length >= 0


def test_field_list_marks_writable_slots(mock_term) -> None:
    """The mock ships two writable fields (USER, PASSWORD). Exactly two
    fields in the returned list must be unprotected."""
    term, _ = mock_term
    writable = [f for f in term.fields() if f.writable]
    assert len(writable) == 2, f"expected 2 writable fields, got {[repr(f) for f in writable]}"


def test_password_field_is_hidden(mock_term) -> None:
    """Mock sets FA_HIDDEN (0x0C) on the PASS field — the parser must
    surface that as ``hidden=True``."""
    term, _ = mock_term
    writable = [f for f in term.fields() if f.writable]
    hidden = [f for f in writable if f.hidden]
    assert len(hidden) == 1, [repr(f) for f in writable]


def test_writable_field_length_is_eight(mock_term) -> None:
    """Mock declares length=8 for both writable fields — the parser
    must compute the same length from SF boundary distance."""
    term, _ = mock_term
    writable = [f for f in term.fields() if f.writable]
    for f in writable:
        assert f.length == 8, f"expected length=8, got {f.length} for {f!r}"


def test_field_after_uses_autodetection(mock_term) -> None:
    """field_after with no explicit length lands on the SF start of the
    writable field — no more heuristic drift into the interior."""
    term, _srv = mock_term
    f = term.field_after("USER")  # note: no length=
    row, col = f.position
    assert row == 6
    # Writable field starts at col 17 (SF at col 16). Auto-detection
    # must land there, not somewhere past.
    assert col == 17, f"expected col=17, got {col}"
    assert f.length == 8


def test_field_after_autodetect_then_type_round_trips(mock_term) -> None:
    """Combine auto-detected field_after with type_text + Enter — the
    mock must observe the value on the correct field."""
    term, srv = mock_term
    term.field_after("USER").type_text("DIANE")
    term.field_after("PASS").type_text("PWD123")
    term.press(AID.ENTER)
    term.wait_change(timeout=5)
    assert srv.last_read is not None
    assert srv.last_read.values.get("user") == "DIANE", srv.last_read.values
    assert srv.last_read.values.get("password") == "PWD123", srv.last_read.values
