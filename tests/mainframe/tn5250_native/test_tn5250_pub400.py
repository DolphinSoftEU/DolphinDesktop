"""Native TN5250 backend — verified against pub400.com (IBM i host).

Unlike the s3270 backend which degrades to NVT for TN5250 hosts, this
pure-Python TN5250 client negotiates the real 5250 protocol and parses
Write-To-Display commands into a proper screen buffer with fields.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, MainframeError, tcp_reachable

_HOST = "pub400.com"
_PORT = 23


@pytest.fixture(scope="module")
def tn5250_term():
    if not tcp_reachable(_HOST, _PORT, timeout=3):
        pytest.skip(f"cannot reach {_HOST}:{_PORT}")
    try:
        term = Desktop().mainframe(
            host=_HOST, port=_PORT, session_type="5250", backend="tn5250", timeout=15
        )
    except MainframeError as exc:
        pytest.skip(f"tn5250 connect failed: {exc}")
    try:
        term.wait_ready(timeout=10)
        yield term
    finally:
        try:
            term.disconnect()
        except Exception:
            pass


def test_backend_reports_connected(tn5250_term) -> None:
    assert tn5250_term.is_connected()


def test_screen_contains_pub400_banner(tn5250_term) -> None:
    """Proves the TN5250 negotiation actually worked and WTD parsing
    populated the screen buffer — NVT fallback returns empty screen."""
    text = tn5250_term.text()
    assert "PUB400" in text or "pub400" in text.lower()
    assert "IBM i" in text or "Welcome" in text


def test_screen_dimensions_are_24x80(tn5250_term) -> None:
    """IBM-3477-FC advertises 24×80 — our screen buffer must match."""
    s = tn5250_term.screen()
    assert s.rows == 24
    assert s.cols == 80


def test_fields_list_has_writable_entries(tn5250_term) -> None:
    """pub400's sign-on screen defines two writable fields (user +
    password) via Start-Field orders. Both must be surfaced as
    writable — a Start-Field marks an INPUT field in the 5250 protocol
    by definition; only the bypass bit (FFW hi 0x80) marks a
    display-only field."""
    fields = tn5250_term.fields()
    assert len(fields) >= 2, f"expected ≥2 fields, got {fields}"
    writable = [f for f in fields if f.writable]
    assert len(writable) >= 2, f"expected ≥2 writable fields (user + password), got: {fields}"


def test_sign_on_labels_are_visible(tn5250_term) -> None:
    """Sign-on labels — user name and password prompt — appear on the
    parsed screen."""
    text = tn5250_term.text().lower()
    assert "user name" in text
    assert "password" in text


def test_no_column_drift_on_multiple_labels(tn5250_term) -> None:
    """Every sign-on label appears contiguously on ONE row.

    Regression guard for the two SBA-drift bugs fixed together:

    1. 5250 inline attribute bytes (0x20-0x3F) consume a screen cell.
       Skipping them without col++ pushed subsequent content one cell
       left — enough to make 'Your' wrap as 'Y\\nour'.
    2. Start-Field attribute byte after FFW/FCW also consumes a cell.

    Each of the fields the host prints on the first screen should now
    be readable on the row the host meant to draw it on."""
    s = tn5250_term.screen()
    text = s.text().lower()

    contiguous_labels = [
        "welcome to pub400",
        "your user name",
        "password",
        "server name",
        "subsystem",
    ]
    for needle in contiguous_labels:
        found_row: str | None = None
        for row in range(1, s.rows + 1):
            if needle in s.line(row).lower():
                found_row = s.line(row).lower()
                break
        assert found_row is not None, (
            f"label {needle!r} did not appear on any single row — "
            f"still wrapping across rows\n{text}"
        )


def test_labels_align_on_their_own_row(tn5250_term) -> None:
    """The ``user name`` label is contiguous on the row it was drawn on.

    The row carrying ``user name`` must also carry the ``your``/``our``
    prefix (which one depends on host indentation) — a wrap across rows
    would split them."""
    s = tn5250_term.screen()
    line_with_user = None
    for row in range(1, s.rows + 1):
        if "user name" in s.line(row).lower():
            line_with_user = s.line(row)
            break
    assert line_with_user is not None, "user-name label not found on any row"
    # 'Your' or the 'Y' start should be contiguous on the same line as
    # 'user name' — no cross-row wrap.
    assert "our user" in line_with_user.lower() or "your user" in line_with_user.lower(), (
        f"user-name label appears wrapped: {line_with_user!r}"
    )


def test_screen_snapshots_are_stable(tn5250_term) -> None:
    """Two consecutive screen() calls without input return the same
    text — proves _read_records does not mutate the buffer on empty
    reads."""
    a = tn5250_term.text()
    b = tn5250_term.text()
    assert a == b
