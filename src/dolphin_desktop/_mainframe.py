"""Mainframe terminal (3270/5250) automation backend.

Automates legacy IBM mainframe (3270) and midrange (5250) terminals via
either:

* **s3270 subprocess** (open source, default) — spawns ``s3270`` /
  ``ws3270`` / ``s5250`` and drives it through its scripting protocol.
  Works on any machine with the x3270 / wc3270 package installed. This
  is the CI-friendly path — the emulator itself is free.

* **HLLAPI/EHLLAPI DLL** (proprietary emulators) — attaches to an
  existing emulator session (IBM PCOMM, Attachmate/Rocket Reflection,
  Micro Focus RUMBA…) through the ``EHLAPI32.DLL`` / ``PCSHLL32.DLL``
  standard function-code interface.

Both backends implement the same :class:`MainframeTerminal` surface so
tests port unchanged between them.

Public entry point::

    from dolphin_desktop import Desktop

    term = Desktop().mainframe(
        host="pub400.com", port=23, session_type="5250", backend="tn5250"
    )
    term.wait_ready()
    print(term.text())
    term.field(row=6, col=53).type_text("MYUSER")
    term.field(row=7, col=53).type_text("MYPASS")
    term.press("Enter")
    term.wait_change()
    term.disconnect()

See :class:`MainframeTerminal` for the full method list.
"""

from __future__ import annotations

import abc
import ctypes
import enum
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from ._exceptions import DolphinError
from ._logging import get_logger

_LOG = get_logger("mainframe")

__all__ = [
    "AID",
    "FieldInfo",
    "MainframeError",
    "MainframeTerminal",
    "TerminalField",
    "TerminalScreen",
]


class MainframeError(DolphinError):
    """Raised when a mainframe terminal operation fails."""


# --------------------------------------------------------------------------- #
# AID (Attention IDentifier) keys                                              #
# --------------------------------------------------------------------------- #


class AID:
    """AID key constants — the "action" keys a mainframe expects.

    Terminal programs freeze the keyboard after unlocking a field and wait
    for one of these keys to trigger a screen transaction. ``Enter`` and
    ``PF3`` (usually "Exit") are the most common.

    Attributes are canonical names used by both backends. Each backend
    translates internally to its own wire format (``PF(3)`` for s3270,
    ``@3`` for HLLAPI, etc.).
    """

    ENTER = "Enter"
    CLEAR = "Clear"
    PA1 = "PA1"
    PA2 = "PA2"
    PA3 = "PA3"

    @staticmethod
    def pf(n: int) -> str:
        """Return the AID name for program-function key *n* (1–24)."""
        if not 1 <= n <= 24:
            raise ValueError(f"PF key must be 1..24, got {n}")
        return f"PF{n}"


# --------------------------------------------------------------------------- #
# Screen snapshot                                                              #
# --------------------------------------------------------------------------- #


class TerminalScreen:
    """Immutable snapshot of a mainframe presentation space.

    A 3270 screen is a fixed grid of characters (typically 24×80 or 43×80
    for 3270 Model 4). A 5250 screen is 24×80 or 27×132. Each cell holds
    exactly one character; the "field" concept is layered on top by the
    application (a run of writable positions bounded by an attribute
    byte).

    :class:`TerminalScreen` exposes the grid without going back to the
    emulator, so calling ``screen.text_at(...)`` many times is cheap. To
    refresh, call :meth:`MainframeTerminal.screen` again.
    """

    __slots__ = ("_cols", "_cursor", "_lines", "_rows")

    def __init__(self, lines: Sequence[str], cursor: tuple[int, int]) -> None:
        self._rows = len(lines)
        self._cols = max((len(ln) for ln in lines), default=0)
        # Pad every line to full column width so text_at never IndexErrors.
        self._lines: tuple[str, ...] = tuple(ln.ljust(self._cols) for ln in lines)
        self._cursor = cursor

    @property
    def rows(self) -> int:
        """Number of rows in the presentation space (e.g. 24)."""
        return self._rows

    @property
    def cols(self) -> int:
        """Number of columns (e.g. 80)."""
        return self._cols

    @property
    def cursor(self) -> tuple[int, int]:
        """``(row, col)`` of the input cursor, 1-indexed to match the emulator."""
        return self._cursor

    def text(self) -> str:
        """Return the whole screen joined by newlines (rows top-to-bottom)."""
        return "\n".join(self._lines)

    def line(self, row: int) -> str:
        """Return the full text of *row* (1-indexed)."""
        self._require_row(row)
        return self._lines[row - 1]

    def text_at(self, row: int, col: int, length: int) -> str:
        """Return *length* characters starting at ``(row, col)``.

        Args:
            row: 1-indexed row.
            col: 1-indexed column.
            length: How many characters to return. Automatically clipped
                to the row width — never raises for over-long reads.
        """
        self._require_row(row)
        if col < 1:
            raise ValueError(f"col must be >= 1, got {col}")
        line = self._lines[row - 1]
        start = col - 1
        return line[start : start + length]

    def contains(self, needle: str, *, row: int | None = None) -> bool:
        """Case-sensitive substring search, whole screen or one row."""
        if row is None:
            return needle in self.text()
        return needle in self.line(row)

    def find(self, needle: str) -> tuple[int, int] | None:
        """Return the ``(row, col)`` of the first occurrence, or None."""
        for r, line in enumerate(self._lines, start=1):
            idx = line.find(needle)
            if idx != -1:
                return (r, idx + 1)
        return None

    def _require_row(self, row: int) -> None:
        if not 1 <= row <= self._rows:
            raise ValueError(f"row must be 1..{self._rows}, got {row}")

    def __repr__(self) -> str:
        return f"TerminalScreen({self._rows}x{self._cols}, cursor={self._cursor})"


# --------------------------------------------------------------------------- #
# Field locator                                                                #
# --------------------------------------------------------------------------- #


class FieldInfo:
    """Metadata for a single 3270 field as declared by the host.

    Returned by :meth:`MainframeTerminal.fields`. Fields on a 3270
    screen are demarcated by Start Field (SF) attribute bytes; this
    class captures the position + attribute so callers can pick
    writable slots by property instead of by column-counting.

    ``protected`` is True for label / static text zones, False for
    input fields. ``hidden`` is True for password fields (display
    intensity zero) and matches the FA_HIDDEN mask.
    """

    __slots__ = ("attr_byte", "col", "hidden", "length", "modified", "numeric", "protected", "row")

    def __init__(
        self,
        row: int,
        col: int,
        length: int,
        *,
        protected: bool,
        hidden: bool,
        numeric: bool,
        modified: bool,
        attr_byte: int,
    ) -> None:
        self.row = row
        self.col = col
        self.length = length
        self.protected = protected
        self.hidden = hidden
        self.numeric = numeric
        self.modified = modified
        self.attr_byte = attr_byte

    @property
    def writable(self) -> bool:
        """True when a user can type into this field — unprotected + not
        display-only. Equivalent to ``not protected``."""
        return not self.protected

    def __repr__(self) -> str:
        flags = []
        if self.protected:
            flags.append("protected")
        if self.hidden:
            flags.append("hidden")
        if self.numeric:
            flags.append("numeric")
        if self.modified:
            flags.append("modified")
        flag_s = f" [{','.join(flags)}]" if flags else ""
        return f"FieldInfo(row={self.row}, col={self.col}, length={self.length}{flag_s})"


class TerminalField:
    """Handle to a specific position on the terminal screen.

    Fields on a mainframe are not first-class objects the way UI controls
    are — the application draws a label, then an unprotected run of
    characters starting at a fixed ``(row, col)``. This class binds those
    coordinates + a length to a terminal so the caller can type into the
    field or read its current value without recomputing offsets.

    Instances are created via :meth:`MainframeTerminal.field` or
    :meth:`MainframeTerminal.field_after`.
    """

    __slots__ = ("_col", "_length", "_row", "_terminal")

    def __init__(self, terminal: MainframeTerminal, row: int, col: int, length: int) -> None:
        self._terminal = terminal
        self._row = row
        self._col = col
        self._length = length

    @property
    def position(self) -> tuple[int, int]:
        """1-indexed ``(row, col)`` where the field starts."""
        return (self._row, self._col)

    @property
    def length(self) -> int:
        """Declared field length in characters."""
        return self._length

    def read(self) -> str:
        """Return the current text of the field, stripped of trailing spaces."""
        raw = self._terminal.screen().text_at(self._row, self._col, self._length)
        return raw.rstrip()

    def type_text(self, text: str, *, clear: bool = True) -> None:
        """Move the cursor to the field and type *text*.

        Args:
            text: Text to type. Mainframe applications typically accept
                only 7-bit ASCII/EBCDIC-representable characters; use the
                emulator's translation for accented characters.
            clear: When True (default), overwrite the field with spaces
                before typing so leftover characters do not stay. When
                False, type at the current cursor position without
                clearing — useful for suffixing.
        """
        self._terminal.move_cursor(self._row, self._col)
        if clear:
            self._terminal.type_text(" " * self._length)
            self._terminal.move_cursor(self._row, self._col)
        self._terminal.type_text(text)

    def __repr__(self) -> str:
        return f"TerminalField(row={self._row}, col={self._col}, length={self._length})"


# --------------------------------------------------------------------------- #
# Backend protocol                                                             #
# --------------------------------------------------------------------------- #


