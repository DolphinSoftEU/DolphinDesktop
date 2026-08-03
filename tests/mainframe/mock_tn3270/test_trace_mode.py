"""Trace mode — every backend command hits the logger when enabled."""

from __future__ import annotations

import pytest

from dolphin_desktop import AID, Desktop, MainframeError
from tests.mainframe.mock_tn3270._mock_server import (
    MockTN3270Server,  # type: ignore[import-not-found]
)

# _mainframe_env + _mock_server are made importable by conftest.py.
from tests.mainframe.pub400._mainframe_env import WS3270  # type: ignore[import-not-found]


@pytest.fixture
def caplog_info(caplog):
    # pytest's caplog accepts either a numeric level (logging.INFO == 20)
    # or the string name; using "INFO" keeps us free of the stdlib import.
    caplog.set_level("INFO", logger="dolphin_desktop.mainframe")
    return caplog


def test_trace_off_produces_no_backend_log(caplog_info) -> None:
    if WS3270 is None:
        pytest.skip("wc3270 not installed")
    srv = MockTN3270Server()
    port = srv.start()
    try:
        term = Desktop().mainframe(host="127.0.0.1", port=port, ws3270_path=WS3270, timeout=10)
        term.wait_ready(timeout=5)
        term.screen()
        term.disconnect()
    finally:
        srv.stop()
    backend_messages = [r for r in caplog_info.records if r.name == "dolphin_desktop.mainframe"]
    assert not backend_messages, (
        f"trace=False should not log: {[r.message for r in backend_messages][:5]}"
    )


def test_trace_on_logs_command_and_response(caplog_info) -> None:
    if WS3270 is None:
        pytest.skip("wc3270 not installed")
    srv = MockTN3270Server()
    port = srv.start()
    try:
        term = Desktop().mainframe(
            host="127.0.0.1",
            port=port,
            ws3270_path=WS3270,
            timeout=10,
            trace=True,
        )
        term.wait_ready(timeout=5)
        term.screen()  # -> Ascii command
        term.press(AID.ENTER)
        try:
            term.wait_change(timeout=5)
        except MainframeError:
            pass
        term.disconnect()
    finally:
        srv.stop()
    messages = [
        r.getMessage() for r in caplog_info.records if r.name == "dolphin_desktop.mainframe"
    ]
    # Expect at least one arrow-in and one arrow-out line.
    outbound = [m for m in messages if m.startswith("s3270 →")]
    inbound = [m for m in messages if m.startswith("s3270 ←")]
    assert outbound, "no outbound trace lines emitted"
    assert inbound, "no inbound trace lines emitted"
    # Ensure the Enter action shows up.
    assert any("Enter" in m for m in outbound), outbound[:10]
