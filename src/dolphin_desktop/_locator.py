"""Locator — lazy, chainable element finder (core of the dolphin API)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pywinauto.keyboard import send_keys as _send_keys

from ._config import get_timeout as _get_timeout
from ._exceptions import ElementNotFoundError, WaitTimeoutError


def _trace_step(
    action: str,
    criteria: dict,
    element: Any = None,
    error: str | None = None,
) -> None:
    from . import _trace

    session = _trace.current_session()
    if session is not None:
        session.record_step(action, repr(criteria), element, error)


if TYPE_CHECKING:
    from PIL.Image import Image

    from ._window import Window


class Locator:
    """Represents a way to find one or more UI elements.

    Locators are lazy — they do not search for elements until an action or
    assertion method is called.  This mirrors Playwright's Locator API.

    Usage::

        btn = window.get_by_title("OK", control_type="Button")
        btn.click()

        edit = window.get_by_role("Edit")
        edit.type_text("hello")
        assert edit.text() == "hello"
    """

    def __init__(
        self,
        parent: Window | Locator,
        **criteria: Any,
    ) -> None:
        self._parent = parent
        self._fallback: list[dict[str, Any]] = list(criteria.pop("fallback", None) or [])
        self._image_fallback: Any = criteria.pop("image_fallback", None)
        self._criteria = criteria
        self._timeout: float = _get_timeout()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def timeout(self, seconds: float) -> Locator:
        """Return a new Locator with a different timeout (does not mutate self)."""
        clone = Locator(
            self._parent,
            fallback=self._fallback,
            image_fallback=self._image_fallback,
            **self._criteria,
        )
        clone._timeout = seconds
        return clone

    # ------------------------------------------------------------------
    # Chaining
    # ------------------------------------------------------------------

    def locator(self, **criteria: Any) -> Locator:
        """Find a descendant element matching *criteria* inside this element."""
        return Locator(self, **criteria)

    def nth(self, index: int) -> Locator:
        """Select the nth match (0-based) from a set of matching elements."""
        return Locator(self._parent, **{**self._criteria, "found_index": index})

    # ------------------------------------------------------------------
    # Resolution (internal)
    # ------------------------------------------------------------------

    def _get_parent_spec(self) -> Any:
        if isinstance(self._parent, Locator):
            return self._parent._resolve()
        return self._parent._get_spec()

    def _resolve(self) -> Any:
        """Find and wait for the element, raise on timeout."""
        parent_spec = self._get_parent_spec()

        # Try primary selector
        primary_exc: Exception | None = None
        try:
            spec = parent_spec.child_window(**self._criteria)
            spec.wait("exists visible", timeout=self._timeout)
            return spec
        except Exception as exc:
            primary_exc = exc

        # Try fallbacks in definition order (immediate check — primary already timed out)
        from . import _selfheal

        for fb in self._fallback:
            try:
                fb_spec = parent_spec.child_window(**fb)
                fb_spec.wait("visible", timeout=0)
                _selfheal.record_fallback(self._criteria, fb)
                return fb_spec
            except Exception:
                continue

        # Last resort: image-based fallback
        if self._image_fallback is not None:
            try:
                result = self._image_fallback.find_with_size()
                if result is not None:
                    cx, cy, tw, th = result
                    from ._image import _ImageElement

                    _selfheal.record_fallback(
                        self._criteria,
                        {"image": str(self._image_fallback._template_path)},
                    )
                    return _ImageElement(cx, cy, tw, th)
            except Exception:
                pass

        # TreeWalker fallback: IUIAutomation::FindAll misses some elements exposed
        # via TreeWalker (e.g. ToolbarWindow32 button children in Notepad++).
        tree_result = _tree_walk_find(parent_spec, self._criteria)
        if tree_result is not None:
            return tree_result

        # All selectors exhausted
        tree = _dump_tree(parent_spec)
        raise ElementNotFoundError(
            f"Element {self._criteria!r} not found after {self._timeout}s. Last seen tree:\n{tree}"
        ) from primary_exc

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def click(self) -> Locator:
        element = None
        try:
            element = self._resolve()
            element.click_input()
        except Exception as exc:
            _trace_step("click", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("click", self._criteria, element=element)
        return self

    def double_click(self) -> Locator:
        element = None
        try:
            element = self._resolve()
            element.double_click_input()
        except Exception as exc:
            _trace_step("double_click", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("double_click", self._criteria, element=element)
        return self

    def right_click(self) -> Locator:
        element = None
        try:
            element = self._resolve()
            element.right_click_input()
        except Exception as exc:
            _trace_step("right_click", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("right_click", self._criteria, element=element)
        return self

    def type_text(
        self,
        text: str,
        *,
        with_spaces: bool = True,
        pause: float = 0.05,
    ) -> Locator:
        """Type text character by character (sends WM_CHAR events).

        The default pause of 0.05 s between keystrokes matches pywinauto's own
        default and prevents missed/doubled keys in modern WinUI/XAML controls.
        """
        element = None
        try:
            element = self._resolve()
            element.type_keys(text, with_spaces=with_spaces, pause=pause)
        except Exception as exc:
            _trace_step("type_text", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("type_text", self._criteria, element=element)
        return self

    def set_text(self, text: str) -> Locator:
        """Replace the entire text content of an edit control.

        Falls back to select-all + keyboard input for Document/RichEdit controls
        (e.g. Windows 11 Notepad) that do not support IValueProvider.SetValue.
        """
        element = None
        try:
            element = self._resolve()
            try:
                element.set_edit_text(text)
                _trace_step("set_text", self._criteria, element=element)
                return self
            except Exception:
                pass
            element.set_focus()
            time.sleep(0.05)
            _send_keys("^a")
            if text:
                element.type_keys(text, with_spaces=True, pause=0.05)
            else:
                _send_keys("{DELETE}")
        except Exception as exc:
            _trace_step("set_text", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("set_text", self._criteria, element=element)
        return self

    def clear(self) -> Locator:
        """Clear the text of an edit control."""
        element = None
        try:
            element = self._resolve()
            try:
                element.set_edit_text("")
                _trace_step("clear", self._criteria, element=element)
                return self
            except Exception:
                pass
            element.set_focus()
            time.sleep(0.05)
            _send_keys("^a{DELETE}")
        except Exception as exc:
            _trace_step("clear", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("clear", self._criteria, element=element)
        return self

    def press_key(self, key: str) -> Locator:
        """Send a key sequence to the element using pywinauto key syntax."""
        element = None
        try:
            element = self._resolve()
            element.type_keys(key)
        except Exception as exc:
            _trace_step("press_key", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("press_key", self._criteria, element=element)
        return self

    def select_item(self, item: str | int) -> Locator:
        """Select an item in a list/combo box by text or 0-based index."""
        element = None
        try:
            element = self._resolve()
        except Exception as exc:
            _trace_step("select_item", self._criteria, error=str(exc))
            raise
        try:
            # Win32 backend and UIA ComboBox expose select(item).
            element.select(item)
        except TypeError:
            # UIA ListBox: UIAWrapper.select() takes no arguments.
            # Find the child ListItem and call select() on it directly.
            if isinstance(item, int):
                element.children(control_type="ListItem")[item].select()
            else:
                element.child_window(title=item, control_type="ListItem").wrapper_object().select()
        except ValueError:
            # WinForms ComboBox (UIA): pywinauto's select() raises ValueError from
            # selected_index() when no item is pre-selected and ISelectionItemProvider
            # is unavailable on child elements.  Fall back to expand → invoke.
            if isinstance(item, int):
                try:
                    element.expand()
                    time.sleep(0.05)
                except Exception:
                    pass
                items = element.children(control_type="ListItem")
                if not items:
                    # WinForms pattern: items live under a List child
                    lists = element.children(control_type="List")
                    if lists:
                        items = lists[0].children(control_type="ListItem")
                if items:
                    items[item].invoke()
                else:
                    element.descendants(control_type="ListItem")[item].click_input()
            else:
                element.child_window(title=item, control_type="ListItem").wrapper_object().select()
        except (IndexError, Exception):
            # Win32 combo box (e.g. VCL TUIStateAwareComboBox): not recognised by
            # pywinauto as ComboBoxWrapper → no select() method → use CB_* messages.
            try:
                import win32gui

                hwnd = element.handle
                if isinstance(item, str):
                    idx = win32gui.SendMessage(hwnd, 0x0158, -1, item)  # CB_FINDSTRINGEXACT
                    if idx == -1:
                        idx = win32gui.SendMessage(hwnd, 0x014C, -1, item)  # CB_FINDSTRING
                else:
                    idx = item
                if idx != -1:
                    win32gui.SendMessage(hwnd, 0x014E, idx, 0)  # CB_SETCURSEL
                    # Notify parent of selection change (VCL needs CBN_SELCHANGE = 1)
                    parent_hwnd = win32gui.GetParent(hwnd)
                    ctrl_id = win32gui.GetDlgCtrlID(hwnd)
                    win32gui.SendMessage(
                        parent_hwnd, 0x0111, (1 << 16) | ctrl_id, hwnd
                    )  # WM_COMMAND/CBN_SELCHANGE
                    _trace_step("select_item", self._criteria, element=element)
                    return self
            except Exception:
                pass
            # Qt ComboBox: the dropdown opens as a detached popup window in the UIA
            # tree — child_window() finds nothing.  After expand(), scan the process's
            # top-level windows for a popup that contains ListItems.
            _select_via_popup(element, item, self._timeout)
        _trace_step("select_item", self._criteria, element=element)
        return self

    def check(self) -> Locator:
        """Check a checkbox."""
        element = None
        try:
            element = self._resolve()
            if _get_check_state(element) == 0:
                _toggle_element(element)
        except Exception as exc:
            _trace_step("check", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("check", self._criteria, element=element)
        return self

    def uncheck(self) -> Locator:
        """Uncheck a checkbox."""
        element = None
        try:
            element = self._resolve()
            if _get_check_state(element) != 0:
                _toggle_element(element)
        except Exception as exc:
            _trace_step("uncheck", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("uncheck", self._criteria, element=element)
        return self

    def focus(self) -> Locator:
        self._resolve().set_focus()
        return self

    def scroll_into_view(self) -> Locator:
        element = self._resolve()
        # UIA wrappers have no scroll_into_view(); use the ScrollItemPattern
        # (iface_scroll_item) when available, falling back to set_focus(), which
        # also brings the element into view for most scrollable containers.
        try:
            element.iface_scroll_item.ScrollIntoView()
        except Exception:
            try:
                element.set_focus()
            except Exception:
                pass
        return self

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def text(self) -> str:
        """Return the text content of the element.

        For standard Edit controls this is window_text().  For Document/RichEdit
        controls (e.g. Windows 11 Notepad RichEditD2DPT) window_text() returns
        an empty string, so we fall back to select-all + clipboard.
        """
        element = self._resolve()
        t = element.window_text()
        if t:
            return t
        try:
            t = element.get_value()
            if t:
                return t
        except Exception:
            pass
        return _read_text_via_clipboard(element)

    def value(self) -> str:
        """Return the value of an edit/spinner control."""
        element = self._resolve()
        try:
            return element.get_value()
        except AttributeError:
            return element.window_text()

    def is_visible(self) -> bool:
        try:
            return bool(self.timeout(0)._resolve().is_visible())
        except Exception:
            return False

    def is_enabled(self) -> bool:
        try:
            return bool(self.timeout(0)._resolve().is_enabled())
        except Exception:
            return False

    def is_checked(self) -> bool:
        return _get_check_state(self._resolve()) == 1

    def exists(self, timeout: float = 0.0) -> bool:
        """Return True if the element exists (and is visible) within *timeout* seconds."""
        try:
            self.timeout(timeout)._resolve()
            return True
        except Exception:
            return False

    def bounding_box(self) -> dict[str, int]:
        """Return {left, top, right, bottom, width, height} in screen coords."""
        rect = self._resolve().rectangle()
        return {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
        }

    # ------------------------------------------------------------------
    # Waiting
    # ------------------------------------------------------------------

    def wait_for(self, *, state: str = "visible", timeout: float | None = None) -> Locator:
        """Wait until the element reaches *state* ('visible', 'enabled', 'exists')."""
        t = timeout if timeout is not None else self._timeout
        parent_spec = self._get_parent_spec()
        try:
            spec = parent_spec.child_window(**self._criteria)
            spec.wait(state, timeout=t)
        except Exception as exc:
            raise WaitTimeoutError(
                f"Element {self._criteria!r} did not reach state '{state}' after {t}s"
            ) from exc
        return self

    def wait_until_hidden(self, timeout: float = 10.0) -> Locator:
        """Wait until the element is no longer visible."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_visible():
                return self
            time.sleep(0.1)
        raise WaitTimeoutError(f"Element {self._criteria!r} still visible after {timeout}s")

    def wait_until_enabled(self, timeout: float = 10.0) -> Locator:
        """Wait until the element is enabled."""
        return self.wait_for(state="enabled", timeout=timeout)

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def screenshot(self, path: str | Path | None = None) -> Image:
        """Capture the element as a PIL Image, optionally saving to *path*."""
        img = self._resolve().capture_as_image()
        if path:
            img.save(path)
        return img

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------

    def hover(self) -> Locator:
        """Move the mouse pointer over the element's centre."""
        import pywinauto.mouse as _mouse  # type: ignore[import-untyped]

        bb = self.bounding_box()
        cx = bb["left"] + bb["width"] // 2
        cy = bb["top"] + bb["height"] // 2
        _mouse.move(coords=(cx, cy))
        return self

    def drag_to(
        self,
        target: Locator | tuple[int, int],
        *,
        duration: float = 0.5,
        button: str = "left",
    ) -> Locator:
        """Drag from this element's centre to *target*."""
        import pywinauto.mouse as _mouse  # type: ignore[import-untyped]

        bb = self.bounding_box()
        src_x = bb["left"] + bb["width"] // 2
        src_y = bb["top"] + bb["height"] // 2

        if isinstance(target, tuple):
            dst_x, dst_y = target
        else:
            tbb = target.bounding_box()
            dst_x = tbb["left"] + tbb["width"] // 2
            dst_y = tbb["top"] + tbb["height"] // 2

        steps = 30
        step_sleep = duration / steps

        _mouse.press(button=button, coords=(src_x, src_y))
        for i in range(1, steps + 1):
            ix = src_x + (dst_x - src_x) * i // steps
            iy = src_y + (dst_y - src_y) * i // steps
            _mouse.move(coords=(ix, iy))
            time.sleep(step_sleep)
        _mouse.release(button=button, coords=(dst_x, dst_y))
        return self

    def scroll(self, direction: str = "down", amount: int = 3) -> Locator:
        """Scroll at the element's centre. direction: 'up' or 'down'."""
        import warnings

        import pywinauto.mouse as _mouse  # type: ignore[import-untyped]

        bb = self.bounding_box()
        cx = bb["left"] + bb["width"] // 2
        cy = bb["top"] + bb["height"] // 2

        if direction == "up":
            wheel_dist = amount
        elif direction == "down":
            wheel_dist = -amount
        else:
            warnings.warn(
                f"scroll direction {direction!r} is not supported; ignoring.",
                stacklevel=2,
            )
            return self

        _mouse.scroll(coords=(cx, cy), wheel_dist=wheel_dist)
        return self

    def get_attribute(self, name: str) -> Any:
        """Return an attribute of the underlying element_info by *name*."""
        info = self._resolve().element_info
        return getattr(info, name, None)

    def select_text(self) -> Locator:
        """Select all text in the element (focus + Ctrl+A)."""
        self._resolve().set_focus()
        time.sleep(0.05)
        _send_keys("^a")
        return self

    def all(self, *, depth: int | None = None) -> list[Locator]:
        """Return all matching elements as a list of Locators.

        If *depth* is None, only direct children are returned.
        If *depth* is given (including 0), descendants up to that depth are returned.
        """
        parent_spec = self._get_parent_spec()
        try:
            if depth is None:
                elements = parent_spec.children(**self._criteria)
            else:
                elements = parent_spec.descendants(depth=depth, **self._criteria)
        except Exception:
            elements = []
        return [_ResolvedLocator(el) for el in elements]

    def count(self) -> int:
        return len(self.all())

    def __repr__(self) -> str:
        return f"Locator({self._criteria!r})"


