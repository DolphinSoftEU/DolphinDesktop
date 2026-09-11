"""Object Spy / Inspector for dolphin_desktop.

Headless API::

    import dolphin_desktop.spy as spy
    tree = spy.inspect(title="Notepad")          # returns dict
    tree = spy.inspect(title="Notepad", depth=3)
    print(spy.format_tree(tree["root"]))
    picked = spy.pick()                          # {"status": ..., "chain": [...]}

CLI (see _cli.py)::

    dolphin spy --window "Notepad"
    dolphin spy --class Notepad --depth 3
    dolphin spy --pick

JSON schema is versioned via ``schema_version`` key (currently 1).
"""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1

_PICK_POLL = 0.05  # seconds between mouse-position checks in pick mode

# Default tree-walk budget.  An unbounded walk of a Chromium / Electron accessibility
# tree is minutes of COM calls followed by a RecursionError, so the default is capped
# and callers that really want the whole tree opt in with ``depth=None``.
_DEFAULT_DEPTH = 12
_DEFAULT_MAX_CHILDREN = 200

# Seconds between full re-scans of the SAP component rectangles in sap_pick()
_SAP_INDEX_REFRESH = 1.5

# SAP GUI Scripting component IDs are fully qualified, e.g.
# ``/app/con[0]/ses[0]/wnd[0]/usr/txtRSYST-BNAME``.  Dolphin's
# ``session.find_by_id`` works relative to a session, so the
# ``/app/con[N]/ses[M]/`` prefix is stripped from generated locators.
_SAP_SESSION_PREFIX = re.compile(r"^/app/con\[\d+\]/ses\[\d+\]/")

# ANSI colours (Windows Terminal + modern consoles)
_C_RESET = "\033[0m"
_C_BOLD = "\033[1m"
_C_DIM = "\033[2m"
_C_CYAN = "\033[36m"
_C_YELLOW = "\033[33m"
_C_GREEN = "\033[32m"
_C_MAGENTA = "\033[35m"


# Internal node model


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


# Selector heuristics


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


# Element info helpers


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


def _node_from_element_info(
    info: Any,
    depth: int | None,
    max_children: int | None = _DEFAULT_MAX_CHILDREN,
    _level: int = 0,
) -> _NodeInfo:
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
            children = list(info.children())
        except Exception:
            children = []
        if max_children is not None:
            children = children[:max_children]
        for child_info in children:
            node.children.append(
                _node_from_element_info(child_info, depth, max_children, _level + 1)
            )

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


# Element-from-point (for pick mode)


def _element_info_from_point(x: int, y: int) -> Any | None:
    """Return ``UIAElementInfo`` for the UIA element at screen coordinates."""
    # Try the classmethod introduced in recent pywinauto
    try:
        from pywinauto.uia_element_info import UIAElementInfo

        return UIAElementInfo.from_point(x, y)
    except Exception:
        # Older pywinauto has no from_point classmethod (AttributeError); newer
        # versions can still fail the underlying COM call.  Both fall through.
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


def _element_owner_is_alive(info: Any) -> bool:
    """Return whether the top-level window behind a picked element still exists."""
    owner = info
    visited: set[int] = set()
    for _ in range(64):
        marker = id(owner)
        if marker in visited:
            break
        visited.add(marker)
        try:
            parent = owner.parent
        except Exception:
            return False
        if parent is None:
            break
        owner = parent

    try:
        handle = int(owner.handle)
    except Exception:
        handle = 0
    if handle:
        try:
            import win32gui

            return bool(win32gui.IsWindow(handle))
        except Exception:
            # A diagnostic failure must not cancel a live picker.
            return True

    try:
        _ = owner.rectangle
    except Exception:
        return False
    return True


