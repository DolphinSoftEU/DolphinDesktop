"""Fixture: MainframeTerminal connected to pub400.com over 3270."""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, MainframeError
from tests.mainframe.pub400._mainframe_env import WS3270  # type: ignore[import-not-found]

_PUB400_HOST = "pub400.com"
_PUB400_PORT = 23


@pytest.fixture(scope="module")
def pub400_term():
    """Live 3270 session against pub400.com.

    Module-scoped so all tests in one file share one connection —
    pub400 rate-limits new sessions per source IP. Auto-skips when
    wc3270 is not installed or the host is unreachable.
    """
    if WS3270 is None:
        pytest.skip(
            "wc3270 not installed. See docs/guides/mainframe.md for install steps "
            "(download wc3270-noinstall from https://x3270.miraheze.org)."
        )

    desktop = Desktop()
    try:
        term = desktop.mainframe(
            host=_PUB400_HOST,
            port=_PUB400_PORT,
            session_type="3270",
            ws3270_path=WS3270,
            timeout=25,
        )
    except MainframeError as exc:
        pytest.skip(f"could not reach {_PUB400_HOST}: {exc}")

    try:
        term.wait_ready(timeout=20)
        yield term
    finally:
        try:
            term.disconnect()
        except Exception:
            pass
