"""Unit tests for the ALV grid lookup used by the live SAP component suite.

``_grid()`` walks SM04 and then SM66 looking for a grid control and skips the
test when neither transaction exposes one. These tests drive it with a fake
SAP GUI scripting tree, so the fallback order, the GuiShell/GridView alias
and the empty-grid guard are verified without a SAP system.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tests.sap.test_sap_components import _grid

import dolphin_desktop._sap as sap

pytestmark = pytest.mark.unit


class _Children:
    """SAP ``Children`` collection: ``Count`` plus call-by-index access."""

    def __init__(self, *items):
        self.items = list(items)
        self.Count = len(self.items)

    def __call__(self, index):
        return self.items[index]


def _node(component_id, component_type, *children, **attrs):
    return SimpleNamespace(
        Id=component_id, Type=component_type, Children=_Children(*children), **attrs
    )


def _user_area(*children):
    return _node("wnd[0]/usr", "GuiUserArea", *children)


def _splitter_only_screen():
    """SM04-style nesting with the grid *not* activated: shells all the way down."""
    inner = _node("wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[0]/shell", "GuiSplitterShell")
    outer = _node(
        "wnd[0]/usr/cntlGRID1/shellcont/shell",
        "GuiSplitterShell",
        _node("wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[0]", "GuiContainerShell", inner),
    )
    return _user_area(_node("wnd[0]/usr/cntlGRID1", "GuiCustomControl", outer))


def _modern_grid_screen(grid_id, column_count):
    """SAP GUI 8.x publishes ALV grids as GuiShell with SubType GridView."""
    grid = _node(grid_id, "GuiShell", SubType="GridView", ColumnCount=column_count, RowCount=3)
    splitter = _node(
        grid_id.rsplit("/shellcont", 1)[0],
        "GuiSplitterShell",
        _node(grid_id.rsplit("/", 1)[0], "GuiContainerShell", grid),
    )
    return _user_area(_node("wnd[0]/usr/cntlGRID1", "GuiCustomControl", splitter))


def _classic_grid_screen(grid_id, column_count):
    """Older releases expose the grid under its logical type name."""
    grid = _node(grid_id, "GuiGridView", ColumnCount=column_count, RowCount=3)
    return _user_area(_node("wnd[0]/usr/cntlGRID1", "GuiCustomControl", grid))


def _index(root, into):
    into[root.Id] = root
    for child in root.Children.items:
        _index(child, into)
    return into


class FakeSap:
    """One fake screen per transaction code; ``transaction()`` switches screens."""

    def __init__(self, screens):
        self.screens = screens
        self.current = None
        self.visited = []
        self.session = sap.SapSession(SimpleNamespace(Busy=False, FindById=self._find_by_id))
        self.session.transaction = Mock(side_effect=self._transaction)
        self.session.dismiss_all_popups = Mock()
        self.session.wait_until_ready = Mock(return_value=self.session)

    def _transaction(self, tcode):
        self.visited.append(tcode)
        self.current = tcode
        return self.session

    def _find_by_id(self, component_id):
        return _index(self.screens[self.current], {})[component_id]


@pytest.fixture(autouse=True)
def instant_timeouts(monkeypatch):
    """Expire every wait on its first miss; the fake tree never changes."""
    monkeypatch.setattr(sap, "_sleep", lambda: None)
    monkeypatch.setattr(sap, "_deadline", lambda timeout: (float(timeout or 0), time.monotonic()))


SM04_GRID = "wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[0]/shell/shellcont[1]/shell"
SM66_GRID = "wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell/shellcont[1]/shell"


def test_grid_is_found_in_sm04_when_published_as_guishell_gridview():
    fake = FakeSap({"SM04": _modern_grid_screen(SM04_GRID, 12)})

    grid = _grid(fake.session)

    assert grid._id == SM04_GRID
    assert grid.column_count() == 12
    assert fake.visited == ["SM04"]
    fake.session.dismiss_all_popups.assert_called_once_with()


def test_grid_accepts_the_classic_guigridview_type_too():
    fake = FakeSap({"SM04": _classic_grid_screen("wnd[0]/usr/cntlGRID1/shellcont/shell", 7)})

    grid = _grid(fake.session)

    assert grid._id == "wnd[0]/usr/cntlGRID1/shellcont/shell"
    assert grid.column_count() == 7


def test_grid_falls_back_to_sm66_when_sm04_has_no_grid():
    fake = FakeSap({"SM04": _splitter_only_screen(), "SM66": _modern_grid_screen(SM66_GRID, 18)})

    grid = _grid(fake.session)

    assert grid._id == SM66_GRID
    assert grid.column_count() == 18
    assert fake.visited == ["SM04", "SM66"]


def test_grid_without_columns_does_not_satisfy_the_lookup():
    fake = FakeSap(
        {
            "SM04": _modern_grid_screen(SM04_GRID, 0),
            "SM66": _modern_grid_screen(SM66_GRID, 18),
        }
    )

    grid = _grid(fake.session)

    assert grid._id == SM66_GRID
    assert fake.visited == ["SM04", "SM66"]


def test_grid_skips_when_no_transaction_exposes_a_grid():
    fake = FakeSap({"SM04": _splitter_only_screen(), "SM66": _splitter_only_screen()})

    with pytest.raises(pytest.skip.Exception, match="no ALV grid with columns"):
        _grid(fake.session)

    assert fake.visited == ["SM04", "SM66"]


def test_grid_skips_when_every_grid_is_empty():
    fake = FakeSap(
        {
            "SM04": _modern_grid_screen(SM04_GRID, 0),
            "SM66": _modern_grid_screen(SM66_GRID, 0),
        }
    )

    with pytest.raises(pytest.skip.Exception):
        _grid(fake.session)

    assert fake.visited == ["SM04", "SM66"]


def test_grid_never_returns_a_splitter_that_merely_wraps_the_grid():
    """The regression this lookup replaced: a fixed id matched the outer splitter."""
    fake = FakeSap({"SM04": _modern_grid_screen(SM04_GRID, 12)})

    grid = _grid(fake.session)

    assert grid._id != "wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[0]/shell"
    assert grid._id == SM04_GRID
