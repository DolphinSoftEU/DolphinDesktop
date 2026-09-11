"""Tests for the implementation behind ``dolphin.spy``.

The spy normally talks to UIAutomation, pywin32 and SAP GUI over COM.  These
tests deliberately replace those boundaries with tiny fakes so every decision
in the pure Python part of the module can be exercised on a CI host without a
real desktop application.
"""

from __future__ import annotations

import builtins
import sys
import types
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dolphin_desktop import _spy


class _Rect:
    def __init__(self, left: int = 10, top: int = 20, right: int = 30, bottom: int = 40):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _Info:
    def __init__(
        self,
        name: str = "Save",
        control_type: str = "Button",
        automation_id: str = "save",
        class_name: str = "ButtonClass",
        rect: _Rect | None = None,
        visible: bool = True,
        enabled: bool = True,
        children: list[object] | None = None,
        parent: object | None = None,
    ) -> None:
        self.name = name
        self.control_type = control_type
        self.automation_id = automation_id
        self.class_name = class_name
        self.rectangle = _Rect() if rect is None else rect
        self.visible = visible
        self.enabled = enabled
        self._children = [] if children is None else children
        self.parent = parent

    def children(self) -> list[object]:
        return self._children


def _module(name: str, **attrs: object) -> types.ModuleType:
    result = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(result, key, value)
    return result


def _install_input_modules(monkeypatch, *, tick: dict[str, int], escape_at: int | None = None):
    """Install deterministic win32api/win32con fakes for picker tests."""

    con = _module(
        "win32con",
        VK_ESCAPE="escape",
        VK_LBUTTON="left",
        VK_CONTROL="control",
        WM_PAINT="paint",
        WM_DESTROY="destroy",
        WS_EX_TRANSPARENT=1,
        WS_EX_TOPMOST=2,
        WS_EX_TOOLWINDOW=4,
        WS_EX_NOACTIVATE=8,
        WS_POPUP=16,
        HWND_TOPMOST=17,
        SWP_NOACTIVATE=32,
        SWP_SHOWWINDOW=64,
        SW_HIDE=0,
        BM_CLICK=128,
    )

    def key_state(vk: str) -> int:
        if vk == con.VK_ESCAPE and escape_at is not None and tick["value"] >= escape_at:
            return 0x8001
        return 0

    api = _module(
        "win32api",
        GetCursorPos=lambda: (50, 30),
        GetAsyncKeyState=key_state,
        GetModuleHandle=lambda value: "instance",
        SendMessage=lambda *args: None,
    )
    monkeypatch.setitem(sys.modules, "win32api", api)
    monkeypatch.setitem(sys.modules, "win32con", con)
    return api, con


class TestSelectorAndNodeHelpers:
    @pytest.mark.parametrize(
        ("auto_id", "name", "class_name", "control_type", "expected"),
        [
            ("save", "Save", "Button", "Button", {"auto_id": "save"}),
            ("0", "Save", "Button", "Button", {"title": "Save", "control_type": "Button"}),
            ("", "Save\tCtrl+S", "Menu", "MenuItem", {"title": "Save", "control_type": "MenuItem"}),
            ("", "Save", "", "", {"title": "Save"}),
            ("", "", "Button", "", {"class_name": "Button"}),
            ("", "", "", "Button", {"control_type": "Button"}),
            ("", "", "", "", {}),
        ],
    )
    def test_selector_preference_order_and_menu_shortcut(
        self, auto_id, name, class_name, control_type, expected
    ):
        assert _spy._suggest_selector(auto_id, name, class_name, control_type) == expected

    def test_bbox_and_node_tolerate_independent_broken_properties(self):
        class Broken:
            @property
            def name(self):
                raise RuntimeError

            @property
            def control_type(self):
                raise RuntimeError

            @property
            def automation_id(self):
                raise RuntimeError

            @property
            def class_name(self):
                raise RuntimeError

            @property
            def rectangle(self):
                raise RuntimeError

            @property
            def visible(self):
                raise RuntimeError

            @property
            def enabled(self):
                raise RuntimeError

            def children(self):
                raise RuntimeError

        node = _spy._node_from_element_info(Broken(), depth=None)
        assert node.name == node.control_type == node.automation_id == node.class_name == ""
        assert node.bounding_box == {"x": 0, "y": 0, "width": 0, "height": 0}
        assert node.visible is True and node.enabled is True and node.children == []

    def test_node_walks_unlimited_children_without_maximum(self):
        leaf = _Info(name="Leaf")
        root = _Info(name="Root", children=[leaf, _Info(name="Second")])
        node = _spy._node_from_element_info(root, depth=None, max_children=None)
        assert [child.name for child in node.children] == ["Leaf", "Second"]
        assert _spy._node_to_dict(node)["children"][0]["name"] == "Leaf"
        assert _spy._node_from_element_info(root, depth=0).children == []

    def test_element_info_from_point_uses_new_api(self, monkeypatch):
        expected = object()

        class UIA:
            @classmethod
            def from_point(cls, x, y):
                assert (x, y) == (4, 5)
                return expected

        monkeypatch.setitem(
            sys.modules,
            "pywinauto.uia_element_info",
            _module("pywinauto.uia_element_info", UIAElementInfo=UIA),
        )
        assert _spy._element_info_from_point(4, 5) is expected

    def test_element_info_from_point_falls_back_to_raw_com(self, monkeypatch):
        com_element = object()
        point = {}

        class UIA:
            @classmethod
            def from_point(cls, x, y):
                raise AttributeError("old pywinauto")

            def __init__(self, element):
                assert element is com_element
                self.element = element

        class Automation:
            def ElementFromPoint(self, value):  # noqa: N802 - mirrors the COM API
                point["value"] = value
                return com_element

        class IUIA:
            def __init__(self):
                self.iuia = Automation()

        class POINT:
            def __init__(self, x, y):
                self.x, self.y = x, y

        monkeypatch.setitem(
            sys.modules,
            "pywinauto.uia_element_info",
            _module("pywinauto.uia_element_info", UIAElementInfo=UIA),
        )
        monkeypatch.setitem(
            sys.modules, "pywinauto.uia_defines", _module("pywinauto.uia_defines", IUIA=IUIA)
        )
        monkeypatch.setitem(
            sys.modules,
            "pywinauto.win32structures",
            _module("pywinauto.win32structures", POINT=POINT),
        )
        result = _spy._element_info_from_point(7, 8)
        assert result.element is com_element
        assert (point["value"].x, point["value"].y) == (7, 8)

    def test_element_info_from_point_returns_none_for_empty_or_broken_com(self, monkeypatch):
        class UIA:
            @classmethod
            def from_point(cls, x, y):
                raise RuntimeError

            def __init__(self, element):
                raise RuntimeError

        monkeypatch.setitem(
            sys.modules,
            "pywinauto.uia_element_info",
            _module("pywinauto.uia_element_info", UIAElementInfo=UIA),
        )
        monkeypatch.setitem(
            sys.modules,
            "pywinauto.uia_defines",
            _module(
                "pywinauto.uia_defines",
                IUIA=lambda: SimpleNamespace(iuia=SimpleNamespace(ElementFromPoint=lambda p: None)),
            ),
        )
        monkeypatch.setitem(
            sys.modules,
            "pywinauto.win32structures",
            _module("pywinauto.win32structures", POINT=lambda x, y: (x, y)),
        )
        assert _spy._element_info_from_point(1, 2) is None

        # A failing import in the fallback is swallowed as well.
        monkeypatch.setitem(
            sys.modules,
            "pywinauto.uia_defines",
            _module("pywinauto.uia_defines", IUIA=lambda: (_ for _ in ()).throw(RuntimeError())),
        )
        assert _spy._element_info_from_point(1, 2) is None


