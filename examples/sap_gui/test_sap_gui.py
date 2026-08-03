"""Example SAP GUI for Windows test.

Requires a running SAP GUI for Windows instance with SAP GUI Scripting enabled
on the client and SAP system.
"""

from dolphin_desktop import SapGui


def test_open_transaction_and_check_status_bar():
    sap = SapGui.connect()
    session = sap.session(connection=0, session=0)

    session.transaction("SE16")
    session.find_by_id("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").set_text("T000")
    session.find_by_id("wnd[0]/tbar[1]/btn[8]").click()

    assert session.find_by_id("wnd[0]/sbar").text() is not None
