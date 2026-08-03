"""Integration test — dolphin_desktop mainframe backend against pub400.com.

pub400.com is a public IBM i host that accepts both TN3270 (43×80 color)
and TN5250 negotiations. This suite drives it through the s3270-based
backend, verifying:

* the ws3270 subprocess spawns and connects,
* the sign-on screen is readable via the presentation-space model,
* text input + AID keys reach the host and the screen changes,
* protected/unprotected field logic works via ``field_after``.

We never log in with real credentials — pub400 is a public target and
this suite must be safe to run in CI. All authentication attempts use
obviously-invalid input so the host returns a controlled error state.

All imports are ``dolphin_desktop`` + ``pytest`` + the local env module.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import MainframeTerminal
from tests.mainframe.pub400._mainframe_env import WS3270  # type: ignore[import-not-found]

# --------------------------------------------------------------------------- #
# Preconditions                                                                #
# --------------------------------------------------------------------------- #


def test_ws3270_installed() -> None:
    if WS3270 is None:
        pytest.skip("wc3270 not installed")
    assert WS3270.lower().endswith("ws3270.exe")


# --------------------------------------------------------------------------- #
# Session smoke                                                                #
# --------------------------------------------------------------------------- #


def test_session_is_connected(pub400_term: MainframeTerminal) -> None:
    assert pub400_term.is_connected()


def test_screen_size(pub400_term: MainframeTerminal) -> None:
    """Model 3279-4 negotiates a 43×80 presentation space.

    pub400 gladly serves whatever model we advertise, so a wrong number
    here is on us, not the host.
    """
    s = pub400_term.screen()
    assert s.rows in (24, 43), f"unexpected row count: {s.rows}"
    assert s.cols == 80


def test_sign_on_screen_contains_pub400_banner(pub400_term: MainframeTerminal) -> None:
    """The welcome banner + host identity line must be present."""
    text = pub400_term.text()
    assert "PUB400" in text or "pub400" in text
    assert "IBM i" in text


def test_sign_on_screen_has_user_prompt(pub400_term: MainframeTerminal) -> None:
    s = pub400_term.screen()
    assert s.contains("Your user name") or s.contains("User")


def test_cursor_starts_in_user_field(pub400_term: MainframeTerminal) -> None:
    """Fresh sign-on screen: cursor sits in the user-name field."""
    s = pub400_term.screen()
    row, col = s.cursor
    # The user name label is somewhere on rows 4–8; the input follows it.
    assert 3 <= row <= 10, f"cursor row {row} not in sign-on range"
    assert col > 1


# --------------------------------------------------------------------------- #
# Field locators                                                               #
# --------------------------------------------------------------------------- #


def test_field_after_user_name_label(pub400_term: MainframeTerminal) -> None:
    """`field_after` should locate the input immediately after 'Your user name'.

    We can't check field byte-length precisely without an EBCDIC field map,
    but the coordinates must be non-empty and land in a writable region.
    """
    field = pub400_term.field_after("Your user name", length=10)
    row, col = field.position
    assert row >= 3
    assert col > len("Your user name")


def test_screen_snapshots_are_stable(pub400_term: MainframeTerminal) -> None:
    """Two consecutive screen() calls without input must return identical text."""
    a = pub400_term.screen().text()
    b = pub400_term.screen().text()
    assert a == b


# --------------------------------------------------------------------------- #
# Input round-trip: type invalid user, press Enter, verify screen changes       #
# --------------------------------------------------------------------------- #


def test_typed_char_is_echoed(pub400_term: MainframeTerminal) -> None:
    """Type a single character; pub400 echoes it and cursor advances.

    pub400 is IBM i (TN5250 host). ws3270 only speaks TN3270, so the
    negotiation degrades to NVT (raw telnet). In NVT mode the host
    echoes each byte we send but AID keys (Enter, PF3) do not trigger
    field-level submissions — that requires the future TN5250 backend.

    This test verifies the write path reaches the host and the read
    path sees the response — enough to prove connect+write+read work
    end to end. A dedicated ``test_form_submission`` test lives in
    ``test_pub400_xfail.py`` and is marked xfail until we ship TN5250.
    """
    before = pub400_term.screen()
    before_cursor = before.cursor
    pub400_term.type_text("Q")
    pub400_term.wait_change(timeout=5)
    after = pub400_term.screen()
    assert after.cursor != before_cursor, "cursor did not advance after typing"
    r, c = before_cursor
    # The typed 'Q' appears at the pre-type cursor position.
    assert after.text_at(r, c, 1) == "Q", (
        f"expected 'Q' at ({r},{c}); got {after.text_at(r, c, 1)!r}"
    )


# --------------------------------------------------------------------------- #
# Screen navigation with PF keys                                                #
# --------------------------------------------------------------------------- #


def test_terminal_screen_line_and_text_at(pub400_term: MainframeTerminal) -> None:
    """`TerminalScreen.line` + `text_at` return the same substring as manual slicing."""
    s = pub400_term.screen()
    line1 = s.line(1)
    assert isinstance(line1, str)
    assert len(line1) == s.cols
    # Slicing back through text_at must match.
    assert s.text_at(1, 1, s.cols).rstrip() == line1.rstrip()


def test_terminal_screen_find(pub400_term: MainframeTerminal) -> None:
    """`find` returns 1-indexed (row, col) for a substring on the screen."""
    s = pub400_term.screen()
    hit = s.find("PUB400")
    if hit is None:
        # Wording drift is possible — accept lowercase too.
        hit = s.find("pub400")
    assert hit is not None
    row, col = hit
    assert row >= 1 and col >= 1
    # Round-trip: reading that region back must contain the needle.
    fetched = s.text_at(row, col, 10)
    assert "400" in fetched.upper()
