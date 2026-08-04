"""Two parallel MainframeTerminal sessions must not collide.

Each s3270 subprocess owns its own stdin/stdout, so sessions are
isolated at the process level — this test proves that in practice by
running two mocks + two terminals concurrently and verifying that input
on session A only reaches mock A.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import AID, Desktop, start_thread
from tests.mainframe.mock_tn3270._mock_server import (  # type: ignore[import-not-found]
    MockTN3270Server,
    ScreenField,
)
from tests.mainframe.pub400._mainframe_env import WS3270  # type: ignore[import-not-found]


def _make_mock(title: str, port_ref: dict[str, int], key: str) -> MockTN3270Server:
    srv = MockTN3270Server(
        title=title,
        fields=[
            ScreenField("user", "USER:", row=6, col=10, length=8),
            ScreenField("password", "PASS:", row=7, col=10, length=8, hidden=True),
        ],
    )
    port_ref[key] = srv.start()
    return srv


def test_two_sessions_do_not_share_state() -> None:
    if WS3270 is None:
        pytest.skip("wc3270 not installed")

    ports: dict[str, int] = {}
    mock_a = _make_mock("SESSION A", ports, "a")
    mock_b = _make_mock("SESSION B", ports, "b")

    try:
        term_a = Desktop().mainframe(
            host="127.0.0.1", port=ports["a"], ws3270_path=WS3270, timeout=10
        )
        term_b = Desktop().mainframe(
            host="127.0.0.1", port=ports["b"], ws3270_path=WS3270, timeout=10
        )
        term_a.wait_ready(timeout=5)
        term_b.wait_ready(timeout=5)

        # Titles must match each mock's banner — proves each terminal
        # reads its own screen buffer, not the neighbour's.
        assert "SESSION A" in term_a.text()
        assert "SESSION B" in term_b.text()

        # Type distinct values into each session.
        term_a.field_after("USER").type_text("ALICE")
        term_b.field_after("USER").type_text("BOB")
        term_a.press(AID.ENTER)
        term_b.press(AID.ENTER)
        term_a.wait_change(timeout=5)
        term_b.wait_change(timeout=5)

        assert mock_a.last_read is not None
        assert mock_b.last_read is not None
        assert mock_a.last_read.values.get("user") == "ALICE", mock_a.last_read.values
        assert mock_b.last_read.values.get("user") == "BOB", mock_b.last_read.values

        term_a.disconnect()
        term_b.disconnect()
    finally:
        mock_a.stop()
        mock_b.stop()


def test_two_sessions_from_threads() -> None:
    """Same as above but the two terminals are driven from separate
    Python threads — proves the s3270 stdio wrappers are safe against
    concurrent operation on distinct instances."""
    if WS3270 is None:
        pytest.skip("wc3270 not installed")

    ports: dict[str, int] = {}
    mock_a = _make_mock("THREAD A", ports, "a")
    mock_b = _make_mock("THREAD B", ports, "b")

    errors: list[str] = []

    def _drive(port: int, expected_title: str, user: str, mock) -> None:
        try:
            term = Desktop().mainframe(host="127.0.0.1", port=port, ws3270_path=WS3270, timeout=10)
            term.wait_ready(timeout=5)
            assert expected_title in term.text()
            term.field_after("USER").type_text(user)
            term.press(AID.ENTER)
            term.wait_change(timeout=5)
            assert mock.last_read.values.get("user") == user
            term.disconnect()
        except Exception as exc:
            errors.append(f"{expected_title}: {exc!r}")

    try:
        t1 = start_thread(_drive, args=(ports["a"], "THREAD A", "AAA", mock_a))
        t2 = start_thread(_drive, args=(ports["b"], "THREAD B", "BBB", mock_b))
        t1.join(timeout=30)
        t2.join(timeout=30)
    finally:
        mock_a.stop()
        mock_b.stop()

    assert not errors, "concurrent session errors:\n" + "\n".join(errors)
