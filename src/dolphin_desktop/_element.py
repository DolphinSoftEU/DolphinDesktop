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

import re
import time
from typing import Any

from ._exceptions import ElementNotFoundError, UnsupportedPatternError, WaitTimeoutError
from ._locator import Locator, _require_check_state, _wrapper_of
from ._win32_criteria import backend_name


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


def _menu_text_key(text: str) -> str:
    """Caption as displayed: no ``\\t``-separated shortcut, no mnemonic marker.

    In a Win32 caption ``&x`` marks ``x`` as the mnemonic and ``&&`` is a
    literal ampersand, so ``R&&D`` displays as ``R&D``.
    """
    return re.sub(r"&(.)", r"\1", str(text).split("\t", 1)[0]).strip()


def _caption_matches(caption: str, wanted: str) -> bool:
    """Whether *wanted* names a caption, as displayed or as raw caption text."""
    return wanted in (_menu_text_key(caption), str(caption).split("\t", 1)[0].strip())


class _Win32MenuEntry:
    """Locator-facing view of one item of a native Win32 menu (``HMENU``).

    A ``MainMenu`` / MFC / VB6 menu bar is not a tree of child windows, so the
    win32 backend cannot ``child_window()`` its items (KAN-747). pywinauto
    reaches them through the owner window's menu handle instead. This wraps
    such an item with the few methods :class:`Locator` calls on a resolved
    element.
    """

    def __init__(self, item: Any) -> None:
        self._item = item

    def click_input(self) -> None:
        """Physical click; pywinauto opens each parent menu on the way."""
        self._item.click_input()

    def invoke(self) -> None:
        """Programmatic selection: ``WM_COMMAND`` to the owner, no mouse input."""
        self._item.select()

    def is_enabled(self) -> bool:
        return bool(self._item.is_enabled())

    def get_check_state(self) -> int:
        """0 or 1 from the ``HMENU``, read without opening the menu."""
        return int(bool(self._item.is_checked()))

    def toggle(self) -> None:
        """Flip a checkable item through its command, like :meth:`invoke`.

        ``select()`` sends ``WM_COMMAND`` and waits for it (up to a second), so
        the application has handled it by the time this returns. A disabled
        item raises ``MenuItemNotEnabled``.
        """
        self._item.select()

    def is_visible(self) -> bool:
        """Whether the item is on screen, as opposed to merely in the ``HMENU``.

        ``GetMenuItemRect`` answers an empty rectangle for an item whose popup
        is closed. A menu-bar item has a rectangle for as long as the owner has
        its menu bar, so the owner window has to be visible as well.
        """
        rect = self._item.rectangle()
        on_screen = rect.right > rect.left and rect.bottom > rect.top
        return on_screen and bool(self._item.ctrl.is_visible())

    def window_text(self) -> str:
        return _menu_text_key(self._item.text())

    def rectangle(self) -> Any:
        return self._item.rectangle()


def _native_menu_root(locator: Locator) -> tuple[Any, list[str]] | None:
    """Return ``(window spec, title path)`` when *locator* is a win32 menu.

    ``None`` keeps the UIA path: the chain is not rooted in a win32 window,
    or some level has no title to match a native menu item by.
    """
    titles: list[str] = []
    node: Any = locator
    while isinstance(node, (Menu, MenuItem)):
        title = node._criteria.get("title")
        if not isinstance(title, str):
            return None
        titles.append(title)
        node = node._parent
    # The HMENU belongs to the top-level window, whatever locator a Menu was
    # chained from, so walk on to the root as _root_window_spec() does.
    while isinstance(node, Locator):
        node = node._parent
    try:
        spec = node._get_spec()
    except Exception:
        return None
    if backend_name(spec) != "win32":
        return None
    titles.reverse()
    return spec, titles


def _find_native_menu_item(spec: Any, titles: list[str]) -> Any:
    """One pass over the owner window's menu; raises ``LookupError`` on a miss."""
    menu = spec.wrapper_object().menu()
    if menu is None:
        raise ElementNotFoundError(
            "this window has no native Win32 menu",
            hint=(
                "a WinForms MenuStrip / ToolStrip or a custom-drawn menu bar is a "
                "control, not an HMENU: locate it with the uia backend instead"
            ),
        )
    item = None
    for depth, title in enumerate(titles):
        if menu is None:
            raise LookupError(f"{titles[depth - 1]!r} has no submenu")
        items = menu.items()
        item = next((i for i in items if _caption_matches(i.text(), title)), None)
        if item is None:
            available = [_menu_text_key(i.text()) for i in items if i.text()]
            raise LookupError(f"no item {title!r} at this level (available: {available})")
        menu = item.sub_menu() if depth < len(titles) - 1 else None
    return item


