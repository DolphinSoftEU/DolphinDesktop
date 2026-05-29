"""Object Spy / Inspector for dolphin_desktop.

Headless API::

    import dolphin_desktop.spy as spy
    tree = spy.inspect(title="Notepad")          # returns dict
    tree = spy.inspect(title="Notepad", depth=3)
    print(spy.format_tree(tree["root"]))
    chain = spy.pick()                           # returns parent->leaf selectors

CLI (see _cli.py)::

    dolphin spy --window "Notepad"
    dolphin spy --class Notepad --depth 3
    dolphin spy --pick

JSON schema is versioned via ``schema_version`` key (currently 1).
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1

_PICK_POLL = 0.05  # seconds between mouse-position checks in pick mode

# ANSI colours (Windows Terminal + modern consoles)
_C_RESET = "\033[0m"
_C_BOLD = "\033[1m"
_C_DIM = "\033[2m"
_C_CYAN = "\033[36m"
_C_YELLOW = "\033[33m"
_C_GREEN = "\033[32m"
_C_MAGENTA = "\033[35m"


# ---------------------------------------------------------------------------
# Internal node model
# ---------------------------------------------------------------------------


@dataclass
class _NodeInfo:
    name: str
    control_type: str
    automation_id: str
    class_name: str
    bounding_box: dict[str, int]  # {x, y, width, height}
    visible: bool
    enabled: bool
    suggested_selector: dict[str, str]
    children: list[_NodeInfo] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Selector heuristics
# ---------------------------------------------------------------------------


def _suggest_selector(
    auto_id: str, name: str, class_name: str, control_type: str
) -> dict[str, str]:
    """Return best Locator criteria in preference order.

    Priority: automation_id > name+control_type > class_name > control_type.
    """
    if auto_id and auto_id not in ("", "0"):
        return {"auto_id": auto_id}
    if name:
        # MenuItem names include the keyboard shortcut after a tab, e.g. "Save\tCtrl+S".
        # Strip it so the generated title matches the visible label only.
        display_name = name.split("\t")[0] if control_type == "MenuItem" and "\t" in name else name
        sel: dict[str, str] = {"title": display_name}
        if control_type:
            sel["control_type"] = control_type
        return sel
    if class_name:
        return {"class_name": class_name}
    if control_type:
        return {"control_type": control_type}
    return {}


# ---------------------------------------------------------------------------
# Element info helpers
# ---------------------------------------------------------------------------


def _bbox_from_rect(rect: Any) -> dict[str, int]:
    try:
        return {
            "x": int(rect.left),
            "y": int(rect.top),
            "width": int(rect.right - rect.left),
            "height": int(rect.bottom - rect.top),
        }
    except Exception:
        return {"x": 0, "y": 0, "width": 0, "height": 0}


def _node_from_element_info(info: Any, depth: int | None, _level: int = 0) -> _NodeInfo:
    """Recursively build :class:`_NodeInfo` from a ``UIAElementInfo``."""
    try:
        name: str = info.name or ""
    except Exception:
        name = ""
    try:
        control_type: str = info.control_type or ""
    except Exception:
        control_type = ""
    try:
        auto_id: str = info.automation_id or ""
    except Exception:
        auto_id = ""
    try:
        class_name: str = info.class_name or ""
    except Exception:
        class_name = ""
    try:
        bbox = _bbox_from_rect(info.rectangle)
    except Exception:
        bbox = {"x": 0, "y": 0, "width": 0, "height": 0}
    try:
        visible = bool(info.visible)
    except Exception:
        visible = True
    try:
        enabled = bool(info.enabled)
    except Exception:
        enabled = True

    node = _NodeInfo(
        name=name,
        control_type=control_type,
        automation_id=auto_id,
        class_name=class_name,
        bounding_box=bbox,
        visible=visible,
        enabled=enabled,
        suggested_selector=_suggest_selector(auto_id, name, class_name, control_type),
    )

    if depth is None or _level < depth:
        try:
            for child_info in info.children():
                node.children.append(_node_from_element_info(child_info, depth, _level + 1))
        except Exception:
            pass

    return node


def _node_to_dict(node: _NodeInfo) -> dict[str, Any]:
    return {
        "name": node.name,
        "control_type": node.control_type,
        "automation_id": node.automation_id,
        "class_name": node.class_name,
        "bounding_box": node.bounding_box,
        "visible": node.visible,
        "enabled": node.enabled,
        "suggested_selector": node.suggested_selector,
        "children": [_node_to_dict(c) for c in node.children],
    }


# ---------------------------------------------------------------------------
# Element-from-point (for pick mode)
# ---------------------------------------------------------------------------


def _element_info_from_point(x: int, y: int) -> Any | None:
    """Return ``UIAElementInfo`` for the UIA element at screen coordinates."""
    # Try the classmethod introduced in recent pywinauto
    try:
        from pywinauto.uia_element_info import UIAElementInfo

        return UIAElementInfo.from_point(x, y)
    except (AttributeError, Exception):
        pass

    # Fallback: raw IUIAutomation COM call
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        from pywinauto.win32structures import POINT

        pt = POINT(x, y)
        com_elem = IUIA().iuia.ElementFromPoint(pt)
        if com_elem:
            return UIAElementInfo(com_elem)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Screen-DC highlight (XOR trick: drawing twice restores original pixels)
# ---------------------------------------------------------------------------

_HIGHLIGHT_COLOR = 0x0000FF00  # BGR → green
_HIGHLIGHT_PEN_WIDTH = 3


class _Highlighter:
    """Draws a 3px green XOR border on the screen DC; erases by redrawing."""

    def __init__(self) -> None:
        self._last: tuple[int, int, int, int] | None = None

    def _draw(self, left: int, top: int, right: int, bottom: int) -> None:
        try:
            import win32con
            import win32gui

            hdc = win32gui.GetDC(0)
            old_rop = win32gui.SetROP2(hdc, win32con.R2_XORPEN)
            pen = win32gui.CreatePen(win32con.PS_SOLID, _HIGHLIGHT_PEN_WIDTH, _HIGHLIGHT_COLOR)
            null_brush = win32gui.GetStockObject(win32con.NULL_BRUSH)
            old_pen = win32gui.SelectObject(hdc, pen)
            old_brush = win32gui.SelectObject(hdc, null_brush)
            win32gui.Rectangle(hdc, left, top, right, bottom)
            win32gui.SelectObject(hdc, old_pen)
            win32gui.SelectObject(hdc, old_brush)
            win32gui.DeleteObject(pen)
            win32gui.SetROP2(hdc, old_rop)
            win32gui.ReleaseDC(0, hdc)
        except Exception:
            pass

    def update(self, bbox: dict[str, int]) -> None:
        left = bbox["x"]
        top = bbox["y"]
        right = left + bbox["width"]
        bottom = top + bbox["height"]

        new = (left, top, right, bottom)
        if new == self._last:
            return

        # Erase previous by XOR-drawing same rect again
        if self._last is not None:
            self._draw(*self._last)

        self._draw(*new)
        self._last = new

    def clear(self) -> None:
        if self._last is not None:
            self._draw(*self._last)
            self._last = None


# ---------------------------------------------------------------------------
# Public API: inspect()
# ---------------------------------------------------------------------------


def _resolve_element_info(
    *,
    window: Any = None,
    app: Any = None,
    title: str | None = None,
    title_re: str | None = None,
    class_name: str | None = None,
    pid: int | None = None,
    backend: str = "uia",
) -> Any:
    """Return a ``UIAElementInfo`` for the requested window."""
    import pywinauto as _pw
    from pywinauto.uia_element_info import UIAElementInfo

    if window is not None:
        try:
            wrapper = window._spec.wrapper_object()
            return wrapper.element_info
        except Exception:
            try:
                return UIAElementInfo.from_hwnd(window._spec.handle)
            except Exception:
                pass

    if app is not None:
        win = app.top_window()
        try:
            wrapper = win._spec.wrapper_object()
            return wrapper.element_info
        except Exception:
            try:
                return UIAElementInfo.from_hwnd(win._spec.handle)
            except Exception:
                pass

    kw: dict[str, Any] = {}
    if title:
        kw["title"] = title
    if title_re:
        kw["title_re"] = title_re
    if class_name:
        kw["class_name"] = class_name
    if pid:
        kw["process"] = pid
    if not kw:
        raise ValueError("Provide window, app, title, title_re, class_name, or pid")

    wrapper = _pw.Desktop(backend=backend).window(**kw).wrapper_object()
    return wrapper.element_info


def inspect(
    *,
    window: Any = None,
    app: Any = None,
    title: str | None = None,
    title_re: str | None = None,
    class_name: str | None = None,
    pid: int | None = None,
    depth: int | None = None,
    backend: str = "uia",
) -> dict[str, Any]:
    """Return a versioned, JSON-serialisable UIA tree for a window.

    Parameters
    ----------
    window:
        A dolphin :class:`~dolphin_desktop._window.Window` instance.
    app:
        A dolphin :class:`~dolphin_desktop._application.Application` (uses ``top_window()``).
    title:
        Exact window title.
    title_re:
        Regex for window title.
    class_name:
        Win32 class name.
    pid:
        Process ID.
    depth:
        Maximum tree depth (``None`` = unlimited).
    backend:
        pywinauto backend — ``"uia"`` (default) or ``"win32"``.

    Returns
    -------
    dict
        ``{"schema_version": 1, "root": {...}}``

        Each node has keys:
        ``name``, ``control_type``, ``automation_id``, ``class_name``,
        ``bounding_box``, ``visible``, ``enabled``, ``suggested_selector``,
        ``children``.
    """
    info = _resolve_element_info(
        window=window,
        app=app,
        title=title,
        title_re=title_re,
        class_name=class_name,
        pid=pid,
        backend=backend,
    )
    root = _node_from_element_info(info, depth=depth)
    return {"schema_version": SCHEMA_VERSION, "root": _node_to_dict(root)}


# ---------------------------------------------------------------------------
# Public API: format_tree() — coloured terminal output
# ---------------------------------------------------------------------------


def _use_color(force: bool | None) -> bool:
    if force is True:
        return True
    if force is False:
        return False
    return sys.stdout.isatty()


def format_tree(
    node: dict[str, Any],
    *,
    indent: int = 0,
    color: bool | None = None,
    _lines: list[str] | None = None,
) -> str:
    """Render a node dict (from :func:`inspect`) as a human-readable tree string.

    Parameters
    ----------
    node:
        The ``"root"`` dict returned by :func:`inspect`, or any child node.
    indent:
        Starting indent level (0 = no indentation).
    color:
        ``True`` force ANSI colour, ``False`` plain, ``None`` auto-detect tty.
    """
    lines: list[str] = [] if _lines is None else _lines
    use_col = _use_color(color)

    prefix = "  " * indent
    ct = node.get("control_type") or "?"
    name = node.get("name") or ""
    auto_id = node.get("automation_id") or ""
    cls = node.get("class_name") or ""
    bbox = node.get("bounding_box") or {}
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)

    if use_col:
        parts = [f"{prefix}{_C_CYAN}{_C_BOLD}{ct}{_C_RESET}"]
        if name:
            parts.append(f'{_C_YELLOW}"{name}"{_C_RESET}')
        if auto_id:
            parts.append(f"{_C_GREEN}id={auto_id!r}{_C_RESET}")
        if cls:
            parts.append(f"{_C_DIM}[{cls}]{_C_RESET}")
        if w or h:
            parts.append(f"{_C_MAGENTA}({w}×{h}){_C_RESET}")  # noqa: RUF001
    else:
        parts = [f"{prefix}{ct}"]
        if name:
            parts.append(f'"{name}"')
        if auto_id:
            parts.append(f"id={auto_id!r}")
        if cls:
            parts.append(f"[{cls}]")
        if w or h:
            parts.append(f"({w}×{h})")  # noqa: RUF001

    lines.append(" ".join(parts))

    for child in node.get("children", []):
        format_tree(child, indent=indent + 1, color=color, _lines=lines)

    return "\n".join(lines) if _lines is None else ""


# ---------------------------------------------------------------------------
# Public API: pick() — interactive element picker
# ---------------------------------------------------------------------------


def _selector_to_code(sel: dict[str, Any]) -> str:
    """Format suggested_selector dict as a dolphin Locator call."""
    if not sel:
        return "locator()  # no selector found"
    parts: list[str] = []
    for k, v in sel.items():
        if isinstance(v, int) and not isinstance(v, bool):
            parts.append(f"{k}={v}")
        else:
            parts.append(f'{k}="{v}"')
    return f"locator({', '.join(parts)})"


def _chain_to_code(chain: list[dict[str, Any]]) -> str:
    """Format a parent→leaf chain of selectors as chained dolphin locator calls."""
    if not chain:
        return "locator()  # no selector found"
    return ".".join(_selector_to_code(s) for s in chain)


def _selector_for_info(info: Any) -> dict[str, str]:
    """Extract a suggested_selector dict from a single UIAElementInfo."""
    try:
        name = info.name or ""
    except Exception:
        name = ""
    try:
        control_type = info.control_type or ""
    except Exception:
        control_type = ""
    try:
        auto_id = info.automation_id or ""
    except Exception:
        auto_id = ""
    try:
        cls = info.class_name or ""
    except Exception:
        cls = ""
    return _suggest_selector(auto_id, name, cls, control_type)


def _is_meaningful_ancestor_selector(sel: dict[str, Any]) -> bool:
    # control_type alone (e.g. {"control_type": "Pane"}) is too broad to be a stable parent
    return any(k in sel for k in ("auto_id", "title", "class_name"))


def _build_parent_chain(info: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Walk up from *info* to the top-level Window and return ``(window_info, chain)``.

    The chain is ordered parent → leaf and excludes the top-level Window itself
    (callers use ``window.<chain>`` so the window is implicit).  Ancestors with
    no meaningful selector (anonymous Panes etc.) are skipped.  The leaf is
    always included even when its selector is empty.
    """
    chain: list[dict[str, Any]] = []
    window_info: Any = None
    current: Any = info
    is_leaf = True
    # Safety cap — UIA trees can be deep; 32 is well above realistic nesting.
    for _ in range(32):
        if current is None:
            break
        try:
            ctype = current.control_type or ""
        except Exception:
            ctype = ""
        try:
            parent = current.parent
        except Exception:
            parent = None

        # Stop at the top-level window — the caller already has it as `window`.
        if not is_leaf and (ctype == "Window" or parent is None):
            window_info = current
            break

        sel = _selector_for_info(current)
        if is_leaf or _is_meaningful_ancestor_selector(sel):
            chain.insert(0, sel)

        if parent is None:
            window_info = current
            break
        current = parent
        is_leaf = False

    return window_info, chain