class TestHighlighter:
    def test_init_and_ensure_strips_success_and_cached_path(self, monkeypatch):
        highlighter = _spy._Highlighter()
        started = []

        class FakeThread:
            def __init__(self, **kwargs):
                started.append(kwargs)

            def start(self):
                highlighter._strips = [1, 2, 3, 4]
                highlighter._ready.set()

        monkeypatch.setattr(_spy.threading, "Thread", FakeThread)
        assert highlighter._ensure_strips() is True
        assert len(started) == 1
        assert highlighter._ensure_strips() is True
        assert len(started) == 1

    def test_ensure_strips_reports_thread_failure(self, monkeypatch):
        highlighter = _spy._Highlighter()

        class FakeThread:
            def __init__(self, **kwargs):
                pass

            def start(self):
                highlighter._ready.set()

        monkeypatch.setattr(_spy.threading, "Thread", FakeThread)
        assert highlighter._ensure_strips() is False
        # A thread object already exists, so the second call must not start another one.
        assert highlighter._ensure_strips() is False

    def test_strip_thread_creates_windows_paints_and_pumps(self, monkeypatch):
        calls = []

        class GuiError(Exception):
            pass

        def wndclass():
            return SimpleNamespace()

        paint_callback = {}
        gui = _module(
            "win32gui",
            error=GuiError,
            WNDCLASS=wndclass,
            CreateSolidBrush=lambda color: calls.append(("brush", color)) or "brush",
            RegisterClass=lambda wc: (
                paint_callback.setdefault("proc", wc.lpfnWndProc),
                calls.append(("register", wc.lpszClassName)),
            ),
            CreateWindowEx=lambda *args: (
                calls.append(("create", args)) or 100 + len([c for c in calls if c[0] == "create"])
            ),
            BeginPaint=lambda hwnd: ("hdc", "paint-struct"),
            GetClientRect=lambda hwnd: (0, 0, 20, 3),
            FillRect=lambda *args: calls.append(("fill", args)),
            EndPaint=lambda *args: calls.append(("end", args)),
            PumpMessages=lambda: calls.append(("pump",)),
        )
        monkeypatch.setitem(sys.modules, "win32gui", gui)
        monkeypatch.setitem(
            sys.modules, "win32api", _module("win32api", GetModuleHandle=lambda _: "instance")
        )
        monkeypatch.setitem(
            sys.modules,
            "win32con",
            _module(
                "win32con",
                WM_PAINT="paint",
                WM_DESTROY="destroy",
                WS_EX_TRANSPARENT=1,
                WS_EX_TOPMOST=2,
                WS_EX_TOOLWINDOW=4,
                WS_EX_NOACTIVATE=8,
                WS_POPUP=16,
                HWND_TOPMOST=17,
                SWP_NOACTIVATE=32,
                SWP_SHOWWINDOW=64,
                SW_HIDE=0,
            ),
        )
        highlighter = _spy._Highlighter()
        highlighter._strip_thread()
        assert highlighter._strips == [101, 102, 103, 104]
        assert paint_callback["proc"]["paint"]("hwnd", "paint", 0, 0) == 0
        assert any(call[0] == "fill" for call in calls)
        assert any(call[0] == "pump" for call in calls)

    def test_strip_thread_ignores_already_registered_class_and_pump_error(self, monkeypatch):
        class GuiError(Exception):
            pass

        gui = _module(
            "win32gui",
            error=GuiError,
            WNDCLASS=lambda: SimpleNamespace(),
            CreateSolidBrush=lambda _: "brush",
            RegisterClass=lambda _: (_ for _ in ()).throw(GuiError()),
            CreateWindowEx=lambda *args: 1,
            PumpMessages=lambda: (_ for _ in ()).throw(RuntimeError()),
        )
        monkeypatch.setitem(sys.modules, "win32gui", gui)
        monkeypatch.setitem(
            sys.modules, "win32api", _module("win32api", GetModuleHandle=lambda _: "instance")
        )
        monkeypatch.setitem(
            sys.modules,
            "win32con",
            _module(
                "win32con",
                WM_PAINT="paint",
                WM_DESTROY="destroy",
                WS_EX_TRANSPARENT=1,
                WS_EX_TOPMOST=2,
                WS_EX_TOOLWINDOW=4,
                WS_EX_NOACTIVATE=8,
                WS_POPUP=16,
            ),
        )
        highlighter = _spy._Highlighter()
        highlighter._strip_thread()
        assert highlighter._strips == [1, 1, 1, 1]

    def test_strip_thread_clears_partial_creation_and_import_failures(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "win32api", None)
        highlighter = _spy._Highlighter()
        highlighter._strip_thread()
        assert highlighter._strips == []
        assert highlighter._ready.is_set()

        class Gui:
            def __init__(self):
                self.calls = 0

            def CreateWindowEx(self, *args):  # noqa: N802 - mirrors the Win32 API
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError
                return self.calls

        g = Gui()
        monkeypatch.setitem(
            sys.modules, "win32api", _module("win32api", GetModuleHandle=lambda _: "i")
        )
        monkeypatch.setitem(
            sys.modules,
            "win32gui",
            _module(
                "win32gui",
                error=RuntimeError,
                WNDCLASS=lambda: SimpleNamespace(),
                CreateSolidBrush=lambda _: "b",
                RegisterClass=lambda _: None,
                CreateWindowEx=g.CreateWindowEx,
            ),
        )
        monkeypatch.setitem(
            sys.modules,
            "win32con",
            _module(
                "win32con",
                WM_PAINT=1,
                WM_DESTROY=2,
                WS_EX_TRANSPARENT=1,
                WS_EX_TOPMOST=2,
                WS_EX_TOOLWINDOW=4,
                WS_EX_NOACTIVATE=8,
                WS_POPUP=16,
            ),
        )
        highlighter._strip_thread()
        assert highlighter._strips == []

    def test_update_draws_four_edges_skips_duplicates_and_clear_hides(self, monkeypatch):
        calls = []
        gui = _module(
            "win32gui",
            SetWindowPos=lambda *args: calls.append(("pos", args)),
            InvalidateRect=lambda *args: calls.append(("invalidate", args)),
            ShowWindow=lambda *args: calls.append(("hide", args)),
        )
        con = _module("win32con", HWND_TOPMOST="top", SWP_NOACTIVATE=1, SWP_SHOWWINDOW=2, SW_HIDE=3)
        monkeypatch.setitem(sys.modules, "win32gui", gui)
        monkeypatch.setitem(sys.modules, "win32con", con)
        highlighter = _spy._Highlighter()
        highlighter._strips = [1, 2, 3, 4]
        highlighter.update({"x": 10, "y": 20, "width": 2, "height": 2})
        assert len([call for call in calls if call[0] == "pos"]) == 4
        highlighter.update({"x": 10, "y": 20, "width": 2, "height": 2})
        assert len([call for call in calls if call[0] == "pos"]) == 4
        highlighter.clear()
        assert len([call for call in calls if call[0] == "hide"]) == 4
        highlighter.clear()
        assert highlighter._last is None

    def test_update_and_clear_swallow_win32_errors(self, monkeypatch):
        highlighter = _spy._Highlighter()
        highlighter._ensure_strips = lambda: False
        highlighter.update({"x": 1, "y": 2, "width": 3, "height": 4})
        assert highlighter._last is None

        highlighter._strips = [1, 2, 3, 4]
        highlighter._ensure_strips = lambda: True
        monkeypatch.setitem(
            sys.modules,
            "win32con",
            _module("win32con", HWND_TOPMOST=1, SWP_NOACTIVATE=2, SWP_SHOWWINDOW=4),
        )
        monkeypatch.setitem(
            sys.modules,
            "win32gui",
            _module("win32gui", SetWindowPos=lambda *args: (_ for _ in ()).throw(RuntimeError())),
        )
        highlighter.update({"x": 1, "y": 2, "width": 3, "height": 4})
        assert highlighter._last is None

        highlighter._last = (1, 2, 3, 4)
        monkeypatch.setitem(
            sys.modules,
            "win32gui",
            _module("win32gui", ShowWindow=lambda *args: (_ for _ in ()).throw(RuntimeError())),
        )
        highlighter.clear()
        assert highlighter._last is None

    def test_clear_destroys_overlay_windows_and_stops_worker(self, monkeypatch):
        calls = []

        class FakeThread:
            native_id = 42
            ident = 42

            def join(self, timeout):
                calls.append(("join", timeout))

        monkeypatch.setitem(
            sys.modules,
            "win32gui",
            _module(
                "win32gui",
                PostMessage=lambda *args: calls.append(("close", args)),
            ),
        )
        monkeypatch.setitem(
            sys.modules,
            "win32api",
            _module(
                "win32api",
                PostThreadMessage=lambda *args: calls.append(("quit", args)),
            ),
        )
        monkeypatch.setitem(sys.modules, "win32con", _module("win32con", WM_QUIT=18))
        highlighter = _spy._Highlighter()
        highlighter._strips = [1, 2, 3, 4]
        highlighter._last = (1, 2, 3, 4)
        highlighter._thread = FakeThread()

        highlighter.clear()

        assert [call[0] for call in calls] == ["close"] * 4 + ["quit", "join"]
        assert all(call[1][0] in {1, 2, 3, 4} for call in calls[:4])
        assert highlighter._strips == []
        assert highlighter._thread is None

    def test_pick_cancels_when_current_owner_window_disappears(self, monkeypatch):
        tick = {"value": -1}
        _install_input_modules(monkeypatch, tick=tick)
        monkeypatch.setitem(
            sys.modules,
            "win32gui",
            _module("win32gui", IsWindow=lambda handle: False),
        )
        info = _Info()
        info.handle = 123
        points = iter([info, None])
        monkeypatch.setattr(
            _spy,
            "_element_info_from_point",
            lambda x, y: next(points),
        )
        highlighter = SimpleNamespace(update=Mock(), clear=Mock())
        monkeypatch.setattr(_spy, "_Highlighter", lambda: highlighter)
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)

        result = _spy.pick()

        assert result["status"] == "cancelled"
        highlighter.clear.assert_called_once_with()

    def test_owner_liveness_stops_at_window_before_uia_desktop_root(self, monkeypatch):
        root = _Info(name="Desktop Root", control_type="Pane")
        root.handle = 456
        window = _Info(name="AUT", control_type="Window", parent=root)
        window.handle = 123
        child = _Info(control_type="Button", parent=window)
        monkeypatch.setitem(
            sys.modules,
            "win32gui",
            _module("win32gui", IsWindow=lambda handle: handle == root.handle),
        )

        assert _spy._element_owner_is_alive(child) is False


