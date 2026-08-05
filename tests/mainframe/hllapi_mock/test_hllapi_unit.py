"""HLLAPI backend unit tests via injected Python fake.

We can't test the ctypes DLL binding without a real emulator (PCOMM,
Attachmate, Rocket) installed. What we CAN unit-test is that the
backend calls its DLL entry-point with the correct HLLAPI function
codes and argument encoding for every public method. That way when
someone plugs in a real DLL, the wire calls line up with what the
vendor documentation specifies.

Uses ``_HLLAPIBackend(_hllapi_fn=fake)`` — the private test-injection
seam that skips ctypes.WinDLL resolution.
"""

from __future__ import annotations

import ctypes

from dolphin_desktop._mainframe import (  # type: ignore[attr-defined]
    _EHLLAPI_CONNECT_PS,
    _EHLLAPI_COPY_PS_TO_STR,
    _EHLLAPI_DISCONNECT_PS,
    _EHLLAPI_QUERY_CURSOR,
    _EHLLAPI_QUERY_SESSION_STATUS,
    _EHLLAPI_SENDKEY,
    _EHLLAPI_SET_CURSOR,
    _EHLLAPI_WAIT,
    _HLLAPIBackend,
)

_HLLAPI_PROTO = ctypes.WINFUNCTYPE(
    None,
    ctypes.POINTER(ctypes.c_ushort),
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.POINTER(ctypes.c_ushort),
    ctypes.POINTER(ctypes.c_ushort),
)


