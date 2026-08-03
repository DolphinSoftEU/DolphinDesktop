"""Tests for the object spy's generated code, tree budget and pick result contract."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dolphin_desktop import _spy

_NOOP_HIGHLIGHT = {"update": lambda bbox: None, "clear": lambda: None}

# Generated locator code must be valid Python


class TestSelectorCodeEscaping:
    @pytest.mark.parametrize(
        "value",
        [r"C:\Users\foo", 'Say "hi"', "it's", "tab\there", "back\\slash"],
    )
    def test_uia_selector_values_survive_escaping(self, value):
        code = _spy._selector_to_code({"title": value})
        compile(f"window.{code}", "<gen>", "eval")

    def test_uia_chain_survives_escaping(self):
        chain = [{"title": r"C:\Users"}, {"title": 'Say "hi"', "control_type": "Button"}]
        compile(f"window.{_spy._chain_to_code(chain)}", "<gen>", "eval")

    def test_integer_criteria_stay_unquoted(self):
        assert _spy._selector_to_code({"found_index": 2}) == "locator(found_index=2)"

    @pytest.mark.parametrize("value", [r"wnd[0]\usr", 'wnd[0]/usr/txt"X"'])
    def test_sap_selector_values_survive_escaping(self, value):
        compile(f"session.{_spy._sap_selector_to_code({'id': value})}", "<gen>", "eval")

    def test_sap_name_and_type_survive_escaping(self):
        code = _spy._sap_selector_to_code({"name": 'a"b', "type": r"GuiText\x"})
        compile(f"session.{code}", "<gen>", "eval")


# Tree walk budget — an unbounded walk of a Chromium a11y tree never returns


class _FakeInfo:
    """Element info with a fixed fan-out and unbounded depth."""

    def __init__(self, fan_out: int, visited: list[int], level: int = 0) -> None:
        self.name = f"n{level}"
        self.control_type = "Pane"
        self.automation_id = ""
        self.class_name = ""
        self.rectangle = None
        self.visible = True
        self.enabled = True
        self._fan_out = fan_out
        self._visited = visited
        self._level = level

    def children(self) -> list[_FakeInfo]:
        self._visited.append(self._level)
        return [
            _FakeInfo(self._fan_out, self._visited, self._level + 1) for _ in range(self._fan_out)
        ]


def _max_depth(node: _spy._NodeInfo, level: int = 0) -> int:
    if not node.children:
        return level
    return max(_max_depth(child, level + 1) for child in node.children)


class TestTreeBudget:
    def test_depth_is_capped_by_default(self):
        root = _spy._node_from_element_info(_FakeInfo(1, []), depth=_spy._DEFAULT_DEPTH)
        assert _max_depth(root) == _spy._DEFAULT_DEPTH

    def test_children_are_capped_by_default(self):
        root = _spy._node_from_element_info(_FakeInfo(500, []), depth=1)
        assert len(root.children) == _spy._DEFAULT_MAX_CHILDREN

    def test_caps_are_overridable(self):
        root = _spy._node_from_element_info(_FakeInfo(3, []), depth=2, max_children=2)
        assert len(root.children) == 2
        assert _max_depth(root) == 2

    def test_inspect_defaults_are_bounded(self):
        import inspect as _inspect

        params = _inspect.signature(_spy.inspect).parameters
        assert params["depth"].default == _spy._DEFAULT_DEPTH
        assert params["max_children"].default == _spy._DEFAULT_MAX_CHILDREN

    def test_sap_inspect_defaults_are_bounded(self):
        import inspect as _inspect

        params = _inspect.signature(_spy.sap_inspect).parameters
        assert params["depth"].default == _spy._DEFAULT_DEPTH
        assert params["max_children"].default == _spy._DEFAULT_MAX_CHILDREN


# pick() must be machine-readable for a non-Python front-end


class TestPickResultContract:
    @pytest.mark.parametrize(
        "status", ["ok", "repaired", "repaired_flat", "unverified", "unreachable", "cancelled"]
    )
    def test_status_is_carried_in_the_result(self, status):
        result = _spy._pick_result(status, [{"title": "OK"}])
        assert result["status"] == status
        assert result["schema_version"] == _spy.SCHEMA_VERSION
        assert result["chain"] == [{"title": "OK"}]

    def test_unverified_statuses_carry_a_message(self):
        for status in ("repaired", "repaired_flat", "unverified", "unreachable"):
            assert _spy._pick_result(status, []).get("message")

    def test_ok_carries_no_warning(self):
        assert _spy._pick_result("ok", []).get("message") == ""

    def test_sap_pick_result_shape(self):
        result = _spy._sap_pick_result("ok", {"id": "wnd[0]/usr/txt"})
        assert result["schema_version"] == _spy.SCHEMA_VERSION
        assert result["status"] == "ok"
        assert result["selector"] == {"id": "wnd[0]/usr/txt"}

    def test_no_session_is_distinguishable_from_cancelled(self):
        assert _spy._sap_pick_result("no_session", {})["message"]
        assert _spy._sap_pick_result("cancelled", {})["message"] == ""


# Template capture must reach a secondary monitor and must not need a FIPS-banned hash


class _Rect:
    left, top, right, bottom = 10, 20, 110, 60


@pytest.fixture
def one_shot_image_pick(monkeypatch, tmp_path):
    """Drive image_pick() through exactly one poll tick ending in a Ctrl+Click."""
    win32api = pytest.importorskip("win32api")
    win32con = pytest.importorskip("win32con")
    from PIL import ImageGrab

    seen: dict = {}

    class _FakeImage:
        def save(self, path):
            Path(path).write_bytes(b"png")

    def _grab(**kwargs):
        seen.update(kwargs)
        return _FakeImage()

    def _async_key_state(vk):
        return 0x8000 if vk in (win32con.VK_LBUTTON, win32con.VK_CONTROL) else 0

    class _NoHighlight:
        def update(self, bbox):
            pass

        def clear(self):
            pass

    monkeypatch.setattr(win32api, "GetCursorPos", lambda: (50, 30))
    monkeypatch.setattr(win32api, "GetAsyncKeyState", _async_key_state)
    monkeypatch.setattr(_spy, "_Highlighter", _NoHighlight)
    monkeypatch.setattr(
        _spy, "_element_info_from_point", lambda x, y: SimpleNamespace(rectangle=_Rect, name="btn")
    )
    monkeypatch.setattr(ImageGrab, "grab", _grab)
    return seen, tmp_path


class TestImagePickCapture:
    def test_grab_spans_every_monitor(self, one_shot_image_pick):
        """Without all_screens a template on a secondary display captures black pixels."""
        seen, tmp_path = one_shot_image_pick
        assert _spy.image_pick(output_dir=tmp_path) is not None
        assert seen["all_screens"] is True

    def test_grab_region_is_the_element_rect(self, one_shot_image_pick):
        seen, tmp_path = one_shot_image_pick
        _spy.image_pick(output_dir=tmp_path)
        assert seen["bbox"] == (10, 20, 110, 60)

    def test_filename_hash_is_not_flagged_as_security_use(self, one_shot_image_pick, monkeypatch):
        """A FIPS-mode host refuses md5 unless it is declared non-security."""
        import hashlib

        seen_kwargs: dict = {}
        real_md5 = hashlib.md5

        def _md5(data=b"", **kwargs):
            seen_kwargs.update(kwargs)
            return real_md5(data, **kwargs)

        monkeypatch.setattr(hashlib, "md5", _md5)
        _, tmp_path = one_shot_image_pick
        _spy.image_pick(output_dir=tmp_path)
        assert seen_kwargs == {"usedforsecurity": False}


# The SAP hit test may not re-walk the component tree on every poll tick


class _FakeSapComponent:
    def __init__(self, name: str, rect: tuple[int, int, int, int], children=()) -> None:
        self.Name = name
        self.Id = f"/app/con[0]/ses[0]/{name}"
        self.Type = "GuiTextField"
        self.Text = ""
        self.ScreenLeft, self.ScreenTop, self.Width, self.Height = rect
        self.Children = list(children)


class TestSapRectIndex:
    def _index(self, monkeypatch, sessions, reads: list[int], walks: list[int] | None = None):
        def _counting_bbox(component):
            reads.append(1)
            return {
                "x": component.ScreenLeft,
                "y": component.ScreenTop,
                "width": component.Width,
                "height": component.Height,
            }

        def _walk(root):
            if walks is not None:
                walks.append(1)
            return iter(root.Children)

        monkeypatch.setattr(_spy, "_sap_bbox", _counting_bbox)
        monkeypatch.setattr(_spy, "_sap_iter_components", _walk)
        return _spy._SapRectIndex(sessions)

    def _session(self):
        return _FakeSapComponent(
            "ses",
            (0, 0, 0, 0),
            children=[
                _FakeSapComponent("wnd", (0, 0, 800, 600)),
                _FakeSapComponent("txt", (10, 10, 100, 20)),
            ],
        )

    def test_repeated_polls_do_not_re_walk_the_tree(self, monkeypatch):
        walks: list[int] = []
        index = self._index(monkeypatch, [self._session()], [], walks)
        index.component_at(20, 15)
        after_first = len(walks)
        for _ in range(50):
            index.component_at(20, 15)
        assert len(walks) == after_first

    def test_a_poll_confirms_only_the_hit_not_the_whole_index(self, monkeypatch):
        """Confirming the hit is a handful of property reads, not another tree walk."""
        reads: list[int] = []
        index = self._index(monkeypatch, [self._session()], reads)
        index.component_at(20, 15)
        del reads[:]
        index.component_at(20, 15)
        assert len(reads) <= 2

    def test_smallest_containing_component_wins(self, monkeypatch):
        index = self._index(monkeypatch, [self._session()], [])
        assert index.component_at(20, 15).Name == "txt"

    def test_point_outside_every_rect_returns_none(self, monkeypatch):
        index = self._index(monkeypatch, [self._session()], [])
        assert index.component_at(5000, 5000) is None

    def test_a_component_that_moved_since_the_walk_is_not_reported(self, monkeypatch):
        """The index is up to _SAP_INDEX_REFRESH seconds old; the highlight is not."""
        session = self._session()
        index = self._index(monkeypatch, [session], [])
        assert index.component_at(20, 15).Name == "txt"

        moved = next(c for c in session.Children if c.Name == "txt")
        moved.ScreenTop = 400
        assert index.component_at(20, 15).Name == "wnd"

    def test_explicit_refresh_re_walks(self, monkeypatch):
        reads: list[int] = []
        index = self._index(monkeypatch, [self._session()], reads)
        index.component_at(20, 15)
        after_first = len(reads)
        index.refresh()
        assert len(reads) > after_first


# Ctrl+Click captures what the cursor is over, or nothing at all


class _ScriptedIndex:
    """Returns one scripted hit-test result per poll tick."""

    def __init__(self, hits: list) -> None:
        self._hits = hits
        self.tick = -1

    def component_at(self, x: int, y: int):
        return self._hits[min(self.tick, len(self._hits) - 1)]


class TestSapPickCapture:
    def _pick(self, monkeypatch, hits: list, click_tick: int) -> dict:
        index = _ScriptedIndex(hits)
        # Esc one tick after the click so a picker that refuses to capture terminates.
        esc_tick = click_tick + 1

        def _key_state(vk: int) -> int:
            if vk == "esc":
                return 0x8001 if index.tick >= esc_tick else 0
            return 0x8000 if index.tick == click_tick else 0

        def _cursor_pos():
            index.tick += 1
            return (20, 15)

        win32api = SimpleNamespace(GetCursorPos=_cursor_pos, GetAsyncKeyState=_key_state)
        win32con = SimpleNamespace(VK_ESCAPE="esc", VK_LBUTTON="lbutton", VK_CONTROL="ctrl")
        monkeypatch.setitem(sys.modules, "win32api", win32api)
        monkeypatch.setitem(sys.modules, "win32con", win32con)

        from dolphin_desktop import _sap

        monkeypatch.setattr(
            _sap,
            "SapGui",
            SimpleNamespace(connect=lambda timeout=None: SimpleNamespace(raw=object())),
            raising=False,
        )
        monkeypatch.setattr(_spy, "_sap_sessions", lambda engine: ["session"])
        monkeypatch.setattr(_spy, "_SapRectIndex", lambda sessions: index)
        monkeypatch.setattr(_spy, "_Highlighter", lambda: SimpleNamespace(**_NOOP_HIGHLIGHT))
        monkeypatch.setattr(_spy, "_sap_bbox", lambda c: {"x": 0, "y": 0, "width": 1, "height": 1})
        monkeypatch.setattr(_spy, "_PICK_POLL", 0.0)
        return _spy.sap_pick()

    def test_ctrl_click_captures_the_hovered_component(self, monkeypatch):
        component = _FakeSapComponent("txt", (10, 10, 100, 20))
        result = self._pick(monkeypatch, [component], click_tick=1)
        assert result["status"] == "ok"
        assert result["selector"] == {"id": "txt"}

    def test_ctrl_click_over_empty_space_captures_nothing(self, monkeypatch):
        """The last hovered control must not be captured once the cursor left it."""
        component = _FakeSapComponent("txt", (10, 10, 100, 20))
        result = self._pick(monkeypatch, [component, None], click_tick=1)
        assert result["status"] == "cancelled"
        assert result["selector"] == {}
