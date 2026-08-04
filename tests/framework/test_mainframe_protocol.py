"""Headless protocol-decoding tests for the mainframe backends."""

from __future__ import annotations

import pytest

from dolphin_desktop._mainframe import (
    _EHLLAPI_PF_MNEMONICS,
    MainframeError,
    _S3270Backend,
    _Tn5250Backend,
)

# s3270 ReadBuffer(Ascii) parsing


class _FakeS3270(_S3270Backend):
    """_S3270Backend with the subprocess replaced by a canned ReadBuffer."""

    def __init__(self, data_lines: list[str], cols: int = 20) -> None:
        self._lines = data_lines
        self._cols = cols

    def _exec(self, command: str, *, raise_on_error: bool = True):
        status = " ".join(
            [
                "U",
                "F",
                "U",
                "C",
                "I",
                "4",
                str(len(self._lines)),
                str(self._cols),
                "0",
                "0",
                "0x0",
                "-",
            ]
        )
        return list(self._lines), status


def test_read_fields_accepts_multi_attribute_sf_tokens() -> None:
    """Color hosts emit SF(c0=xx,42=yy); the field must not be dropped."""
    backend = _FakeS3270(["SF(c0=e0) 00 00 SF(c0=4d,42=f4) 00 00"])
    fields = backend.read_fields()
    assert [f.attr_byte for f in fields] == [0xE0, 0x4D]


def test_read_fields_sa_token_consumes_no_buffer_position() -> None:
    """SA(...) sets a character attribute in place — it occupies no cell."""
    with_sa = _FakeS3270(["SF(c0=e0) 00 00 SA(42=f4) SF(c0=4d,42=f4) 00 00"]).read_fields()
    without_sa = _FakeS3270(["SF(c0=e0) 00 00 SF(c0=4d,42=f4) 00 00"]).read_fields()
    assert [(f.row, f.col) for f in with_sa] == [(f.row, f.col) for f in without_sa]
    assert [(f.row, f.col) for f in with_sa] == [(1, 2), (1, 5)]


def test_read_fields_decodes_attribute_bits() -> None:
    fields = _FakeS3270(["SF(c0=e0) 00 00 SF(c0=4d,42=f4) 00 00"]).read_fields()
    protected, password = fields
    assert protected.protected is True
    assert protected.hidden is False
    assert password.protected is False
    assert password.hidden is True
    assert password.modified is True


def test_read_fields_lengths_span_to_next_start_field() -> None:
    fields = _FakeS3270(["SF(c0=e0) 00 00 SF(c0=4d,42=f4) 00 00"]).read_fields()
    assert fields[0].length == 2
    # One row of 20 cells — the trailing field runs to end-of-buffer.
    assert fields[1].length == 20 - 4


# TN5250 Write-To-Display field decoding


def _sf(
    *,
    ffw: bytes = b"\x40\x00",
    fcws: bytes = b"",
    attr: int = 0x20,
    length: int = 4,
) -> bytes:
    """Build a 5250 Start Field order.

    RFC 1205 §5.2.4.3 order: ``1D · FFW(2) · [FCW(2)…] · attribute · LL(2)``.
    """
    return b"\x1d" + ffw + fcws + bytes([attr]) + bytes([length >> 8, length & 0xFF])


def _sba(row: int, col: int) -> bytes:
    return bytes([0x11, row, col])


def _wtd(*orders: bytes, row: int = 5, col: int = 10) -> bytes:
    """Build a WTD stream: 2 CC bytes, an SBA, then the given orders."""
    return b"\x00\x00" + _sba(row, col) + b"".join(orders)


def _wtd_record(*orders: bytes) -> bytes:
    """Wrap *orders* in a full 5250 data record the backend can dispatch.

    10-byte GDS header, then ESC (0x04) + WriteToDisplay (0x11) + 2 CC bytes.
    """
    body = b"\x04\x11\x00\x00" + b"".join(orders)
    return bytes([0x00, len(body) + 10, 0x12, 0xA0, 0, 0, 0, 0, 0, 0]) + body


