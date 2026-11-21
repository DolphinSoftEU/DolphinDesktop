"""Read the status bar of an explicitly selected, existing SAP test session."""

import os

import pytest

from dolphin_desktop import SapGui

pytestmark = pytest.mark.integration


def test_read_session_status():
    if os.environ.get("DOLPHIN_SAP_EXAMPLE") != "1":
        pytest.skip("Set DOLPHIN_SAP_EXAMPLE=1 to inspect an existing SAP test session")
    sap = SapGui.connect()
    session = sap.session(
        connection=int(os.environ.get("DOLPHIN_SAP_CONNECTION", "0")),
        session=int(os.environ.get("DOLPHIN_SAP_SESSION", "0")),
    )
    assert isinstance(session.find_by_id("wnd[0]/sbar").text(), str)
