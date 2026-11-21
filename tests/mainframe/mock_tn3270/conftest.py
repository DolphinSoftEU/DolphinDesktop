"""Fixtures for the TN3270 mock server round-trip tests."""

from __future__ import annotations

import pytest

from dolphin_desktop import (
    Desktop,
    MainframeError,
    env_var,
)
from tests.mainframe._emulator_env import (
    WS3270,  # type: ignore[import-not-found]
)
from tests.mainframe.mock_tn3270._mock_server import MockTN3270Server, ScreenField


@pytest.fixture
def mock_server():
    """Fresh mock TN3270 server bound to an ephemeral port."""
    srv = MockTN3270Server(
        title="MOCK-3270 SIGN-ON",
        fields=[
            ScreenField("user", "USER:", row=6, col=10, length=8),
            ScreenField("password", "PASS:", row=7, col=10, length=8, hidden=True),
        ],
        footer="ENTER=submit  PF3=exit",
        cursor_field="user",
    )
    port = srv.start()
    yield srv, port
    srv.stop()


@pytest.fixture
def mock_term(mock_server):
    """MainframeTerminal connected to the mock server via ws3270."""
    if WS3270 is None:
        if (env_var("DOLPHIN_COMPONENT_PREFLIGHT") or "").strip() == "1":
            raise RuntimeError("wc3270 is required when DOLPHIN_COMPONENT_PREFLIGHT=1")
        pytest.skip("wc3270 not installed")
    srv, port = mock_server
    try:
        term = Desktop().mainframe(
            host="127.0.0.1",
            port=port,
            session_type="3270",
            ws3270_path=WS3270,
            timeout=10,
        )
    except MainframeError as exc:
        if (env_var("DOLPHIN_COMPONENT_PREFLIGHT") or "").strip() == "1":
            raise
        pytest.skip(f"mock connect failed: {exc}")
    try:
        term.wait_ready(timeout=8)
        yield term, srv
    finally:
        try:
            term.disconnect()
        except Exception:
            pass