def _info_rect(info: Any) -> tuple[int, int, int, int] | None:
    try:
        r = info.rectangle
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def _wrapper_rect(wrapper: Any) -> tuple[int, int, int, int] | None:
    try:
        r = wrapper.rectangle()
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def _resolve_chain_to_wrapper(window_info: Any, chain: list[dict[str, Any]], backend: str) -> Any:
    """Resolve the chain through live pywinauto; return wrapper or ``None``."""
    try:
        import pywinauto as _pw
    except Exception:
        return None

    try:
        win_cls = window_info.class_name or ""
    except Exception:
        win_cls = ""
    try:
        win_name = window_info.name or ""
    except Exception:
        win_name = ""

    kw: dict[str, Any] = {}
    if win_cls:
        kw["class_name"] = win_cls
    if win_name:
        kw["title"] = win_name
    if not kw:
        return None

    try:
        spec = _pw.Desktop(backend=backend).window(**kw)
        for sel in chain:
            spec = spec.child_window(**sel)
        spec.wait("exists", timeout=1.5)
        return spec.wrapper_object()
    except Exception:
        return None


def _chain_matches_picked(
    window_info: Any, chain: list[dict[str, Any]], picked: Any, backend: str
) -> bool:
    wrapper = _resolve_chain_to_wrapper(window_info, chain, backend)
    if wrapper is None:
        return False
    pr = _info_rect(picked)
    wr = _wrapper_rect(wrapper)
    if pr is None or wr is None:
        return False
    return pr == wr