def _dump_tree(spec: Any, max_depth: int = 3) -> str:
    """Return a compact text snapshot of *spec*'s child tree (for error messages)."""
    lines: list[str] = []
    try:
        _collect_tree(spec, lines, 0, max_depth)
    except Exception:
        return "(could not capture tree)"
    return "\n".join(lines) if lines else "(empty)"


def _collect_tree(
    node: Any, lines: list[str], depth: int, max_depth: int, max_children: int = 20
) -> None:
    if depth > max_depth:
        return
    try:
        children = node.children()
    except Exception:
        return
    for child in children[:max_children]:
        try:
            info = child.element_info
            ctrl = str(getattr(info, "control_type", "") or "")
            name = str(getattr(info, "name", "") or "")
            auto_id = str(getattr(info, "automation_id", "") or "")
            indent = "  " * depth
            parts = [ctrl or "?"]
            if name:
                parts.append(f"name={name!r}")
            if auto_id:
                parts.append(f"id={auto_id!r}")
            lines.append(f"{indent}{' '.join(parts)}")
        except Exception:
            lines.append(f"{'  ' * depth}?")
        _collect_tree(child, lines, depth + 1, max_depth, max_children)


def _select_via_popup(element: Any, item: str | int, timeout: float) -> None:
    """Select a ComboBox item that appears in a detached popup window (Qt pattern).

    Qt QComboBox opens its dropdown as a separate top-level window in the UIA
    tree, so pywinauto's child_window() cannot find the items.  This helper
    expands the combo, waits for the popup, scans top-level windows belonging
    to the same process for ListItem elements, and clicks the target item.
    """
    from pywinauto import Desktop as _Desktop

    try:
        element.expand()
    except Exception:
        pass
    time.sleep(0.2)

    proc_id = element.element_info.process_id
    deadline = time.time() + timeout
    while time.time() < deadline:
        for win in _Desktop(backend="uia").windows():
            try:
                if win.element_info.process_id != proc_id:
                    continue
                all_items = win.descendants(control_type="ListItem")
                if not all_items:
                    continue
                if isinstance(item, str):
                    for li in all_items:
                        try:
                            if li.window_text() == item:
                                li.click_input()
                                return
                        except Exception:
                            continue
                else:
                    if item < len(all_items):
                        all_items[item].click_input()
                        return
            except Exception:
                continue
        time.sleep(0.1)
    raise ElementNotFoundError(f"ComboBox popup item {item!r} not found after {timeout}s")


