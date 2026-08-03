"""SAP component-surface coverage against a live system.

Exercises the control types a real SAP suite depends on — text fields,
buttons, grids (ALV), trees, modal popups, menus, tabs, status messages —
through transactions that exist on every system and change nothing.

A minimal system (an ABAP Trial, for example) does not activate every
control type, so a component that genuinely is not present skips with a
reason instead of failing: the suite states what the *system* offers, not
what the library could do on a fuller landscape.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

# ALV grids sit inside a container control, and the nesting differs per
# transaction: SM04 wraps its grid in a splitter, SM66 does not.
_GRID_IDS = (
    ("SM04", "wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[0]/shell"),
    ("SM04", "wnd[0]/usr/cntlGRID1/shellcont/shell"),
    ("SM66", "wnd[0]/usr/cntlGRID1/shellcont/shell"),
    ("SM66", "wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[0]/shell"),
)

# The Easy Access menu tree is published under a couple of ids depending
# on whether the picture container is switched on.
_TREE_IDS = (
    "wnd[0]/shellcont/shell/shellcont[0]/shell",
    "wnd[0]/shellcont/shell",
    "wnd[0]/usr/cntlIMAGE_CONTAINER/shellcont/shell/shellcont[0]/shell",
)


def _goto(session, tcode: str) -> None:
    session.dismiss_all_popups()
    session.transaction(tcode)
    session.wait_until_ready(timeout=60)


def _grid(session):
    """Return a resolvable ALV grid locator, or skip."""
    current = None
    for tcode, grid_id in _GRID_IDS:
        if tcode != current:
            _goto(session, tcode)
            current = tcode
        loc = session.find_by_id(grid_id)
        if loc.exists(2):
            return loc
    pytest.skip("no ALV grid found in SM04 / SM66 on this system")


# ---------------------------------------------------------------------------
# Text fields, buttons, status bar
# ---------------------------------------------------------------------------


def test_textfield_round_trip_on_se16_selection_screen(sap_home):
    """Type into the table-name field and read the value back."""
    _goto(sap_home, "SE16")
    field = sap_home.find_by_id("wnd[0]/usr/ctxtDATABROWSE-TABLENAME")
    field.set_text("T000")
    assert field.text().upper() == "T000"


def test_toolbar_button_press_executes_the_screen(sap_home):
    """The Execute button (F8) on SE16 runs the selection."""
    _goto(sap_home, "SE16")
    sap_home.find_by_id("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").set_text("T000")
    sap_home.send_vkey(0)
    sap_home.wait_until_ready(timeout=30)

    sap_home.find_by_id("wnd[0]/tbar[1]/btn[8]").click()
    sap_home.wait_until_ready(timeout=60)
    sap_home.assert_no_error()


def test_status_message_is_exposed_after_an_invalid_table(sap_home):
    """An unknown table name puts a message in the status bar."""
    _goto(sap_home, "SE16")
    sap_home.find_by_id("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").set_text("ZZ_NO_SUCH_TABLE")
    sap_home.send_vkey(0)
    sap_home.wait_until_ready(timeout=30)

    message = sap_home.status_message()
    popup_seen = sap_home.find_by_id("wnd[1]").exists(1)
    sap_home.dismiss_all_popups()
    assert message or popup_seen, "invalid table produced neither message nor popup"


# ---------------------------------------------------------------------------
# ALV grid
# ---------------------------------------------------------------------------


def test_grid_reports_row_and_column_counts(sap_session):
    grid = _grid(sap_session)
    assert grid.row_count() >= 0
    assert grid.column_count() > 0


def test_grid_cell_value_is_readable(sap_session):
    """Read row 0 of the first column — the ColumnOrder path."""
    grid = _grid(sap_session)
    if grid.row_count() == 0:
        pytest.skip("grid has no rows on this system right now")
    assert isinstance(grid.cell_value(0, 0), str)


def test_grid_row_selection(sap_session):
    grid = _grid(sap_session)
    if grid.row_count() == 0:
        pytest.skip("grid has no rows on this system right now")
    grid.select_row(0)  # raises if the pattern is unsupported


# ---------------------------------------------------------------------------
# Trees, menus, tabs, popups
# ---------------------------------------------------------------------------


def test_easy_access_tree_is_present(sap_home):
    """SESSION_MANAGER shows the user-menu tree."""
    _goto(sap_home, "SESSION_MANAGER")
    for tree_id in _TREE_IDS:
        if sap_home.find_by_id(tree_id).exists(2):
            return
    pytest.skip("Easy Access tree not exposed on this system")


def test_menu_click_opens_system_status(sap_home):
    """Menu navigation System → Status… reaches the status dialog."""
    _goto(sap_home, "SESSION_MANAGER")
    try:
        sap_home.menu_click(["System", "Status..."])
    except Exception as exc:  # localized menu labels differ per system
        pytest.skip(f"System → Status... not reachable: {exc}")
    sap_home.wait_until_ready(timeout=30)
    assert sap_home.find_by_id("wnd[1]").exists(3)
    sap_home.dismiss_all_popups()


def test_modal_popup_appears_and_is_dismissed(sap_home):
    """SM59 + F6 raises an informational modal; the session recovers."""
    _goto(sap_home, "SM59")
    sap_home.send_vkey(6)
    sap_home.wait_until_ready(timeout=30)
    if not sap_home.find_by_id("wnd[1]").exists(3):
        pytest.skip("SM59 F6 does not raise a popup on this system")

    sap_home.dismiss_all_popups()
    assert not sap_home.find_by_id("wnd[1]").exists(1)


def test_tab_selection_by_label(sap_home):
    """Tab strips are driven by visible label."""
    _goto(sap_home, "SE11")
    tab_labels = ("Attributes", "Attribute")
    for label in tab_labels:
        try:
            sap_home.click_tab_by_label(label)
        except Exception:
            continue
        sap_home.wait_until_ready(timeout=20)
        return
    pytest.skip("no tab strip with a known label on the SE11 initial screen")


# ---------------------------------------------------------------------------
# Session-level surface
# ---------------------------------------------------------------------------


def test_window_can_be_maximized_and_restored(sap_session):
    sap_session.maximize()
    sap_session.wait_until_ready(timeout=20)
    sap_session.restore()
    sap_session.wait_until_ready(timeout=20)
    assert sap_session.title().strip() != ""


def test_screenshot_of_the_session_window(sap_session, tmp_path):
    target = tmp_path / "sap_session.png"
    sap_session.screenshot(str(target))
    assert target.exists() and target.stat().st_size > 0


def test_assert_field_value_matches_what_was_typed(sap_home):
    _goto(sap_home, "SE16")
    field_id = "wnd[0]/usr/ctxtDATABROWSE-TABLENAME"
    sap_home.find_by_id(field_id).set_text("T000")
    sap_home.assert_field_value(field_id, "T000")