def _index_among_siblings(info: Any) -> int | None:
    """Index of *info* among siblings sharing its control_type and class_name."""
    try:
        parent = info.parent
    except Exception:
        return None
    if parent is None:
        return None
    target_rect = _info_rect(info)
    if target_rect is None:
        return None
    try:
        ct = info.control_type or ""
    except Exception:
        ct = ""
    try:
        cls = info.class_name or ""
    except Exception:
        cls = ""
    try:
        children = parent.children()
    except Exception:
        return None
    idx = 0
    for child in children:
        try:
            c_ct = child.control_type or ""
            c_cls = child.class_name or ""
        except Exception:
            continue
        if c_ct != ct or c_cls != cls:
            continue
        if _info_rect(child) == target_rect:
            return idx
        idx += 1
    return None


def _find_info_by_selector(root_info: Any, sel: dict[str, Any]) -> Any | None:
    """Find first UIAElementInfo descendant matching *sel* via TreeWalker traversal.

    IUIAutomation::FindAll (used by pywinauto child_window/descendants) misses
    some elements that TreeWalker's GetFirstChild/GetNextSibling can reach,
    e.g. button children of ToolbarWindow32 in Notepad++.
    """
    title = sel.get("title", "")
    ct = sel.get("control_type", "")

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
                return child
            result = _search(child, depth - 1)
            if result is not None:
                return result
        return None

    return _search(root_info, 8)