class TestInspectAndFormatting:
    def test_resolve_element_info_prefers_window_wrapper(self):
        info = object()
        wrapper = SimpleNamespace(element_info=info)
        window = SimpleNamespace(_spec=SimpleNamespace(wrapper_object=lambda: wrapper, handle=10))
        assert _spy._resolve_element_info(window=window) is info

    def test_resolve_element_info_uses_window_handle_fallback(self, monkeypatch):
        info = object()
        window = SimpleNamespace(
            _spec=SimpleNamespace(
                wrapper_object=lambda: (_ for _ in ()).throw(RuntimeError()), handle=11
            )
        )
        from pywinauto.uia_element_info import UIAElementInfo

        monkeypatch.setattr(
            UIAElementInfo, "from_hwnd", classmethod(lambda cls, hwnd: info), raising=False
        )
        assert _spy._resolve_element_info(window=window) is info

    def test_resolve_element_info_uses_app_wrapper_and_handle_fallback(self, monkeypatch):
        info = object()
        wrapper = SimpleNamespace(element_info=info)
        win = SimpleNamespace(_spec=SimpleNamespace(wrapper_object=lambda: wrapper, handle=12))
        app = SimpleNamespace(top_window=lambda: win)
        assert _spy._resolve_element_info(app=app) is info

        win._spec.wrapper_object = lambda: (_ for _ in ()).throw(RuntimeError())
        from pywinauto.uia_element_info import UIAElementInfo

        monkeypatch.setattr(
            UIAElementInfo, "from_hwnd", classmethod(lambda cls, hwnd: info), raising=False
        )
        assert _spy._resolve_element_info(app=app) is info

    def test_resolve_element_info_desktop_filters_and_validation(self, monkeypatch):
        info = object()
        calls = {}
        spec = SimpleNamespace(wrapper_object=lambda: SimpleNamespace(element_info=info))

        class Desktop:
            def __init__(self, **kwargs):
                calls["backend"] = kwargs

            def window(self, **kwargs):
                calls["window"] = kwargs
                return spec

        import pywinauto

        monkeypatch.setattr(pywinauto, "Desktop", Desktop)
        assert (
            _spy._resolve_element_info(
                title="Exact", title_re="^E", class_name="Class", pid=42, backend="win32"
            )
            is info
        )
        assert calls == {
            "backend": {"backend": "win32"},
            "window": {"title": "Exact", "title_re": "^E", "class_name": "Class", "process": 42},
        }
        with pytest.raises(ValueError, match="Provide window"):
            _spy._resolve_element_info()

    def test_resolve_element_info_continues_after_window_and_app_handle_failures(self, monkeypatch):
        import pywinauto
        from pywinauto.uia_element_info import UIAElementInfo

        calls = []
        monkeypatch.setattr(
            UIAElementInfo,
            "from_hwnd",
            classmethod(lambda cls, hwnd: (_ for _ in ()).throw(RuntimeError())),
            raising=False,
        )
        spec = SimpleNamespace(
            wrapper_object=lambda: (_ for _ in ()).throw(RuntimeError()), handle=99
        )
        window = SimpleNamespace(_spec=spec)
        app = SimpleNamespace(top_window=lambda: SimpleNamespace(_spec=spec))
        expected = object()

        class Desktop:
            def __init__(self, **kwargs):
                calls.append(("desktop", kwargs))

            def window(self, **kwargs):
                calls.append(("window", kwargs))
                return SimpleNamespace(
                    wrapper_object=lambda: SimpleNamespace(element_info=expected)
                )

        monkeypatch.setattr(pywinauto, "Desktop", Desktop)
        assert _spy._resolve_element_info(window=window, title="fallback") is expected
        assert _spy._resolve_element_info(app=app, title="fallback-app") is expected
        assert calls == [
            ("desktop", {"backend": "uia"}),
            ("window", {"title": "fallback"}),
            ("desktop", {"backend": "uia"}),
            ("window", {"title": "fallback-app"}),
        ]

    def test_inspect_passes_options_and_returns_schema(self, monkeypatch):
        info = object()
        node = _spy._NodeInfo(
            "Root",
            "Window",
            "id",
            "Class",
            {"x": 0, "y": 0, "width": 1, "height": 1},
            True,
            True,
            {},
        )
        seen = {}

        def resolve(**kwargs):
            seen.update(kwargs)
            return info

        def build(value, depth, max_children):
            assert value is info
            seen["walk"] = (depth, max_children)
            return node

        monkeypatch.setattr(_spy, "_resolve_element_info", resolve)
        monkeypatch.setattr(_spy, "_node_from_element_info", build)
        result = _spy.inspect(title="Demo", depth=3, max_children=4, backend="win32")
        assert seen == {
            "window": None,
            "app": None,
            "title": "Demo",
            "title_re": None,
            "class_name": None,
            "pid": None,
            "backend": "win32",
            "walk": (3, 4),
        }
        assert result == {
            "schema_version": 1,
            "limits": {"depth": 3, "max_children": 4},
            "root": _spy._node_to_dict(node),
        }

    def test_color_selection_and_plain_tree_format(self, monkeypatch):
        monkeypatch.setattr(_spy.sys.stdout, "isatty", lambda: True)
        assert _spy._use_color(True) is True
        assert _spy._use_color(False) is False
        assert _spy._use_color(None) is True
        monkeypatch.setattr(_spy.sys.stdout, "isatty", lambda: False)
        assert _spy._use_color(None) is False

        node = {
            "control_type": "Window",
            "name": "Demo",
            "automation_id": "root",
            "class_name": "Main",
            "bounding_box": {"width": 100, "height": 50},
            "children": [
                {"control_type": "Button", "name": "OK", "bounding_box": {}, "children": []}
            ],
        }
        assert _spy.format_tree(node, color=False, indent=1) == (
            '  Window "Demo" id=\'root\' [Main] (100×50)\n    Button "OK"'
        )
        assert _spy.format_tree({}, color=False) == "?"
        coloured = _spy.format_tree(node, color=True)
        assert all(
            token in coloured
            for token in (_spy._C_YELLOW, _spy._C_GREEN, _spy._C_DIM, _spy._C_MAGENTA)
        )
        lines: list[str] = []
        assert _spy.format_tree({}, color=True, _lines=lines) == ""
        assert lines[0].startswith(_spy._C_CYAN)

    def test_selector_helpers_handle_info_failures_and_types(self):
        class Broken:
            @property
            def name(self):
                raise RuntimeError

            @property
            def control_type(self):
                raise RuntimeError

            @property
            def automation_id(self):
                raise RuntimeError

            @property
            def class_name(self):
                raise RuntimeError

        assert _spy._selector_for_info(Broken()) == {}
        assert _spy._is_meaningful_ancestor_selector({"auto_id": "x"})
        assert _spy._is_meaningful_ancestor_selector({"title": "x"})
        assert _spy._is_meaningful_ancestor_selector({"class_name": "x"})
        assert not _spy._is_meaningful_ancestor_selector({"control_type": "Pane"})
        assert _spy._selector_to_code({"enabled": True, "found_index": 2}) == (
            "locator(enabled=True, found_index=2)"
        )
        assert _spy._selector_to_code({}) == "locator()  # no selector found"
        assert _spy._chain_to_code([{"title": "Panel"}, {"found_index": 1}]) == (
            "locator(title='Panel').locator(found_index=1)"
        )
        assert _spy._chain_to_code([]) == "locator()  # no selector found"