# Screen highlight — four click-through overlay windows forming a border.
#
# Overlay windows rather than drawing on the screen DC: a screen-DC scribble
# lands outside DWM's composition, belongs to no window surface, and so is
# never restored — any repaint underneath smears it and fragments stay on
# screen. Destroying an overlay is the erase. WS_EX_TRANSPARENT keeps the
# strips out of hit-testing so element_from_point under the cursor never
# lands on the border; WS_EX_NOACTIVATE leaves focus with the inspected app.

_HIGHLIGHT_COLOR = 0x0000FF00  # BGR → green
_HIGHLIGHT_PEN_WIDTH = 3
_HIGHLIGHT_WND_CLASS = "DolphinSpyHighlight"
_HIGHLIGHT_CLOSE_MESSAGE = 0x8001


class _Highlighter:
    """Green click-through border built from four overlay strips."""

    def __init__(self) -> None:
        self._last: tuple[int, int, int, int] | None = None
        self._strips: list[int] = []
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    # The strips live on their own thread running a message loop: a window
    # is only painted once its owning thread services WM_PAINT, and the
    # picker loop never pumps a queue of its own.
    def _strip_thread(self) -> None:
        try:
            import win32api
            import win32con
            import win32gui

            brush = win32gui.CreateSolidBrush(_HIGHLIGHT_COLOR)

            def _on_paint(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
                # Painting explicitly rather than relying on the class
                # background brush: with the brush alone the strips stayed
                # blank on Windows 11.
                hdc, paint_struct = win32gui.BeginPaint(hwnd)
                win32gui.FillRect(hdc, win32gui.GetClientRect(hwnd), brush)
                win32gui.EndPaint(hwnd, paint_struct)
                return 0

            def _on_close(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
                win32gui.DestroyWindow(hwnd)
                return 0

            wc = win32gui.WNDCLASS()
            wc.lpszClassName = _HIGHLIGHT_WND_CLASS
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.hbrBackground = brush
            wc.lpfnWndProc = {
                win32con.WM_PAINT: _on_paint,
                win32con.WM_DESTROY: lambda *args: 0,
                _HIGHLIGHT_CLOSE_MESSAGE: _on_close,
            }
            try:
                win32gui.RegisterClass(wc)
            except win32gui.error:
                pass  # already registered by an earlier picker run

            ex_style = (
                win32con.WS_EX_TRANSPARENT
                | win32con.WS_EX_TOPMOST
                | win32con.WS_EX_TOOLWINDOW
                | win32con.WS_EX_NOACTIVATE
            )
            for _ in range(4):
                hwnd = win32gui.CreateWindowEx(
                    ex_style,
                    _HIGHLIGHT_WND_CLASS,
                    None,
                    win32con.WS_POPUP,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    wc.hInstance,
                    None,
                )
                self._strips.append(hwnd)
        except Exception:
            self._strips = []
        finally:
            self._ready.set()

        if not self._strips:
            return
        try:
            import win32gui

            win32gui.PumpMessages()
        except Exception:
            pass

    def _ensure_strips(self) -> bool:
        if self._strips:
            return True
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._strip_thread, name="dolphin-spy-highlight", daemon=True
            )
            self._thread.start()
            self._ready.wait(timeout=3.0)
        return bool(self._strips)

    def update(self, bbox: dict[str, int]) -> None:
        left = bbox["x"]
        top = bbox["y"]
        right = left + bbox["width"]
        bottom = top + bbox["height"]

        new = (left, top, right, bottom)
        if new == self._last:
            return
        if not self._ensure_strips():
            return

        try:
            import win32con
            import win32gui

            width = _HIGHLIGHT_PEN_WIDTH
            strips = (
                (left, top, right - left, width),  # top edge
                (left, max(bottom - width, top), right - left, width),  # bottom edge
                (left, top, width, bottom - top),  # left edge
                (max(right - width, left), top, width, bottom - top),  # right edge
            )
            flags = win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
            for hwnd, (x, y, cx, cy) in zip(self._strips, strips, strict=True):
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, cx, cy, flags)
                win32gui.InvalidateRect(hwnd, None, True)
            self._last = new
        except Exception:
            pass

    def clear(self) -> None:
        if self._last is None and not self._strips:
            return
        thread = self._thread
        try:
            import win32api
            import win32con
            import win32gui

            for hwnd in self._strips:
                win32gui.PostMessage(hwnd, _HIGHLIGHT_CLOSE_MESSAGE, 0, 0)
            if thread is not None:
                thread_id = getattr(thread, "native_id", None) or thread.ident
                if thread_id is not None:
                    win32api.PostThreadMessage(thread_id, win32con.WM_QUIT, 0, 0)
                thread.join(timeout=3.0)
        except Exception:
            try:
                import win32con
                import win32gui

                for hwnd in self._strips:
                    win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            except Exception:
                pass
        finally:
            self._last = None
            self._strips = []
            self._thread = None
            self._ready = threading.Event()