def _validate_and_repair(
    window_info: Any,
    chain: list[dict[str, Any]],
    picked: Any,
    backend: str,
) -> tuple[list[dict[str, Any]], str]:
    """Return ``(chain, status)`` where ``status`` is one of:

    - ``"ok"`` — original chain resolves to the picked element
    - ``"repaired"`` — leaf rewritten to ``found_index`` to make it match
    - ``"repaired_flat"`` — chain reduced to leaf-only (flat search from root worked)
    - ``"unverified"`` — could not validate (no window root or pywinauto miss)
    - ``"unreachable"`` — chain and all fallbacks failed to match
    """
    if not chain or window_info is None:
        return chain, "unverified"

    if _chain_matches_picked(window_info, chain, picked, backend):
        return chain, "ok"

    # Try found_index repair: replace leaf selector with positional index.
    idx = _index_among_siblings(picked)
    if idx is not None:
        leaf = chain[-1]
        new_leaf: dict[str, Any] = {}
        if leaf.get("class_name"):
            new_leaf["class_name"] = leaf["class_name"]
        if leaf.get("control_type"):
            new_leaf["control_type"] = leaf["control_type"]
        new_leaf["found_index"] = idx
        new_chain = [*chain[:-1], new_leaf]
        if _chain_matches_picked(window_info, new_chain, picked, backend):
            return new_chain, "repaired"

    # Last resort: flat search from the window root using only the leaf selector.
    # Elements inside legacy Win32 controls (ToolbarWindow32, etc.) are reachable
    # via window.descendants() but not through a chained child_window() path,
    # because pywinauto can't traverse into their UIA children when the control
    # is used as an intermediate search root.
    leaf_sel = chain[-1]
    if len(chain) > 1 and (leaf_sel.get("title") or leaf_sel.get("auto_id")):
        flat_chain = [leaf_sel]
        if _chain_matches_picked(window_info, flat_chain, picked, backend):
            return flat_chain, "repaired_flat"
        # FindAll-based verification failed; try TreeWalker traversal.
        # Toolbar buttons (ToolbarWindow32) are reachable via TreeWalker but
        # not via IUIAutomation::FindAll, so child_window() misses them.
        found = _find_info_by_selector(window_info, leaf_sel)
        if found is not None and _info_rect(found) == _info_rect(picked):
            return flat_chain, "repaired_flat"
        # Neither FindAll nor TreeWalker could verify the locator.  Return the
        # flat (simplified) chain anyway — it's less fragile than the full
        # ancestor chain and dolphin's _resolve() TreeWalker fallback may still
        # reach the element at runtime.
        return flat_chain, "unreachable"

    return chain, "unreachable"


