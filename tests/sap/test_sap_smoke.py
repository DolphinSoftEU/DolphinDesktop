"""SAP GUI smoke suite — read-only checks against a live system.

Every test here only reads: no document is created, posted or deleted, so
the suite is safe against a shared sandbox. Transactions used are SE16
(data browser, display) and SU53 / system info, all display-only.

Run with the environment described in ``conftest.py``; without it the
whole module skips.
"""

from __future__ import annotations

import pytest

from tests.sap.conftest import sap_config

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


def test_session_reports_expected_system(sap_session):
    """system_info() answers with the client and user we signed in as."""
    info = sap_session.system_info()
    cfg = sap_config()

    assert info["system"], "SAP system name is empty"
    assert info["user"], "SAP user is empty"
    if cfg["client"]:
        assert info["client"] == cfg["client"], (
            f"expected client {cfg['client']}, session reports {info['client']}"
        )
    if cfg["user"]:
        assert info["user"].upper() == cfg["user"].upper()


def test_status_bar_is_readable(sap_session):
    """The status bar resolves and returns a string (possibly empty)."""
    text = sap_session.find_by_id("wnd[0]/sbar").text()
    assert isinstance(text, str)


def test_window_title_is_present(sap_session):
    assert sap_session.title().strip() != ""


def test_transaction_navigation_round_trip(sap_home):
    """Enter SE16, confirm the transaction changed, then return home."""
    sap_home.transaction("SE16")
    sap_home.wait_until_ready(timeout=30)
    assert sap_home.current_transaction().upper().startswith("SE16")

    sap_home.transaction("SESSION_MANAGER")
    sap_home.wait_until_ready(timeout=30)
    assert not sap_home.current_transaction().upper().startswith("SE16")


def test_se16_table_browser_displays_client_table(sap_home):
    """SE16 on T000 (client table) reaches the selection screen and runs.

    T000 is present on every SAP system and the flow is display-only.
    """
    sap_home.transaction("SE16")
    sap_home.wait_until_ready(timeout=30)

    sap_home.find_by_id("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").set_text("T000")
    sap_home.send_vkey(0)
    sap_home.wait_until_ready(timeout=30)
    sap_home.assert_no_error()

    sap_home.find_by_id("wnd[0]/tbar[1]/btn[8]").click()  # Execute (F8)
    sap_home.wait_until_ready(timeout=60)
    sap_home.assert_no_error()

    assert isinstance(sap_home.find_by_id("wnd[0]/sbar").text(), str)


def test_locator_by_name_resolves_a_toolbar_button(sap_session):
    """The name-based locator surface works against a live screen."""
    loc = sap_session.locator(id="wnd[0]/tbar[0]/btn[3]")  # Back
    assert loc.exists()