class TestParentChainAndResolution:
    def test_parent_chain_skips_anonymous_ancestors_and_handles_empty_input(self):
        window = _Info(
            name="Main", control_type="Window", automation_id="", class_name="Main", parent=None
        )
        named_pane = _Info(
            name="Panel", control_type="Pane", automation_id="", class_name="", parent=window
        )
        anonymous = _Info(
            name="", control_type="Pane", automation_id="", class_name="", parent=named_pane
        )
        leaf = _Info(name="", control_type="", automation_id="", class_name="", parent=anonymous)
        assert _spy._build_parent_chain(leaf) == (
            window,
            [{"title": "Panel", "control_type": "Pane"}, {}],
        )
        assert _spy._build_parent_chain(None) == (None, [])

    def test_parent_chain_stops_on_window_and_survives_property_errors(self):
        class Broken:
            @property
            def control_type(self):
                raise RuntimeError

            @property
            def parent(self):
                raise RuntimeError

            @property
            def name(self):
                raise RuntimeError

            @property
            def automation_id(self):
                raise RuntimeError

            @property
            def class_name(self):
                raise RuntimeError

        owner, chain = _spy._build_parent_chain(Broken())
        assert owner is not None and chain == [{}]

    def test_parent_chain_safety_cap(self):
        nodes = [_Info(name=f"n{i}", control_type="Pane", class_name=f"C{i}") for i in range(34)]
        for current, parent in pairwise(nodes):
            current.parent = parent
        nodes[-1].parent = None
        owner, chain = _spy._build_parent_chain(nodes[0])
        assert owner is None and len(chain) == 32

    def test_rect_helpers_return_none_on_bad_values(self):
        assert _spy._info_rect(SimpleNamespace(rectangle=object())) is None
        assert _spy._wrapper_rect(SimpleNamespace(rectangle=lambda: object())) is None

    def test_resolve_chain_handles_no_pywinauto(self, monkeypatch):
        real_import = builtins.__import__

        def no_pywinauto(name, *args, **kwargs):
            if name == "pywinauto":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_pywinauto)
        assert _spy._resolve_chain_to_wrapper(_Info(), [], "uia") is None

    def test_resolve_chain_rejects_blank_window_and_catches_desktop_errors(self, monkeypatch):
        blank = _Info(name="", class_name="", control_type="Window")
        assert _spy._resolve_chain_to_wrapper(blank, [], "uia") is None
        import pywinauto

        monkeypatch.setattr(pywinauto, "Desktop", Mock(side_effect=RuntimeError))
        window = _Info(name="Demo", class_name="Main", control_type="Window")
        assert _spy._resolve_chain_to_wrapper(window, [{"title": "Save"}], "uia") is None

    def test_resolve_chain_reads_broken_window_fields_and_succeeds(self, monkeypatch):
        class BrokenFields:
            @property
            def class_name(self):
                raise RuntimeError

            @property
            def name(self):
                raise RuntimeError

        assert _spy._resolve_chain_to_wrapper(BrokenFields(), [], "uia") is None

        class Spec:
            def child_window(self, **selector):
                assert selector == {"title": "Save"}
                return self

            def wait(self, condition, timeout):
                assert (condition, timeout) == ("exists", 1.5)

            def wrapper_object(self):
                return "wrapper"

        import pywinauto

        monkeypatch.setattr(
            pywinauto,
            "Desktop",
            lambda **kwargs: SimpleNamespace(window=lambda **kw: Spec()),
        )
        window = _Info(name="Demo", class_name="Main", control_type="Window")
        assert _spy._resolve_chain_to_wrapper(window, [{"title": "Save"}], "uia") == "wrapper"

    def test_chain_matching_covers_missing_wrapper_and_rectangles(self, monkeypatch):
        window = _Info(control_type="Window", name="Demo", class_name="Main")
        picked = _Info()
        monkeypatch.setattr(_spy, "_resolve_chain_to_wrapper", lambda *args: None)
        assert not _spy._chain_matches_picked(window, [], picked, "uia")

        wrapper = SimpleNamespace(rectangle=lambda: _Rect())
        monkeypatch.setattr(_spy, "_resolve_chain_to_wrapper", lambda *args: wrapper)
        broken_picked = SimpleNamespace(rectangle=object())
        assert not _spy._chain_matches_picked(window, [], broken_picked, "uia")
        broken_wrapper = SimpleNamespace(rectangle=lambda: object())
        monkeypatch.setattr(_spy, "_resolve_chain_to_wrapper", lambda *args: broken_wrapper)
        assert not _spy._chain_matches_picked(window, [], picked, "uia")
        matching_wrapper = SimpleNamespace(rectangle=lambda: _Rect())
        monkeypatch.setattr(_spy, "_resolve_chain_to_wrapper", lambda *args: matching_wrapper)
        assert _spy._chain_matches_picked(window, [], picked, "uia") is True

    def test_index_among_siblings_returns_all_failure_variants(self):
        assert _spy._index_among_siblings(SimpleNamespace(parent=None)) is None

        class BrokenParent:
            @property
            def parent(self):
                raise RuntimeError

        assert _spy._index_among_siblings(BrokenParent()) is None
        parent = SimpleNamespace(children=lambda: (_ for _ in ()).throw(RuntimeError()))
        target = _Info(parent=parent)
        assert _spy._index_among_siblings(target) is None
        target.rectangle = object()
        assert _spy._index_among_siblings(target) is None

        parent = SimpleNamespace(
            children=lambda: [
                SimpleNamespace(
                    control_type="Button", class_name="ButtonClass", rectangle=_Rect(0, 0, 5, 5)
                ),
                _Info(),
            ]
        )
        target = _Info(parent=parent)
        assert _spy._index_among_siblings(target) == 1
        parent.children = lambda: [
            SimpleNamespace(control_type="Button", class_name="Button", rectangle=object())
        ]
        assert _spy._index_among_siblings(target) is None

        class BrokenAttributes:
            @property
            def control_type(self):
                raise RuntimeError

            @property
            def class_name(self):
                raise RuntimeError

        broken = BrokenAttributes()
        broken.parent = SimpleNamespace(children=lambda: [broken])
        broken.rectangle = _Rect()
        assert _spy._index_among_siblings(broken) is None

        class BrokenChild:
            @property
            def control_type(self):
                raise RuntimeError

            @property
            def class_name(self):
                raise RuntimeError

        parent.children = lambda: [BrokenChild()]
        assert _spy._index_among_siblings(target) is None

    def test_find_info_by_selector_handles_depth_errors_and_misses(self):
        target = _Info(name="Save", control_type="Button")
        nested = _Info(name="Panel", control_type="Pane", children=[target])
        root = _Info(children=[nested])
        assert (
            _spy._find_info_by_selector(root, {"title": "Save", "control_type": "Button"}) is target
        )
        assert _spy._find_info_by_selector(root, {"title": "Other"}) is None
        assert (
            _spy._find_info_by_selector(
                SimpleNamespace(children=lambda: (_ for _ in ()).throw(RuntimeError())), {}
            )
            is None
        )

        deep = _Info()
        current = deep
        for _ in range(10):
            child = _Info(name="not the target")
            current._children = [child]
            current = child
        assert _spy._find_info_by_selector(deep, {"title": "Save"}) is None

        class BadChild:
            @property
            def name(self):
                raise RuntimeError

        assert (
            _spy._find_info_by_selector(SimpleNamespace(children=lambda: [BadChild()]), {}) is None
        )

    def test_validate_repair_all_statuses(self, monkeypatch):
        picked = _Info()
        window = _Info(control_type="Window", name="Demo", class_name="Main")
        assert _spy._validate_and_repair(None, [{"title": "Save"}], picked, "uia") == (
            [{"title": "Save"}],
            "unverified",
        )
        assert _spy._validate_and_repair(window, [], picked, "uia")[1] == "unverified"

        chain = [{"title": "Save", "class_name": "Button", "control_type": "Button"}]
        monkeypatch.setattr(_spy, "_chain_matches_picked", lambda *args: True)
        assert _spy._validate_and_repair(window, chain, picked, "uia") == (chain, "ok")

        monkeypatch.setattr(_spy, "_chain_matches_picked", lambda *args: False)
        monkeypatch.setattr(_spy, "_index_among_siblings", lambda info: None)
        assert _spy._validate_and_repair(window, chain, picked, "uia")[1] == "unreachable"

        monkeypatch.setattr(_spy, "_index_among_siblings", lambda info: 3)
        monkeypatch.setattr(_spy, "_chain_matches_picked", Mock(side_effect=[False, True]))
        repaired, status = _spy._validate_and_repair(window, chain, picked, "uia")
        assert status == "repaired" and repaired == [
            {"class_name": "Button", "control_type": "Button", "found_index": 3}
        ]

        simple_chain = [{"title": "Panel"}, {"title": "Save"}]
        monkeypatch.setattr(_spy, "_chain_matches_picked", Mock(side_effect=[False, True]))
        repaired, status = _spy._validate_and_repair(window, simple_chain, picked, "uia")
        assert status == "repaired" and repaired == [{"title": "Panel"}, {"found_index": 3}]

        monkeypatch.setattr(_spy, "_chain_matches_picked", Mock(side_effect=[False, False, True]))
        assert _spy._validate_and_repair(window, simple_chain, picked, "uia")[1] == "repaired_flat"

        flat_chain = [{"title": "Panel", "control_type": "Pane"}, {"title": "Save"}]
        monkeypatch.setattr(_spy, "_index_among_siblings", lambda info: None)
        monkeypatch.setattr(_spy, "_chain_matches_picked", Mock(side_effect=[False, True]))
        assert _spy._validate_and_repair(window, flat_chain, picked, "uia")[1] == "repaired_flat"

        monkeypatch.setattr(_spy, "_chain_matches_picked", lambda *args: False)
        monkeypatch.setattr(_spy, "_find_info_by_selector", lambda *args: picked)
        monkeypatch.setattr(_spy, "_info_rect", lambda info: (1, 2, 3, 4))
        assert _spy._validate_and_repair(window, flat_chain, picked, "uia")[1] == "repaired_flat"
        monkeypatch.setattr(_spy, "_find_info_by_selector", lambda *args: None)
        assert _spy._validate_and_repair(window, flat_chain, picked, "uia")[1] == "unreachable"

        no_flat = [{"control_type": "Button"}, {"control_type": "Button"}]
        assert _spy._validate_and_repair(window, no_flat, picked, "uia")[1] == "unreachable"