# Public API: inspect()


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
    depth: int | None = _DEFAULT_DEPTH,
    max_children: int | None = _DEFAULT_MAX_CHILDREN,
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
        Maximum tree depth (default 12; ``None`` = unlimited).
    max_children:
        Maximum children walked per node (default 200; ``None`` = unlimited).
    backend:
        pywinauto backend — ``"uia"`` (default) or ``"win32"``.

    Returns
    -------
    dict
        ``{"schema_version": 1, "limits": {...}, "root": {...}}``

        ``limits`` reports the walk budget actually applied, so a caller can tell a
        complete tree from one the caps cut short.

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
    root = _node_from_element_info(info, depth=depth, max_children=max_children)
    return {
        "schema_version": SCHEMA_VERSION,
        "limits": {"depth": depth, "max_children": max_children},
        "root": _node_to_dict(root),
    }


# Public API: format_tree() — coloured terminal output


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
            parts.append(f"{_C_MAGENTA}({w}×{h}){_C_RESET}")
    else:
        parts = [f"{prefix}{ct}"]
        if name:
            parts.append(f'"{name}"')
        if auto_id:
            parts.append(f"id={auto_id!r}")
        if cls:
            parts.append(f"[{cls}]")
        if w or h:
            parts.append(f"({w}×{h})")

    lines.append(" ".join(parts))

    for child in node.get("children", []):
        format_tree(child, indent=indent + 1, color=color, _lines=lines)

    return "\n".join(lines) if _lines is None else ""


# Public API: pick() — interactive element picker


def _selector_to_code(sel: dict[str, Any]) -> str:
    """Format suggested_selector dict as a dolphin Locator call.

    Values go through ``repr`` — element names routinely contain backslashes
    (``C:\\Users\\...``) and quotes, which hand-quoting turns into source that
    does not parse.
    """
    if not sel:
        return "locator()  # no selector found"
    parts: list[str] = []
    for k, v in sel.items():
        if isinstance(v, int) and not isinstance(v, bool):
            parts.append(f"{k}={v}")
        else:
            parts.append(f"{k}={v!r}")
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


# Human-readable note per pick status.  Kept out of pick() itself so the result stays
# machine-readable for a front-end that never sees this process's stdout.
_PICK_STATUS_NOTES: dict[str, str] = {
    "ok": "",
    "cancelled": "",
    "repaired": (
        "note: leaf rewritten to found_index — original title/name did not match via pywinauto"
    ),
    "repaired_flat": (
        "note: ancestor chain simplified — direct search from window root succeeded "
        "(intermediate class_name steps were unreliable)"
    ),
    "unverified": "note: chain not verified (no window root or pywinauto unavailable)",
    "unreachable": (
        "warning: chain does NOT resolve to the picked element. Consider ImageLocator "
        "or a keyboard shortcut for this control."
    ),
}


def _pick_result(status: str, chain: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "chain": chain,
        "message": _PICK_STATUS_NOTES.get(status, ""),
    }


