"""KAN-756: tree paths on a tree view that UI Automation lists as a flat grid.

``_FakeQtTree`` reproduces what PyQt5 and PySide6 were observed to publish: every
cell of every open row is a sibling ``TreeItem``, levels show only as indentation,
only rows around the viewport are listed (two of them hidden above it once
scrolled), one wheel event moves at most a page, and a cell indented past its
column has an empty rectangle. The real-window counterpart is
``tests/qt/test_qt_tree_paths.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dolphin_desktop._element import Tree
from dolphin_desktop._exceptions import (
    DolphinError,
    ElementNotFoundError,
    UnsupportedCapabilityError,
)
from dolphin_desktop._flat_tree import FlatTree, is_flat_tree

UIA = SimpleNamespace(name="uia")
LEFT, TOP, RIGHT = 100, 100, 600
HEADER_BOTTOM = TOP + 20
ROW_HEIGHT = 20
INDENT = 20
LINES_PER_NOTCH = 3


class _Rect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _Node:
    def __init__(self, name: str, *children: _Node, expanded: bool = False) -> None:
        self.name = name
        self.children = list(children)
        self.expanded = expanded


class _Cell:
    def __init__(self, row: int, column: int, name: str, rect: _Rect) -> None:
        self.iface_grid_item = SimpleNamespace(CurrentRow=row, CurrentColumn=column)
        self.element_info = SimpleNamespace(name=name, rectangle=rect)


class _FakeQtTree:
    backend = UIA

    def __init__(
        self,
        *roots: _Node,
        visible_rows: int = 10,
        column_width: int = 200,
        root_decorated: bool = True,
        header: bool = True,
        scroll_x: int = 0,
    ) -> None:
        self.roots = list(roots)
        self.scroll_x = scroll_x
        self.visible_rows = visible_rows
        self.column_width = column_width
        self.root_decorated = root_decorated
        self.header = header
        self.first_row = 0
        self.clicked: list[str] = []
        self.toggled: list[str] = []
        self.wheels: list[int] = []
        self.iface_grid = SimpleNamespace()
        self._sync_row_count()

    # What pywinauto's wrapper offers

    def rectangle(self) -> _Rect:
        return _Rect(LEFT, TOP, RIGHT, self._rows_top() + self.visible_rows * ROW_HEIGHT)

    def children(self, control_type: str | None = None) -> list[object]:
        if control_type == "Header":
            if not self.header:
                return []
            rect = _Rect(LEFT, TOP, LEFT + self.column_width, HEADER_BOTTOM)
            return [SimpleNamespace(rectangle=lambda: rect)]
        cells: list[object] = []
        rows = self._rows()
        for index in range(max(0, self.first_row - 2), self.first_row + self.visible_rows + 1):
            if index < len(rows):
                node, depth = rows[index]
                cells.append(_Cell(index, 0, node.name, self._cell_rect(index, depth)))
                cells.append(_Cell(index, 1, "folder", self._second_column_rect(index)))
        return cells

    def wheel_mouse_input(self, wheel_dist: int = 1) -> None:
        self.wheels.append(wheel_dist)
        lines = max(-self.visible_rows, min(self.visible_rows, wheel_dist * LINES_PER_NOTCH))
        self._scroll_to(self.first_row - lines)

    def click_input(self, coords: tuple[int, int]) -> None:
        x, y = LEFT + coords[0], TOP + coords[1]
        assert y >= self._rows_top(), f"click at y={y} landed on the header"
        assert LEFT < x < RIGHT, f"click at x={x} landed outside the tree"
        index = self.first_row + (y - self._rows_top()) // ROW_HEIGHT
        node, depth = self._rows()[index]
        cell_left = LEFT + self._indent(depth) - self.scroll_x
        if node.children and cell_left - INDENT <= x < cell_left:
            node.expanded = not node.expanded
            self.toggled.append(node.name)
            self._sync_row_count()
        else:
            self.clicked.append(node.name)

    # The simulated view

    def _rows_top(self) -> int:
        return HEADER_BOTTOM if self.header else TOP

    def _indent(self, depth: int) -> int:
        return INDENT * (depth + (1 if self.root_decorated else 0))

    def _rows(self) -> list[tuple[_Node, int]]:
        out: list[tuple[_Node, int]] = []

        def visit(node: _Node, depth: int) -> None:
            out.append((node, depth))
            if node.expanded:
                for child in node.children:
                    visit(child, depth + 1)

        for root in self.roots:
            visit(root, 0)
        return out

    def _sync_row_count(self) -> None:
        self.iface_grid.CurrentRowCount = len(self._rows())

    def _scroll_to(self, first: int) -> None:
        self.first_row = max(0, min(first, max(0, len(self._rows()) - self.visible_rows)))

    def _row_top(self, index: int) -> int:
        return self._rows_top() + (index - self.first_row) * ROW_HEIGHT

    def _cell_rect(self, index: int, depth: int) -> _Rect:
        left = LEFT + self._indent(depth) - self.scroll_x
        right = LEFT + self.column_width - self.scroll_x
        if left >= right:
            return _Rect(0, 0, 0, 0)
        top = self._row_top(index)
        return _Rect(left, top, right, top + ROW_HEIGHT)

    def _second_column_rect(self, index: int) -> _Rect:
        top = self._row_top(index)
        return _Rect(LEFT + self.column_width, top, RIGHT, top + ROW_HEIGHT)


def _project() -> _Node:
    return _Node(
        "Project",
        _Node("src", _Node("main.py"), expanded=True),
        _Node("README.md"),
        expanded=True,
    )


def _flat(view: _FakeQtTree, timeout: float = 1.0) -> FlatTree:
    return FlatTree(view, timeout=timeout)


@pytest.fixture(autouse=True)
def no_real_waits(monkeypatch):
    """Polling must not slow the suite; retries are still bounded by the deadline."""
    clock = {"now": 0.0}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    def monotonic() -> float:
        clock["now"] += 0.001
        return clock["now"]

    monkeypatch.setattr(
        "dolphin_desktop._flat_tree.time",
        SimpleNamespace(sleep=sleep, monotonic=monotonic),
    )


class TestIsFlatTree:
    def test_tree_publishing_a_grid_of_grid_items_is_flat(self):
        assert is_flat_tree(_FakeQtTree(_project())) is True

    def test_empty_tree_with_the_grid_pattern_is_flat(self):
        assert is_flat_tree(_FakeQtTree()) is True

    def test_tree_without_the_grid_pattern_is_nested(self):
        def no_pattern():
            raise RuntimeError("NoPatternInterfaceError")

        wrapper = Mock(spec=["backend", "children"])
        wrapper.backend = UIA
        type(wrapper).iface_grid = property(lambda _self: no_pattern())
        assert is_flat_tree(wrapper) is False

    def test_tree_whose_grid_pattern_is_none_is_nested(self):
        wrapper = SimpleNamespace(backend=UIA, iface_grid=None, children=Mock(return_value=[]))
        assert is_flat_tree(wrapper) is False

    def test_grid_tree_whose_items_have_no_grid_item_stays_nested(self):
        def no_pattern():
            raise RuntimeError("NoPatternInterfaceError")

        class Item:
            iface_grid_item = property(lambda _self: no_pattern())

        wrapper = SimpleNamespace(
            backend=UIA,
            iface_grid=SimpleNamespace(),
            children=Mock(return_value=[Item()]),
        )
        assert is_flat_tree(wrapper) is False

    @pytest.mark.parametrize(
        "wrapper",
        [
            SimpleNamespace(backend=SimpleNamespace(name="win32"), iface_grid=object()),
            SimpleNamespace(iface_grid=object()),
            Mock(),
            object(),
        ],
    )
    def test_other_backends_and_doubles_are_not_flat(self, wrapper):
        assert is_flat_tree(wrapper) is False


class TestSelect:
    def test_open_path_clicks_only_the_final_row(self):
        view = _FakeQtTree(_project())
        _flat(view).select_item_by_path(("Project", "src", "main.py"))
        assert view.clicked == ["main.py"]
        assert view.toggled == []
        assert view.wheels == []

    def test_root_and_sibling_rows(self):
        view = _FakeQtTree(_project())
        _flat(view).select_item_by_path(("Project",))
        _flat(view).select_item_by_path(("Project", "README.md"))
        assert view.clicked == ["Project", "README.md"]

    def test_closed_ancestors_open_through_the_arrow_without_a_click_on_them(self):
        view = _FakeQtTree(_Node("Project", _Node("src", _Node("main.py"))))
        _flat(view).select_item_by_path(("Project", "src", "main.py"))
        assert view.toggled == ["Project", "src"]
        assert view.clicked == ["main.py"]

    def test_duplicate_names_are_told_apart_by_their_ancestors(self):
        alpha = _Node("Alpha", _Node("src", _Node("util.py")), expanded=True)
        beta = _Node("Beta", _Node("src", _Node("main.py")))
        view = _FakeQtTree(alpha, beta)
        _flat(view).select_item_by_path(("Beta", "src", "main.py"))
        assert view.clicked == ["main.py"]
        assert view.toggled == ["Beta", "src"]

    def test_second_sibling_of_the_same_name_is_tried(self):
        empty = _Node("src", _Node("other.py"), expanded=True)
        full = _Node("src", _Node("main.py"), expanded=True)
        view = _FakeQtTree(_Node("Project", empty, full, expanded=True))
        _flat(view).select_item_by_path(("Project", "src", "main.py"))
        assert view.clicked == ["main.py"]

    def test_a_grandchild_is_not_taken_for_a_child(self):
        view = _FakeQtTree(_project())
        with pytest.raises(ElementNotFoundError, match="'Project' was reached"):
            _flat(view, timeout=0.2).select_item_by_path(("Project", "main.py"))
        assert view.clicked == []

    def test_a_nested_row_is_not_taken_for_a_root(self):
        view = _FakeQtTree(_project())
        with pytest.raises(ElementNotFoundError, match="no top-level row is named 'src'"):
            _flat(view, timeout=0.2).select_item_by_path(("src",))

    def test_another_column_is_not_a_node(self):
        view = _FakeQtTree(_project())
        with pytest.raises(ElementNotFoundError):
            _flat(view, timeout=0.2).select_item_by_path(("folder",))

    def test_scrolled_out_row_is_reached_and_clicked_inside_the_viewport(self):
        rows = [_Node(f"Row {i:02d}") for i in range(60)]
        view = _FakeQtTree(*rows, _Node("Omega", _Node("Tail")))
        _flat(view).select_item_by_path(("Omega", "Tail"))
        assert view.clicked == ["Tail"]
        assert view.toggled == ["Omega"]
        assert all(dist < 0 for dist in view.wheels)

    def test_returns_to_the_top_for_a_row_above_the_viewport(self):
        rows = [_Node(f"Row {i:02d}") for i in range(60)]
        view = _FakeQtTree(*rows)
        view._scroll_to(40)
        _flat(view).select_item_by_path(("Row 00",))
        assert view.clicked == ["Row 00"]
        assert view.first_row == 0

    def test_row_listed_but_hidden_above_the_viewport_is_scrolled_to(self):
        # After scrolling, UIA lists two rows hidden behind the header. Clicking
        # one would hit the header, which the fake refuses.
        rows = [_Node(f"Row {i:02d}") for i in range(30)]
        view = _FakeQtTree(*rows)
        view._scroll_to(12)
        row = _flat(view)._reveal(10)
        assert row.top >= view._rows_top()
        assert view.first_row <= 10

    def test_row_partly_below_the_viewport_is_scrolled_in(self):
        view = _FakeQtTree(*[_Node(f"Row {i:02d}") for i in range(30)])
        # The row after the last whole one is listed, its bottom past the tree.
        row = _flat(view)._reveal(view.visible_rows)
        assert row.bottom <= view.rectangle().bottom
        assert view.wheels == [-1]

    def test_works_without_a_header(self):
        view = _FakeQtTree(_project(), header=False)
        _flat(view).select_item_by_path(("Project", "src", "main.py"))
        assert view.clicked == ["main.py"]

    def test_a_column_wider_than_the_tree_is_clicked_inside_it(self):
        view = _FakeQtTree(_project(), column_width=2000)
        _flat(view).select_item_by_path(("Project",))
        assert view.clicked == ["Project"]

    def test_unreadable_header_falls_back_to_the_top_of_the_tree(self):
        view = _FakeQtTree(_project(), header=False)
        original = view.children

        def children(control_type=None):
            if control_type == "Header":
                raise RuntimeError("ElementNotAvailable")
            return original(control_type)

        view.children = children
        _flat(view).select_item_by_path(("Project", "README.md"))
        assert view.clicked == ["README.md"]

    def test_child_added_late_is_found_by_polling(self):
        src = _Node("src", expanded=True)
        view = _FakeQtTree(_Node("Project", src, expanded=True))
        original = view.children
        calls = {"n": 0}

        def children(control_type=None):
            calls["n"] += 1
            if calls["n"] == 12:
                src.children.append(_Node("late.py"))
                view._sync_row_count()
            return original(control_type)

        view.children = children
        _flat(view, timeout=5).select_item_by_path(("Project", "src", "late.py"))
        # src showed no children, so its arrow was tried once, which is a click
        # on a row with nothing to open.
        assert view.clicked == ["src", "late.py"]


class TestFailures:
    def test_missing_child_names_how_far_the_path_got(self):
        view = _FakeQtTree(_project())
        with pytest.raises(ElementNotFoundError, match=r"'Project -> src' was reached") as caught:
            _flat(view, timeout=0.2).select_item_by_path(("Project", "src", "nope"))
        assert "indentation" in str(caught.value)

    def test_empty_tree_reports_not_found(self):
        view = _FakeQtTree()
        with pytest.raises(ElementNotFoundError, match="not found after"):
            _flat(view, timeout=0.2).select_item_by_path(("Project",))

    def test_child_of_a_leaf_clicks_the_leaf_only_once_across_retries(self):
        view = _FakeQtTree(_project())
        with pytest.raises(ElementNotFoundError, match=r"'Project -> README\.md' was reached"):
            _flat(view, timeout=1).select_item_by_path(("Project", "README.md", "x"))
        assert view.clicked == ["README.md"]

    def test_cell_indented_past_its_column_is_reported_as_unsupported(self):
        deep = _Node("a", _Node("b", _Node("c", _Node("d"))), expanded=True)
        view = _FakeQtTree(deep, column_width=INDENT * 3)
        with pytest.raises(UnsupportedCapabilityError, match="first column") as caught:
            _flat(view).select_item_by_path(("a", "b", "c", "d"))
        assert "widen" in str(caught.value)
        assert view.clicked == []

    def test_root_without_an_arrow_cannot_be_opened(self):
        view = _FakeQtTree(_Node("Project", _Node("src")), root_decorated=False)
        with pytest.raises(ElementNotFoundError):
            _flat(view, timeout=0.2).select_item_by_path(("Project", "src"))
        assert view.toggled == []
        assert view.clicked == []

    def test_undecorated_root_still_has_arrows_below_it(self):
        src = _Node("src", _Node("main.py"))
        view = _FakeQtTree(_Node("Project", src, expanded=True), root_decorated=False)
        _flat(view).select_item_by_path(("Project", "src", "main.py"))
        assert view.toggled == ["src"]
        assert view.clicked == ["main.py"]

    def test_rows_vanishing_mid_read_are_retried(self):
        view = _FakeQtTree(_project())
        original = view.children
        failures = iter([RuntimeError("ElementNotAvailable")])

        def children(control_type=None):
            if control_type == "TreeItem":
                failure = next(failures, None)
                if failure is not None:
                    raise failure
            return original(control_type)

        view.children = children
        _flat(view).select_item_by_path(("Project",))
        assert view.clicked == ["Project"]

    def test_rows_that_never_read_are_not_found_at_the_deadline(self):
        view = _FakeQtTree(_project())
        original = view.children

        def children(control_type=None):
            if control_type == "TreeItem":
                raise RuntimeError("ElementNotAvailable")
            return original(control_type)

        view.children = children
        with pytest.raises(ElementNotFoundError, match="not found after"):
            _flat(view, timeout=0.2).select_item_by_path(("Project",))
        assert view.clicked == []

    def test_row_gone_before_the_click_is_not_found(self):
        view = _FakeQtTree(_project())
        flat = _flat(view, timeout=0.2)
        locate = flat._locate

        def locate_then_collapse(path):
            match = locate(path)
            view.roots[0].expanded = False
            view._sync_row_count()
            return match

        flat._locate = locate_then_collapse  # type: ignore[method-assign]
        with pytest.raises(ElementNotFoundError, match=r"'Project -> README\.md' not found"):
            flat.select_item_by_path(("Project", "README.md"))
        assert view.clicked == []

    def test_branch_arrow_scrolled_out_of_the_tree_is_not_clicked(self):
        # Scrolled sideways, the arrow of src lies left of the tree's edge.
        root = _Node("Project", _Node("src", _Node("main.py")), expanded=True)
        view = _FakeQtTree(root, scroll_x=50)
        with pytest.raises(ElementNotFoundError, match=r"'Project -> src' was reached"):
            _flat(view, timeout=0.2).select_item_by_path(("Project", "src", "main.py"))
        assert view.toggled == []
        assert view.clicked == []

    def test_wheeling_stops_at_the_deadline(self):
        view = _FakeQtTree(*[_Node(f"Row {i}") for i in range(60)])
        view._scroll_to(40)
        with pytest.raises(ElementNotFoundError):
            _flat(view, timeout=0).select_item_by_path(("Row 0",))
        assert view.wheels == []


class TestExpand:
    def test_opens_the_whole_path_without_selecting(self):
        src = _Node("src", _Node("main.py"))
        root = _Node("Project", src)
        view = _FakeQtTree(root)
        _flat(view).expand_item(("Project", "src"))
        assert root.expanded is True
        assert src.expanded is True
        assert view.clicked == []

    def test_open_nodes_stay_open(self):
        view = _FakeQtTree(_project())
        _flat(view).expand_item(("Project", "src"))
        assert view.toggled == []
        assert view.clicked == []


def _tree_on(view: object, *, hidden: bool = False) -> Tree:
    tree = Tree(SimpleNamespace(_get_spec=Mock(return_value=view)), title="Files")
    tree._resolve = Mock(return_value=view)  # type: ignore[method-assign]
    tree._focus_for_input = Mock()  # type: ignore[method-assign]
    tree._is_hidden_desktop = Mock(return_value=hidden)  # type: ignore[method-assign]
    return tree


class TestTreeControl:
    def test_select_item_by_path_routes_a_flat_tree(self):
        view = _FakeQtTree(_project())
        tree = _tree_on(view)
        assert tree.select_item_by_path("Project", "src", "main.py") is tree
        assert view.clicked == ["main.py"]
        tree._focus_for_input.assert_called_once_with()

    def test_expand_item_routes_a_flat_tree(self):
        root = _Node("Project", _Node("src"))
        view = _FakeQtTree(root)
        tree = _tree_on(view)
        assert tree.expand_item("Project") is tree
        assert root.expanded is True

    def test_empty_paths_are_no_ops(self):
        view = _FakeQtTree(_project())
        tree = _tree_on(view)
        assert tree.select_item_by_path() is tree
        assert tree.expand_item() is tree
        assert view.clicked == []
        assert view.toggled == []

    def test_nested_tree_keeps_the_tree_item_search(self):
        item = Mock()
        element = Mock()
        element.child_window.return_value = item
        tree = Tree.__new__(Tree)
        tree._timeout = 1
        tree._resolve = Mock(return_value=element)  # type: ignore[method-assign]
        tree.select_item_by_path("Leaf")
        element.child_window.assert_called_once_with(title="Leaf", control_type="TreeItem")
        item.select.assert_called_once_with()

    def test_pointer_failure_on_a_hidden_desktop_is_explained(self):
        view = _FakeQtTree(_project())
        view.click_input = Mock(side_effect=RuntimeError("desktop is not active"))
        with pytest.raises(DolphinError, match=r"select_item_by_path.*hidden desktop"):
            _tree_on(view, hidden=True).select_item_by_path("Project")

    def test_pointer_failure_elsewhere_is_not_rewritten(self):
        view = _FakeQtTree(_project())
        view.click_input = Mock(side_effect=ValueError("boom"))
        with pytest.raises(ValueError, match="boom"):
            _tree_on(view).select_item_by_path("Project")

    def test_not_found_is_the_library_error_not_a_pywinauto_timeout(self):
        view = _FakeQtTree(_project())
        tree = _tree_on(view)
        tree._timeout = 0.2
        with pytest.raises(ElementNotFoundError, match="Project -> nope"):
            tree.select_item_by_path("Project", "nope")