class TestPickers:
    def test_pick_reports_missing_pywin32(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "win32api", None)
        with pytest.raises(RuntimeError, match=r"pick\(\) requires pywin32"):
            _spy.pick()

    def test_pick_cancel(self, monkeypatch):
        tick = {"value": 0}
        _install_input_modules(monkeypatch, tick=tick, escape_at=0)
        highlighter = SimpleNamespace(update=Mock(), clear=Mock())
        monkeypatch.setattr(_spy, "_Highlighter", lambda: highlighter)
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        result = _spy.pick()
        assert result["status"] == "cancelled" and result["chain"] == []
        highlighter.clear.assert_called_once_with()

    def test_pick_captures_and_validates_current_info(self, monkeypatch):
        tick = {"value": -1}
        con_api, con = _install_input_modules(monkeypatch, tick=tick)
        info = _Info(name="Save")

        def cursor():
            tick["value"] += 1
            return (50, 30)

        def key_state(vk):
            if vk == con.VK_LBUTTON and tick["value"] == 1:
                return 0x8000
            if vk == con.VK_CONTROL and tick["value"] == 1:
                return 0x8000
            return 0

        con_api.GetCursorPos = cursor
        con_api.GetAsyncKeyState = key_state
        monkeypatch.setattr(_spy, "_element_info_from_point", lambda x, y: info)
        highlighter = SimpleNamespace(update=Mock(), clear=Mock())
        monkeypatch.setattr(_spy, "_Highlighter", lambda: highlighter)
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        monkeypatch.setattr(
            _spy, "_build_parent_chain", lambda current: ("window", [{"auto_id": "save"}])
        )
        monkeypatch.setattr(
            _spy, "_validate_and_repair", lambda *args: ([{"auto_id": "save"}], "ok")
        )
        result = _spy.pick("win32")
        assert result["status"] == "ok" and result["chain"] == [{"auto_id": "save"}]
        assert highlighter.update.call_count >= 1
        highlighter.clear.assert_called_once_with()

    def test_pick_tolerates_point_and_bbox_errors_before_cancel(self, monkeypatch):
        tick = {"value": -1}
        api, _con = _install_input_modules(monkeypatch, tick=tick, escape_at=2)
        api.GetCursorPos = lambda: tick.__setitem__("value", tick["value"] + 1) or (1, 2)
        calls = {"count": 0}

        def point(x, y):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError
            return SimpleNamespace(name="still current", control_type="Button", parent=None)

        monkeypatch.setattr(_spy, "_element_info_from_point", point)
        monkeypatch.setattr(_spy, "_bbox_from_rect", Mock(side_effect=RuntimeError))
        monkeypatch.setattr(
            _spy, "_Highlighter", lambda: SimpleNamespace(update=Mock(), clear=Mock())
        )
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        result = _spy.pick()
        assert result["status"] == "cancelled"

    def test_pick_handles_a_point_miss_and_zero_sized_bbox(self, monkeypatch):
        tick = {"value": -1}
        api, _con = _install_input_modules(monkeypatch, tick=tick, escape_at=2)
        api.GetCursorPos = lambda: tick.__setitem__("value", tick["value"] + 1) or (1, 2)
        points = iter([None, SimpleNamespace(rectangle=_Rect(0, 0, 0, 0))])
        monkeypatch.setattr(_spy, "_element_info_from_point", lambda x, y: next(points))
        monkeypatch.setattr(
            _spy, "_Highlighter", lambda: SimpleNamespace(update=Mock(), clear=Mock())
        )
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        assert _spy.pick()["status"] == "cancelled"

    def test_image_pick_reports_missing_pywin32(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "win32api", None)
        with pytest.raises(RuntimeError, match=r"image_pick\(\) requires pywin32"):
            _spy.image_pick()

    def test_image_pick_cancel_and_bbox_error(self, monkeypatch, tmp_path):
        tick = {"value": -1}
        api, _con = _install_input_modules(monkeypatch, tick=tick, escape_at=1)
        api.GetCursorPos = lambda: tick.__setitem__("value", tick["value"] + 1) or (1, 2)
        monkeypatch.setattr(
            _spy, "_element_info_from_point", lambda x, y: SimpleNamespace(rectangle=object())
        )
        monkeypatch.setattr(_spy, "_bbox_from_rect", Mock(side_effect=RuntimeError))
        monkeypatch.setattr(
            _spy, "_Highlighter", lambda: SimpleNamespace(update=Mock(), clear=Mock())
        )
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        assert _spy.image_pick(tmp_path) is None

    def test_image_pick_tolerates_point_miss_before_cancel(self, monkeypatch, tmp_path):
        tick = {"value": -1}
        api, _con = _install_input_modules(monkeypatch, tick=tick, escape_at=1)
        api.GetCursorPos = lambda: tick.__setitem__("value", tick["value"] + 1) or (1, 2)
        monkeypatch.setattr(_spy, "_element_info_from_point", lambda x, y: None)
        monkeypatch.setattr(
            _spy, "_Highlighter", lambda: SimpleNamespace(update=Mock(), clear=Mock())
        )
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        assert _spy.image_pick(tmp_path) is None

    def test_image_pick_handles_zero_bbox_and_point_exception(self, monkeypatch, tmp_path):
        tick = {"value": -1}
        api, _con = _install_input_modules(monkeypatch, tick=tick, escape_at=2)
        api.GetCursorPos = lambda: tick.__setitem__("value", tick["value"] + 1) or (1, 2)
        calls = {"count": 0}

        def point(x, y):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError
            return SimpleNamespace(rectangle=_Rect(0, 0, 0, 0))

        monkeypatch.setattr(_spy, "_element_info_from_point", point)
        monkeypatch.setattr(
            _spy, "_Highlighter", lambda: SimpleNamespace(update=Mock(), clear=Mock())
        )
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        assert _spy.image_pick(tmp_path) is None

    def test_image_pick_sanitizes_failing_name_and_saves(self, monkeypatch, tmp_path):
        tick = {"value": -1}
        api, con = _install_input_modules(monkeypatch, tick=tick)
        api.GetCursorPos = lambda: tick.__setitem__("value", tick["value"] + 1) or (50, 30)

        class BrokenName:
            @property
            def name(self):
                raise RuntimeError

        info = BrokenName()
        info.rectangle = _Rect(10, 20, 110, 60)
        monkeypatch.setattr(_spy, "_element_info_from_point", lambda x, y: info)
        api.GetAsyncKeyState = lambda vk: 0x8000 if vk in (con.VK_LBUTTON, con.VK_CONTROL) else 0
        monkeypatch.setattr(
            _spy, "_Highlighter", lambda: SimpleNamespace(update=Mock(), clear=Mock())
        )
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)

        saved = {}
        from PIL import ImageGrab

        class Image:
            def save(self, path):
                saved["path"] = path

        monkeypatch.setattr(ImageGrab, "grab", lambda **kwargs: saved.update(kwargs) or Image())
        result = _spy.image_pick(tmp_path)
        assert result is not None and result.endswith(".png")
        assert Path(result).parent == tmp_path
        assert saved["bbox"] == (10, 20, 110, 60) and saved["all_screens"] is True

    def test_image_pick_sanitizes_a_normal_name(self, monkeypatch, tmp_path):
        tick = {"value": -1}
        api, con = _install_input_modules(monkeypatch, tick=tick)
        api.GetCursorPos = lambda: tick.__setitem__("value", tick["value"] + 1) or (50, 30)
        info = _Info(name="A/B: C?", rect=_Rect(0, 0, 2, 3))
        monkeypatch.setattr(_spy, "_element_info_from_point", lambda x, y: info)
        api.GetAsyncKeyState = lambda vk: 0x8000 if vk in (con.VK_LBUTTON, con.VK_CONTROL) else 0
        monkeypatch.setattr(
            _spy, "_Highlighter", lambda: SimpleNamespace(update=Mock(), clear=Mock())
        )
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        from PIL import ImageGrab

        class Image:
            def save(self, path):
                pass

        monkeypatch.setattr(ImageGrab, "grab", lambda **kwargs: Image())
        result = _spy.image_pick(tmp_path)
        assert result is not None and "A_B__C__" in Path(result).name