def pick(backend: str = "uia") -> list[dict[str, Any]]:
    """Interactive element picker.

    Move the cursor over the target element, then press **Ctrl+Click** to
    capture it.  The element is highlighted with a green border while
    hovering.  Press **Esc** to cancel.

    Parameters
    ----------
    backend:
        pywinauto backend — ``"uia"`` (default) or ``"win32"``.

    Returns
    -------
    list[dict]
        Chain of ``suggested_selector`` dicts ordered from outermost ancestor
        down to the picked element, suitable for chained ``locator()`` calls.
        Returns ``[]`` if cancelled.
    """
    try:
        import win32api
        import win32con
    except ImportError as exc:
        raise RuntimeError(
            "pick() requires pywin32 (win32api). Install: pip install pywin32"
        ) from exc

    print("Pick mode — move cursor over an element.")
    print("  Ctrl+Click  → select")
    print("  Esc         → cancel")
    print()

    highlighter = _Highlighter()
    current_info: Any = None
    prev_lbutton = False

    try:
        while True:
            x, y = win32api.GetCursorPos()

            if win32api.GetAsyncKeyState(win32con.VK_ESCAPE) & 0x8001:
                print("Cancelled.")
                return []

            # Update highlight
            try:
                info = _element_info_from_point(x, y)
                if info is not None:
                    current_info = info
                    try:
                        bbox = _bbox_from_rect(info.rectangle)
                        if bbox["width"] > 0 and bbox["height"] > 0:
                            highlighter.update(bbox)
                    except Exception:
                        pass
            except Exception:
                pass

            # Detect Ctrl+Click (rising edge: button was up, now down)
            lbutton_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
            ctrl_down = bool(win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000)

            if lbutton_down and not prev_lbutton and ctrl_down and current_info is not None:
                break

            prev_lbutton = lbutton_down
            time.sleep(_PICK_POLL)

    finally:
        highlighter.clear()

    if current_info is None:
        return []

    window_info, chain = _build_parent_chain(current_info)
    chain, status = _validate_and_repair(window_info, chain, current_info, backend)
    if status == "repaired":
        print(
            "  note: leaf rewritten to found_index — original title/name "
            "did not match via pywinauto"
        )
    elif status == "repaired_flat":
        print(
            "  note: ancestor chain simplified — direct search from window root "
            "succeeded (intermediate class_name steps were unreliable)"
        )
    elif status == "unreachable":
        print(
            "  warning: chain does NOT resolve to the picked element. "
            "Consider ImageLocator or a keyboard shortcut for this control."
        )
    elif status == "unverified":
        print("  note: chain not verified (no window root or pywinauto unavailable)")
    return chain


