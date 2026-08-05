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

from ._exceptions import ElementNotFoundError, UnsupportedPatternError
from ._locator import Locator, _require_check_state


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
        """Select this radio button via UIA ``SelectionItemPattern``.

        UIA radio buttons implement SelectionItem, never Toggle — going
        through :meth:`check` would call ``toggle()`` and raise
        ``NoPatternInterfaceError`` on standard WPF / WinForms / Qt radios.
        Win32 (VCL, MFC) radio buttons are the inverse: they expose only a
        check state, so they fall back to :meth:`check`.

        Raises :class:`UnsupportedPatternError` when the radio implements
        neither pattern — never a raw pywinauto error.
        """
        try:
            super().select()
        except UnsupportedPatternError:
            self.check()
        return self

    def is_checked(self) -> bool:
        """Return True when this radio button is the selected option in its group.

        Raises :class:`UnsupportedPatternError` when neither SelectionItem nor
        a check state is readable.
        """
        element = self._resolve()
        try:
            return bool(element.is_selected())
        except Exception:
            return _require_check_state(element, "is_checked") == 1


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
                # Click via mouse can fail (locked workstation, off-screen);
                # try UIA Invoke pattern as a fallback so menu still opens.
                try:
                    parent_el.invoke()
                except Exception:
                    pass
            time.sleep(0.2)
            # First, try to find the item as a direct child of the parent element
            # (covers Win32 menus and UIA in-tree menus).
            try:
                spec = parent_el.child_window(**self._criteria)
                spec.wait("exists visible", timeout=max(self._timeout * 0.4, 0.5))
                return spec
            except Exception:
                pass
            # Second: search the root main window scope.
            root = self._root_window_spec()
            try:
                spec = root.child_window(**self._criteria)
                spec.wait("exists visible", timeout=max(self._timeout * 0.3, 0.5))
                return spec
            except Exception:
                pass
            # Third (Qt-aware fallback): Qt opens popup menus as top-level
            # ``QMenu`` windows detached from the main window. Walk every
            # visible top-level window of the same process and search for the
            # MenuItem there. Same approach used by ``_select_combo_popup``.
            try:
                spec = self._find_in_popup_windows()
                if spec is not None:
                    return spec
            except Exception:
                pass
            raise ElementNotFoundError(
                f"MenuItem {self._criteria!r} not found after {self._timeout}s "
                "(searched parent-element scope, main window, and same-process "
                "top-level windows). Qt popup submenus and Win32 detached menus "
                "should be reachable via this fallback — if you hit this, the "
                "submenu may not have opened (parent click failed) or its name "
                "is localised differently."
            )
        return super()._resolve()

    def _resolve_readonly(self) -> Any:
        """Locate the item without clicking the parent menu open.

        ``is_visible`` / ``is_enabled`` / ``exists`` — and therefore
        ``wait_until_hidden``, which polls every 100 ms — must not drive real
        input into the application under test just to answer a question. An
        item of a closed menu is simply not in the tree, which is the correct
        answer for all three.

        Honours ``self._timeout`` like every other resolver: ``exists(5.0)``
        on a menu an action repopulates asynchronously has to wait.
        ``is_visible`` / ``is_enabled`` pre-set it to ``0`` via ``timeout(0)``,
        which keeps them single-pass.
        """
        if isinstance(self._parent, (Menu, MenuItem)):
            deadline = time.monotonic() + self._timeout
            while True:
                spec = self._find_open_item()
                if spec is not None:
                    return spec
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            raise ElementNotFoundError(
                f"MenuItem {self._criteria!r} is not currently in the UI tree "
                f"after {self._timeout}s (its menu is closed, or the item is "
                "named differently)"
            )
        return super()._resolve_readonly()

    def _find_open_item(self) -> Any | None:
        """One non-mutating pass for this item in an already-open menu.

        Same scope order as :meth:`_resolve` — parent element, root window,
        detached popup windows — minus the click that opens the parent.
        """
        # The parent menu scope comes first: the root window scope alone cannot
        # tell two identically-named items apart (a "Copy" in the Edit menu and
        # a "Copy" in a context menu would answer for each other).
        scopes = []
        parent = self._parent
        if isinstance(parent, Locator):
            try:
                scopes.append(parent.timeout(0)._resolve_readonly())
            except Exception:
                pass
        scopes.append(self._root_window_spec())
        for scope in scopes:
            try:
                spec = scope.child_window(**self._criteria)
                spec.wait("exists visible", timeout=0)
                return spec
            except Exception:
                continue
        return self._find_in_popup_windows(timeout=0)

    def _root_window_spec(self) -> Any:
        p: Any = self._parent
        while isinstance(p, Locator):
            p = p._parent
        return p._get_spec()

    def _find_in_popup_windows(self, *, timeout: float | None = None) -> Any | None:
        """Walk top-level windows of the same process; return matching MenuItem if any.

        Qt's ``QMenu`` popups are top-level windows (not children of the
        menubar). pywinauto's ``child_window`` from the main window misses
        them — we must enumerate ``pywinauto.Desktop`` and filter by PID.

        The PID filter is pushed into ``windows(process=...)`` so UIA applies
        it inside a single ``FindAll``: this runs on every poll of a closed
        menu (~100 times for a 10 s ``wait_until_hidden``), and filtering in
        Python costs one COM round-trip per top-level window on the desktop.

        *timeout* overrides the derived scan budget; ``0`` makes it a single
        pass, which is what the polled read-only path needs.
        """
        from pywinauto import Desktop as _PwDesktop

        root_spec = self._root_window_spec()
        try:
            target_pid = root_spec.wrapper_object().process_id()
        except Exception:
            return None

        # Monotonic clock: wall-clock ``time.time()`` would misbehave on
        # NTP adjustments or user clock changes, either cutting the menu
        # scan short or leaving it running past the timeout.
        budget = max(self._timeout * 0.3, 0.5) if timeout is None else timeout
        deadline = time.monotonic() + budget
        while True:
            try:
                tops = _PwDesktop(backend="uia").windows(process=target_pid)
            except Exception:
                tops = []
            for top in tops:
                try:
                    spec = top.child_window(**self._criteria)
                    spec.wait("exists visible", timeout=0)
                    return spec
                except Exception:
                    continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)


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
        """Select a tab page by its visible name.

        Raises :class:`ElementNotFoundError` when no tab page matches *name*.
        """
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
        raise ElementNotFoundError(
            f"Tab page {name!r} not found in {self._criteria!r} "
            f"(searched TabItem children and every child's visible text)"
        )


class Toolbar(Element):
    """A toolbar control.

    Usage::

        window.toolbar().button("Bold").click()
        window.toolbar(name="Formatting").button("Italic").click()
    """

    def button(self, name: str) -> Button:
        """Return a :class:`Button` locator for a toolbar button by its accessible name."""
        return Button(self, title=name, control_type="Button")