def _read_text_via_clipboard(element: Any) -> str:
    """Read text from a Document/RichEdit control via select-all + clipboard.

    Used as a fallback when window_text() returns empty (e.g. Windows 11
    Notepad's RichEditD2DPT which does not expose text via INameProvider).

    The clipboard is cleared first so that when the editor is empty (and ^c
    copies nothing), GetClipboardData raises instead of returning stale data.
    """
    import win32clipboard
    import win32con

    # Clear clipboard so stale content isn't returned for an empty editor
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.CloseClipboard()
    except Exception:
        pass

    element.set_focus()
    time.sleep(0.05)
    _send_keys("^a^c")
    time.sleep(0.1)

    try:
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except Exception:
            return ""  # Empty clipboard → editor was empty
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""


def _get_check_state(element: Any) -> int:
    """Return 0/1/2 (unchecked/checked/indeterminate) for Win32 and UIA backends.

    Win32 controls expose get_check_state(); UIA controls expose get_toggle_state().
    """
    try:
        return element.get_check_state()
    except AttributeError:
        pass
    try:
        return element.get_toggle_state()
    except Exception:
        return 0


def _toggle_element(element: Any) -> None:
    """Toggle a checkbox using the UIA Toggle pattern, with click_input() fallback.

    toggle() (IToggleProvider) updates UIA state synchronously; click_input() may
    have a brief delay before UIA reflects the new state.
    """
    try:
        element.toggle()
    except AttributeError:
        element.click_input()