def pick(backend: str = "uia") -> dict[str, Any]:
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
    dict
        ``{"schema_version": 1, "status": ..., "chain": [...], "message": ...}``

        ``chain`` is the list of ``suggested_selector`` dicts ordered from
        outermost ancestor down to the picked element, suitable for chained
        ``locator()`` calls; it is empty when nothing was picked.  ``status`` is
        one of ``"ok"``, ``"repaired"``, ``"repaired_flat"``, ``"unverified"``,
        ``"unreachable"`` or ``"cancelled"`` — anything but ``"ok"`` means the
        chain was not verified to resolve back to the element that was picked.
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
                return _pick_result("cancelled", [])

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
                elif current_info is not None and not _element_owner_is_alive(current_info):
                    print("Cancelled.")
                    return _pick_result("cancelled", [])
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
        return _pick_result("cancelled", [])

    window_info, chain = _build_parent_chain(current_info)
    chain, status = _validate_and_repair(window_info, chain, current_info, backend)
    return _pick_result(status, chain)


# Public API: image_pick() — interactive template PNG capture


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
    # all_screens: without it the grab is clipped to the primary monitor, so an
    # element picked on a secondary display captures the wrong pixels.
    img = ImageGrab.grab(bbox=region, all_screens=True)

    out = _Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    try:
        elem_name = (current_info.name or "").replace(" ", "_")[:30] or "element"
        # strip chars that are invalid in filenames
        elem_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in elem_name)
    except Exception:
        elem_name = "element"

    # usedforsecurity=False: this is a filename suffix, and md5 is unavailable on a
    # FIPS-mode host without it.
    suffix = hashlib.md5(str(current_bbox).encode(), usedforsecurity=False).hexdigest()[:6]
    filename = f"template_{elem_name}_{suffix}.png"
    full_path = out / filename
    img.save(str(full_path))

    abs_path = str(full_path.resolve())
    print(f"\nTemplate saved: {abs_path}")
    print(f'\nUse it with:   window.image("{abs_path}").click()')
    return abs_path


# ---------------------------------------------------------------------------
# SAP GUI Scripting support
#
# SAP GUI for Windows is not addressable through the UIA/Win32 tree the rest of
# the spy walks — the generated ``auto_id``/``title`` selectors do not map to
# anything Dolphin's SAP API can resolve.  SAP GUI Scripting instead exposes
# stable component IDs (``wnd[0]/usr/...``) plus per-control screen rectangles
# over COM, so the SAP spy drives that engine directly and emits
# ``session.find_by_id(...)`` locators.
# ---------------------------------------------------------------------------


@dataclass
class _SapNodeInfo:
    id: str  # session-relative id, e.g. wnd[0]/usr/txtFOO
    full_id: str  # absolute id, e.g. /app/con[0]/ses[0]/wnd[0]/usr/txtFOO
    name: str
    type: str
    text: str
    bounding_box: dict[str, int]  # {x, y, width, height}
    changeable: bool
    suggested_selector: dict[str, str]
    children: list[_SapNodeInfo] = field(default_factory=list)


def _sap_relative_id(full_id: str) -> str:
    """Strip the ``/app/con[N]/ses[M]/`` prefix from a SAP component ID."""
    return _SAP_SESSION_PREFIX.sub("", full_id or "")


def _sap_str(component: Any, names: tuple[str, ...]) -> str:
    from ._sap import _get_first

    value = _get_first(component, names)
    return "" if value is None else str(value)


def _sap_bbox(component: Any) -> dict[str, int]:
    """Return the screen rectangle of a SAP component, or a zero box."""
    from ._sap import _get_first

    left = _get_first(component, ("ScreenLeft", "screenLeft"))
    top = _get_first(component, ("ScreenTop", "screenTop"))
    width = _get_first(component, ("Width", "width"))
    height = _get_first(component, ("Height", "height"))
    try:
        return {
            "x": int(left),
            "y": int(top),
            "width": int(width),
            "height": int(height),
        }
    except (TypeError, ValueError):
        return {"x": 0, "y": 0, "width": 0, "height": 0}


