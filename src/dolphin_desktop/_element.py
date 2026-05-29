"""Specialized UI control classes built on top of Locator.

Hierarchy::

    Locator
    └── Element          — base class for all named controls
        ├── Button
        ├── Edit
        ├── ComboBox
        ├── CheckBox
        ├── RadioButton
        ├── Menu         — produces MenuItem via .item()
        ├── Tree
        ├── ListBox
        ├── Tab
        └── Toolbar      — produces Button via .button()

Use the factory methods on :class:`Window` to create instances rather than
instantiating these classes directly.
"""

from __future__ import annotations

import time
from typing import Any

from ._exceptions import ElementNotFoundError
from ._locator import Locator


class Element(Locator):
    """Base class for all specialized UI controls.

    Extends :class:`Locator` with a named-control type.  Use the factory
    methods on :class:`Window` rather than instantiating directly:

    Usage::

        btn = window.button(name="OK")
        btn.click()

        edit = window.edit(name="File name")
        edit.set_text("report.xlsx")
    """


class Button(Element):
    """A clickable button control.

    Usage::

        window.button(name="OK").click()
        assert window.button(name="Apply").is_enabled()
        window.button(name="Cancel").wait_until_enabled()
    """


class Edit(Element):
    """A single-line or multi-line text input control.

    Usage::

        window.edit(name="Username").type_text("admin")
        window.edit(name="Password").set_text("s3cr3t")
        text = window.edit().text()
    """


class ComboBox(Element):
    """A combo box (drop-down list) control.

    Usage::

        window.combo_box(name="Language").select_item("English")
        window.combo_box().expand()
        current = window.combo_box().selected_item()
    """

    def expand(self) -> ComboBox:
        """Open the dropdown list."""
        try:
            self._resolve().expand()
        except Exception:
            self._resolve().click_input()
        return self

    def collapse(self) -> ComboBox:
        """Close the dropdown list."""
        try:
            self._resolve().collapse()
        except Exception:
            pass
        return self

    def selected_item(self) -> str:
        """Return the text of the currently selected item."""
        element = self._resolve()
        try:
            return element.selected_item()
        except Exception:
            return element.window_text()


class CheckBox(Element):
    """A check box control.

    Usage::

        window.check_box(name="Remember me").check()
        assert window.check_box(name="Remember me").is_checked()
        window.check_box(name="Send updates").uncheck()
    """


class RadioButton(Element):
    """A radio button control.

    Usage::

        window.radio_button(name="Option A").select()
        assert window.radio_button(name="Option A").is_checked()
    """

    def select(self) -> RadioButton:
        """Select this radio button (alias for :meth:`check`)."""
        return self.check()  # type: ignore[return-value]


class MenuItem(Locator):
    """A menu item that automatically opens its parent menu on resolution.

    Created via :meth:`Menu.item`; not typically instantiated directly.

    Clicking resolves the parent menu first (expanding it), then resolves
    the menu item itself.  Works for both in-tree UIA menus and detached
    popup windows (e.g. Win32 context menus).

    Supports chaining into nested submenus::

        window.menu("View").item("Zoom").item("100%").click()
    """

    def item(self, text: str) -> MenuItem:
        """Return a locator for a nested submenu item by its visible text."""
        return MenuItem(self, title=text, control_type="MenuItem")

    def _resolve(self) -> Any:
        if isinstance(self._parent, (Menu, MenuItem)):
            # Open the parent menu by clicking it.
            parent_el = self._parent._resolve()
            try:
                parent_el.click_input()
            except Exception:
                pass
            time.sleep(0.15)
            # First, try to find the item as a direct child of the parent element
            # (covers Win32 menus and UIA in-tree menus).
            try:
                spec = parent_el.child_window(**self._criteria)
                spec.wait("exists visible", timeout=self._timeout)
                return spec
            except Exception:
                pass
            # Fallback: search the root window scope for detached popup menus.
            root = self._root_window_spec()
            try:
                spec = root.child_window(**self._criteria)
                spec.wait("exists visible", timeout=self._timeout)
                return spec
            except Exception as exc:
                raise ElementNotFoundError(
                    f"MenuItem {self._criteria!r} not found after {self._timeout}s"
                ) from exc
        return super()._resolve()

    def _root_window_spec(self) -> Any:
        p: Any = self._parent
        while isinstance(p, Locator):
            p = p._parent
        return p._get_spec()