def _tree_walk_find(parent_spec: Any, criteria: dict[str, Any]) -> Any | None:
    """Find an element via UIAElementInfo.children() (IUIAutomation::TreeWalker).

    pywinauto's child_window()/descendants() use IUIAutomation::FindAll which
    misses elements only reachable via TreeWalker, e.g. toolbar buttons inside
    Win32 ToolbarWindow32 controls.  This fallback uses UIAElementInfo.children()
    which calls TreeWalker internally and can reach those elements.
    Returns a pywinauto wrapper on success, or None.
    """
    title = criteria.get("title", "")
    ct = criteria.get("control_type", "")
    if not title and not ct:
        return None

    try:
        root_info = parent_spec.wrapper_object().element_info
    except Exception:
        return None

    def _search(info: Any, depth: int) -> Any | None:
        if depth <= 0:
            return None
        try:
            children = info.children()
        except Exception:
            return None
        for child in children:
            try:
                child_name = (child.name or "").split("\t")[0]
                child_ct = child.control_type or ""
            except Exception:
                continue
            if (not title or child_name == title) and (not ct or child_ct == ct):
                try:
                    import pywinauto as _pw

                    wrapper_cls = _pw.Application(backend="uia").backend.generic_wrapper_class
                    return wrapper_cls(child)
                except Exception:
                    pass
            result = _search(child, depth - 1)
            if result is not None:
                return result
        return None

    return _search(root_info, 8)


class _ResolvedLocator(Locator):
    """A Locator wrapping an already-resolved pywinauto element."""

    def __init__(self, element: Any) -> None:
        self._element = element
        self._criteria: dict[str, Any] = {}
        self._timeout = _get_timeout()

    def timeout(self, seconds: float) -> _ResolvedLocator:
        clone = _ResolvedLocator(self._element)
        clone._timeout = seconds
        return clone

    def _get_parent_spec(self) -> Any:
        return self._element

    def _resolve(self) -> Any:
        return self._element