def _sap_suggest_selector(rel_id: str, name: str, sap_type: str) -> dict[str, str]:
    """Return the best SAP locator criteria in preference order.

    The session-relative component ID is by far the most stable selector, so it
    wins whenever present.  Name + type is the fallback for the rare component
    that exposes no usable ID.
    """
    if rel_id:
        return {"id": rel_id}
    sel: dict[str, str] = {}
    if name:
        sel["name"] = name
    if sap_type:
        sel["type"] = sap_type
    return sel


def _sap_selector_to_code(sel: dict[str, Any]) -> str:
    """Format a SAP suggested_selector dict as a Dolphin session call."""
    if not sel:
        return "find_by_id()  # no SAP id found"
    if "id" in sel:
        return f"find_by_id({sel['id']!r})"
    parts: list[str] = []
    if "name" in sel:
        parts.append(f"name={sel['name']!r}")
    if "type" in sel:
        parts.append(f"type={sel['type']!r}")
    return f"locator({', '.join(parts)})"


def _sap_node_from_component(
    component: Any,
    depth: int | None,
    max_children: int | None = _DEFAULT_MAX_CHILDREN,
    _level: int = 0,
) -> _SapNodeInfo:
    """Recursively build a :class:`_SapNodeInfo` from a SAP GUI component."""
    from ._sap import _bool_attr, _iter_children

    full_id = _sap_str(component, ("Id", "id"))
    rel_id = _sap_relative_id(full_id)
    name = _sap_str(component, ("Name", "name"))
    sap_type = _sap_str(component, ("Type", "type"))
    text = _sap_str(component, ("Text", "text"))

    node = _SapNodeInfo(
        id=rel_id,
        full_id=full_id,
        name=name,
        type=sap_type,
        text=text,
        bounding_box=_sap_bbox(component),
        changeable=_bool_attr(component, ("Changeable", "changeable"), True),
        suggested_selector=_sap_suggest_selector(rel_id, name, sap_type),
    )

    if depth is None or _level < depth:
        for index, child in enumerate(_iter_children(component)):
            if max_children is not None and index >= max_children:
                break
            node.children.append(_sap_node_from_component(child, depth, max_children, _level + 1))

    return node


def _sap_node_to_dict(node: _SapNodeInfo) -> dict[str, Any]:
    return {
        "id": node.id,
        "full_id": node.full_id,
        "name": node.name,
        "type": node.type,
        "text": node.text,
        "bounding_box": node.bounding_box,
        "changeable": node.changeable,
        "suggested_selector": node.suggested_selector,
        "children": [_sap_node_to_dict(c) for c in node.children],
    }