def _backend(stream: bytes) -> _Tn5250Backend:
    backend = _Tn5250Backend()
    backend._parse_wtd(stream, 0)
    return backend


def _parse(stream: bytes):
    return _backend(stream)._fields


def test_tn5250_bypass_bit_marks_field_protected() -> None:
    """FFW top bits are B'01', so bypass lives at 0x20 of byte 0."""
    # 0x40 = plain FFW, 0x60 = FFW + bypass. Attribute 0x20 = green.
    unprotected, protected = _parse(_wtd(_sf(), _sf(ffw=b"\x60\x00")))
    assert unprotected.protected is False
    assert protected.protected is True


def test_tn5250_numeric_shift_read_from_low_bits_of_ffw_byte0() -> None:
    alpha, numeric_only, signed = _parse(_wtd(_sf(), _sf(ffw=b"\x43\x00"), _sf(ffw=b"\x47\x00")))
    assert alpha.numeric is False
    assert numeric_only.numeric is True
    assert signed.numeric is True


def test_tn5250_nondisplay_attribute_marks_password_field() -> None:
    """0x27 is the common 5250 password attribute — nondisplay is attr&0x07."""
    visible, hidden = _parse(_wtd(_sf(), _sf(attr=0x27)))
    assert visible.hidden is False
    assert hidden.hidden is True


def test_tn5250_fcw_pairs_are_skipped_without_column_drift() -> None:
    """The attribute byte is the one matching (b & 0xE0) == 0x20."""
    plain = _parse(_wtd(_sf(attr=0x27)))
    with_fcws = _parse(_wtd(_sf(fcws=b"\x81\x02\x85\x06", attr=0x27)))
    assert len(with_fcws) == 1
    assert with_fcws[0].hidden is True
    assert (with_fcws[0].row, with_fcws[0].col) == (plain[0].row, plain[0].col)


def test_tn5250_field_starts_one_cell_after_the_attribute_byte() -> None:
    (field,) = _parse(_wtd(_sf(), row=5, col=10))
    assert (field.row, field.col) == (5, 11)


def test_tn5250_field_length_comes_from_the_order() -> None:
    """The SF order carries LL — the end-of-row guess is only a fallback."""
    (field,) = _parse(_wtd(_sf(length=10), row=5, col=10))
    assert field.length == 10


def test_tn5250_length_bytes_are_consumed_not_painted_as_content() -> None:
    """LL_lo of a 40-char field is 0x28, which the order dispatcher would
    otherwise take for an inline display attribute: it blanks a cell and
    advances the column, drifting everything the host draws afterwards."""
    backend = _backend(_wtd(_sf(length=40), b"\xc8\xc5\xd3\xd3\xd6", row=5, col=10))
    (field,) = backend._fields
    assert field.length == 40
    # Attribute byte at col 10, field data from col 11 — "HELLO" in cp037.
    assert backend._screen[4][10] == "H"
    assert "".join(backend._screen[4])[10:15] == "HELLO"


def test_tn5250_long_field_length_does_not_paint_an_ebcdic_character() -> None:
    """LL for a field of 200 is 00 C8 — 0xC8 is EBCDIC 'H', a data byte."""
    backend = _backend(_wtd(_sf(length=200), row=5, col=10))
    (field,) = backend._fields
    assert field.length == 200
    assert "".join(backend._screen[4]).strip() == ""


def test_tn5250_sf_without_an_ffw_creates_no_input_field() -> None:
    """SF carrying only an attribute marks a display-only zone.

    tn5250_session_start_of_field builds a field only when the FFW was
    present (its ``input_field`` flag); with ffw_hi defaulted to 0 the
    bypass bit reads clear, so the zone came back as a writable field and
    field_after() would type into a protected region — keyboard locked.
    """
    assert _parse(_wtd(_sf(ffw=b""))) == []


