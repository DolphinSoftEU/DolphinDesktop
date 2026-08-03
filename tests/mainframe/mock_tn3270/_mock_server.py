"""Minimal TN3270 mock server for round-trip integration testing.

Implements just enough of the RFC 1041 / RFC 1576 TN3270 telnet dialect
and the 3270 data stream to let a real ``ws3270`` client:

* Negotiate to TN3270 mode (BINARY + EOR + TERMINAL-TYPE options).
* Receive a formatted 24×80 screen with three fields (header + two
  writable input fields).
* Read Modified back after Enter / PF, extracting AID and field text.
* Receive a follow-up screen so the client sees a state transition.

This is a *mock*: it does not implement every 3270 order (no partitions,
no scroll, no field attributes beyond protected/unprotected). It exists
to prove that the dolphin_desktop mainframe wrapper drives a real 3270
session end-to-end — positional MoveCursor, TerminalField.type_text,
AID keys, and wait_change all work as advertised.

Reference material used:

* IBM 3270 Data Stream Programmer's Reference (GA23-0059)
* RFC 1041 — Telnet 3270 regime option
* RFC 1576 — TN3270 current practices
* x3270 source code (ctlr.c for command dispatch)

The mock ships as a self-contained Python module — no non-stdlib
dependencies — so any test can spin it up with ``MockTN3270Server()``.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from typing import Any

# --------------------------------------------------------------------------- #
# Telnet negotiation constants                                                 #
# --------------------------------------------------------------------------- #

IAC = 0xFF
DONT = 0xFE
DO = 0xFD
WONT = 0xFC
WILL = 0xFB
SB = 0xFA
SE = 0xF0
EOR = 0xEF

OPT_BINARY = 0x00
OPT_EOR = 0x19
OPT_TERMTYPE = 0x18
OPT_TN3270E = 0x28

TT_SEND = 0x01
TT_IS = 0x00

# --------------------------------------------------------------------------- #
# 3270 data-stream constants                                                   #
# --------------------------------------------------------------------------- #

# Outbound commands (server → client)
CMD_WRITE = 0xF1
CMD_ERASE_WRITE = 0xF5
CMD_ERASE_WRITE_ALT = 0x7E
CMD_ERASE_ALL_UNPROTECTED = 0x6F
CMD_READ_MODIFIED = 0xF6
CMD_READ_BUFFER = 0xF2

# 3270 orders
ORDER_SBA = 0x11  # Set Buffer Address
ORDER_SF = 0x1D  # Start Field
ORDER_IC = 0x13  # Insert Cursor
ORDER_PT = 0x05  # Program Tab
ORDER_RA = 0x3C  # Repeat to Address
ORDER_EUA = 0x12  # Erase Unprotected to Address

# WCC (Write Control Character) bits
# 0x40 (bit 1) = reset partition
# 0x20 (bit 2) = start printer
# 0x10 (bit 3) = sound alarm
# 0x02 (bit 6) = keyboard restore (unlock)
# 0x01 (bit 7) = reset MDT
WCC_STANDARD = 0xC3  # reset + unlock + reset MDT

# AID (Attention Identifier) — first byte of Read Modified response
AID_ENTER = 0x7D
AID_CLEAR = 0x6D
AID_PA1 = 0x6C
AID_PA2 = 0x6E
AID_PA3 = 0x6B
AID_PF1 = 0xF1
AID_PF2 = 0xF2
AID_PF3 = 0xF3
AID_PF4 = 0xF4
AID_PF5 = 0xF5
AID_PF6 = 0xF6
AID_PF7 = 0xF7
AID_PF8 = 0xF8
AID_PF9 = 0xF9
AID_PF10 = 0x7A
AID_PF11 = 0x7B
AID_PF12 = 0x7C
AID_PF13 = 0xC1
AID_PF14 = 0xC2
AID_PF15 = 0xC3
AID_PF16 = 0xC4
AID_PF17 = 0xC5
AID_PF18 = 0xC6
AID_PF19 = 0xC7
AID_PF20 = 0xC8
AID_PF21 = 0xC9
AID_PF22 = 0x4A
AID_PF23 = 0x4B
AID_PF24 = 0x4C

# Field Attribute bits (SF byte)
# bit 5 = protected
# bit 4 = numeric
# bits 2..3 = display/select intensity
# bit 0 = MDT (modified)
FA_PROTECT = 0x20
FA_UNPROTECT = 0x00
FA_HIDDEN = 0x0C

# --------------------------------------------------------------------------- #
# 12-bit SBA address encoding                                                  #
# --------------------------------------------------------------------------- #

_SBA_TABLE = bytes(
    [
        0x40,
        0xC1,
        0xC2,
        0xC3,
        0xC4,
        0xC5,
        0xC6,
        0xC7,
        0xC8,
        0xC9,
        0x4A,
        0x4B,
        0x4C,
        0x4D,
        0x4E,
        0x4F,
        0x50,
        0xD1,
        0xD2,
        0xD3,
        0xD4,
        0xD5,
        0xD6,
        0xD7,
        0xD8,
        0xD9,
        0x5A,
        0x5B,
        0x5C,
        0x5D,
        0x5E,
        0x5F,
        0x60,
        0x61,
        0xE2,
        0xE3,
        0xE4,
        0xE5,
        0xE6,
        0xE7,
        0xE8,
        0xE9,
        0x6A,
        0x6B,
        0x6C,
        0x6D,
        0x6E,
        0x6F,
        0xF0,
        0xF1,
        0xF2,
        0xF3,
        0xF4,
        0xF5,
        0xF6,
        0xF7,
        0xF8,
        0xF9,
        0x7A,
        0x7B,
        0x7C,
        0x7D,
        0x7E,
        0x7F,
    ]
)
_SBA_REVERSE = {b: i for i, b in enumerate(_SBA_TABLE)}


def _encode_sba(row: int, col: int, cols: int = 80) -> bytes:
    """Encode a 1-indexed (row, col) as a 12-bit SBA byte pair."""
    offset = (row - 1) * cols + (col - 1)
    hi = (offset >> 6) & 0x3F
    lo = offset & 0x3F
    return bytes([_SBA_TABLE[hi], _SBA_TABLE[lo]])


def _decode_sba(hi_byte: int, lo_byte: int, cols: int = 80) -> tuple[int, int]:
    """Decode a 2-byte SBA back to 1-indexed (row, col). Raises on unknown byte."""
    hi = _SBA_REVERSE.get(hi_byte)
    lo = _SBA_REVERSE.get(lo_byte)
    if hi is None or lo is None:
        # 14/16-bit encoding: value stored directly in low 6 bits of each byte
        hi = hi_byte & 0x3F
        lo = lo_byte & 0x3F
    offset = (hi << 6) | lo
    row = offset // cols + 1
    col = offset % cols + 1
    return (row, col)


# --------------------------------------------------------------------------- #
# EBCDIC translation via cp037                                                 #
# --------------------------------------------------------------------------- #


def to_ebcdic(text: str) -> bytes:
    return text.encode("cp037", errors="replace")


def from_ebcdic(data: bytes) -> str:
    return data.decode("cp037", errors="replace")


# --------------------------------------------------------------------------- #
# Screen definition                                                            #
# --------------------------------------------------------------------------- #


class ScreenField:
    """One writable field on a mock screen.

    :attr:`label` is drawn as a protected field to the left of the input.
    :attr:`row`, :attr:`col`, :attr:`length` define the writable slot
    that follows the label. Values submitted by the client come back as
    the ``value`` attribute on the ``ReadModified`` result.
    """

    __slots__ = ("col", "hidden", "label", "length", "name", "row")

    def __init__(
        self,
        name: str,
        label: str,
        row: int,
        col: int,
        length: int,
        *,
        hidden: bool = False,
    ) -> None:
        self.name = name
        self.label = label
        self.row = row
        self.col = col
        self.length = length
        self.hidden = hidden


class ReadModified:
    """Parsed content of a client Read-Modified response."""

    def __init__(self, aid: int, cursor: tuple[int, int], values: dict[str, str]) -> None:
        self.aid = aid
        self.cursor = cursor
        self.values = values

    def __repr__(self) -> str:
        return f"ReadModified(aid=0x{self.aid:02X}, cursor={self.cursor}, values={self.values})"


# --------------------------------------------------------------------------- #
# Screen builder                                                               #
# --------------------------------------------------------------------------- #


def build_screen(
    title: str,
    fields: list[ScreenField],
    *,
    footer: str = "",
    cursor_field: str | None = None,
    cols: int = 80,
) -> bytes:
    """Build a full Erase/Write payload for the given title + fields."""
    payload = bytearray([CMD_ERASE_WRITE, WCC_STANDARD])

    # Title on row 1, col 1 — protected high-intensity
    payload += bytes([ORDER_SBA]) + _encode_sba(1, 1, cols)
    payload += bytes([ORDER_SF, FA_PROTECT | 0x08])  # protected + high intensity
    payload += to_ebcdic(title)

    # Each field
    for f in fields:
        # Label — protected
        payload += bytes([ORDER_SBA]) + _encode_sba(f.row, f.col, cols)
        payload += bytes([ORDER_SF, FA_PROTECT])
        payload += to_ebcdic(f.label)
        # Input field — unprotected, MDT reset
        input_col = f.col + len(f.label) + 1
        payload += bytes([ORDER_SBA]) + _encode_sba(f.row, input_col, cols)
        attr = FA_UNPROTECT
        if f.hidden:
            attr |= FA_HIDDEN
        payload += bytes([ORDER_SF, attr])
        # Reserve the slot with spaces so the write extends the field
        # width; s3270 uses the next SF to bound it.
        payload += to_ebcdic(" " * f.length)
        # End-of-field marker — protected, closes the writable range
        end_col = input_col + f.length + 1
        if end_col <= cols:
            payload += bytes([ORDER_SBA]) + _encode_sba(f.row, end_col, cols)
            payload += bytes([ORDER_SF, FA_PROTECT])

    # Footer text on row 23
    if footer:
        payload += bytes([ORDER_SBA]) + _encode_sba(23, 1, cols)
        payload += bytes([ORDER_SF, FA_PROTECT])
        payload += to_ebcdic(footer)

    # Cursor — either at the named field's input area or at (1,1)
    cursor_target: tuple[int, int]
    if cursor_field is not None:
        for f in fields:
            if f.name == cursor_field:
                cursor_target = (f.row, f.col + len(f.label) + 2)
                break
        else:
            cursor_target = (1, 1)
    else:
        cursor_target = (1, 1)
    payload += bytes([ORDER_SBA]) + _encode_sba(*cursor_target, cols)
    payload += bytes([ORDER_IC])

    return bytes(payload)


def build_result_screen(cols: int = 80, **kv: str) -> bytes:
    """Build a plain read-only screen showing key/value pairs — used as
    the response after Enter is pressed."""
    payload = bytearray([CMD_ERASE_WRITE, WCC_STANDARD])
    payload += bytes([ORDER_SBA]) + _encode_sba(1, 1, cols)
    payload += bytes([ORDER_SF, FA_PROTECT | 0x08])
    payload += to_ebcdic("SIGN-ON RESULT")

    for i, (key, val) in enumerate(kv.items(), start=3):
        payload += bytes([ORDER_SBA]) + _encode_sba(i, 1, cols)
        payload += bytes([ORDER_SF, FA_PROTECT])
        payload += to_ebcdic(f"{key.upper()}: {val}")

    # Add a status line + trailing readable marker.
    payload += bytes([ORDER_SBA]) + _encode_sba(22, 1, cols)
    payload += bytes([ORDER_SF, FA_PROTECT | 0x08])
    payload += to_ebcdic("READY")

    payload += bytes([ORDER_SBA]) + _encode_sba(23, 1, cols)
    payload += bytes([ORDER_IC])
    return bytes(payload)


# --------------------------------------------------------------------------- #
# Read-Modified parser                                                         #
# --------------------------------------------------------------------------- #


def parse_read_modified(data: bytes, fields: list[ScreenField], cols: int = 80) -> ReadModified:
    """Parse a Read-Modified reply: first byte is AID, then 2-byte
    cursor address, then a sequence of (SBA + field-value) blocks."""
    if not data:
        return ReadModified(0, (1, 1), {})
    aid = data[0]
    cursor = (1, 1)
    values: dict[str, str] = {}

    if len(data) >= 3:
        cursor = _decode_sba(data[1], data[2], cols)

    # After the header, the client sends the modified fields as:
    #    SBA(0x11) hi lo <ebcdic bytes>
    # Any 0x11 marks the start of a new field.
    i = 3
    while i < len(data):
        if data[i] == ORDER_SBA and i + 2 < len(data):
            row, col = _decode_sba(data[i + 1], data[i + 2], cols)
            j = i + 3
            end = j
            while end < len(data) and data[end] != ORDER_SBA:
                end += 1
            raw = data[j:end]
            text = from_ebcdic(raw).rstrip("\x00 ")
            # Match to a field by scanning for a field whose input area
            # starts at (row, col).
            for f in fields:
                input_col = f.col + len(f.label) + 2
                if f.row == row and input_col == col:
                    values[f.name] = text
                    break
            i = end
        else:
            i += 1

    return ReadModified(aid, cursor, values)


# --------------------------------------------------------------------------- #
# Server                                                                       #
# --------------------------------------------------------------------------- #


class MockTN3270Server:
    """Threaded TN3270 mock server bound to 127.0.0.1 on an ephemeral port.

    Usage::

        srv = MockTN3270Server(fields=[...])
        srv.start()
        port = srv.port
        # ... drive client against 127.0.0.1:port ...
        srv.stop()

    Each connection walks through:

    1. Telnet negotiation (BINARY + EOR + TERMINAL-TYPE).
    2. Server sends the initial screen built from ``fields``.
    3. Server reads the client's Read-Modified reply, parses AID +
       field values, records them on ``last_read``, and replies with a
       result screen.

    The default policy is: any AID other than PF3 loops back to the
    result screen; PF3 disconnects. Override :meth:`on_aid` for
    per-AID behavior.
    """

    def __init__(
        self,
        *,
        title: str = "MOCK SIGN-ON",
        fields: list[ScreenField] | None = None,
        footer: str = "ENTER=submit  PF3=exit",
        cursor_field: str | None = "user",
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        self.title = title
        self.fields = fields or [
            ScreenField("user", "USER:", row=6, col=10, length=8),
            ScreenField("password", "PASS:", row=7, col=10, length=8, hidden=True),
        ]
        self.footer = footer
        self.cursor_field = cursor_field
        self.cols = cols
        self.rows = rows
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._client_sock: socket.socket | None = None
        self._client_addr: Any = None
        # Public: last Read-Modified the server observed.
        self.last_read: ReadModified | None = None
        self.reads: list[ReadModified] = []
        # Public: whether the client sent PF3 (exit).
        self.disconnected = False
        # Hook for tests that want to override reply behaviour.
        self.on_aid: Callable[[ReadModified], bytes | None] = self._default_on_aid

    # ---- lifecycle ------------------------------------------------------ #

    def start(self) -> int:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.port

    @property
    def port(self) -> int:
        assert self._sock is not None
        return self._sock.getsockname()[1]

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        for s in (self._client_sock, self._sock):
            try:
                if s:
                    s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                if s:
                    s.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=timeout)

    def __enter__(self) -> MockTN3270Server:
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ---- server loop ---------------------------------------------------- #

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, addr = self._sock.accept()
            except (TimeoutError, OSError):
                continue
            self._client_sock = client
            self._client_addr = addr
            try:
                self._serve_client(client)
            except (ConnectionError, OSError, RuntimeError):
                pass
            finally:
                try:
                    client.close()
                except Exception:
                    pass
                self._client_sock = None

    def _serve_client(self, client: socket.socket) -> None:
        client.settimeout(5.0)
        self._negotiate(client)
        # Send initial screen wrapped in IAC EOR.
        client.sendall(self._wrap(self._build_screen()))

        while not self._stop.is_set() and not self.disconnected:
            frame = self._recv_frame(client)
            if frame is None:
                return
            if not frame:
                continue
            read = parse_read_modified(frame, self.fields, self.cols)
            self.last_read = read
            self.reads.append(read)
            reply = self.on_aid(read)
            if reply is None:
                # PF3 (Exit) — close cleanly.
                self.disconnected = True
                return
            client.sendall(self._wrap(reply))

    # ---- telnet negotiation --------------------------------------------- #

    def _negotiate(self, client: socket.socket) -> None:
        """Perform the WILL/DO handshake that ws3270 expects."""
        # Server: DO TERMINAL-TYPE, WILL BINARY, WILL EOR, DO BINARY, DO EOR
        client.sendall(bytes([IAC, DO, OPT_TERMTYPE]))
        client.sendall(bytes([IAC, WILL, OPT_EOR]))
        client.sendall(bytes([IAC, DO, OPT_EOR]))
        client.sendall(bytes([IAC, WILL, OPT_BINARY]))
        client.sendall(bytes([IAC, DO, OPT_BINARY]))

        # Read the client responses until BINARY + EOR both agreed on
        # both directions. ws3270 sends the responses in one or two
        # bursts; we time out after 5 s.
        agreed = {"binary_us": False, "binary_them": False, "eor_us": False, "eor_them": False}
        client.settimeout(5.0)
        pending = bytearray()
        deadline_reads = 40  # cap on how many bytes we read during negotiation

        while not all(agreed.values()) and deadline_reads > 0:
            try:
                chunk = client.recv(256)
            except TimeoutError:
                break
            if not chunk:
                break
            pending += chunk
            deadline_reads -= 1
            # Process telnet commands byte by byte.
            i = 0
            while i < len(pending):
                if pending[i] != IAC:
                    i += 1
                    continue
                if i + 2 >= len(pending):
                    break
                verb, opt = pending[i + 1], pending[i + 2]
                consumed = 3
                if verb == SB:
                    # Skip subneg until IAC SE.
                    j = i + 3
                    while j < len(pending) - 1:
                        if pending[j] == IAC and pending[j + 1] == SE:
                            j += 2
                            break
                        j += 1
                    consumed = j - i
                    # Extract subneg content — mostly TT IS "IBM-3278-2".
                    subneg = pending[i + 3 : i + consumed - 2]
                    if len(subneg) >= 2 and subneg[0] == OPT_TERMTYPE:
                        # Confirmed terminal type; nothing to send back.
                        pass
                else:
                    if verb == WILL and opt == OPT_BINARY:
                        agreed["binary_them"] = True
                    elif verb == WILL and opt == OPT_EOR:
                        agreed["eor_them"] = True
                    elif verb == DO and opt == OPT_BINARY:
                        agreed["binary_us"] = True
                    elif verb == DO and opt == OPT_EOR:
                        agreed["eor_us"] = True
                    elif verb == WILL and opt == OPT_TERMTYPE:
                        # Ask for its terminal type: IAC SB TT SEND IAC SE
                        client.sendall(bytes([IAC, SB, OPT_TERMTYPE, TT_SEND, IAC, SE]))
                del pending[i : i + consumed]
                # Restart scan.
                i = 0

        # If we did not converge on all four, ws3270 is still likely to
        # accept the 3270 data stream if BINARY+EOR are set both ways.
        # Any leftover pending bytes are start-of-3270 data; keep them.
        self._pending = bytes(pending)

    # ---- framing helpers ------------------------------------------------ #

    def _wrap(self, payload: bytes) -> bytes:
        """Escape IAC bytes in payload then append IAC EOR."""
        escaped = payload.replace(b"\xff", b"\xff\xff")
        return escaped + bytes([IAC, EOR])

    def _recv_frame(self, client: socket.socket) -> bytes | None:
        """Read one 3270 data frame terminated by IAC EOR.

        Strips any mid-stream telnet commands (IAC WILL/DO/WONT/DONT/etc.)
        from the returned frame — ws3270 renegotiates options mid-session
        and those bytes must not appear in the 3270 data stream. IAC IAC
        is unescaped to a single 0xFF byte.
        """
        buf = bytearray(getattr(self, "_pending", b""))
        self._pending = b""
        while True:
            frame = bytearray()
            i = 0
            done_at = -1
            while i < len(buf):
                b = buf[i]
                if b != IAC:
                    frame.append(b)
                    i += 1
                    continue
                # b == IAC — need at least one lookahead byte.
                if i + 1 >= len(buf):
                    break
                nxt = buf[i + 1]
                if nxt == EOR:
                    done_at = i + 2
                    break
                if nxt == IAC:
                    frame.append(IAC)
                    i += 2
                    continue
                if nxt in (WILL, WONT, DO, DONT):
                    # 3-byte telnet command — drop.
                    if i + 2 >= len(buf):
                        break
                    i += 3
                    continue
                if nxt == SB:
                    # Skip subneg block ending with IAC SE.
                    j = i + 2
                    while j < len(buf) - 1:
                        if buf[j] == IAC and buf[j + 1] == SE:
                            j += 2
                            break
                        j += 1
                    else:
                        break
                    i = j
                    continue
                # Unknown 2-byte command; drop it.
                i += 2

            if done_at >= 0:
                self._pending = bytes(buf[done_at:])
                return bytes(frame)

            try:
                chunk = client.recv(4096)
            except (TimeoutError, ConnectionError, OSError):
                return None
            if not chunk:
                return None
            buf += chunk

    # ---- default policy ------------------------------------------------- #

    def _build_screen(self) -> bytes:
        return build_screen(
            self.title,
            self.fields,
            footer=self.footer,
            cursor_field=self.cursor_field,
            cols=self.cols,
        )

    def _default_on_aid(self, read: ReadModified) -> bytes | None:
        # PF3 → disconnect
        if read.aid == AID_PF3:
            return None
        # Anything else → echo the entered fields on a result screen.
        return build_result_screen(cols=self.cols, aid=f"0x{read.aid:02X}", **read.values)


# --------------------------------------------------------------------------- #
# Convenience — decode a plain screen dump for assertions                      #
# --------------------------------------------------------------------------- #


def dump_screen_ascii(payload: bytes, cols: int = 80, rows: int = 24) -> list[str]:
    """Interpret a server-sent screen payload and render as an ASCII grid.

    Used inside test helpers so a test can compare what would be shown
    to the user without going through the full ws3270 pipeline.
    """
    grid = [[" "] * cols for _ in range(rows)]
    if not payload:
        return ["".join(r) for r in grid]
    # Skip CMD + WCC.
    i = 2
    cur = 0
    while i < len(payload):
        b = payload[i]
        if b == ORDER_SBA and i + 2 < len(payload):
            row, col = _decode_sba(payload[i + 1], payload[i + 2], cols)
            cur = (row - 1) * cols + (col - 1)
            i += 3
        elif b == ORDER_SF and i + 1 < len(payload):
            # Field attribute occupies one cell (usually rendered blank).
            grid[cur // cols][cur % cols] = " "
            cur += 1
            i += 2
        elif b == ORDER_IC:
            i += 1
        elif b in (ORDER_PT, ORDER_EUA, ORDER_RA):
            i += 3
        else:
            # Regular EBCDIC data byte.
            ch = bytes([b]).decode("cp037", errors="replace")
            r = cur // cols
            c = cur % cols
            if 0 <= r < rows and 0 <= c < cols:
                grid[r][c] = ch if ch.isprintable() else " "
            cur += 1
            i += 1
    return ["".join(r) for r in grid]