class Menu(Element):
    """A menu bar item or top-level menu.

    Usage::

        window.menu("File").item("Save").click()
        window.menu("Edit").item("Find...").click()
        window.menu("View").item("Zoom").item("100%").click()
    """

    def item(self, text: str) -> MenuItem:
        """Return a locator for a child menu item by its visible text.

        The parent menu is automatically opened before the item is resolved.
        Supports chaining for nested submenus::

            window.menu("View").item("Zoom").item("100%").click()
        """
        return MenuItem(self, title=text, control_type="MenuItem")


class Tree(Element):
    """A tree view control.

    Usage::

        tree = window.tree()
        tree.expand_item("Root", "Branch")
        tree.select_item_by_path("Root", "Branch", "Leaf")
    """

    def expand_item(self, *path: str) -> Tree:
        """Expand a tree node by its path of node titles."""
        element = self._resolve()
        for name in path:
            item = element.child_window(title=name, control_type="TreeItem")
            item.wait("exists visible", timeout=self._timeout)
            try:
                item.expand()
            except Exception:
                item.click_input()
            time.sleep(0.1)
            element = item
        return self

    def select_item_by_path(self, *path: str) -> Tree:
        """Select a tree node by path, expanding parent nodes as needed."""
        if not path:
            return self
        element = self._resolve()
        nodes = list(path)
        while len(nodes) > 1:
            name = nodes.pop(0)
            item = element.child_window(title=name, control_type="TreeItem")
            item.wait("exists visible", timeout=self._timeout)
            try:
                item.expand()
            except Exception:
                item.click_input()
            time.sleep(0.1)
            element = item
        final = element.child_window(title=nodes[0], control_type="TreeItem")
        final.wait("exists visible", timeout=self._timeout)
        try:
            final.select()
        except Exception:
            final.click_input()
        return self


class ListBox(Element):
    """A list box control.

    Usage::

        lb = window.list_box()
        lb.select_item("Option A")
        names = lb.items()
        selected = lb.selected_items()
    """

    def items(self) -> list[str]:
        """Return the text of all list items."""
        element = self._resolve()
        children = element.children(control_type="ListItem")
        return [c.window_text() for c in children]

    def selected_items(self) -> list[str]:
        """Return the text of currently selected list items."""
        element = self._resolve()
        result = []
        for child in element.children(control_type="ListItem"):
            try:
                if child.is_selected():
                    result.append(child.window_text())
            except Exception:
                # is_selected() not available — fall back to toggle state
                try:
                    if child.get_toggle_state():
                        result.append(child.window_text())
                except Exception:
                    pass
        return result


class Tab(Element):
    """A tab control (notebook / property sheet).

    Usage::

        window.tab().select_tab("General")
        window.tab(name="Options").select_tab("Advanced")
    """

    def select_tab(self, name: str) -> Tab:
        """Select a tab page by its visible name."""
        element = self._resolve()
        try:
            element.child_window(title=name, control_type="TabItem").click_input()
            return self
        except Exception:
            pass
        for child in element.children():
            try:
                if child.window_text() == name:
                    child.click_input()
                    return self
            except Exception:
                continue
        return self


class Toolbar(Element):
    """A toolbar control.

    Usage::

        window.toolbar().button("Bold").click()
        window.toolbar(name="Formatting").button("Italic").click()
    """

    def button(self, name: str) -> Button:
        """Return a :class:`Button` locator for a toolbar button by its accessible name."""
        return Button(self, title=name, control_type="Button")