def test_tn5250_ffw_less_sf_still_consumes_its_attribute_and_length() -> None:
    """Only the field creation is conditional — the bytes are consumed either
    way, or the LL would be painted back onto the screen as content."""
    (field,) = _parse(_wtd(_sf(ffw=b"", length=4), _sf(), row=5, col=10))
    assert (field.row, field.col) == (5, 12)


def test_tn5250_field_length_cannot_run_past_the_end_of_the_screen() -> None:
    """A corrupt LL of 0xFFFF makes TerminalField.type_text(clear=True) send
    65535 spaces to the host."""
    (bottom,) = _parse(_wtd(_sf(length=0xFFFF), row=24, col=70))
    assert bottom.length == 10  # cols 71..80
    (top,) = _parse(_wtd(_sf(length=0xFFFF), row=1, col=1))
    assert top.length == 24 * 80 - 1


def test_tn5250_truncated_fcw_does_not_overshoot_the_record() -> None:
    """An FCW is two bytes; a record cut after the first must not skip past
    the end and take a byte of the next command with it."""
    stream = _wtd(b"\x1d\x40\x00\x81", row=5, col=10)
    backend = _Tn5250Backend()
    assert backend._parse_wtd(stream, 0) <= len(stream)


def test_tn5250_write_to_display_record_lays_out_a_sign_on_screen() -> None:
    """Full record: GDS header + ESC/WTD + SBA/SF/text orders."""
    backend = _Tn5250Backend()
    backend._process_record(
        _wtd_record(
            _sba(6, 40),
            "USER".encode("cp037"),
            _sba(6, 52),
            _sf(length=10),
            _sba(7, 40),
            "PASSWORD".encode("cp037"),
            _sba(7, 52),
            _sf(attr=0x27, length=10),
        )
    )
    user, password = backend._fields
    assert (user.row, user.col, user.length) == (6, 53, 10)
    assert (password.row, password.col, password.length) == (7, 53, 10)
    assert password.hidden is True
    assert "".join(backend._screen[5])[39:43] == "USER"
    assert "".join(backend._screen[6])[39:47] == "PASSWORD"


# s3270 backend guards


def test_s3270_refuses_every_spelling_of_a_5250_session() -> None:
    """x3270 has no 5250 support at all; "5250E" / "TN5250" must not slip by."""
    for session_type in ("5250", "5250E", "tn5250", "TN5250"):
        backend = _S3270Backend.__new__(_S3270Backend)
        with pytest.raises(MainframeError, match="TN3270 only"):
            backend.connect("host", 23, session_type=session_type)


# EHLLAPI SendKey mnemonics


def test_ehllapi_pf_mnemonic_table() -> None:
    assert _EHLLAPI_PF_MNEMONICS[1] == "@1"
    assert _EHLLAPI_PF_MNEMONICS[9] == "@9"
    assert _EHLLAPI_PF_MNEMONICS[10] == "@a"
    assert _EHLLAPI_PF_MNEMONICS[12] == "@c"
    assert _EHLLAPI_PF_MNEMONICS[13] == "@d"
    assert _EHLLAPI_PF_MNEMONICS[24] == "@o"
    assert set(_EHLLAPI_PF_MNEMONICS) == set(range(1, 25))
    # Every mnemonic is distinct and lowercase — the uppercase letters
    # mean unrelated keys in the EHLLAPI table.
    values = list(_EHLLAPI_PF_MNEMONICS.values())
    assert len(set(values)) == 24
    assert all(v == v.lower() for v in values)


# 5250 command codes are not guessable from one another


def _command_record(cmd: int, *body: bytes, cc: bool = True) -> bytes:
    """A data record carrying a single 5250 command.

    *cc* controls the two control-character bytes. They are NOT universal:
    Read Immediate (0x72) and Read Screen Immediate (0x62) carry none, and a
    helper that emitted them unconditionally would hide a parser that skips
    two bytes too many.
    """
    payload = bytes([0x04, cmd]) + (bytes(2) if cc else b"") + b"".join(body)
    return bytes([0x00, len(payload) + 10, 0x12, 0xA0, 0, 0, 0, 0, 0, 0]) + payload