class _TerminalBackend(abc.ABC):
    """Adapter interface implemented by s3270 and HLLAPI backends."""

    @abc.abstractmethod
    def connect(self, host: str, port: int, *, session_type: str) -> None: ...
    @abc.abstractmethod
    def disconnect(self) -> None: ...
    @abc.abstractmethod
    def is_connected(self) -> bool: ...
    @abc.abstractmethod
    def read_screen(self) -> tuple[list[str], tuple[int, int]]: ...
    @abc.abstractmethod
    def send_string(self, text: str) -> None: ...
    @abc.abstractmethod
    def send_aid(self, aid: str) -> None: ...
    @abc.abstractmethod
    def move_cursor(self, row: int, col: int) -> None: ...
    @abc.abstractmethod
    def wait_unlock(self, timeout: float) -> None: ...
    @abc.abstractmethod
    def wait_output(self, timeout: float) -> None: ...
    @abc.abstractmethod
    def is_keyboard_locked(self) -> bool: ...
    @abc.abstractmethod
    def read_fields(self) -> list[FieldInfo]: ...


# --------------------------------------------------------------------------- #
# s3270 / ws3270 subprocess backend                                            #
# --------------------------------------------------------------------------- #


_S3270_CANDIDATES = (
    "ws3270.exe",
    "s3270.exe",
    "s3270",
    "ws3270",
)


def _find_s3270() -> str | None:
    """Return the path to the first available s3270-family binary, or None."""
    for name in _S3270_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    # Fall back to well-known install directories on Windows.
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\wc3270\ws3270.exe",
            r"C:\Program Files (x86)\wc3270\ws3270.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\wc3270\ws3270.exe"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
    return None


class _S3270Backend(_TerminalBackend):
    """Drive ``ws3270``/``s3270`` through its line-based scripting protocol.

    Protocol summary (see http://x3270.bgp.nu/x3270-script.html):

    * The emulator reads one action per line on stdin.
    * The response is 0+ ``data:`` lines, then one status line, then a
      final ``ok\\n`` or ``error\\n``.
    * The status line has 12 space-separated fields including keyboard
      state, connection state, cursor row/col, screen size, etc.
    """

    def __init__(
        self,
        binary: str | None = None,
        *,
        model: str = "3279-4",
        codepage: str | None = None,
        extra_args: Sequence[str] | None = None,
        trace: bool = False,
    ) -> None:
        binary_path = binary or _find_s3270()
        if binary_path is None:
            raise MainframeError(
                "No ws3270/s3270 binary found. Install wc3270 (Windows) or "
                "the x3270 package (Linux/macOS), or pass ws3270_path=... "
                "to Desktop.mainframe(). See docs/guides/mainframe.md for the "
                "install steps."
            )
        self._binary: str = binary_path
        self._model = model
        self._codepage = codepage
        self._extra_args = list(extra_args or ())
        self._trace = trace
        self._proc: subprocess.Popen[bytes] | None = None
        self._connected = False

    # ---- lifecycle ------------------------------------------------------- #

    def _spawn(self) -> None:
        if self._proc is not None:
            return
        args = [
            self._binary,
            "-model",
            self._model,
            "-utf8",
        ]
        if self._codepage:
            args += ["-charset", self._codepage]
        args += list(self._extra_args)
        creationflags = 0
        if sys.platform == "win32":
            # CREATE_NO_WINDOW — hide the console the emulator would open.
            creationflags = 0x08000000
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=creationflags,
        )

    def connect(self, host: str, port: int, *, session_type: str) -> None:
        # x3270's host prefixes are all about the transport (``L:`` opens a
        # TLS tunnel, ``B:`` forces non-E TN3270…) — none of them selects
        # 5250, which x3270 does not speak at all.
        # "5250", "5250E", "tn5250" — all name the protocol x3270 cannot speak.
        if "5250" in session_type.lower():
            raise MainframeError(
                "the s3270 backend speaks TN3270 only — x3270 has no 5250 "
                "support, so a 5250 session cannot be driven through it",
                hint=(
                    'use backend="tn5250" for IBM i / AS-400 hosts, or '
                    'session_type="3270" if this host really is a 3270 host'
                ),
            )
        self._spawn()
        self._exec(f"Connect({host}:{port})")
        self._connected = True
        # Wait for the initial screen to draw.
        self._exec("Wait(15,InputField)", raise_on_error=False)

    def disconnect(self) -> None:
        if self._proc is None:
            return
        try:
            if self._connected:
                self._exec("Disconnect", raise_on_error=False)
            self._exec("Quit", raise_on_error=False)
        except Exception:
            pass
        try:
            self._proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None
        self._connected = False

    def is_connected(self) -> bool:
        if not self._connected or self._proc is None:
            return False
        return self._proc.poll() is None

    # ---- read + status --------------------------------------------------- #

    def read_screen(self) -> tuple[list[str], tuple[int, int]]:
        data, status = self._exec("Ascii")
        # status format:
        #  0: keyboard state (L=locked, U=unlocked, E=error)
        #  1: screen formatting (F=formatted, U=unformatted)
        #  2: field protection at cursor (P=protected, U=unprotected)
        #  3: connection state (N=not, C=host, ...)
        #  4: emulator mode
        #  5: model number
        #  6: rows
        #  7: cols
        #  8: cursor row (0-indexed)
        #  9: cursor col (0-indexed)
        # 10: window id
        # 11: exec time
        parts = status.split()
        try:
            cur_row = int(parts[8]) + 1
            cur_col = int(parts[9]) + 1
        except (IndexError, ValueError):
            cur_row, cur_col = 1, 1
        return list(data), (cur_row, cur_col)

    def is_keyboard_locked(self) -> bool:
        _, status = self._exec("Query(KeyboardLock)", raise_on_error=False)
        # Status field 0 is U (unlocked), L (locked by the host) or E
        # (locked by an operator error, e.g. a keystroke into a protected
        # field). E is a locked keyboard too — it needs a Reset before the
        # next keystroke lands.
        return status.split()[:1] in (["L"], ["E"]) if status else False

    # ---- field list (ReadBuffer parser) --------------------------------- #

    _RE_SF = re.compile(r"^SF\(([^)]*)\)$")
    _RE_SA = re.compile(r"^SA\(([^)]*)\)$")
    # x3270 attribute-type code for the basic 3270 field attribute; on a
    # color host an SF token carries extended pairs alongside it, e.g.
    # "SF(c0=e8,42=f4)".
    _SF_FIELD_ATTR_TYPE = "c0"

    @classmethod
    def _sf_attr_byte(cls, attrs: str) -> int | None:
        """Return the 3270 field-attribute byte from an ``SF(...)`` body."""
        single = attrs.strip()
        for pair in attrs.split(","):
            key, sep, value = pair.strip().partition("=")
            if sep and key.strip().lower() == cls._SF_FIELD_ATTR_TYPE:
                try:
                    return int(value.strip(), 16)
                except ValueError:
                    return None
        if "=" not in single:
            try:
                return int(single, 16)
            except ValueError:
                return None
        return None

    def read_fields(self) -> list[FieldInfo]:
        """Parse the buffer's Start-Field attribute bytes into a list.

        s3270's ``ReadBuffer(Ascii)`` returns each screen row as a
        space-separated hex byte stream with ``SF(c0=XX)`` markers where
        a Start Field attribute lives. On a color host (and ``3279-4``,
        the default model, is one) the marker carries extra attribute
        pairs — ``SF(c0=XX,42=YY,41=ZZ)``. ``SA(...)`` tokens set
        character attributes and occupy no buffer position at all.

        XX is the field-attribute byte:

        * bit 0x20 → protected (label / static text zone)
        * bit 0x0C == 0x0C → hidden (password fields)
        * bit 0x10 → numeric only
        * bit 0x01 → MDT (modified since last read)

        The field's writable range starts one cell after the SF and
        extends to the cell before the next SF (or to end-of-buffer).
        Fields wrap past end-of-row — the parser walks the buffer as a
        single linear stream.
        """
        data_lines, status = self._exec("ReadBuffer(Ascii)")
        try:
            cols = int(status.split()[7])
        except (IndexError, ValueError):
            cols = 80
        # Collect (linear_offset, attr_byte) for every SF marker.
        starts: list[tuple[int, int]] = []
        for row_idx, line in enumerate(data_lines):
            tokens = line.split()
            col = 0
            for tok in tokens:
                if self._RE_SA.match(tok) is not None:
                    # Character attribute — no buffer position consumed.
                    continue
                m = self._RE_SF.match(tok)
                if m is not None:
                    attr = self._sf_attr_byte(m.group(1))
                    if attr is not None:
                        starts.append((row_idx * cols + col, attr))
                col += 1
        if not starts:
            return []
        total_cells = len(data_lines) * cols
        fields: list[FieldInfo] = []
        for idx, (offset, attr) in enumerate(starts):
            # Data starts one cell after the SF.
            data_start = offset + 1
            end = starts[idx + 1][0] if idx + 1 < len(starts) else total_cells
            length = max(0, end - data_start)
            row = data_start // cols + 1
            col = data_start % cols + 1
            fields.append(
                FieldInfo(
                    row=row,
                    col=col,
                    length=length,
                    protected=bool(attr & 0x20),
                    hidden=(attr & 0x0C) == 0x0C,
                    numeric=bool(attr & 0x10),
                    modified=bool(attr & 0x01),
                    attr_byte=attr,
                )
            )
        return fields

    # ---- keyboard input -------------------------------------------------- #

    def send_string(self, text: str) -> None:
        # s3270 String() takes a double-quoted argument; escape inner quotes
        # and backslashes so passwords with special characters survive.
        esc = text.replace("\\", "\\\\").replace('"', '\\"')
        self._exec(f'String("{esc}")')

    def send_aid(self, aid: str) -> None:
        low = aid.lower()
        if low == "enter":
            self._exec("Enter")
        elif low == "clear":
            self._exec("Clear")
        elif low.startswith("pf"):
            try:
                n = int(low[2:])
            except ValueError as exc:
                raise MainframeError(f"invalid PF key: {aid!r}") from exc
            self._exec(f"PF({n})")
        elif low.startswith("pa"):
            try:
                n = int(low[2:])
            except ValueError as exc:
                raise MainframeError(f"invalid PA key: {aid!r}") from exc
            self._exec(f"PA({n})")
        else:
            raise MainframeError(f"unknown AID key: {aid!r}")

    def move_cursor(self, row: int, col: int) -> None:
        # s3270 MoveCursor uses 0-indexed by default.
        self._exec(f"MoveCursor({row - 1},{col - 1})")

    # ---- waits ----------------------------------------------------------- #

    def wait_unlock(self, timeout: float) -> None:
        self._exec(f"Wait({int(timeout)},Unlock)")

    def wait_output(self, timeout: float) -> None:
        # Tolerate a disconnect that happens DURING the wait — a host may
        # legitimately close after processing an AID (e.g. PF3=Exit).
        try:
            self._exec(f"Wait({int(timeout)},Output)")
        except MainframeError as exc:
            if "not connected" in str(exc).lower():
                return
            raise

    # ---- low-level scripting protocol ------------------------------------ #

    def _exec(self, command: str, *, raise_on_error: bool = True) -> tuple[list[str], str]:
        """Send *command* to s3270 and parse the response.

        Returns ``(data_lines, status_line)``.
        """
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise MainframeError("s3270 subprocess is not running")
        if self._trace:
            _LOG.info("s3270 → %s", command)
        try:
            self._proc.stdin.write(command.encode("utf-8") + b"\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MainframeError(f"s3270 pipe closed while sending {command!r}: {exc}") from exc

        data: list[str] = []
        status = ""
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise MainframeError("s3270 exited unexpectedly")
            text = line.rstrip(b"\r\n").decode("utf-8", errors="replace")
            if text == "ok":
                if self._trace:
                    self._trace_response(command, "ok", data, status)
                return (data, status)
            if text == "error":
                if self._trace:
                    self._trace_response(command, "error", data, status)
                if raise_on_error:
                    joined = "\n".join(data) or "unknown"
                    raise MainframeError(f"s3270 {command!r} failed: {joined}")
                return (data, status)
            if text.startswith("data: "):
                data.append(text[6:])
                continue
            # Every response ends with exactly one status line before ok/error.
            status = text

    def _trace_response(self, command: str, verdict: str, data: list[str], status: str) -> None:
        # Truncate huge ReadBuffer responses so trace logs stay readable.
        for i, line in enumerate(data[:4]):
            _LOG.info("s3270 ← data[%d]: %s", i, line[:200])
        if len(data) > 4:
            _LOG.info("s3270 ← data[…]: (%d more lines)", len(data) - 4)
        if status:
            _LOG.info("s3270 ← status: %s", status)
        _LOG.info("s3270 ← %s (%s)", verdict, command)


# --------------------------------------------------------------------------- #
# HLLAPI / EHLLAPI ctypes backend                                              #
# --------------------------------------------------------------------------- #


class HllapiFn(enum.IntEnum):
    """EHLLAPI standard function codes.

    Portable across every EHLLAPI-compatible emulator (IBM PCOMM,
    Attachmate/Rocket Reflection, Micro Focus Rumba, WRQ/AttachmateWRQ).
    Names match the EHLLAPI specification so tracing / vendor
    documentation cross-references straight through.

    See IBM's ``ehlapi32.h`` reference or Attachmate's "HLLAPI Function
    Reference" for the full table (~100 entries) — we only bind the
    ~10 needed to drive a 3270/5250 session end-to-end.
    """

    CONNECT_PS = 1
    DISCONNECT_PS = 2
    SEND_KEY = 3
    WAIT = 4
    COPY_PS = 5
    QUERY_CURSOR = 7
    COPY_PS_TO_STR = 8
    COPY_STR_TO_PS = 15
    QUERY_SESSION_STATUS = 22
    SET_CURSOR = 40


# Legacy module-level aliases — kept for backward compatibility with
# any external code that imported the old private constants. New code
# should reference the ``HllapiFn`` enum.
_EHLLAPI_CONNECT_PS = HllapiFn.CONNECT_PS
_EHLLAPI_DISCONNECT_PS = HllapiFn.DISCONNECT_PS
_EHLLAPI_SENDKEY = HllapiFn.SEND_KEY
_EHLLAPI_WAIT = HllapiFn.WAIT
_EHLLAPI_COPY_PS = HllapiFn.COPY_PS
_EHLLAPI_QUERY_CURSOR = HllapiFn.QUERY_CURSOR
_EHLLAPI_COPY_PS_TO_STR = HllapiFn.COPY_PS_TO_STR
_EHLLAPI_SET_CURSOR = HllapiFn.SET_CURSOR
_EHLLAPI_QUERY_SESSION_STATUS = HllapiFn.QUERY_SESSION_STATUS
_EHLLAPI_COPY_STR_TO_PS = HllapiFn.COPY_STR_TO_PS
_EHLLAPI_COPY_PS_TO_STR_LEN = HllapiFn.COPY_PS_TO_STR  # historical typo alias

# EHLLAPI SendKey (function 3) mnemonics for the AID keys. PF1-PF9 are the
# digits, PF10-PF24 continue through the lowercase alphabet from 'a'; the
# uppercase letters mean something else entirely, so a computed offset is
# not interchangeable with this table. See IBM's "EHLLAPI Programming
# Reference", Send Key — Keyboard Mnemonics.
_EHLLAPI_PF_MNEMONICS: dict[int, str] = {n: f"@{n}" for n in range(1, 10)} | {
    n: f"@{chr(ord('a') + n - 10)}" for n in range(10, 25)
}

# PA1-PA3.
_EHLLAPI_PA_MNEMONICS: dict[int, str] = {1: "@x", 2: "@y", 3: "@z"}

_HLLAPI_DLL_CANDIDATES = (
    # (DLL name, exported function name)
    ("PCSHLL32.DLL", "hllapi"),  # IBM PCOMM
    ("EHLAPI32.DLL", "hllapi"),  # Attachmate / Rocket Reflection
    ("WHLAPI32.DLL", "hllapi"),  # Older Rocket
    ("PCSHLL.DLL", "HLLAPI"),  # Legacy
)


def _resolve_hllapi_dll(explicit: str | None) -> tuple[Any, Any]:
    """Load the first available HLLAPI DLL. Returns ``(dll, hllapi_fn)``."""
    tried: list[str] = []
    candidates: list[tuple[str, str]] = []
    if explicit:
        candidates.append((explicit, "hllapi"))
        candidates.append((explicit, "HLLAPI"))
    candidates.extend(_HLLAPI_DLL_CANDIDATES)

    for dll_name, fn_name in candidates:
        tried.append(f"{dll_name}!{fn_name}")
        try:
            dll = ctypes.WinDLL(dll_name)  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            continue
        try:
            fn = getattr(dll, fn_name)
        except AttributeError:
            continue
        # int hllapi(unsigned short* func, char* data, unsigned short* length, unsigned short* rc)
        # POINTER(c_ubyte) is a real ctypes pointer at the C level (still
        # a char*), but unlike c_char_p it does NOT auto-convert to bytes
        # when passed through a WINFUNCTYPE callback — the test-side fake
        # relies on this so it can read/write the buffer.
        fn.argtypes = [
            ctypes.POINTER(ctypes.c_ushort),
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.c_ushort),
            ctypes.POINTER(ctypes.c_ushort),
        ]
        fn.restype = None
        return dll, fn

    raise MainframeError(
        "No HLLAPI-compatible DLL found. Tried: "
        + ", ".join(tried)
        + ". Install IBM Personal Communications, Attachmate/Rocket "
        "Reflection, or another EHLLAPI-capable emulator, then pass "
        "hllapi_dll_path='C:\\\\path\\\\to\\\\PCSHLL32.DLL' to "
        "Desktop.mainframe(backend='hllapi')."
    )