class FakeHLLAPI:
    """Recorder + script for HLLAPI calls in test mode.

    Behaves as the DLL entry-point does: takes 4 ctypes pointers, reads
    the func code, records the call, then writes a scripted response
    back into the buffers before returning. Test cases inspect the
    ``calls`` list and pre-seed ``responses`` for each function code.

    Wraps a Python callable in ``ctypes.WINFUNCTYPE`` so the ctypes
    machinery converts POINTER args to real ``c_ushort`` objects the
    callable can subscript (``fn_ref[0]``).
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        # Per-func default return: (rc, data_bytes, ret_length_or_None).
        # None for ret_length means "keep buffer length unchanged".
        self.responses: dict[int, tuple[int, bytes, int | None]] = {}
        self._fn = _HLLAPI_PROTO(self._callback)

    def script(
        self, func: int, rc: int = 0, data: bytes = b"", ret_length: int | None = None
    ) -> None:
        self.responses[func] = (rc, data, ret_length)

    def _callback(self, fn_ref, data_ptr, len_ref, rc_ref) -> None:
        func = fn_ref[0]
        buf_len = len_ref[0]
        # Length-bounded view of the caller's buffer.
        raw = ctypes.string_at(data_ptr, buf_len) if buf_len > 0 else b""
        self.calls.append(
            {
                "func": func,
                "data_in": raw,
                "length_in": buf_len,
                # The 4th parameter is the PS position on the way in and
                # the return code on the way out.
                "ps_in": rc_ref[0],
            }
        )
        rc, out_data, ret_len = self.responses.get(func, (0, b"", None))
        if out_data:
            n = min(len(out_data), max(buf_len, len(out_data)))
            ctypes.memmove(data_ptr, out_data, n)
        rc_ref[0] = rc
        if ret_len is not None:
            len_ref[0] = ret_len


def _make_backend() -> tuple[_HLLAPIBackend, FakeHLLAPI]:
    fake = FakeHLLAPI()
    be = _HLLAPIBackend(session_id="A", _hllapi_fn=fake._fn)
    return be, fake


# --------------------------------------------------------------------------- #
# connect / disconnect                                                         #
# --------------------------------------------------------------------------- #


def test_connect_issues_ConnectPS_with_session_id() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_CONNECT_PS, rc=0)
    fake.script(_EHLLAPI_QUERY_SESSION_STATUS, rc=0, data=b"", ret_length=0)
    be.connect("ignored.host", 23, session_type="3270")
    connects = [c for c in fake.calls if c["func"] == _EHLLAPI_CONNECT_PS]
    assert len(connects) == 1
    assert connects[0]["data_in"].startswith(b"A")


def test_connect_nonzero_rc_raises() -> None:
    import pytest

    from dolphin_desktop import MainframeError

    be, fake = _make_backend()
    fake.script(_EHLLAPI_CONNECT_PS, rc=1)
    with pytest.raises(MainframeError, match="ConnectPS failed"):
        be.connect("", 23, session_type="3270")


def test_disconnect_issues_DisconnectPS_when_connected() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_CONNECT_PS, rc=0)
    fake.script(_EHLLAPI_QUERY_SESSION_STATUS, rc=0, data=b"", ret_length=0)
    be.connect("", 23, session_type="3270")
    fake.calls.clear()
    be.disconnect()
    disc = [c for c in fake.calls if c["func"] == _EHLLAPI_DISCONNECT_PS]
    assert len(disc) == 1
    assert disc[0]["data_in"].startswith(b"A")


def test_disconnect_noop_when_not_connected() -> None:
    be, fake = _make_backend()
    be.disconnect()
    assert fake.calls == []


# --------------------------------------------------------------------------- #
# input                                                                        #
# --------------------------------------------------------------------------- #


def test_send_string_writes_data_via_SENDKEY() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_SENDKEY, rc=0)
    be.send_string("HELLO")
    calls = [c for c in fake.calls if c["func"] == _EHLLAPI_SENDKEY]
    assert len(calls) == 1
    assert calls[0]["data_in"].startswith(b"HELLO")
    assert calls[0]["length_in"] == 5


def test_send_aid_enter_sends_at_E() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_SENDKEY, rc=0)
    be.send_aid("Enter")
    call = [c for c in fake.calls if c["func"] == _EHLLAPI_SENDKEY][-1]
    assert call["data_in"].startswith(b"@E")


def test_send_aid_pf3_sends_at_3() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_SENDKEY, rc=0)
    be.send_aid("PF3")
    call = [c for c in fake.calls if c["func"] == _EHLLAPI_SENDKEY][-1]
    # PF1..PF9 are the digit mnemonics; @c is PF12.
    assert call["data_in"].startswith(b"@3"), call["data_in"]


def test_send_aid_pf_mnemonic_table_matches_ehllapi_spec() -> None:
    """Lock the whole PF1-PF24 table — @1..@9 then @a..@o."""
    expected = {
        1: b"@1",
        2: b"@2",
        3: b"@3",
        4: b"@4",
        5: b"@5",
        6: b"@6",
        7: b"@7",
        8: b"@8",
        9: b"@9",
        10: b"@a",
        11: b"@b",
        12: b"@c",
        13: b"@d",
        14: b"@e",
        15: b"@f",
        16: b"@g",
        17: b"@h",
        18: b"@i",
        19: b"@j",
        20: b"@k",
        21: b"@l",
        22: b"@m",
        23: b"@n",
        24: b"@o",
    }
    be, fake = _make_backend()
    fake.script(_EHLLAPI_SENDKEY, rc=0)
    for n, mnemonic in expected.items():
        be.send_aid(f"PF{n}")
        call = [c for c in fake.calls if c["func"] == _EHLLAPI_SENDKEY][-1]
        assert call["data_in"].startswith(mnemonic), (n, call["data_in"])


def test_send_aid_pf_out_of_range_raises() -> None:
    import pytest

    from dolphin_desktop import MainframeError

    be, _ = _make_backend()
    with pytest.raises(MainframeError, match="invalid PF key"):
        be.send_aid("PF25")


def test_send_aid_pa_mnemonics() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_SENDKEY, rc=0)
    for n, mnemonic in ((1, b"@x"), (2, b"@y"), (3, b"@z")):
        be.send_aid(f"PA{n}")
        call = [c for c in fake.calls if c["func"] == _EHLLAPI_SENDKEY][-1]
        assert call["data_in"].startswith(mnemonic), (n, call["data_in"])


def test_send_string_doubles_literal_at_sign() -> None:
    """SendKey reads a bare @ as a mnemonic escape — it must be doubled."""
    be, fake = _make_backend()
    fake.script(_EHLLAPI_SENDKEY, rc=0)
    be.send_string("user@host.com")
    call = [c for c in fake.calls if c["func"] == _EHLLAPI_SENDKEY][-1]
    assert call["data_in"].startswith(b"user@@host.com"), call["data_in"]
    assert call["length_in"] == len("user@@host.com")


def test_send_aid_pa1_sends_at_x() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_SENDKEY, rc=0)
    be.send_aid("PA1")
    call = [c for c in fake.calls if c["func"] == _EHLLAPI_SENDKEY][-1]
    assert call["data_in"].startswith(b"@x"), call["data_in"]


def test_send_aid_clear_sends_at_C_uppercase() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_SENDKEY, rc=0)
    be.send_aid("Clear")
    call = [c for c in fake.calls if c["func"] == _EHLLAPI_SENDKEY][-1]
    assert call["data_in"].startswith(b"@C"), call["data_in"]


def test_send_aid_unknown_raises() -> None:
    import pytest

    from dolphin_desktop import MainframeError

    be, _ = _make_backend()
    with pytest.raises(MainframeError, match="unknown AID"):
        be.send_aid("MYSTERY")


# --------------------------------------------------------------------------- #
# cursor + screen read                                                         #
# --------------------------------------------------------------------------- #


def test_move_cursor_uses_SET_CURSOR_with_offset() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_SET_CURSOR, rc=0)
    be.move_cursor(6, 20)  # 1-indexed
    # offset = (6-1)*80 + 20 = 420
    call = [c for c in fake.calls if c["func"] == _EHLLAPI_SET_CURSOR][-1]
    # Set Cursor takes the target position in the 4th parameter, not in
    # the data string.
    assert call["ps_in"] == 420, call


def test_read_screen_starts_copy_at_ps_position_one() -> None:
    be, fake = _make_backend()
    fill = b"A" * (24 * 80)
    fake.script(_EHLLAPI_COPY_PS_TO_STR, rc=0, data=fill, ret_length=len(fill))
    fake.script(_EHLLAPI_QUERY_CURSOR, rc=0, ret_length=1)
    be.read_screen()
    call = [c for c in fake.calls if c["func"] == _EHLLAPI_COPY_PS_TO_STR][-1]
    # Copy PS to String needs the 1-based copy-start position, not 0.
    assert call["ps_in"] == 1, call


def test_connect_reads_binary_rows_cols_from_session_status() -> None:
    """Mod-5 (27x132) must be picked up from the packed status struct."""
    status = (
        b"A"  # [0]     short session id
        + b"SESSION1"  # [1:9]   long name
        + b"D"  # [9]     display session
        + b"\x00"  # [10]    characteristics
        + (27).to_bytes(2, "little")  # [11:13] rows
        + (132).to_bytes(2, "little")  # [13:15] columns
        + (37).to_bytes(2, "little")  # [15:17] host code page
        + b"\x00"  # [17]    reserved
    )
    assert len(status) == 18
    be, fake = _make_backend()
    fake.script(_EHLLAPI_CONNECT_PS, rc=0)
    fake.script(_EHLLAPI_QUERY_SESSION_STATUS, rc=0, data=status)
    be.connect("", 23, session_type="3270")
    assert (be._rows, be._cols) == (27, 132)

    fake.script(_EHLLAPI_COPY_PS_TO_STR, rc=0, data=b"B" * (27 * 132), ret_length=27 * 132)
    fake.script(_EHLLAPI_QUERY_CURSOR, rc=0, ret_length=1)
    lines, _ = be.read_screen()
    assert len(lines) == 27
    assert all(len(line) == 132 for line in lines)


def test_connect_keeps_default_size_on_implausible_status() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_CONNECT_PS, rc=0)
    fake.script(_EHLLAPI_QUERY_SESSION_STATUS, rc=0, data=b"\xff" * 18)
    be.connect("", 23, session_type="3270")
    assert (be._rows, be._cols) == (24, 80)


def test_read_screen_calls_COPY_PS_TO_STR() -> None:
    be, fake = _make_backend()
    # Fill screen buffer with 'A' bytes (1920 chars = 24x80).
    fill = b"A" * (24 * 80)
    fake.script(_EHLLAPI_COPY_PS_TO_STR, rc=0, data=fill, ret_length=len(fill))
    fake.script(_EHLLAPI_QUERY_CURSOR, rc=0, ret_length=1)  # offset 1 → row 1, col 1
    lines, cursor = be.read_screen()
    assert len(lines) == 24
    assert all(line == "A" * 80 for line in lines)
    assert cursor == (1, 1)


def test_read_screen_query_cursor_offset_maps_to_row_col() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_COPY_PS_TO_STR, rc=0, data=b" " * 1920, ret_length=1920)
    # offset 161 → (161-1)/80 + 1 = 3, (161-1)%80 + 1 = 1
    fake.script(_EHLLAPI_QUERY_CURSOR, rc=0, ret_length=161)
    _, cursor = be.read_screen()
    assert cursor == (3, 1), cursor


# --------------------------------------------------------------------------- #
# keyboard status                                                              #
# --------------------------------------------------------------------------- #


def test_is_keyboard_locked_true_when_WAIT_returns_nonzero() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_WAIT, rc=5)  # keyboard inhibited
    assert be.is_keyboard_locked() is True


def test_is_keyboard_locked_false_when_WAIT_returns_zero() -> None:
    be, fake = _make_backend()
    fake.script(_EHLLAPI_WAIT, rc=0)
    assert be.is_keyboard_locked() is False