def _wait_native_menu_visible(
    locator: Locator, root: tuple[Any, list[str]], timeout: float
) -> None:
    """Wait for a native menu item to be on screen, without opening its menu.

    Resolving proves only that the item is in the ``HMENU``, and raises
    :class:`ElementNotFoundError` when it is not. Unlike a UIA menu item, whose
    resolution clicks the parent menu open, a native one stays closed: waiting
    must not drive input into the application.
    """
    deadline = time.monotonic() + timeout
    entry = _resolve_native_menu(locator, root, deadline=deadline, single_attempt=timeout <= 0)
    while not entry.is_visible():
        if time.monotonic() >= deadline:
            raise WaitTimeoutError(
                f"Win32 menu item {' -> '.join(root[1])!r} is in the menu but not on "
                f"screen after {timeout}s",
                hint="open its parent menu first, for example with menu.click()",
            )
        time.sleep(0.05)


def _resolve_native_menu(
    locator: Locator,
    root: tuple[Any, list[str]],
    *,
    deadline: float | None,
    single_attempt: bool,
) -> _Win32MenuEntry:
    spec, titles = root
    if single_attempt:
        deadline = time.monotonic()
    elif deadline is None:
        deadline = time.monotonic() + locator._timeout
    while True:
        try:
            return _Win32MenuEntry(_find_native_menu_item(spec, titles))
        except ElementNotFoundError:
            raise
        except Exception as exc:
            last_error: Exception = exc
        if time.monotonic() >= deadline:
            raise ElementNotFoundError(
                f"Win32 menu item {' -> '.join(titles)!r} not found after "
                f"{locator._timeout}s: {last_error}"
            ) from last_error
        time.sleep(0.1)


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

    def _resolve(
        self,
        deadline: float | None = None,
        *,
        single_attempt: bool = False,
    ) -> Any:
        native = _native_menu_root(self)
        if native is not None:
            return _resolve_native_menu(
                self,
                native,
                deadline=deadline,
                single_attempt=single_attempt or self._timeout <= 0,
            )
        inherited_deadline = deadline is not None
        single_attempt = single_attempt or self._timeout <= 0
        from ._locator import (
            _deadline_expired,
            _effective_deadline,
            _wait_until_visible,
        )

        deadline, enforce_deadline = _effective_deadline(
            self._timeout,
            deadline,
            single_attempt=single_attempt,
        )
        bounded = inherited_deadline or single_attempt
        if isinstance(self._parent, (Menu, MenuItem)):
            # Open the parent menu by clicking it.
            parent_el = self._parent._resolve(
                deadline=deadline,
                single_attempt=single_attempt,
            )
            try:
                parent_el.click_input()
            except Exception:
                # Click via mouse can fail (locked workstation, off-screen);
                # try UIA Invoke pattern as a fallback so menu still opens.
                try:
                    parent_el.invoke()
                except Exception:
                    pass
            if not single_attempt:
                remaining = max(0.0, deadline - time.monotonic())
                if enforce_deadline and remaining <= 0:
                    raise ElementNotFoundError("menu item resolution deadline expired")
                time.sleep(min(0.2, remaining) if enforce_deadline else 0.2)
                if _deadline_expired(deadline, self._timeout, enforce=enforce_deadline):
                    raise ElementNotFoundError("menu item resolution deadline expired")
            # First, try to find the item as a direct child of the parent element
            # (covers Win32 menus and UIA in-tree menus).
            try:
                spec = parent_el.child_window(**self._criteria)
                if bounded:
                    _wait_until_visible(spec, 0)
                else:
                    spec.wait("exists visible", timeout=max(self._timeout * 0.4, 0.5))
                if not _deadline_expired(deadline, self._timeout, enforce=enforce_deadline):
                    return spec
            except Exception:
                pass
            # Second: search the root main window scope.
            root = self._root_window_spec()
            try:
                spec = root.child_window(**self._criteria)
                if bounded:
                    _wait_until_visible(spec, 0)
                else:
                    spec.wait("exists visible", timeout=max(self._timeout * 0.3, 0.5))
                if not _deadline_expired(deadline, self._timeout, enforce=enforce_deadline):
                    return spec
            except Exception:
                pass
            # Third (Qt-aware fallback): Qt opens popup menus as top-level
            # ``QMenu`` windows detached from the main window. Walk every
            # visible top-level window of the same process and search for the
            # MenuItem there. Same approach used by ``_select_combo_popup``.
            try:
                spec = self._find_in_popup_windows(timeout=0 if bounded else None)
                if spec is not None and not _deadline_expired(
                    deadline,
                    self._timeout,
                    enforce=enforce_deadline,
                ):
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
        return super()._resolve(deadline=deadline, single_attempt=single_attempt)

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

        A native Win32 menu item is read from the menu handle, which opens
        nothing either.
        """
        native = _native_menu_root(self)
        if native is not None:
            return _resolve_native_menu(
                self, native, deadline=None, single_attempt=self._timeout <= 0
            )
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

    def _resolve_presence(
        self,
        deadline: float | None = None,
        *,
        single_attempt: bool = False,
    ) -> Any:
        native = _native_menu_root(self)
        if native is not None:
            return _resolve_native_menu(
                self,
                native,
                deadline=deadline,
                single_attempt=single_attempt or self._timeout <= 0,
            )
        return super()._resolve_presence(deadline=deadline, single_attempt=single_attempt)

    def wait_for(self, *, state: str = "visible", timeout: float | None = None) -> Locator:
        native = _native_menu_root(self) if state == "visible" else None
        if native is None:
            return super().wait_for(state=state, timeout=timeout)
        _wait_native_menu_visible(self, native, self._timeout if timeout is None else timeout)
        return self

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

    # A menu-bar entry of a win32 window lives in the window's HMENU, not in
    # its child windows: resolve it there (KAN-747), everything else as usual.

    def _resolve(
        self,
        deadline: float | None = None,
        *,
        single_attempt: bool = False,
    ) -> Any:
        native = _native_menu_root(self)
        if native is not None:
            return _resolve_native_menu(
                self,
                native,
                deadline=deadline,
                single_attempt=single_attempt or self._timeout <= 0,
            )
        return super()._resolve(deadline=deadline, single_attempt=single_attempt)

    def _resolve_presence(
        self,
        deadline: float | None = None,
        *,
        single_attempt: bool = False,
    ) -> Any:
        native = _native_menu_root(self)
        if native is not None:
            return _resolve_native_menu(
                self,
                native,
                deadline=deadline,
                single_attempt=single_attempt or self._timeout <= 0,
            )
        return super()._resolve_presence(deadline=deadline, single_attempt=single_attempt)

    def wait_for(self, *, state: str = "visible", timeout: float | None = None) -> Locator:
        native = _native_menu_root(self) if state == "visible" else None
        if native is None:
            return super().wait_for(state=state, timeout=timeout)
        _wait_native_menu_visible(self, native, self._timeout if timeout is None else timeout)
        return self


class Tree(Element):
    """A tree view control.

    Usage::

        tree = window.tree()
        tree.expand_item("Root", "Branch")
        tree.select_item_by_path("Root", "Branch", "Leaf")
    """

    def _win32_tree(self, element: Any) -> Any | None:
        """The pywinauto ``TreeViewWrapper`` behind *element*, on win32 only.

        A Win32 ``SysTreeView32`` (classic, WinForms, VCL) is one window whose
        nodes are not child windows, so the UIA ``TreeItem`` search below can
        never see them (KAN-747). pywinauto drives them through ``TVM_*``
        messages instead.
        """
        wrapper = _wrapper_of(element)
        if backend_name(wrapper) != "win32" or not callable(getattr(wrapper, "get_item", None)):
            return None
        return wrapper

    def _win32_tree_item(self, tree: Any, path: tuple[str, ...]) -> Any:
        """Find the node at *path* by exact text, expanding its ancestors.

        Polls like the UIA path does, for nodes an application adds lazily.
        """
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                item = tree.get_item(list(path), exact=True)
                if item is not None:
                    return item
                last_error: Exception = LookupError("the tree has no items")
            except Exception as exc:
                last_error = exc
            if time.monotonic() >= deadline:
                raise ElementNotFoundError(
                    f"Tree item {' -> '.join(path)!r} not found after {self._timeout}s: "
                    f"{last_error}"
                ) from last_error
            time.sleep(0.1)

    def expand_item(self, *path: str) -> Tree:
        """Expand a tree node by its path of node titles."""
        element = self._resolve()
        win32_tree = self._win32_tree(element)
        if win32_tree is not None:
            if path:
                self._win32_tree_item(win32_tree, path).expand()
            return self
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
        win32_tree = self._win32_tree(element)
        if win32_tree is not None:
            # TVM_SELECTITEM raises TVN_SELCHANGED, so the application sees
            # the same selection-changed event a click would fire.
            self._win32_tree_item(win32_tree, path).select()
            return self
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

    @staticmethod
    def _win32_list(element: Any) -> tuple[Any, str] | None:
        """``(wrapper, friendly class)`` for a win32 list box or list view.

        Their entries are not child windows, so the UIA ``ListItem`` search
        below finds nothing on win32 (KAN-747); pywinauto reads them with
        ``LB_*`` / ``LVM_*`` messages instead.
        """
        wrapper = _wrapper_of(element)
        if backend_name(wrapper) != "win32":
            return None
        try:
            kind = str(wrapper.friendly_class_name())
        except Exception:
            return None
        return (wrapper, kind) if kind in ("ListBox", "ListView") else None

    @staticmethod
    def _win32_list_texts(wrapper: Any, kind: str) -> list[str]:
        if kind == "ListBox":
            return [str(text) for text in wrapper.item_texts()]
        return [str(wrapper.get_item(index).text()) for index in range(wrapper.item_count())]

    def items(self) -> list[str]:
        """Return the text of all list items.

        On win32 a list view reports the first column of each row.
        """
        element = self._resolve()
        win32_list = self._win32_list(element)
        if win32_list is not None:
            return self._win32_list_texts(*win32_list)
        children = element.children(control_type="ListItem")
        return [c.window_text() for c in children]

    def selected_items(self) -> list[str]:
        """Return the text of currently selected list items."""
        element = self._resolve()
        win32_list = self._win32_list(element)
        if win32_list is not None:
            wrapper, kind = win32_list
            texts = self._win32_list_texts(wrapper, kind)
            if kind == "ListBox":
                # A single-selection list box reports LB_ERR (-1) for "none".
                return [texts[i] for i in wrapper.selected_indices() if 0 <= i < len(texts)]
            return [
                texts[index] for index in range(len(texts)) if wrapper.get_item(index).is_selected()
            ]
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
        wrapper = _wrapper_of(element)
        if backend_name(wrapper) == "win32" and callable(getattr(wrapper, "get_tab_text", None)):
            self._select_win32_tab(wrapper, name)
            return self
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

    def _select_win32_tab(self, wrapper: Any, name: str) -> None:
        """Select a ``SysTabControl32`` tab by exact text (KAN-747).

        Its tabs are not child windows, so the ``TabItem`` search cannot see
        them. pywinauto's ``select(text)`` best-matches the text and could pick
        a different tab, so the index is found here by exact text (ignoring an
        ``&`` mnemonic) and selected by index.

        ``select(index)`` sends ``TCM_SETCURFOCUS``, which raises
        ``TCN_SELCHANGE`` on a regular tab control. With ``TCS_BUTTONS`` that
        message moves only the focus, so pywinauto posts a click on the tab
        instead. The selection is read back either way: the click can miss and
        the application can veto the change from ``TCN_SELCHANGING``, and
        neither of those may pass for success.
        """
        texts = [str(wrapper.get_tab_text(index)) for index in range(wrapper.tab_count())]
        for index, text in enumerate(texts):
            if _caption_matches(text, name):
                wrapper.select(index)
                self._confirm_win32_tab(wrapper, name, index)
                return
        raise ElementNotFoundError(
            f"Tab page {name!r} not found in {self._criteria!r} (tabs: {texts})"
        )

    def _confirm_win32_tab(self, wrapper: Any, name: str, index: int) -> None:
        deadline = time.monotonic() + min(self._timeout, 2.0)
        while True:
            selected = wrapper.get_selected_tab()
            if selected == index:
                return
            if time.monotonic() >= deadline:
                raise WaitTimeoutError(
                    f"Tab page {name!r} was not selected in {self._criteria!r}: the "
                    f"control stayed on tab index {selected}",
                    hint="the application may refuse the change, or a modal dialog is open",
                )
            time.sleep(0.05)


class Toolbar(Element):
    """A toolbar control.

    Usage::

        window.toolbar().button("Bold").click()
        window.toolbar(name="Formatting").button("Italic").click()
    """

    def button(self, name: str) -> Button:
        """Return a :class:`Button` locator for a toolbar button by its accessible name."""
        return Button(self, title=name, control_type="Button")