class _SapComponent:
    def __init__(
        self, full_id, name, sap_type, text="", rect=(1, 2, 30, 40), changeable=True, children=()
    ):
        self.Id = full_id
        self.Name = name
        self.Type = sap_type
        self.Text = text
        self.ScreenLeft, self.ScreenTop, self.Width, self.Height = rect
        self.Changeable = changeable
        self.Children = list(children)


class TestSapHelpers:
    def test_sap_ids_strings_bbox_and_selectors(self, monkeypatch):
        assert _spy._sap_relative_id("/app/con[2]/ses[4]/wnd[0]") == "wnd[0]"
        assert _spy._sap_relative_id("wnd[0]") == "wnd[0]"
        assert _spy._sap_relative_id("") == ""

        component = SimpleNamespace(value="x")
        from dolphin_desktop import _sap

        monkeypatch.setattr(
            _sap,
            "_get_first",
            lambda obj, names: None if names[0] == "none" else getattr(obj, "value", None),
        )
        assert _spy._sap_str(component, ("value",)) == "x"
        assert _spy._sap_str(component, ("none",)) == ""
        assert _spy._sap_bbox(SimpleNamespace(value="x")) == {
            "x": 0,
            "y": 0,
            "width": 0,
            "height": 0,
        }

        values = iter([1, 2, "30", "40"])
        monkeypatch.setattr(_sap, "_get_first", lambda obj, names: next(values))
        assert _spy._sap_bbox(component) == {"x": 1, "y": 2, "width": 30, "height": 40}
        assert _spy._sap_suggest_selector("wnd[0]", "Name", "GuiButton") == {"id": "wnd[0]"}
        assert _spy._sap_suggest_selector("", "Name", "GuiButton") == {
            "name": "Name",
            "type": "GuiButton",
        }
        assert _spy._sap_suggest_selector("", "", "GuiButton") == {"type": "GuiButton"}
        assert _spy._sap_suggest_selector("", "", "") == {}

    @pytest.mark.parametrize(
        ("selector", "expected"),
        [
            ({}, "find_by_id()  # no SAP id found"),
            ({"id": "wnd[0]"}, "find_by_id('wnd[0]')"),
            ({"name": "Save", "type": "GuiButton"}, "locator(name='Save', type='GuiButton')"),
            ({"name": "Save"}, "locator(name='Save')"),
            ({"type": "GuiButton"}, "locator(type='GuiButton')"),
            ({"other": "ignored"}, "locator()"),
        ],
    )
    def test_sap_selector_code(self, selector, expected):
        assert _spy._sap_selector_to_code(selector) == expected

    def test_sap_node_walk_and_dict(self):
        leaf = _SapComponent("", "Save", "GuiButton", "Save", changeable=False)
        child = _SapComponent("/app/con[0]/ses[0]/usr", "usr", "GuiUserArea", children=[leaf])
        root = _SapComponent("/app/con[0]/ses[0]/wnd[0]", "wnd", "GuiMainWindow", children=[child])
        node = _spy._sap_node_from_component(root, depth=None, max_children=None)
        assert node.id == "wnd[0]" and node.children[0].children[0].changeable is False
        assert _spy._sap_node_to_dict(node)["children"][0]["children"][0]["name"] == "Save"
        limited = _spy._sap_node_from_component(root, depth=1, max_children=0)
        assert limited.children == []

    def test_sap_inspect_connects_to_requested_session(self, monkeypatch):
        from dolphin_desktop import _sap

        root = _SapComponent("/app/con[1]/ses[2]/wnd[0]", "wnd", "GuiMainWindow")
        session = SimpleNamespace(raw=root)
        calls = {}

        class SapGui:
            @classmethod
            def connect(cls, timeout=None):
                calls["timeout"] = timeout
                return cls()

            def session(self, **kwargs):
                calls["session"] = kwargs
                return session

        monkeypatch.setattr(_sap, "SapGui", SapGui)
        result = _spy.sap_inspect(connection=1, session=2, depth=0, max_children=3, timeout=4.5)
        assert calls == {"timeout": 4.5, "session": {"connection": 1, "session": 2}}
        assert result["limits"] == {"depth": 0, "max_children": 3}
        assert result["root"]["id"] == "wnd[0]"

    def test_sap_tree_format_and_hit_boundaries(self):
        node = {
            "type": "GuiButton",
            "id": "wnd[0]/btn",
            "name": "Button",
            "text": "Save",
            "bounding_box": {"width": 10, "height": 20},
            "children": [
                {
                    "type": "GuiText",
                    "id": "same",
                    "name": "same",
                    "text": "",
                    "bounding_box": {},
                    "children": [],
                }
            ],
        }
        assert _spy.format_sap_tree(node, color=False, indent=1) == (
            '  GuiButton wnd[0]/btn "Save" [Button] (10×20)\n    GuiText same'
        )
        assert _spy.format_sap_tree({}, color=False) == "?"
        coloured = _spy.format_sap_tree(node, color=True)
        assert all(
            token in coloured
            for token in (_spy._C_GREEN, _spy._C_YELLOW, _spy._C_DIM, _spy._C_MAGENTA)
        )
        lines: list[str] = []
        assert _spy.format_sap_tree({}, color=True, _lines=lines) == ""
        assert lines[0].startswith(_spy._C_CYAN)
        bbox = {"x": 10, "y": 20, "width": 5, "height": 4}
        assert _spy._bbox_contains(bbox, 10, 20)
        assert _spy._bbox_contains(bbox, 14, 23)
        assert not _spy._bbox_contains(bbox, 15, 23)
        assert not _spy._bbox_contains(bbox, 14, 24)

    def test_sap_sessions_and_depth_first_components(self):
        s1 = SimpleNamespace(Children=[])
        s2 = SimpleNamespace(Children=[])
        engine = SimpleNamespace(
            Children=[SimpleNamespace(Children=[s1]), SimpleNamespace(Children=[s2])]
        )
        assert _spy._sap_sessions(engine) == [s1, s2]
        a = SimpleNamespace(Children=[])
        b = SimpleNamespace(Children=[])
        root = SimpleNamespace(Children=[a, b])
        assert list(_spy._sap_iter_components(root)) == [b, a]

    def test_sap_rect_index_skips_invalid_boxes_and_refreshes_after_expiry(self, monkeypatch):
        good = _SapComponent("good", "good", "GuiText", rect=(0, 0, 10, 10))
        invalid = _SapComponent("bad", "bad", "GuiText", rect=(0, 0, 0, 10))
        index = _spy._SapRectIndex([SimpleNamespace(Children=[good, invalid])], refresh=1.0)
        clock = iter([10.0, 10.5, 10.5, 12.0, 12.0, 12.0] + [12.0] * 10)
        monkeypatch.setattr(_spy.time, "monotonic", lambda: next(clock))
        assert index.component_at(5, 5) is good
        assert index.component_at(5, 5) is good
        assert index.component_at(5, 5) is good
        assert index.component_at(20, 20) is None

        moved = _SapComponent("moved", "moved", "GuiText", rect=(100, 100, 10, 10))
        index = _spy._SapRectIndex([SimpleNamespace(Children=[moved])], refresh=60.0)
        assert index.component_at(105, 105) is moved
        moved.ScreenLeft = 500
        assert index.component_at(105, 105) is None

    def test_sap_pick_missing_pywin32_and_no_session(self, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "win32api", None)
        with pytest.raises(RuntimeError, match=r"sap_pick\(\) requires pywin32"):
            _spy.sap_pick()

        tick = {"value": 0}
        _api, _con = _install_input_modules(monkeypatch, tick=tick)
        from dolphin_desktop import _sap

        monkeypatch.setattr(
            _sap,
            "SapGui",
            SimpleNamespace(connect=lambda timeout=None: SimpleNamespace(raw=object())),
        )
        monkeypatch.setattr(_spy, "_sap_sessions", lambda engine: [])
        result = _spy.sap_pick(timeout=2.0)
        assert result["status"] == "no_session" and result["selector"] == {}
        assert "No SAP sessions" in capsys.readouterr().out

    def test_sap_pick_handles_index_errors_zero_boxes_and_cancel(self, monkeypatch):
        tick = {"value": -1}
        api, con = _install_input_modules(monkeypatch, tick=tick, escape_at=2)
        api.GetCursorPos = lambda: tick.__setitem__("value", tick["value"] + 1) or (1, 2)
        api.GetAsyncKeyState = lambda vk: (
            0x8001 if vk == con.VK_ESCAPE and tick["value"] >= 2 else 0
        )
        from dolphin_desktop import _sap

        monkeypatch.setattr(
            _sap,
            "SapGui",
            SimpleNamespace(connect=lambda timeout=None: SimpleNamespace(raw=object())),
        )
        monkeypatch.setattr(_spy, "_sap_sessions", lambda engine: [object()])
        monkeypatch.setattr(
            _spy,
            "_SapRectIndex",
            lambda sessions: SimpleNamespace(
                component_at=lambda x, y: (_ for _ in ()).throw(RuntimeError())
            ),
        )
        highlighter = SimpleNamespace(update=Mock(), clear=Mock())
        monkeypatch.setattr(_spy, "_Highlighter", lambda: highlighter)
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        monkeypatch.setattr(
            _spy, "_sap_bbox", lambda component: {"x": 0, "y": 0, "width": 0, "height": 0}
        )
        result = _spy.sap_pick()
        assert result["status"] == "cancelled"
        assert highlighter.clear.call_count >= 1

    def test_sap_pick_captures_component_with_empty_id_fallback(self, monkeypatch):
        tick = {"value": -1}
        api, con = _install_input_modules(monkeypatch, tick=tick)
        api.GetCursorPos = lambda: tick.__setitem__("value", tick["value"] + 1) or (1, 2)
        api.GetAsyncKeyState = lambda vk: 0x8000 if vk in (con.VK_LBUTTON, con.VK_CONTROL) else 0
        from dolphin_desktop import _sap

        component = _SapComponent("", "Save", "GuiButton", rect=(0, 0, 1, 1))
        monkeypatch.setattr(
            _sap,
            "SapGui",
            SimpleNamespace(connect=lambda timeout=None: SimpleNamespace(raw=object())),
        )
        monkeypatch.setattr(_spy, "_sap_sessions", lambda engine: [object()])
        monkeypatch.setattr(
            _spy,
            "_SapRectIndex",
            lambda sessions: SimpleNamespace(component_at=lambda x, y: component),
        )
        monkeypatch.setattr(
            _spy, "_Highlighter", lambda: SimpleNamespace(update=Mock(), clear=Mock())
        )
        monkeypatch.setattr(_spy, "_sap_bbox", lambda c: {"x": 0, "y": 0, "width": 1, "height": 1})
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        result = _spy.sap_pick()
        assert result["status"] == "ok" and result["selector"] == {
            "name": "Save",
            "type": "GuiButton",
        }

    def test_sap_pick_ignores_zero_sized_hover_before_cancel(self, monkeypatch):
        tick = {"value": -1}
        api, _con = _install_input_modules(monkeypatch, tick=tick, escape_at=1)
        api.GetCursorPos = lambda: tick.__setitem__("value", tick["value"] + 1) or (1, 2)
        from dolphin_desktop import _sap

        component = _SapComponent("txt", "Text", "GuiText")
        monkeypatch.setattr(
            _sap,
            "SapGui",
            SimpleNamespace(connect=lambda timeout=None: SimpleNamespace(raw=object())),
        )
        monkeypatch.setattr(_spy, "_sap_sessions", lambda engine: [object()])
        monkeypatch.setattr(
            _spy,
            "_SapRectIndex",
            lambda sessions: SimpleNamespace(component_at=lambda x, y: component),
        )
        monkeypatch.setattr(
            _spy, "_Highlighter", lambda: SimpleNamespace(update=Mock(), clear=Mock())
        )
        monkeypatch.setattr(
            _spy,
            "_sap_bbox",
            lambda c: {"x": 0, "y": 0, "width": 0, "height": 0},
        )
        monkeypatch.setattr(_spy, "_PICK_POLL", 0)
        assert _spy.sap_pick()["status"] == "cancelled"