def _backend_with_a_field() -> _Tn5250Backend:
    backend = _Tn5250Backend()
    backend._process_record(_wtd_record(_sba(5, 10), _sf(length=8)))
    assert backend._fields, "fixture must start with a field to retire"
    return backend


def test_clear_format_table_is_0x50_and_retires_the_fields() -> None:
    """0x50 was classified as a read command, so nothing but Clear Unit ever
    reset _fields — a repaint left the previous panel's fields in place and
    field_after() then matched a stale entry."""
    backend = _backend_with_a_field()
    backend._process_record(_command_record(0x50))
    assert backend._fields == []


def test_0x41_is_not_a_command_and_does_not_clear_the_format_table() -> None:
    backend = _backend_with_a_field()
    backend._process_record(_command_record(0x41))
    assert backend._fields, "0x41 is not a 5250 command — it must not retire fields"


def test_clear_unit_wipes_the_screen_as_well_as_the_fields() -> None:
    backend = _backend_with_a_field()
    backend._process_record(_command_record(0x40))
    assert backend._fields == []
    assert all(cell == " " for row in backend._screen for cell in row)


@pytest.mark.parametrize(
    ("cmd", "cc"),
    [(0x42, True), (0x52, True), (0x82, True), (0x62, False), (0x72, False)],
)
def test_a_read_command_leaves_the_format_table_alone(cmd: int, cc: bool) -> None:
    backend = _backend_with_a_field()
    backend._process_record(_command_record(cmd, cc=cc))
    assert backend._fields, f"read command 0x{cmd:02X} must not retire fields"


@pytest.mark.parametrize("cmd", [0x62, 0x72])
def test_a_bare_read_command_does_not_swallow_the_next_command(cmd: int) -> None:
    """Read Immediate and Read Screen Immediate carry no control characters.

    Skipping two bytes for them consumed the following ESC + command, so a
    Write-To-Display sharing the record was lost and read_screen() kept
    returning the previous panel.
    """
    read = bytes([0x04, cmd])
    wtd = bytes([0x04, 0x11, 0x00, 0x00]) + _sba(1, 1) + "AB".encode("cp037")
    payload = read + wtd
    record = bytes([0x00, len(payload) + 10, 0x12, 0xA0, 0, 0, 0, 0, 0, 0]) + payload

    backend = _Tn5250Backend()
    backend._process_record(record)
    assert "".join(backend._screen[0][:2]) == "AB"


# The input record's byte order is what the host actually reads


def _sent_input_record(backend: _Tn5250Backend, aid: str) -> bytes:
    captured: dict[str, bytes] = {}

    class _Sock:
        def sendall(self, wire: bytes) -> None:
            captured["wire"] = wire

    backend._sock = _Sock()
    backend.send_aid(aid)
    return captured["wire"]


def test_input_record_sends_cursor_before_the_aid() -> None:
    """Reference tn5250 writes row, column, AID. Emitting the AID first made
    byte 0 the AID code, which the host reads as a cursor row — 0xF1 for
    Enter is row 241, so IBM i never answered the record."""
    backend = _Tn5250Backend()
    backend.move_cursor(7, 53)
    wire = _sent_input_record(backend, "Enter")
    payload = wire[10:]
    assert payload[0] == 7
    assert payload[1] == 53
    assert payload[2] == 0xF1


def test_an_unencodable_cursor_is_reported_not_a_bare_value_error(monkeypatch) -> None:
    backend = _Tn5250Backend()
    backend.move_cursor(1, 300)
    backend._sock = object()
    with pytest.raises(MainframeError, match="cannot be encoded"):
        backend.send_aid("Enter")


@pytest.mark.parametrize("key", ["PA1", "PA2", "PA3"])
def test_5250_refuses_the_3270_attention_keys(key: str) -> None:
    """Refusing beats substituting "the nearest" PF: press("PA1") would send
    PF1 — Help on IBM i — and report success."""
    backend = _Tn5250Backend()
    with pytest.raises(MainframeError, match="3270-only"):
        backend.send_aid(key)