class _HLLAPIBackend(_TerminalBackend):
    """Attach to an already-running emulator through EHLLAPI.

    Unlike the s3270 backend which spawns and connects, HLLAPI attaches
    to a session created by the emulator itself (PCOMM's session "A",
    Reflection's session "1", etc.). The *session_id* must match the
    letter/number the emulator assigns.
    """

    def __init__(
        self,
        *,
        session_id: str = "A",
        dll_path: str | None = None,
        trace: bool = False,
        _hllapi_fn: Any = None,
    ) -> None:
        self._session_id = session_id[0].upper()
        if _hllapi_fn is not None:
            # Test-injection path: skip DLL resolve entirely.
            self._dll = None
            self._fn = _hllapi_fn
        else:
            self._dll, self._fn = _resolve_hllapi_dll(dll_path)
        self._trace = trace
        self._connected = False
        # Screen size discovered at connect time via Query Session Status.
        self._rows = 24
        self._cols = 80

    def _call(
        self,
        func: HllapiFn | int,
        data: bytes = b"",
        length: int | None = None,
        *,
        ps_pos: int = 0,
    ) -> tuple[int, bytes, int]:
        """Invoke the EHLLAPI entry point and return ``(rc, data, length)``.

        The fourth EHLLAPI parameter is bidirectional: on input it carries
        the presentation-space position for the functions that address the
        PS (Copy PS to String, Set Cursor); on return it always holds the
        return code. Passing 0 where a position is required makes those
        functions fail with rc=7 on a real emulator DLL.
        """
        if self._trace:
            fn_label = func.name if isinstance(func, HllapiFn) else f"#{func}"
            _LOG.info(
                "HLLAPI → func=%s data=%r len=%s ps=%s",
                fn_label,
                data[:32] + b"..." if len(data) > 32 else data,
                length,
                ps_pos,
            )
        fn = ctypes.c_ushort(int(func))
        buf_size = max(len(data), 1)
        buf = (ctypes.c_ubyte * buf_size)(*data.ljust(buf_size, b"\x00"))
        ln = ctypes.c_ushort(length if length is not None else len(data))
        rc = ctypes.c_ushort(ps_pos)
        self._fn(
            ctypes.byref(fn),
            buf,
            ctypes.byref(ln),
            ctypes.byref(rc),
        )
        if self._trace:
            _LOG.info("HLLAPI ← rc=%d ret_len=%d", rc.value, ln.value)
        return (rc.value, bytes(buf)[: ln.value], ln.value)

    def connect(self, host: str, port: int, *, session_type: str) -> None:
        # HLLAPI attaches to a preexisting session — host/port/session_type
        # are not passed to the DLL. They are kept in the signature so
        # user code is portable between backends.
        del host, port, session_type
        rc, _, _ = self._call(_EHLLAPI_CONNECT_PS, self._session_id.encode("ascii"))
        if rc != 0:
            raise MainframeError(
                f"EHLLAPI ConnectPS failed with rc={rc} (session {self._session_id!r})"
            )
        self._connected = True
        # Query screen dimensions.
        rc, buf, _ = self._call(
            _EHLLAPI_QUERY_SESSION_STATUS,
            self._session_id.encode("ascii") + b"\x00" * 17,
            18,
        )
        if rc == 0 and len(buf) >= 15:
            # Query Session Status (22) returns the packed struct from IBM's
            # hapi_c.h: [0] short session id, [1:9] long name, [9] session
            # type, [10] characteristics, [11:13] rows, [13:15] columns,
            # [15:17] host code page. Rows/columns are binary USHORTs, not
            # text. Implausible values mean an unexpected vendor layout —
            # keep the 24x80 default rather than slice the PS at nonsense
            # boundaries.
            rows = int.from_bytes(buf[11:13], "little")
            cols = int.from_bytes(buf[13:15], "little")
            if 1 <= rows <= 100 and 1 <= cols <= 300:
                self._rows = rows
                self._cols = cols

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._call(_EHLLAPI_DISCONNECT_PS, self._session_id.encode("ascii"))
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def read_screen(self) -> tuple[list[str], tuple[int, int]]:
        size = self._rows * self._cols
        # Copy PS to String starts at the PS position given in the fourth
        # parameter; positions are 1-based, so the whole screen is ps_pos=1.
        rc, buf, actual = self._call(_EHLLAPI_COPY_PS_TO_STR, b"\x00" * size, size, ps_pos=1)
        if rc != 0:
            raise MainframeError(f"EHLLAPI CopyPS failed rc={rc}")
        text = buf[:actual].decode("cp1252", errors="replace")
        lines = [text[i : i + self._cols] for i in range(0, self._rows * self._cols, self._cols)]

        # Cursor position — QueryCursor returns 1-indexed offset into PS.
        rc, _, offset = self._call(_EHLLAPI_QUERY_CURSOR)
        if rc == 0 and offset > 0:
            zero_based = offset - 1
            cur_row = zero_based // self._cols + 1
            cur_col = zero_based % self._cols + 1
        else:
            cur_row, cur_col = 1, 1
        return lines, (cur_row, cur_col)

    def send_string(self, text: str) -> None:
        # SendKey reads "@" as the start of a keyboard mnemonic; a literal
        # "@" must be doubled or e-mail addresses and passwords inject
        # keystrokes instead of characters.
        data = text.replace("@", "@@").encode("cp1252", errors="replace")
        rc, _, _ = self._call(_EHLLAPI_SENDKEY, data, len(data))
        if rc != 0:
            raise MainframeError(f"EHLLAPI SendKey('{text[:16]}…') failed rc={rc}")

    def send_aid(self, aid: str) -> None:
        # HLLAPI uses @ escape sequences for AID keys. See EHLLAPI docs.
        low = aid.lower()
        if low == "enter":
            key = "@E"
        elif low == "clear":
            key = "@C"
        elif low.startswith("pf"):
            try:
                n = int(low[2:])
            except ValueError as exc:
                raise MainframeError(f"invalid PF key: {aid!r}") from exc
            key = _EHLLAPI_PF_MNEMONICS.get(n, "")
            if not key:
                raise MainframeError(f"invalid PF key: {aid!r} (PF1..PF24)")
        elif low.startswith("pa"):
            try:
                n = int(low[2:])
            except ValueError as exc:
                raise MainframeError(f"invalid PA key: {aid!r}") from exc
            key = _EHLLAPI_PA_MNEMONICS.get(n, "")
            if not key:
                raise MainframeError(f"invalid PA key: {aid!r}")
        else:
            raise MainframeError(f"unknown AID key: {aid!r}")
        data = key.encode("ascii")
        rc, _, _ = self._call(_EHLLAPI_SENDKEY, data, len(data))
        if rc != 0:
            raise MainframeError(f"EHLLAPI SendKey({key!r}) failed rc={rc}")

    def move_cursor(self, row: int, col: int) -> None:
        # Set Cursor takes the 1-based target position in the fourth
        # parameter; its data string and length are unused.
        offset = (row - 1) * self._cols + col
        rc, _, _ = self._call(_EHLLAPI_SET_CURSOR, b"", 0, ps_pos=offset)
        if rc != 0:
            raise MainframeError(f"EHLLAPI SetCursor({row},{col}) failed rc={rc}")

    def wait_unlock(self, timeout: float) -> None:
        # EHLLAPI Wait function has its own timeout controlled by
        # SetSessionParameters. Best-effort: call Wait() up to *timeout*
        # seconds, polling once per 100ms.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rc, _, _ = self._call(_EHLLAPI_WAIT)
            if rc == 0:
                return
            time.sleep(0.1)
        raise MainframeError(f"keyboard did not unlock within {timeout}s")

    def wait_output(self, timeout: float) -> None:
        self.wait_unlock(timeout)

    def is_keyboard_locked(self) -> bool:
        rc, _, _ = self._call(_EHLLAPI_WAIT)
        return rc != 0

    def read_fields(self) -> list[FieldInfo]:
        """Not yet implemented for HLLAPI.

        EHLLAPI's Query Field Attribute (function code 14 / 25 depending
        on vendor) can enumerate fields but the exact call sequence
        varies between IBM PCOMM and Attachmate. Best-effort placeholder
        — returns an empty list so callers can fall back to positional
        addressing.
        """
        return []


