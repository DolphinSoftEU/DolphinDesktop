"""Walk a tree view that UI Automation presents as a flat grid of cells (KAN-756).

Qt's ``QTreeView`` / ``QTreeWidget`` do not nest their UIA elements. Every cell
of every row is a direct ``TreeItem`` child of the ``Tree``, so a
``child_window`` under ``Project`` never finds ``src``. Observed on PyQt5 and
PySide6 under ``QT_ACCESSIBILITY=1``:

- The element's ``GridItem`` pattern gives the row, counted across the whole
  view and stable while scrolling, and the column. Other columns repeat as
  further ``TreeItem`` elements, so only column 0 names a node.
- Nothing states a row's level. The indentation of its column-0 cell is the
  only trace of the hierarchy.
- Only rows around the viewport are listed, including rows hidden above it, and
  a collapsed node has no rows below it.
- The patterns do not drive the tree. Qt 5 has neither ``ExpandCollapse`` nor
  ``SelectionItem``. On Qt 6 ``Expand`` / ``Collapse`` do nothing, and
  ``SelectionItem.Select`` picks the view row numbered like the node's position
  under its parent, so ``main.py`` selects ``Project`` without any error.

So this reads the rows and acts with the pointer, as a user would: the branch
arrow opens a node without selecting it, and a click on the cell selects it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ._exceptions import ElementNotFoundError, UnsupportedCapabilityError
from ._win32_criteria import backend_name

# How long one wheel notch or branch click may take to change the listed rows.
_SETTLE_SECONDS = 0.5
_POLL_SECONDS = 0.02
# A root cell further than this from the tree's edge has a branch arrow left of
# it. Anything nearer is just the frame of the widget.
_FRAME_PIXELS = 4


def is_flat_tree(wrapper: Any) -> bool:
    """Whether *wrapper* is a UIA ``Tree`` that lists its nodes as grid rows.

    The ``Grid`` pattern on the tree holds even while it has no items yet. A
    nested tree that also publishes it keeps the ``child_window`` path, because
    its items have no ``GridItem``.
    """
    if backend_name(wrapper) != "uia":
        return False
    try:
        if wrapper.iface_grid is None:
            return False
        items = wrapper.children(control_type="TreeItem")
        return not items or items[0].iface_grid_item is not None
    except Exception:
        return False


@dataclass(frozen=True)
class _Row:
    index: int
    name: str
    # None when UIA reports an empty rectangle: the indentation has pushed the
    # cell past the right edge of the column, so its level cannot be read.
    left: int | None
    right: int
    top: int
    bottom: int


@dataclass(frozen=True)
class _Level:
    left: int
    child_left: int


@dataclass(frozen=True)
class _Match:
    row: _Row
    depth: int


class FlatTree:
    """Find, open and select nodes of one flat tree by their path of titles."""

    def __init__(self, tree: Any, *, timeout: float) -> None:
        self._tree = tree
        self._timeout = timeout
        self._deadline = time.monotonic() + timeout
        self._path: tuple[str, ...] = ()
        self._reached: tuple[str, ...] = ()
        self._root_left: int | None = None
        self._cached: dict[int, _Row] | None = None
        self._arrows_clicked: set[tuple[int, str]] = set()

    def select_item_by_path(self, path: tuple[str, ...]) -> None:
        """Click the node at *path*, opening its ancestors on the way."""
        match = self._locate(path)
        row = self._reveal(match.row.index)
        if row.left is None:
            raise self._too_narrow(row.name)
        rect = self._tree.rectangle()
        # A column wider than the tree would put the cell's centre outside it.
        x = min((row.left + row.right) // 2, rect.right - _FRAME_PIXELS)
        self._click(x, (row.top + row.bottom) // 2)

    def expand_item(self, path: tuple[str, ...]) -> None:
        """Open the node at *path* and its ancestors. Open nodes stay open."""
        match = self._locate(path)
        self._child_left(match.row, match.depth)

    def _locate(self, path: tuple[str, ...]) -> _Match:
        self._path = path
        self._reached = ()
        self._arrows_clicked = set()
        while True:
            self._cached = None
            match = self._walk()
            if match is not None:
                return match
            if time.monotonic() >= self._deadline:
                raise self._not_found()
            time.sleep(0.1)

    def _walk(self) -> _Match | None:
        """One pass over the rows from the top, in depth-first view order."""
        self._scroll_to_top()
        first = self._row_at(0)
        if first is None:
            return None
        self._root_left = first.left
        levels: list[_Level] = []
        index = 0
        while True:
            row = self._row_at(index)
            if row is None:
                return None
            while levels and row.left is not None and row.left <= levels[-1].left:
                levels.pop()
            depth = len(levels)
            if row.name == self._path[depth]:
                if row.left is None:
                    raise self._too_narrow(row.name)
                level_left = levels[-1].child_left if levels else first.left
                if row.left == level_left:
                    if depth == len(self._path) - 1:
                        return _Match(row, depth)
                    reached = self._path[: depth + 1]
                    if len(reached) > len(self._reached):
                        self._reached = reached
                    child_left = self._child_left(row, depth)
                    if child_left is not None:
                        levels.append(_Level(row.left, child_left))
            index += 1

    def _child_left(self, row: _Row, depth: int) -> int | None:
        """Indentation of *row*'s children, opening it if none are listed.

        ``None`` means it has no children, or none could be opened.
        """
        child_left = self._listed_child_left(row)
        if child_left is None and self._open(row, depth):
            child_left = self._listed_child_left(row)
        return child_left

    def _listed_child_left(self, row: _Row) -> int | None:
        following = self._row_at(row.index + 1)
        if following is None:
            return None
        if following.left is None:
            raise self._too_narrow(following.name)
        return following.left if row.left is not None and following.left > row.left else None

    def _open(self, row: _Row, depth: int) -> bool:
        """Click *row*'s branch arrow. Only ever called while it shows no children.

        Qt's arrow occupies one indentation step left of the cell. A click there
        opens the node without selecting it, while on a leaf it acts as a click
        on the row. Nothing tells the two apart on Qt 5, so a row gets this click
        once per lookup, however often a missing path is retried.
        """
        attempt = (row.index, row.name)
        if attempt in self._arrows_clicked:
            return False
        self._arrows_clicked.add(attempt)
        row = self._reveal(row.index)
        rect = self._tree.rectangle()
        decorated = self._root_left is not None and (self._root_left - rect.left > _FRAME_PIXELS)
        indent_levels = depth + (1 if decorated else 0)
        if row.left is None or indent_levels == 0:
            return False
        x = int(row.left - (row.left - rect.left) / indent_levels / 2)
        if not rect.left < x < rect.right:
            return False
        count = self._row_count()
        self._click(x, (row.top + row.bottom) // 2)
        settle = time.monotonic() + _SETTLE_SECONDS
        while self._row_count() == count and time.monotonic() < settle:
            time.sleep(_POLL_SECONDS)
        self._cached = None
        return self._row_count() != count

    def _scroll_to_top(self) -> None:
        """Bring row 0 into view, wheeling only when it is not there already."""
        while True:
            rows = self._page()
            first = rows.get(0)
            if first is not None and first.top >= self._viewport_top() - 1:
                return
            if not rows and self._row_count() == 0:
                return
            self._wheel(self._notches(rows))

    def _row_at(self, index: int) -> _Row | None:
        """The row at view index *index*, scrolling to it, or ``None`` past the end."""
        while True:
            if index >= self._row_count():
                return None
            rows = self._page()
            row = rows.get(index)
            if row is not None:
                return row
            notches = self._notches(rows)
            self._wheel(notches if rows and index < min(rows) else -notches)

    def _reveal(self, index: int) -> _Row:
        """The row at *index*, once it is wholly inside the viewport."""
        while True:
            row = self._row_at(index)
            if row is None:
                raise self._not_found()
            if row.top < self._viewport_top() - 1:
                self._wheel(1)
            elif row.bottom > self._tree.rectangle().bottom:
                self._wheel(-1)
            else:
                return row

    @staticmethod
    def _notches(rows: dict[int, _Row]) -> int:
        """Wheel distance staying inside the listed rows.

        A notch is a few rows and Qt limits one wheel event to a page, so no
        row can be skipped whatever the system's scroll setting is.
        """
        return max(1, len(rows) // 4)

    def _wheel(self, notches: int) -> None:
        if time.monotonic() >= self._deadline:
            raise self._not_found()
        before = sorted(self._page())
        self._tree.wheel_mouse_input(wheel_dist=notches)
        settle = time.monotonic() + _SETTLE_SECONDS
        while True:
            self._cached = None
            if sorted(self._page()) != before or time.monotonic() >= settle:
                return
            time.sleep(_POLL_SECONDS)

    def _click(self, x: int, y: int) -> None:
        rect = self._tree.rectangle()
        self._tree.click_input(coords=(x - rect.left, y - rect.top))
        self._cached = None

    def _viewport_top(self) -> int:
        """Top of the rows area: below the header cells when they are shown."""
        try:
            headers = self._tree.children(control_type="Header")
            bottoms = [header.rectangle().bottom for header in headers]
        except Exception:
            bottoms = []
        return max([self._tree.rectangle().top, *bottoms])

    def _row_count(self) -> int:
        count = self._tree.iface_grid.CurrentRowCount
        return int(count)

    def _page(self) -> dict[int, _Row]:
        """Column-0 rows listed now, by view index. Elements can vanish mid-read."""
        if self._cached is None:
            try:
                self._cached = self._read_rows()
            except Exception:
                if time.monotonic() >= self._deadline:
                    raise self._not_found() from None
                time.sleep(_POLL_SECONDS)
                return {}
        return self._cached

    def _read_rows(self) -> dict[int, _Row]:
        rows: dict[int, _Row] = {}
        for item in self._tree.children(control_type="TreeItem"):
            grid_item = item.iface_grid_item
            if int(grid_item.CurrentColumn) != 0:
                continue
            info = item.element_info
            rect = info.rectangle
            empty = rect.left == rect.right and rect.top == rect.bottom
            index = int(grid_item.CurrentRow)
            rows[index] = _Row(
                index=index,
                name=str(info.name),
                left=None if empty else int(rect.left),
                right=int(rect.right),
                top=int(rect.top),
                bottom=int(rect.bottom),
            )
        return rows

    def _not_found(self) -> ElementNotFoundError:
        reached = (
            f"{' -> '.join(self._reached)!r} was reached and has no such child"
            if self._reached
            else f"no top-level row is named {self._path[0]!r}"
        )
        return ElementNotFoundError(
            f"Tree item {' -> '.join(self._path)!r} not found after {self._timeout}s: {reached}",
            hint=(
                "this tree lists its rows flat in UI Automation, so levels come from "
                "the indentation of the first column: a tree without indentation "
                "cannot be walked by path"
            ),
        )

    def _too_narrow(self, name: str) -> UnsupportedCapabilityError:
        return UnsupportedCapabilityError(
            f"Tree item {name!r} reports an empty rectangle while resolving "
            f"{' -> '.join(self._path)!r}: its indentation exceeds the width of the "
            "first column, so its level cannot be read and it cannot be clicked",
            hint="widen the tree's first column, or resize the window, before selecting by path",
        )