def test_spy_formats_tree_and_selector_code_without_terminal_features() -> None:
    from dolphin_desktop._spy import _bbox_from_rect, _chain_to_code, format_tree

    node = {
        "control_type": "Window",
        "name": "Demo",
        "automation_id": "main",
        "class_name": "AppWindow",
        "bounding_box": {"width": 100, "height": 50},
        "children": [{"control_type": "Button", "name": "Save"}],
    }
    rendered = format_tree(node, color=False)
    assert 'Window "Demo"' in rendered and '  Button "Save"' in rendered
    assert _chain_to_code([{"title": "Demo"}, {"auto_id": "save"}]) == (
        "locator(title='Demo').locator(auto_id='save')"
    )
    assert _bbox_from_rect(SimpleNamespace(left=1, top=2, right=6, bottom=9)) == {
        "x": 1,
        "y": 2,
        "width": 5,
        "height": 7,
    }


def test_spy_chain_resolution_and_repair_helpers(monkeypatch) -> None:
    import dolphin_desktop._spy as spy

    def node(**kwargs):
        return SimpleNamespace(**kwargs)

    rect = SimpleNamespace(left=10, top=20, right=30, bottom=40)
    window = node(
        control_type="Window",
        name="Demo",
        automation_id="",
        class_name="MainWindow",
        parent=None,
        rectangle=rect,
    )
    pane = node(
        control_type="Pane",
        name="Toolbar",
        automation_id="",
        class_name="Toolbar",
        parent=window,
        rectangle=rect,
    )
    sibling = node(
        control_type="Button",
        name="Other",
        automation_id="",
        class_name="Button",
        parent=pane,
        rectangle=SimpleNamespace(left=0, top=0, right=5, bottom=5),
    )
    picked = node(
        control_type="Button",
        name="Save",
        automation_id="save",
        class_name="Button",
        parent=pane,
        rectangle=rect,
    )
    pane.children = lambda: [sibling, picked]
    window.children = lambda: [pane]

    owner, chain = spy._build_parent_chain(picked)
    assert owner is window
    assert chain == [{"title": "Toolbar", "control_type": "Pane"}, {"auto_id": "save"}]
    assert spy._index_among_siblings(picked) == 1
    assert spy._find_info_by_selector(window, {"title": "Save", "control_type": "Button"}) is picked

    class _Spec:
        def child_window(self, **_selector):
            return self

        def wait(self, *_args, **_kwargs):
            return self

        def wrapper_object(self):
            return SimpleNamespace(rectangle=lambda: rect)

    def desktop(**_kwargs):
        return SimpleNamespace(window=lambda **_kw: _Spec())

    monkeypatch.setattr("pywinauto.Desktop", desktop)
    assert spy._resolve_chain_to_wrapper(window, chain, "uia") is not None
    assert spy._info_rect(picked) == (10, 20, 30, 40)
    assert spy._wrapper_rect(SimpleNamespace(rectangle=lambda: rect)) == (10, 20, 30, 40)

    monkeypatch.setattr(spy, "_chain_matches_picked", Mock(side_effect=[False, True]))
    repaired, status = spy._validate_and_repair(window, chain, picked, "uia")
    assert status == "repaired"
    assert repaired[-1]["found_index"] == 1