# --------------------------------------------------------------------------- #
# Native TN5250 backend                                                        #
# --------------------------------------------------------------------------- #


# Telnet
_T_IAC = 0xFF
_T_DONT = 0xFE
_T_DO = 0xFD
_T_WONT = 0xFC
_T_WILL = 0xFB
_T_SB = 0xFA
_T_SE = 0xF0
_T_EOR = 0xEF

_TOPT_BINARY = 0x00
_TOPT_EOR = 0x19
_TOPT_TERMTYPE = 0x18
_TOPT_NEWENV = 0x27

# 5250 commands (inside GDS record)
_C_WRITE_TO_DISPLAY = 0x11  # actually part of WTD via CC byte pattern
_C_WTD = 0xF1
_C_READ_INPUT_FIELDS = 0x42
_C_READ_IMMEDIATE = 0x72
_C_WSF = 0xF3

# Remaining 5250 command codes, spelled out because the numbers are not
# guessable from each other: Clear Format Table is 0x50, one bit away from
# Read MDT Fields (0x52), and 0x41 / 0x51 / 0x53 are not commands at all.
_C_CLEAR_UNIT = 0x40
_C_CLEAR_UNIT_ALT = 0x20
_C_CLEAR_FORMAT_TABLE = 0x50
_C_READ_MDT_FIELDS = 0x52
_C_READ_MDT_FIELDS_ALT = 0x82
_C_READ_SCREEN_IMMEDIATE = 0x62

#: Host reads that carry two control-character bytes after the command.
_C_READ_COMMANDS_WITH_CC = frozenset(
    {
        _C_READ_INPUT_FIELDS,
        _C_READ_MDT_FIELDS,
        _C_READ_MDT_FIELDS_ALT,
    }
)

#: Host reads that carry nothing after the command. Skipping two bytes for
#: these ate the following ESC + command, so a Write-To-Display sharing the
#: record was lost and the screen buffer silently kept the previous panel.
_C_READ_COMMANDS_BARE = frozenset({_C_READ_IMMEDIATE, _C_READ_SCREEN_IMMEDIATE})

_C_READ_COMMANDS = _C_READ_COMMANDS_WITH_CC | _C_READ_COMMANDS_BARE

# 5250 orders (inside WTD data)
_O_SBA = 0x11
_O_RA = 0x02
_O_EA = 0x03
_O_IC = 0x13
_O_SF = 0x1D
_O_TD = 0x10
_O_MC = 0x14

# AID keys (5250)
_A5_ENTER = 0xF1
_A5_HELP = 0xF3
_A5_ROLLDOWN = 0xF4
_A5_ROLLUP = 0xF5
_A5_PRINT = 0xF6
_A5_CLEAR = 0xBD
_A5_PF1 = 0x31
_A5_PF2 = 0x32
_A5_PF3 = 0x33
_A5_PF4 = 0x34
_A5_PF5 = 0x35
_A5_PF6 = 0x36
_A5_PF7 = 0x37
_A5_PF8 = 0x38
_A5_PF9 = 0x39
_A5_PF10 = 0x3A
_A5_PF11 = 0x3B
_A5_PF12 = 0x3C
_A5_PF13 = 0xB1
_A5_PF14 = 0xB2
_A5_PF15 = 0xB3
_A5_PF16 = 0xB4
_A5_PF17 = 0xB5
_A5_PF18 = 0xB6
_A5_PF19 = 0xB7
_A5_PF20 = 0xB8
_A5_PF21 = 0xB9
_A5_PF22 = 0xBA
_A5_PF23 = 0xBB
_A5_PF24 = 0xBC