# ---------------------------------------------------------------------------
# Public API: image_pick() — interactive template PNG capture
# ---------------------------------------------------------------------------


def image_pick(
    output_dir: str | Any = ".",
    backend: str = "uia",
) -> str | None:
    """Interactive element picker that saves a template PNG.

    Move the cursor over the target element, then press **Ctrl+Click** to
    capture its bounding box as a PNG template for use with
    ``window.image()``.  Press **Esc** to cancel.

    Parameters
    ----------
    output_dir:
        Directory where the template PNG is saved (default: current directory).
    backend:
        pywinauto backend used to resolve the element under the cursor.

    Returns
    -------
    str | None
        Absolute path to the saved PNG, or ``None`` if cancelled.
    """
    import hashlib
    from pathlib import Path as _Path

    from PIL import ImageGrab

    try:
        import win32api
        import win32con
    except ImportError as exc:
        raise RuntimeError("image_pick() requires pywin32. Install: pip install pywin32") from exc

    print("Image pick mode — move cursor over an element.")
    print("  Ctrl+Click  → capture template PNG")
    print("  Esc         → cancel")
    print()

    highlighter = _Highlighter()
    current_info: Any = None
    current_bbox: dict | None = None
    prev_lbutton = False

    try:
        while True:
            x, y = win32api.GetCursorPos()

            if win32api.GetAsyncKeyState(win32con.VK_ESCAPE) & 0x8001:
                print("Cancelled.")
                return None

            try:
                info = _element_info_from_point(x, y)
                if info is not None:
                    current_info = info
                    try:
                        bbox = _bbox_from_rect(info.rectangle)
                        if bbox["width"] > 0 and bbox["height"] > 0:
                            highlighter.update(bbox)
                            current_bbox = bbox
                    except Exception:
                        pass
            except Exception:
                pass

            lbutton_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
            ctrl_down = bool(win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000)

            if lbutton_down and not prev_lbutton and ctrl_down and current_bbox is not None:
                break

            prev_lbutton = lbutton_down
            time.sleep(_PICK_POLL)
    finally:
        highlighter.clear()

    if current_bbox is None:
        return None

    region = (
        current_bbox["x"],
        current_bbox["y"],
        current_bbox["x"] + current_bbox["width"],
        current_bbox["y"] + current_bbox["height"],
    )
    img = ImageGrab.grab(bbox=region)

    out = _Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    try:
        elem_name = (current_info.name or "").replace(" ", "_")[:30] or "element"
        # strip chars that are invalid in filenames
        elem_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in elem_name)
    except Exception:
        elem_name = "element"

    suffix = hashlib.md5(str(current_bbox).encode()).hexdigest()[:6]
    filename = f"template_{elem_name}_{suffix}.png"
    full_path = out / filename
    img.save(str(full_path))

    abs_path = str(full_path.resolve())
    print(f"\nTemplate saved: {abs_path}")
    print(f'\nUse it with:   window.image("{abs_path}").click()')
    return abs_path