def test_spy_selector_prefers_stable_identifiers() -> None:
    from dolphin_desktop._spy import _suggest_selector

    assert _suggest_selector("save", "Save", "Button", "Button") == {"auto_id": "save"}
    assert _suggest_selector("", "Save\tCtrl+S", "", "MenuItem") == {
        "title": "Save",
        "control_type": "MenuItem",
    }


def test_spy_tree_and_selector_helpers_handle_depth_errors_and_sap_rects() -> None:
    import dolphin_desktop._spy as spy

    rect = SimpleNamespace(left=1, top=2, right=11, bottom=22)
    leaf = SimpleNamespace(
        name="Save",
        control_type="Button",
        automation_id="save",
        class_name="ButtonClass",
        rectangle=rect,
        visible=True,
        enabled=False,
        children=lambda: [],
    )
    root = SimpleNamespace(
        name="Main",
        control_type="Window",
        automation_id="main",
        class_name="App",
        rectangle=rect,
        visible=True,
        enabled=True,
        children=lambda: [leaf],
    )
    node = spy._node_from_element_info(root, depth=1)
    assert node.suggested_selector == {"auto_id": "main"}
    assert node.children[0].suggested_selector == {"auto_id": "save"}
    assert spy._node_to_dict(node)["children"][0]["name"] == "Save"
    assert spy._bbox_from_rect(object()) == {"x": 0, "y": 0, "width": 0, "height": 0}
    assert spy._suggest_selector("", "", "", "") == {}
    assert spy._selector_to_code({}) == "locator()  # no selector found"
    assert spy._chain_to_code([]) == "locator()  # no selector found"

    window = SimpleNamespace(
        control_type="Window",
        parent=None,
        name="Main",
        automation_id="main",
        class_name="App",
    )
    pane = SimpleNamespace(
        control_type="Pane",
        parent=window,
        name="",
        automation_id="",
        class_name="",
    )
    button = SimpleNamespace(
        control_type="Button",
        parent=pane,
        name="Save",
        automation_id="save",
        class_name="Button",
    )
    owner, chain = spy._build_parent_chain(button)
    assert owner is window
    assert chain == [{"auto_id": "save"}]

    component = SimpleNamespace(
        Id="/app/con[0]/ses[0]/wnd[0]/usr",
        Name="usr",
        Type="GuiUserArea",
        Text="",
        ScreenLeft=10,
        ScreenTop=20,
        Width=100,
        Height=50,
        Changeable=False,
        Children=[],
    )
    sap_node = spy._sap_node_from_component(component, depth=0)
    assert sap_node.id == "wnd[0]/usr"
    assert sap_node.suggested_selector == {"id": "wnd[0]/usr"}
    assert spy._sap_selector_to_code({"name": "Save", "type": "GuiButton"}) == (
        "locator(name='Save', type='GuiButton')"
    )
    assert spy._sap_bbox(object()) == {"x": 0, "y": 0, "width": 0, "height": 0}


def test_spy_sap_rect_index_returns_smallest_live_component() -> None:
    from dolphin_desktop._spy import _SapRectIndex

    child = SimpleNamespace(
        ScreenLeft=20,
        ScreenTop=20,
        Width=10,
        Height=10,
        Children=[],
    )
    parent = SimpleNamespace(
        ScreenLeft=0,
        ScreenTop=0,
        Width=100,
        Height=100,
        Children=[child],
    )
    index = _SapRectIndex([SimpleNamespace(Children=[parent])], refresh=60)
    index.refresh()
    assert index.component_at(25, 25) is child
    assert index.component_at(150, 150) is None