class _Tn5250Backend(_TerminalBackend):
    """Pure-Python TN5250 client for IBM i / AS400 hosts.

    Implements RFC 1205 / RFC 2877 enough to:

    * Complete TN5250 telnet negotiation (BINARY + EOR + TERMINAL-TYPE
      as IBM-3477-FC) without falling back to NVT.
    * Parse Write-To-Display command streams into a 24×80 EBCDIC screen
      buffer with cursor tracking + Start-Field metadata.
    * Send Read-Input-Fields responses carrying the AID key + cursor
      position + modified field data.

    Not a full 5250 implementation — no partitions, no scrolled data,
    no printer support. Enough to sign on to IBM i and drive typical
    text menus.
    """

    _MODEL = "IBM-3477-FC"  # 24x80 color extended field data

    def __init__(
        self,
        *,
        codepage: str = "cp037",
        trace: bool = False,
        rows: int = 24,
        cols: int = 80,
    ) -> None:
        import socket as _socket

        self._socket = _socket
        self._sock: Any = None
        self._trace = trace
        self._codepage = codepage
        self._rows = rows
        self._cols = cols
        self._screen: list[list[str]] = [[" "] * cols for _ in range(rows)]
        self._cursor: tuple[int, int] = (1, 1)
        self._fields: list[FieldInfo] = []
        # Type buffer — accumulates characters typed since the last
        # cursor move / last AID; flushed into the RIF response.
        self._pending_writes: list[tuple[int, int, bytes]] = []
        # Cursor position we should send in the next RIF response.
        self._input_cursor: tuple[int, int] = (1, 1)
        self._connected = False

    # ---- lifecycle ------------------------------------------------------ #

    def connect(self, host: str, port: int, *, session_type: str) -> None:
        self._sock = self._socket.create_connection((host, port), timeout=15)
        self._sock.settimeout(5.0)
        self._negotiate()
        self._connected = True
        # Read the initial WTD burst so the buffer is populated.
        try:
            self._read_records(timeout=8.0)
        except (self._socket.timeout, OSError):
            pass

    def disconnect(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.shutdown(self._socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._sock is not None

    # ---- telnet negotiation --------------------------------------------- #

    def _negotiate(self) -> None:
        """Perform the TN5250 handshake.

        We ADVERTISE (WILL) both TERMINAL-TYPE and NEW-ENVIRON so the
        server sees us as a proper TN5250 client; we accept (DO) EOR
        and BINARY in both directions.
        """
        # Send our advertisements first so IBM i sees a real 5250 client.
        self._send_raw(bytes([_T_IAC, _T_WILL, _TOPT_NEWENV]))
        self._send_raw(bytes([_T_IAC, _T_WILL, _TOPT_TERMTYPE]))
        self._send_raw(bytes([_T_IAC, _T_DO, _TOPT_EOR]))
        self._send_raw(bytes([_T_IAC, _T_WILL, _TOPT_EOR]))
        self._send_raw(bytes([_T_IAC, _T_DO, _TOPT_BINARY]))
        self._send_raw(bytes([_T_IAC, _T_WILL, _TOPT_BINARY]))

        # Read + answer telnet commands until we see the first record.
        deadline_bytes = 4096
        rx = bytearray()
        while deadline_bytes > 0:
            try:
                chunk = self._sock.recv(1024)
            except (self._socket.timeout, OSError):
                break
            if not chunk:
                break
            rx += chunk
            deadline_bytes -= len(chunk)
            i = 0
            while i < len(rx):
                if rx[i] != _T_IAC:
                    # Non-telnet byte — start of 5250 data. Push back
                    # to the read buffer.
                    self._push_rx(bytes(rx[i:]))
                    return
                if i + 1 >= len(rx):
                    break
                cmd = rx[i + 1]
                if cmd == _T_SB:
                    # Sub-negotiation: seek IAC SE.
                    j = i + 2
                    while j < len(rx) - 1 and not (rx[j] == _T_IAC and rx[j + 1] == _T_SE):
                        j += 1
                    if j >= len(rx) - 1:
                        break
                    sub = bytes(rx[i + 2 : j])
                    self._on_subneg(sub)
                    i = j + 2
                    continue
                if cmd in (_T_DO, _T_DONT, _T_WILL, _T_WONT):
                    if i + 2 >= len(rx):
                        break
                    opt = rx[i + 2]
                    self._on_negotiation(cmd, opt)
                    i += 3
                    continue
                # Any other 2-byte command — skip.
                i += 2
            del rx[:i]
        # Push back whatever we still have.
        self._push_rx(bytes(rx))

    def _on_negotiation(self, verb: int, opt: int) -> None:
        # DO X → WILL X (if we support it) else WONT.
        if verb == _T_DO:
            if opt in (_TOPT_BINARY, _TOPT_EOR, _TOPT_TERMTYPE, _TOPT_NEWENV):
                self._send_raw(bytes([_T_IAC, _T_WILL, opt]))
            else:
                self._send_raw(bytes([_T_IAC, _T_WONT, opt]))
        elif verb == _T_DONT:
            self._send_raw(bytes([_T_IAC, _T_WONT, opt]))
        elif verb == _T_WILL:
            if opt in (_TOPT_BINARY, _TOPT_EOR, _TOPT_TERMTYPE, _TOPT_NEWENV):
                self._send_raw(bytes([_T_IAC, _T_DO, opt]))
            else:
                self._send_raw(bytes([_T_IAC, _T_DONT, opt]))
        elif verb == _T_WONT:
            self._send_raw(bytes([_T_IAC, _T_DONT, opt]))

    def _on_subneg(self, sub: bytes) -> None:
        if len(sub) < 2:
            return
        if sub[0] == _TOPT_TERMTYPE and sub[1] == 0x01:  # SEND
            payload = bytes([_TOPT_TERMTYPE, 0x00]) + self._MODEL.encode("ascii")
            self._send_raw(bytes([_T_IAC, _T_SB]) + payload + bytes([_T_IAC, _T_SE]))
        elif sub[0] == _TOPT_NEWENV and sub[1] == 0x01:  # SEND (VARs)
            # Reply with an empty IS — no user vars.
            payload = bytes([_TOPT_NEWENV, 0x00])
            self._send_raw(bytes([_T_IAC, _T_SB]) + payload + bytes([_T_IAC, _T_SE]))

    # ---- socket + record IO --------------------------------------------- #

    _rx_backlog: bytes = b""

    def _push_rx(self, data: bytes) -> None:
        self._rx_backlog = data + self._rx_backlog

    def _send_raw(self, data: bytes) -> None:
        if self._trace:
            _LOG.info("tn5250 → raw %s", data[:32].hex())
        self._sock.sendall(data)

    def _recv_bytes(self, n: int, timeout: float) -> bytes:
        """Read at most n bytes with respect to backlog + timeout."""
        if self._rx_backlog:
            out = self._rx_backlog[:n]
            self._rx_backlog = self._rx_backlog[n:]
            return out
        self._sock.settimeout(timeout)
        return self._sock.recv(n)

    def _read_records(self, timeout: float) -> list[bytes]:
        """Consume telnet-framed records; return list of 5250 payloads."""
        records: list[bytes] = []
        buf = bytearray(self._rx_backlog)
        self._rx_backlog = b""
        deadline = time.monotonic() + timeout
        while True:
            # Look for IAC EOR in buf, handling IAC IAC escape.
            record_end = -1
            i = 0
            while i < len(buf) - 1:
                if buf[i] == _T_IAC:
                    if buf[i + 1] == _T_EOR:
                        record_end = i
                        break
                    if buf[i + 1] == _T_IAC:
                        i += 2
                        continue
                    if buf[i + 1] in (_T_DO, _T_DONT, _T_WILL, _T_WONT):
                        if i + 2 >= len(buf):
                            break
                        # Handle mid-stream negotiation.
                        self._on_negotiation(buf[i + 1], buf[i + 2])
                        del buf[i : i + 3]
                        continue
                    if buf[i + 1] == _T_SB:
                        # Skip subneg block.
                        j = i + 2
                        while j < len(buf) - 1 and not (buf[j] == _T_IAC and buf[j + 1] == _T_SE):
                            j += 1
                        if j >= len(buf) - 1:
                            break
                        self._on_subneg(bytes(buf[i + 2 : j]))
                        del buf[i : j + 2]
                        continue
                    i += 2
                    continue
                i += 1
            if record_end >= 0:
                raw = bytes(buf[:record_end]).replace(b"\xff\xff", b"\xff")
                records.append(raw)
                del buf[: record_end + 2]
                self._process_record(raw)
                continue
            if time.monotonic() >= deadline and not records:
                # No record yet — bail so caller can keep polling.
                self._rx_backlog = bytes(buf)
                return records
            if records:
                # We already got at least one record; drain briefly.
                self._sock.settimeout(0.2)
            else:
                self._sock.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                chunk = self._sock.recv(4096)
            except (self._socket.timeout, OSError):
                self._rx_backlog = bytes(buf)
                return records
            if not chunk:
                self._rx_backlog = bytes(buf)
                return records
            buf += chunk

    # ---- 5250 record parsing -------------------------------------------- #

    def _process_record(self, data: bytes) -> None:
        """Parse a 5250 record: GDS header (10 bytes) + ESC-framed commands.

        Each 5250 command is preceded by an ESC byte (0x04). The codes are
        spelled out as ``_C_*`` constants above rather than inline here —
        see those constants for the authoritative set.
        """
        if len(data) < 10:
            return
        if self._trace:
            _LOG.info("tn5250 ← record %s", data[:80].hex())
        cursor = 10
        while cursor < len(data):
            b = data[cursor]
            # Any byte before ESC is header padding / continuation; skip.
            if b != 0x04:
                cursor += 1
                continue
            if cursor + 1 >= len(data):
                break
            cmd = data[cursor + 1]
            cursor += 2
            if cmd == 0x11:
                cursor = self._parse_wtd(data, cursor)
            elif cmd in (_C_CLEAR_UNIT, _C_CLEAR_UNIT_ALT):
                self._screen = [[" "] * self._cols for _ in range(self._rows)]
                self._fields = []
            elif cmd == _C_CLEAR_FORMAT_TABLE:
                # The host's only way to retire a panel's fields without also
                # wiping the screen — a repaint after an input error sends it
                # alone. Misfiling it as a read left every previous panel's
                # fields in _fields, so field_after() matched a stale entry
                # and typed into coordinates that belonged to the old screen.
                self._fields = []
            elif cmd in _C_READ_COMMANDS_WITH_CC:
                # 2 CC bytes then the host waits for our input record.
                cursor += 2
            elif cmd in _C_READ_COMMANDS_BARE:
                # Read Immediate and Read Screen Immediate carry no control
                # characters at all — consuming two here swallowed the next
                # ESC + command.
                pass
            elif cmd == 0xF3:
                # Write Structured Field — length-prefixed nested cmd
                if cursor + 1 >= len(data):
                    break
                sf_len = (data[cursor] << 8) | data[cursor + 1]
                cursor += max(sf_len, 4)
            else:
                # Unknown command — skip one byte and try to resync at
                # the next ESC.
                pass

    def _parse_wtd(self, data: bytes, cursor: int) -> int:
        """Parse a Write-To-Display command stream starting at *cursor*.

        Returns the cursor position AT the next command escape (0x04) or
        end of data. Inside WTD, 0x04 is not a valid order — it always
        marks the start of the next 5250 command.
        """
        if cursor + 2 > len(data):
            return len(data)
        # CC bytes (2)
        cursor += 2
        # Track running position on the screen.
        row = 1
        col = 1
        while cursor < len(data):
            b = data[cursor]
            if b == 0x04:
                # Next command escape — return so caller resumes.
                return cursor
            if b == 0x01:
                # SOH — Start of Header. Length byte at cursor+1 tells
                # us how many bytes to skip.
                if cursor + 1 >= len(data):
                    break
                soh_len = data[cursor + 1]
                cursor += 2 + soh_len
                continue
            if b == _O_SBA:
                if cursor + 2 >= len(data):
                    break
                row = data[cursor + 1]
                col = data[cursor + 2]
                cursor += 3
            elif b == _O_IC:
                if cursor + 2 >= len(data):
                    break
                self._cursor = (data[cursor + 1], data[cursor + 2])
                cursor += 3
            elif b == _O_RA:
                # Repeat char to address: RA row col char
                if cursor + 3 >= len(data):
                    break
                end_row = data[cursor + 1]
                end_col = data[cursor + 2]
                ch_byte = data[cursor + 3]
                ch = bytes([ch_byte]).decode(self._codepage, errors="replace")
                target_end = (end_row - 1) * self._cols + (end_col - 1)
                start = (row - 1) * self._cols + (col - 1)
                while start < target_end:
                    r = start // self._cols
                    c = start % self._cols
                    if 0 <= r < self._rows and 0 <= c < self._cols:
                        self._screen[r][c] = ch
                    start += 1
                row = end_row
                col = end_col
                cursor += 4
            elif b == _O_SF:
                # 5250 Start Field: [FFW (2 bytes) [+ FCW pairs]] + attribute
                # byte + field length (2 bytes). The optional parts cannot be
                # told apart by a flag bit — the attribute byte is the one
                # identified by ``(byte & 0xE0) == 0x20``, and everything
                # before it after the FFW is a stream of two-byte FCWs
                # (RFC 1205 §5.2.4.3 / §7; same discrimination the tn5250
                # reference client uses).
                #
                # The attribute byte consumes ONE screen position at the
                # current (row, col) — rendered as a blank space. Missing
                # this advance is the source of a 1-col drift that would
                # push every subsequent field one cell to the left of where
                # the host intended it.
                if cursor + 1 >= len(data):
                    break
                cursor += 1
                ffw_hi = 0
                ffw_lo = 0
                has_ffw = False
                if (data[cursor] & 0xE0) != 0x20:
                    has_ffw = True
                    if cursor + 1 >= len(data):
                        break
                    ffw_hi = data[cursor]
                    ffw_lo = data[cursor + 1]
                    cursor += 2
                    # An FCW is two bytes — a truncated record holding only
                    # the first must not push the cursor past the end.
                    while cursor + 1 < len(data) and (data[cursor] & 0xE0) != 0x20:
                        cursor += 2
                # Capture the attribute byte value HERE (while cursor still
                # points at it) — otherwise a truncated WTD stream that
                # omits the attribute byte lets the later ``data[cursor-1]``
                # read fall onto the last FCW byte and misinterpret it as
                # display flags. Default 0 when the byte is missing.
                display_attr = 0
                if cursor < len(data) and (data[cursor] & 0xE0) == 0x20:
                    display_attr = data[cursor]
                    r, c = row - 1, col - 1
                    if 0 <= r < self._rows and 0 <= c < self._cols:
                        self._screen[r][c] = " "
                    col += 1
                    if col > self._cols:
                        col = 1
                        row = min(row + 1, self._rows)
                    cursor += 1
                # The attribute byte is followed by the two-byte field
                # length (RFC 1205 §5.2.4.3). Leaving those bytes in the
                # stream feeds them back to the order dispatcher, where an
                # LL_lo of 0x20-0x3F is taken for an inline display
                # attribute and blanks a cell — reintroducing the very
                # column drift the branches above exist to prevent — and an
                # LL_lo >= 0x40 paints an EBCDIC character on the screen.
                length = 0
                if cursor + 1 < len(data):
                    length = (data[cursor] << 8) | data[cursor + 1]
                    cursor += 2
                    # A corrupt or truncated stream can yield an LL of
                    # 0xFFFF; a field cannot run past the end of the
                    # screen, and TerminalField.clear() would otherwise
                    # send 65535 spaces to the host.
                    start = (row - 1) * self._cols + (col - 1)
                    length = max(0, min(length, self._rows * self._cols - start))
                if not has_ffw:
                    # No FFW means no input field: the SF order carries a
                    # display attribute for a protected zone only. This is
                    # tn5250's ``input_field`` discrimination in
                    # ``tn5250_session_start_of_field`` — the attribute and
                    # the length bytes are consumed either way, but only an
                    # FFW-bearing SF creates a field.
                    continue
                # FFW bit layout (RFC 1205 §5.2.4.3 + tn5250's field.h):
                #   byte 0 (ffw_hi):
                #     0xC0 = B'01', the constant that identifies an FFW
                #     0x20 = bypass (protected — cursor auto-skips)
                #     0x10 = duplication allowed
                #     0x08 = MDT (modified since last read)
                #     0x07 = field shift / edit specification
                #   byte 1 (ffw_lo):
                #     0x80 = auto-enter   0x40 = FER   0x20 = monocase
                #     0x08 = mandatory entry   0x07 = mandatory fill
                # Shift/edit values that restrict entry to digits:
                #   2 numeric shift, 3 numeric only, 5 digits only,
                #   7 signed numeric.
                # The nearby attribute byte (already consumed above) carries
                # the display flags; nondisplay is (attr & 0x07) == 0x07,
                # which covers 0x27 — the usual password attribute.
                bypass = bool(ffw_hi & 0x20)
                numeric = (ffw_hi & 0x07) in (0x02, 0x03, 0x05, 0x07)
                hidden = (display_attr & 0x07) == 0x07
                self._fields.append(
                    FieldInfo(
                        row=row,
                        col=col,
                        length=length,
                        protected=bypass,
                        hidden=hidden,
                        numeric=numeric,
                        modified=False,
                        attr_byte=(ffw_hi << 8) | ffw_lo,
                    )
                )
            elif 0x20 <= b <= 0x3F:
                # 5250 inline display attribute byte (color/highlight/blink).
                # Occupies ONE screen position (rendered as blank) and
                # resets the visual attribute for subsequent characters.
                # Missing this advance is the second source of the col
                # drift on pub400's sign-on screen — each of the ~20
                # attribute markers per record pushed content 1 col left.
                r, c = row - 1, col - 1
                if 0 <= r < self._rows and 0 <= c < self._cols:
                    self._screen[r][c] = " "
                col += 1
                if col > self._cols:
                    col = 1
                    row = min(row + 1, self._rows)
                cursor += 1
            elif b < 0x40:
                # Genuine control byte (0x00-0x1F) — skip.
                cursor += 1
            else:
                # Data byte: EBCDIC char to screen[row][col].
                ch = bytes([b]).decode(self._codepage, errors="replace")
                r = row - 1
                c = col - 1
                if 0 <= r < self._rows and 0 <= c < self._cols:
                    self._screen[r][c] = ch
                col += 1
                if col > self._cols:
                    col = 1
                    row = min(row + 1, self._rows)
                cursor += 1
        # Fill in any field whose length bytes were cut off by a truncated
        # record; the SF order carries the real length for the rest.
        self._recompute_field_lengths()
        return cursor

    def _recompute_field_lengths(self) -> None:
        """Approximate the length of fields the SF order did not supply."""
        for i, f in enumerate(self._fields):
            if f.length:
                continue
            if i + 1 < len(self._fields):
                nxt = self._fields[i + 1]
                if nxt.row == f.row:
                    f.length = max(0, nxt.col - f.col - 1)
                else:
                    f.length = max(0, self._cols - f.col + 1)
            else:
                f.length = max(0, self._cols - f.col + 1)

    # ---- backend interface --------------------------------------------- #

    def read_screen(self) -> tuple[list[str], tuple[int, int]]:
        # Pump any pending records without blocking too long.
        try:
            self._read_records(timeout=0.05)
        except OSError:
            pass
        lines = ["".join(row) for row in self._screen]
        return lines, self._cursor

    def read_fields(self) -> list[FieldInfo]:
        # Return copies so callers cannot mutate our state.
        return list(self._fields)

    def send_string(self, text: str) -> None:
        row, col = self._input_cursor if self._input_cursor != (1, 1) else self._cursor
        data = text.encode(self._codepage, errors="replace")
        self._pending_writes.append((row, col, data))
        # Advance in-memory cursor + local screen preview.
        for ch in text:
            r, c = row - 1, col - 1
            if 0 <= r < self._rows and 0 <= c < self._cols:
                self._screen[r][c] = ch
            col += 1
            if col > self._cols:
                col = 1
                row = min(row + 1, self._rows)
        self._input_cursor = (row, col)

    def move_cursor(self, row: int, col: int) -> None:
        self._input_cursor = (row, col)
        self._cursor = (row, col)

    def send_aid(self, aid: str) -> None:
        """Send an AID key to the host.

        .. warning::
            The native TN5250 backend's AID / field-write path was
            **known not to trigger a host response on pub400.com**.
            The record emitted the AID ahead of the cursor address,
            so byte 0 of every record was the AID code where the host
            expects a row — 0xF1 for Enter reads as row 241. That is
            corrected here to the reference tn5250 client's order
            (row, column, AID), but the fix has **not** been confirmed
            against a live IBM i, so treat the write path as unverified
            rather than as working.

            (A missing **WSF Query Reply** handshake may additionally
            be required for TN5250E device negotiation, but a malformed
            input record explains the observed symptom on its own.)

            Read-side (screen enumeration, field detection, cursor
            tracking, EBCDIC → Unicode) IS production-ready.

            Interim workarounds for write-side interaction with an
            IBM i host:

            * Use ``backend='hllapi'`` with a running enterprise
              emulator (PCOMM, Attachmate) — HLLAPI's field-write path
              goes through the emulator's own 5250 protocol stack.
            * Use ``backend='s3270'`` which falls back to NVT (raw
              telnet echo) — writes single characters but does not
              submit forms.
            * Fall back to SSH on port 2222 (pub400 advertises this)
              for text-mode interaction.
        """
        aid_map = {
            "enter": _A5_ENTER,
            "clear": _A5_CLEAR,
            "help": _A5_HELP,
        }
        low = aid.lower()
        if low in ("pa1", "pa2", "pa3"):
            # These are 3270 keys. Substituting "the nearest" PF sent PF1 —
            # Help on IBM i — and reported success, so a suite ported from the
            # s3270 backend popped a help panel and carried on believing it had
            # sent an attention key.
            raise MainframeError(
                f"5250 has no {aid.upper()} key",
                hint=(
                    "PA1..PA3 are 3270-only — use AID.CLEAR or an AID.pf(n), or "
                    "connect the 3270 backend if the host really is a 3270 system"
                ),
            )
        aid_code: int | None = aid_map.get(low)
        if aid_code is None and low.startswith("pf"):
            try:
                n = int(low[2:])
            except ValueError as exc:
                raise MainframeError(f"invalid PF key: {aid!r}") from exc
            if not 1 <= n <= 24:
                # Unchecked arithmetic runs straight off the end of the PF
                # block: PF25 lands on 0xBD, which is Clear.
                raise MainframeError(
                    f"invalid PF key: {aid!r}",
                    hint="5250 defines PF1..PF24 — use AID.pf(n) to build the name",
                )
            base = _A5_PF1
            aid_code = base + (n - 1) if n <= 12 else _A5_PF13 + (n - 13)
        if aid_code is None:
            raise MainframeError(
                f"unknown AID key: {aid!r}",
                hint=(
                    "valid values are AID.ENTER, AID.CLEAR, AID.HELP and "
                    "AID.pf(n) for n in 1..24 — PA1..PA3 are 3270-only"
                ),
            )

        # Build the input record: GDS header + cursor + AID + fields.
        row, col = self._input_cursor if self._input_cursor != (1, 1) else self._cursor
        if not (1 <= row <= 255 and 1 <= col <= 255):
            # One byte each on the wire, so an out-of-range cursor cannot be
            # encoded at all — say so instead of letting bytearray.append
            # raise a bare ValueError from inside the protocol layer.
            raise MainframeError(
                f"cursor ({row}, {col}) cannot be encoded in a 5250 input record",
                hint="row and column are single bytes — both must be in 1..255",
            )
        payload = bytearray()
        # Cursor row, cursor column, then the AID — the order the reference
        # tn5250 client writes (lib5250/session.c, tn5250_session_send_fields).
        # Emitting the AID first made byte 0 of every record the AID code,
        # which the host reads as a cursor row: 0xF1 for Enter is row 241, so
        # the record is malformed and IBM i simply never answers it.
        payload.append(row)
        payload.append(col)
        payload.append(aid_code)
        # Fields: for each pending write, emit SBA + data. Checked like the
        # cursor above, and not covered by that check: send_string() normalises
        # _input_cursor when it wraps but leaves the original coordinates in
        # _pending_writes, so an out-of-range write slipped past the guard and
        # still raised a bare ValueError from bytearray.append.
        for r, c, data in self._pending_writes:
            if not (1 <= r <= 255 and 1 <= c <= 255):
                # Dropped before raising, or the poisoned entry stays in the
                # queue and every later send_aid re-raises on it — including
                # ones the caller has since corrected.
                self._pending_writes = []
                raise MainframeError(
                    f"pending write at ({r}, {c}) cannot be encoded in a 5250 input record",
                    hint="row and column are single bytes — both must be in 1..255",
                )
            payload.append(_O_SBA)
            payload.append(r)
            payload.append(c)
            payload += data
        # Wrap in GDS record. RFC 1205 §4:
        #   bytes 0-1: total length (network byte order)
        #   bytes 2-3: record type 0x12A0 (normal 5250 data)
        #   bytes 4-5: reserved 0x0000
        #   byte 6:    variable header length = 0x04
        #   byte 7:    reserved
        #   byte 8:    flags 0x00
        #   byte 9:    opcode
        #              0x03 = Put/Get (client-to-host input data)
        length = len(payload) + 10
        header = bytearray(
            [
                (length >> 8) & 0xFF,
                length & 0xFF,
                0x12,
                0xA0,
                0x00,
                0x00,
                0x04,
                0x00,
                0x00,  # flags
                0x03,  # opcode: Put/Get (client-to-host input data)
            ]
        )
        record = bytes(header + payload)
        # Escape IAC + trailing IAC EOR.
        wire = record.replace(b"\xff", b"\xff\xff") + bytes([_T_IAC, _T_EOR])
        self._sock.sendall(wire)
        # Reset pending state.
        self._pending_writes = []
        self._input_cursor = (1, 1)

    def wait_unlock(self, timeout: float) -> None:
        # Best-effort: pump records for a moment.
        try:
            self._read_records(timeout=timeout)
        except OSError:
            pass

    def wait_output(self, timeout: float) -> None:
        try:
            self._read_records(timeout=timeout)
        except OSError:
            pass

    def is_keyboard_locked(self) -> bool:
        return False  # TN5250 client does not track keyboard state itself.


# --------------------------------------------------------------------------- #
# Public facade                                                                #
# --------------------------------------------------------------------------- #


class MainframeTerminal:
    """Automate a mainframe (3270) or midrange (5250) terminal session.

    Two backends implement the same surface — pick with the ``backend``
    argument to :meth:`Desktop.mainframe`:

    * ``"s3270"`` (default) — spawns the ``ws3270`` binary. Testable on
      any machine, works against public hosts like ``pub400.com``. Needs
      the wc3270 / x3270 package installed and on PATH (or set via
      ``ws3270_path``).

    * ``"hllapi"`` — attaches through EHLLAPI to a running enterprise
      emulator. No host/port needed; the emulator handles the network
      side. Pass ``session_id="A"`` etc. to select the PS.

    Construction goes through the factory ``Desktop.mainframe(...)`` — do
    not instantiate directly outside tests.
    """

    #: Registered :class:`Backend` id this facade fronts.
    backend_id: str = "mainframe"

    @classmethod
    def backend(cls):
        from ._backend import resolve as _resolve

        return _resolve(cls.backend_id)

    @classmethod
    def backend_supports(cls, capability) -> bool:
        return cls.backend().supports(capability)

    @classmethod
    def require_capability(cls, capability) -> None:
        cls.backend().require_capability(capability)

    def __init__(self, backend: _TerminalBackend) -> None:
        self._backend = backend
        self._last_screen: TerminalScreen | None = None

    # ---- session -------------------------------------------------------- #

    def connect(
        self,
        host: str = "",
        port: int = 23,
        *,
        session_type: str = "3270",
        timeout: float = 15.0,
    ) -> None:
        """Establish the session.

        For s3270 this dials TCP to *host:port*. For HLLAPI this attaches
        to the emulator's presentation space (arguments are recorded but
        ignored — the emulator manages the network side).
        """
        self._backend.connect(host, port, session_type=session_type)
        # Give the host a moment to paint the first screen.
        try:
            self._backend.wait_output(timeout)
        except MainframeError:
            pass

    def disconnect(self) -> None:
        """Tear down the session. Idempotent."""
        self._backend.disconnect()

    def is_connected(self) -> bool:
        return self._backend.is_connected()

    # ---- screen --------------------------------------------------------- #

    def screen(self) -> TerminalScreen:
        """Return a fresh :class:`TerminalScreen` snapshot of the PS."""
        lines, cursor = self._backend.read_screen()
        self._last_screen = TerminalScreen(lines, cursor)
        return self._last_screen

    def text(self) -> str:
        """Convenience: ``self.screen().text()``."""
        return self.screen().text()

    def field(self, *, row: int, col: int, length: int = 1) -> TerminalField:
        """Return a :class:`TerminalField` bound to (*row*, *col*, *length*)."""
        return TerminalField(self, row, col, length)

    def fields(self) -> list[FieldInfo]:
        """Return the full list of Start-Field ranges on the current screen.

        Powered by the backend's ``ReadBuffer`` parser (s3270). For the
        HLLAPI backend this currently returns an empty list — callers
        should fall back to :meth:`field` with explicit coordinates.

        Each :class:`FieldInfo` in the returned list carries ``row``,
        ``col``, ``length``, ``protected``, ``hidden`` etc. — enough to
        pick writable slots without column-counting.
        """
        return self._backend.read_fields()

    def field_after(
        self,
        label: str,
        *,
        length: int | None = None,
        row: int | None = None,
    ) -> TerminalField:
        """Return the writable field that follows *label* on the screen.

        Args:
            label: Substring to search for.
            length: Force the returned field's declared length. When
                ``None`` (default), the field boundary is auto-detected
                from the host's Start-Field attributes — this is the
                preferred usage.
            row: Restrict search to this 1-indexed row; ``None`` searches
                the whole screen and returns the first hit.

        Returns the first writable field whose Start-Field position is
        past the label's end. When the auto-detection cannot find a
        writable field for the row (e.g. HLLAPI backend, or a field
        list that has not populated yet), falls back to the label-scan
        heuristic — bounded to at most 4 separator characters so a
        blank input area does not run the loop to end-of-row.

        Raises:
            MainframeError: if *label* is not on the current screen.
        """
        s = self.screen()
        if row is not None:
            line = s.line(row)
            idx = line.find(label)
            if idx == -1:
                raise MainframeError(
                    f"label {label!r} not found on row {row}",
                    hint=(
                        f"check screen().line({row}) or wait_for_text({label!r}, row={row}) before "
                        f"field_after()"
                    ),
                )
            found_row = row
            found_col = idx + 1
        else:
            hit = s.find(label)
            if hit is None:
                raise MainframeError(
                    f"label {label!r} not found on screen",
                    hint=(
                        f"wait_for_text({label!r}) before field_after(), or print(term.text()) to "
                        f"inspect the current buffer"
                    ),
                )
            found_row, found_col = hit
        label_end_col = found_col + len(label) - 1

        # Auto-detect via field attribute map — first writable field
        # whose position is past the label.
        for info in self.fields():
            if info.protected:
                continue
            if info.row == found_row and info.col > label_end_col:
                use_length = length if length is not None else info.length
                return TerminalField(self, info.row, info.col, use_length)
            # Or on a wrapped-next-row field (rare on standard sign-on
            # screens but the buffer is a linear stream).
            if (info.row, info.col) > (found_row, label_end_col + 1) and length is None:
                return TerminalField(self, info.row, info.col, info.length)

        # Fallback: heuristic scan bounded to 4 separator chars.
        line = s.line(found_row)
        cursor = label_end_col
        skipped = 0
        while cursor < s.cols and skipped < 4 and line[cursor] in " .:_>":
            cursor += 1
            skipped += 1
        return TerminalField(self, found_row, cursor + 1, length or 20)

    # ---- input ---------------------------------------------------------- #

    def type_text(self, text: str) -> None:
        """Type *text* at the current cursor position."""
        self._backend.send_string(text)

    def press(self, aid: str) -> None:
        """Send an AID key (``"Enter"``, ``"PF3"``, ``"Clear"`` …).

        Accepts any name from :class:`AID` or ``"PFN"`` / ``"PAN"``.

        ``PA1``–``PA3`` are 3270 keys: the s3270 and HLLAPI backends send
        them, and the native TN5250 backend raises, because 5250 has no
        equivalent and substituting the nearest PF key sent Help while
        reporting success.
        """
        self._backend.send_aid(aid)

    def move_cursor(self, row: int, col: int) -> None:
        """Move the input cursor to a 1-indexed position."""
        self._backend.move_cursor(row, col)

    # ---- waits ---------------------------------------------------------- #

    def wait_ready(self, timeout: float = 15.0) -> None:
        """Block until the keyboard is unlocked (host finished responding)."""
        self._backend.wait_unlock(timeout)

    def wait_change(self, timeout: float = 15.0) -> None:
        """Block until the host writes to the screen after your last input."""
        self._backend.wait_output(timeout)

    def wait_for_text(
        self,
        needle: str,
        *,
        timeout: float = 15.0,
        row: int | None = None,
        poll_interval: float = 0.25,
    ) -> None:
        """Poll ``screen().contains(needle, row=row)`` until True or *timeout*.

        Empty ``needle`` is rejected — ``"" in any_string`` is
        universally True, so ``wait_for_text("")`` would return
        immediately regardless of screen content (silent no-op that
        looks like a passing assertion). Same footgun class as the
        Locator/JABLocator/DelphiComponent ``wait_for_text`` guards
        added earlier.
        """
        if not needle:
            raise ValueError(
                "wait_for_text(needle='') always matches immediately — "
                "pass a real substring; use wait_ready() to wait for the "
                "screen buffer to become non-empty"
            )
        self._poll_until(
            lambda: self.screen().contains(needle, row=row),
            timeout=timeout,
            poll_interval=poll_interval,
            description=f"text {needle!r}" + (f" on row {row}" if row else ""),
        )

    def wait_for_cursor(
        self,
        row: int,
        col: int,
        *,
        timeout: float = 15.0,
        poll_interval: float = 0.1,
    ) -> None:
        """Block until the input cursor arrives at ``(row, col)``.

        Some hosts move the cursor asynchronously after an AID — the
        follow-up screen may be readable before the cursor lands where
        the app wants the next input. Waiting on cursor position avoids
        the race where a subsequent ``type_text`` fires into the wrong
        field.
        """
        self._poll_until(
            lambda: self.screen().cursor == (row, col),
            timeout=timeout,
            poll_interval=poll_interval,
            description=f"cursor at ({row},{col})",
        )

    def wait_for_field(
        self,
        row: int,
        col: int,
        *,
        writable: bool = True,
        timeout: float = 15.0,
        poll_interval: float = 0.1,
    ) -> FieldInfo:
        """Block until a field starting at ``(row, col)`` appears.

        By default (``writable=True``) waits for an unprotected field so
        the caller can immediately type into it. Set ``writable=False``
        to accept protected labels/display zones too — useful for
        waiting on a header the host paints last.

        Returns the matching :class:`FieldInfo`.
        """
        result: dict[str, Any] = {}

        def _found() -> bool:
            for f in self.fields():
                if f.row == row and f.col == col and (not writable or f.writable):
                    result["hit"] = f
                    return True
            return False

        self._poll_until(
            _found,
            timeout=timeout,
            poll_interval=poll_interval,
            description=f"{'writable ' if writable else ''}field at ({row},{col})",
        )
        return result["hit"]

    def wait_for(
        self,
        predicate: Callable[[MainframeTerminal], bool],
        *,
        timeout: float = 15.0,
        poll_interval: float = 0.1,
        description: str = "predicate",
    ) -> None:
        """Escape hatch: block until *predicate(self)* returns True.

        Use this when the built-in waits do not cover your case —
        e.g. "wait until row 22 shows 'MENU' AND cursor is on row 5".

        The predicate is called with ``self`` as its only argument and
        must return a truthy value when the condition holds. It runs
        on the caller's thread; any exception it raises propagates.
        """
        self._poll_until(
            lambda: bool(predicate(self)),
            timeout=timeout,
            poll_interval=poll_interval,
            description=description,
        )

    def _poll_until(
        self,
        check: Callable[[], bool],
        *,
        timeout: float,
        poll_interval: float,
        description: str,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if check():
                    return
            except MainframeError:
                # Backend hiccup mid-poll — retry.
                pass
            time.sleep(poll_interval)
        raise MainframeError(
            f"{description} did not become true within {timeout}s",
            hint=(
                "increase timeout=, or print(term.text()) to inspect the buffer at the moment the "
                "wait started"
            ),
        )

    def is_keyboard_locked(self) -> bool:
        """True if the emulator has the keyboard inhibited (host still working)."""
        return self._backend.is_keyboard_locked()

    # ---- context manager ------------------------------------------------ #

    def __enter__(self) -> MainframeTerminal:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.disconnect()

    def __repr__(self) -> str:
        return f"MainframeTerminal(backend={type(self._backend).__name__})"


# --------------------------------------------------------------------------- #
# Factory helpers (called by Desktop.mainframe)                                #
# --------------------------------------------------------------------------- #


def _build_terminal(
    *,
    backend: str,
    ws3270_path: str | None,
    model: str,
    codepage: str | None,
    session_id: str,
    hllapi_dll_path: str | None,
    extra_args: Sequence[str] | None,
    trace: bool = False,
) -> MainframeTerminal:
    if backend == "s3270":
        impl: _TerminalBackend = _S3270Backend(
            binary=ws3270_path,
            model=model,
            codepage=codepage,
            extra_args=extra_args,
            trace=trace,
        )
    elif backend == "hllapi":
        impl = _HLLAPIBackend(session_id=session_id, dll_path=hllapi_dll_path, trace=trace)
    elif backend == "tn5250":
        impl = _Tn5250Backend(codepage=codepage or "cp037", trace=trace)
    else:
        raise MainframeError(
            f"unknown mainframe backend {backend!r}",
            hint=(
                "valid values: 's3270' (default, requires wc3270), 'tn5250' (pure Python, IBM i), "
                "'hllapi' (requires PCOMM/Attachmate DLL)"
            ),
        )
    return MainframeTerminal(impl)