def sap_inspect(
    *,
    connection: int = 0,
    session: int = 0,
    depth: int | None = _DEFAULT_DEPTH,
    max_children: int | None = _DEFAULT_MAX_CHILDREN,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Return a versioned, JSON-serialisable SAP component tree.

    Connects to the running SAP GUI Scripting engine and walks the component
    tree of the selected session.  Each node carries the session-relative
    component ``id`` (the locator Dolphin uses), the absolute ``full_id``,
    ``name``, ``type``, ``text``, screen ``bounding_box``, ``changeable`` flag,
    and a ``suggested_selector`` ready for :func:`_sap_selector_to_code`.

    Parameters
    ----------
    connection:
        Zero-based SAP connection index.
    session:
        Zero-based session index within the connection.
    depth:
        Maximum tree depth (default 12; ``None`` = unlimited).
    max_children:
        Maximum children walked per node (default 200; ``None`` = unlimited).
    timeout:
        Seconds to wait for the SAP GUI Scripting engine (``None`` = config default).

    Returns
    -------
    dict
        ``{"schema_version": 1, "limits": {...}, "root": {...}}``
    """
    from ._sap import SapGui

    sap = SapGui.connect(timeout=timeout)
    sess = sap.session(connection=connection, session=session)
    root = _sap_node_from_component(sess.raw, depth=depth, max_children=max_children)
    return {
        "schema_version": SCHEMA_VERSION,
        "limits": {"depth": depth, "max_children": max_children},
        "root": _sap_node_to_dict(root),
    }


def format_sap_tree(
    node: dict[str, Any],
    *,
    indent: int = 0,
    color: bool | None = None,
    _lines: list[str] | None = None,
) -> str:
    """Render a SAP node dict (from :func:`sap_inspect`) as a tree string."""
    lines: list[str] = [] if _lines is None else _lines
    use_col = _use_color(color)

    prefix = "  " * indent
    sap_type = node.get("type") or "?"
    rid = node.get("id") or ""
    name = node.get("name") or ""
    text = node.get("text") or ""
    bbox = node.get("bounding_box") or {}
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)

    if use_col:
        parts = [f"{prefix}{_C_CYAN}{_C_BOLD}{sap_type}{_C_RESET}"]
        if rid:
            parts.append(f"{_C_GREEN}{rid}{_C_RESET}")
        if text:
            parts.append(f'{_C_YELLOW}"{text}"{_C_RESET}')
        if name and name != rid:
            parts.append(f"{_C_DIM}[{name}]{_C_RESET}")
        if w or h:
            parts.append(f"{_C_MAGENTA}({w}×{h}){_C_RESET}")
    else:
        parts = [f"{prefix}{sap_type}"]
        if rid:
            parts.append(rid)
        if text:
            parts.append(f'"{text}"')
        if name and name != rid:
            parts.append(f"[{name}]")
        if w or h:
            parts.append(f"({w}×{h})")

    lines.append(" ".join(parts))

    for child in node.get("children", []):
        format_sap_tree(child, indent=indent + 1, color=color, _lines=lines)

    return "\n".join(lines) if _lines is None else ""


def _bbox_contains(bbox: dict[str, int], x: int, y: int) -> bool:
    return (
        bbox["x"] <= x < bbox["x"] + bbox["width"] and bbox["y"] <= y < bbox["y"] + bbox["height"]
    )


def _sap_sessions(engine: Any) -> list[Any]:
    """Return every live SAP session COM object across all connections."""
    from ._sap import _iter_children

    sessions: list[Any] = []
    for connection in _iter_children(engine):
        sessions.extend(_iter_children(connection))
    return sessions


def _sap_iter_components(root: Any) -> Any:
    """Yield every descendant component of *root* (depth-first)."""
    from ._sap import _iter_children

    stack = list(_iter_children(root))
    while stack:
        component = stack.pop()
        yield component
        stack.extend(_iter_children(component))


class _SapRectIndex:
    """Screen rectangles of every SAP component, rebuilt on a slow timer.

    One full walk costs four cross-process COM property reads per component, so
    rebuilding it on every pick poll tick is tens of thousands of COM calls per
    second and visibly stalls SAP GUI itself.  Between rebuilds a hit test is
    pure Python over the cached rectangles.
    """

    def __init__(self, sessions: list[Any], refresh: float = _SAP_INDEX_REFRESH) -> None:
        self._sessions = sessions
        self._refresh = refresh
        # (bbox, component), sorted by area so the first containing hit is the
        # smallest — the most specific control, as UIA element-from-point does.
        self._entries: list[tuple[dict[str, int], Any]] = []
        self._built_at: float | None = None

    def refresh(self) -> None:
        entries: list[tuple[int, dict[str, int], Any]] = []
        for session in self._sessions:
            for component in _sap_iter_components(session):
                bbox = _sap_bbox(component)
                if bbox["width"] <= 0 or bbox["height"] <= 0:
                    continue
                entries.append((bbox["width"] * bbox["height"], bbox, component))
        entries.sort(key=lambda entry: entry[0])
        self._entries = [(bbox, component) for _area, bbox, component in entries]
        self._built_at = time.monotonic()

    def component_at(self, x: int, y: int) -> Any | None:
        """Return the smallest component whose *current* rect contains (x, y).

        An indexed hit is confirmed against the component's live rectangle: the index
        is up to *refresh* seconds old, and a control that scrolled or moved since the
        walk would otherwise be reported — and highlighted — away from the cursor.
        """
        if self._built_at is None or time.monotonic() - self._built_at >= self._refresh:
            self.refresh()
        for bbox, component in self._entries:
            if not _bbox_contains(bbox, x, y):
                continue
            if _bbox_contains(_sap_bbox(component), x, y):
                return component
        return None


def _sap_pick_result(status: str, selector: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "selector": selector,
        "message": _SAP_PICK_STATUS_NOTES.get(status, ""),
    }


_SAP_PICK_STATUS_NOTES: dict[str, str] = {
    "ok": "",
    "cancelled": "",
    "no_session": ("No SAP sessions found. Ensure SAP GUI is running with a session open."),
}


def sap_pick(*, timeout: float | None = None) -> dict[str, Any]:
    """Interactive SAP element picker.

    Move the cursor over a SAP control, then press **Ctrl+Click** to capture
    it.  The control is highlighted with a green border while hovering.  Press
    **Esc** to cancel.

    Returns
    -------
    dict
        ``{"schema_version": 1, "status": ..., "selector": {...}, "message": ...}``

        ``status`` is ``"ok"``, ``"cancelled"`` or ``"no_session"``.  ``selector``
        is a ``suggested_selector`` dict (``{"id": "wnd[0]/usr/..."}`` for the
        common case), suitable for :func:`_sap_selector_to_code`; it is empty
        for any status other than ``"ok"``.
    """
    try:
        import win32api
        import win32con
    except ImportError as exc:
        raise RuntimeError(
            "sap_pick() requires pywin32 (win32api). Install: pip install pywin32"
        ) from exc

    from ._sap import SapGui

    engine = SapGui.connect(timeout=timeout).raw
    sessions = _sap_sessions(engine)
    if not sessions:
        print(_SAP_PICK_STATUS_NOTES["no_session"])
        return _sap_pick_result("no_session", {})

    print("SAP pick mode — move cursor over a SAP control.")
    print("  Ctrl+Click  → select")
    print("  Esc         → cancel")
    print()

    highlighter = _Highlighter()
    index = _SapRectIndex(sessions)
    current: Any = None
    prev_lbutton = False

    try:
        while True:
            x, y = win32api.GetCursorPos()

            if win32api.GetAsyncKeyState(win32con.VK_ESCAPE) & 0x8001:
                print("Cancelled.")
                return _sap_pick_result("cancelled", {})

            try:
                component = index.component_at(x, y)
            except Exception:
                component = None
            # Reset on a miss: keeping the previous hit would make Ctrl+Click over
            # empty space capture whatever was hovered last.
            if component is None:
                highlighter.clear()
            else:
                bbox = _sap_bbox(component)
                if bbox["width"] > 0 and bbox["height"] > 0:
                    highlighter.update(bbox)
            current = component

            lbutton_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
            ctrl_down = bool(win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000)

            if lbutton_down and not prev_lbutton and ctrl_down and current is not None:
                break

            prev_lbutton = lbutton_down
            time.sleep(_PICK_POLL)
    finally:
        highlighter.clear()

    if current is None:
        return _sap_pick_result("cancelled", {})

    full_id = _sap_str(current, ("Id", "id"))
    selector = _sap_suggest_selector(
        _sap_relative_id(full_id),
        _sap_str(current, ("Name", "name")),
        _sap_str(current, ("Type", "type")),
    )
    return _sap_pick_result("ok", selector)
