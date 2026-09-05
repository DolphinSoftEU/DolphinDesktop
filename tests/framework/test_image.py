"""Tests for :mod:`dolphin_desktop._image`.

The project deliberately keeps OpenCV and Tesseract optional.  These tests
use tiny dependency doubles so the matching and OCR control flow is exercised
even on a base installation where numpy/cv2 are not present.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from PIL import Image

import dolphin_desktop._image as image
from dolphin_desktop._exceptions import WaitTimeoutError


class _Array:
    def __init__(self, shape: tuple[int, ...], values: list[float] | None = None) -> None:
        self.shape = shape
        self.values = values or []


class _CV2:
    TM_SQDIFF_NORMED = 1
    TM_CCOEFF_NORMED = 2
    IMREAD_COLOR = 3
    COLOR_RGB2BGR = 4

    def __init__(self, result: object = "result") -> None:
        self.result = result
        self.calls: list[tuple[str, object]] = []

    def resize(self, arr: _Array, size: tuple[int, int]) -> _Array:
        self.calls.append(("resize", size))
        return _Array((size[1], size[0]))

    def matchTemplate(self, screen, tmpl, method):  # noqa: N802
        self.calls.append(("matchTemplate", method))
        return self.result

    def minMaxLoc(self, result):  # noqa: N802
        return (0.1, 0.95, (2, 3), (4, 5))

    def imdecode(self, data, mode):
        self.calls.append(("imdecode", mode))
        return _Array((4, 5, 3))

    def cvtColor(self, arr, mode):  # noqa: N802
        self.calls.append(("cvtColor", mode))
        return (arr, mode)

    def dilate(self, scores, kernel):
        return scores


class _Values:
    def __init__(self, values: list[float]) -> None:
        self.values = values
        self.size = len(values)

    def __neg__(self):
        return self

    def __getitem__(self, index):
        if isinstance(index, _Index):
            return _Values([self.values[i] for i in index.values])
        if isinstance(index, slice):
            return _Values(self.values[index])
        return self.values[index]


class _Index:
    def __init__(self, values: list[int]) -> None:
        self.values = values
        self.size = len(values)

    def __getitem__(self, index):
        if isinstance(index, _Index):
            return _Index([self.values[i] for i in index.values])
        if isinstance(index, slice):
            return _Index(self.values[index])
        return self.values[index]

    def __len__(self):
        return len(self.values)

    def tolist(self):
        return list(self.values)


class _Numeric:
    def __init__(self, size: int) -> None:
        self.values = [0] * size
        self.size = size

    def __setitem__(self, index, value) -> None:
        self.values[index] = value

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _Vector(self.values[index])
        return self.values[index]


class _Vector:
    def __init__(self, values: list[int]) -> None:
        self.values = values

    def __sub__(self, value: int):
        return _Vector([v - value for v in self.values])

    def __abs__(self):
        return _Vector([abs(v) for v in self.values])

    def __lt__(self, value: int):
        return _Vector([v < value for v in self.values])

    def __and__(self, other):
        return _Vector([a and b for a, b in zip(self.values, other.values, strict=False)])


class _Scores:
    def __ge__(self, other):
        return self

    def __rsub__(self, other):
        return self

    def __and__(self, other):
        return self

    def __getitem__(self, key):
        count = len(key[0]) if isinstance(key, tuple) else 3
        return _Values([0.97 - (i * 0.01) for i in range(count)])


class _Numpy:
    uint8 = object()
    int64 = object()

    @staticmethod
    def array(value):
        return ("array", value)

    @staticmethod
    def fromfile(path, dtype):
        return ("bytes", path, dtype)

    @staticmethod
    def std(value):
        return 0.0

    @staticmethod
    def ones(shape, dtype):
        return (shape, dtype)

    @staticmethod
    def where(condition):
        return _Index([0, 1]), _Index([0, 12])

    @staticmethod
    def argpartition(values, count):
        return _Index(list(range(min(count, values.size))))

    @staticmethod
    def argsort(values):
        return _Index(list(range(values.size)))

    @staticmethod
    def empty(size, dtype):
        return _Numeric(size)

    @staticmethod
    def abs(value):
        return abs(value)

    @staticmethod
    def any(value):
        return any(value.values)


def _install_numpy(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "numpy", _Numpy)


def test_optional_import_success_paths_are_executable() -> None:
    previous = {name: sys.modules.get(name) for name in ("cv2", "pytesseract")}
    present = {name: name in sys.modules for name in previous}
    sys.modules["cv2"] = object()
    sys.modules["pytesseract"] = object()
    try:
        reloaded = importlib.reload(image)
        assert reloaded._CV2_ERROR is None
        assert reloaded._TESS_ERROR is None
    finally:
        for name, was_present in present.items():
            if was_present:
                sys.modules[name] = previous[name]
            else:
                sys.modules.pop(name, None)
        importlib.reload(image)


def test_dependency_guards_capture_install_hints(monkeypatch) -> None:
    monkeypatch.setattr(image, "_CV2_ERROR", ImportError("cv2 missing"))
    monkeypatch.setattr(image, "_cv2", None)
    with pytest.raises(RuntimeError, match="opencv-python"):
        image._require_cv2()
    monkeypatch.setattr(image, "_TESS_ERROR", ImportError("tesseract missing"))
    monkeypatch.setattr(image, "_pytesseract", None)
    with pytest.raises(RuntimeError, match="pytesseract"):
        image._require_tesseract()

    cv2 = object()
    tess = object()
    monkeypatch.setattr(image, "_CV2_ERROR", None)
    monkeypatch.setattr(image, "_cv2", cv2)
    monkeypatch.setattr(image, "_TESS_ERROR", None)
    monkeypatch.setattr(image, "_pytesseract", tess)
    assert image._require_cv2() is cv2
    assert image._require_tesseract() is tess


def test_capture_origins_and_pil_conversion(monkeypatch) -> None:
    with patch("PIL.ImageGrab.grab") as grab:
        image._grab((1, 2, 3, 4))
    grab.assert_called_once_with(bbox=(1, 2, 3, 4), all_screens=True)
    assert image._grab_origin((5, 6, 7, 8)) == (5, 6)
    monkeypatch.setattr(image, "_virtual_origin", lambda: (-10, -20))
    assert image._grab_origin(None) == (-10, -20)
    assert image._virtual_origin() == (-10, -20)

    _install_numpy(monkeypatch)
    cv2 = _CV2()
    monkeypatch.setattr(image, "_CV2_ERROR", None)
    monkeypatch.setattr(image, "_cv2", cv2)
    result = image._pil_to_cv(Image.new("RGB", (2, 3), (1, 2, 3)))
    assert result[1] == cv2.COLOR_RGB2BGR
    assert image._is_low_variance_image(object()) is True


def test_virtual_origin_falls_back_when_win32_api_fails(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "win32api", Mock(GetSystemMetrics=Mock(side_effect=OSError())))
    monkeypatch.setitem(
        sys.modules,
        "win32con",
        SimpleNamespace(SM_XVIRTUALSCREEN=1, SM_YVIRTUALSCREEN=2),
    )
    assert image._virtual_origin() == (0, 0)


def test_image_element_proxy_delegates_every_supported_operation(monkeypatch) -> None:
    element = image._ImageElement(10, 20, tw=0, th=-4)
    monkeypatch.setattr(image, "_grab", Mock(return_value=Image.new("RGB", (1, 1))))
    with (
        patch("pywinauto.mouse.click") as click,
        patch("pywinauto.mouse.double_click") as double_click,
        patch("pywinauto.mouse.right_click") as right_click,
        patch("pywinauto.mouse.move") as move,
        patch("pywinauto.keyboard.send_keys") as send_keys,
    ):
        element.click_input()
        element.double_click_input()
        element.right_click_input()
        element.move_mouse_input()
        element.move_mouse_input((1, 2))
        element.type_keys("a", with_spaces=True, unknown=True)
        element.set_focus()
        element.type_keys("b")
    assert click.call_count == 3
    assert double_click.call_args == call(coords=(10, 20))
    assert right_click.call_args == call(coords=(10, 20))
    assert move.call_args == call(coords=(1, 2))
    send_keys.assert_has_calls([call("a", with_spaces=True), call("b")])
    assert element.rectangle().right == 11
    assert element.window_text() == ""
    with pytest.raises(AttributeError):
        element.get_value()
    assert element.is_visible() is True
    assert element.is_enabled() is True
    assert element.wait() is element
    assert element.scroll_into_view() is None
    assert element.capture_as_image() is not None


def test_template_loading_and_matching_strategies(monkeypatch, tmp_path: Path) -> None:
    _install_numpy(monkeypatch)
    cv2 = _CV2()
    monkeypatch.setattr(image, "_CV2_ERROR", None)
    monkeypatch.setattr(image, "_cv2", cv2)
    path = tmp_path / "template.png"
    path.write_bytes(b"png")
    locator = image.ImageLocator(path, threshold=0.9, scales=[0.5, 1.0])
    assert locator._load_template().shape == (4, 5, 3)
    missing = image.ImageLocator(tmp_path / "missing.png")
    with pytest.raises(FileNotFoundError):
        missing._load_template()
    cv2.imdecode = Mock(return_value=None)
    with pytest.raises(ValueError, match="decoded"):
        locator._load_template()

    cv2.imdecode = _CV2().imdecode
    screen = _Array((10, 12, 3))
    tmpl = _Array((4, 5, 3))
    monkeypatch.setattr(image, "_is_low_variance_image", lambda _: True)
    assert locator._match(screen, tmpl, 0.5)[:3] == ((2, 3), 2, 2)
    locator._threshold = 1.0
    assert locator._match(screen, tmpl, 1.0)[0] is None
    assert locator._match(_Array((2, 2, 3)), tmpl, 1.0)[0] is None
    monkeypatch.setattr(image, "_is_low_variance_image", lambda _: False)
    locator._threshold = 0.9
    assert locator._match(screen, tmpl, 1.0)[0] == (4, 5)
    locator._threshold = 1.0
    assert locator._match(screen, tmpl, 1.0)[0] is None


def test_find_find_with_size_and_find_impl_choose_best_scale(monkeypatch) -> None:
    locator = image.ImageLocator("template", scales=[1.0, 2.0], region=(1, 2, 10, 20))
    monkeypatch.setattr(locator, "_load_template", Mock(return_value=_Array((5, 6, 3))))
    monkeypatch.setattr(image, "_pil_to_cv", Mock(return_value=_Array((30, 40, 3))))
    monkeypatch.setattr(image, "_grab", Mock(return_value=object()))
    monkeypatch.setattr(image, "_grab_origin", Mock(return_value=(100, 200)))
    monkeypatch.setattr(
        locator,
        "_match",
        Mock(side_effect=[((1, 2), 6, 5, 0.8), ((3, 4), 12, 10, 0.95)]),
    )
    assert locator.find() == (109, 209)
    locator._match.side_effect = [((1, 2), 6, 5, 0.8), (None, 12, 10, 0.0)]
    assert locator.find_with_size((5, 6, 20, 30)) == (104, 204, 6, 5)
    locator._match.side_effect = [(None, 6, 5, 0.0), (None, 12, 10, 0.0)]
    assert locator.find() is None


def test_find_all_suppresses_overlap_and_handles_scale_and_small_screen(monkeypatch) -> None:
    _install_numpy(monkeypatch)
    cv2 = _CV2(result=_Scores())
    monkeypatch.setattr(image, "_CV2_ERROR", None)
    monkeypatch.setattr(image, "_cv2", cv2)
    locator = image.ImageLocator("template", threshold=0.8, scales=[2.0], region=(10, 20, 100, 100))
    monkeypatch.setattr(locator, "_load_template", Mock(return_value=_Array((4, 5, 3))))
    monkeypatch.setattr(image, "_pil_to_cv", Mock(return_value=_Array((30, 40, 3))))
    monkeypatch.setattr(image, "_grab", Mock(return_value=object()))
    monkeypatch.setattr(image, "_grab_origin", Mock(return_value=(10, 20)))
    monkeypatch.setattr(image, "_is_low_variance_image", lambda _: False)
    assert locator.find_all() == [(15, 24), (27, 25)]
    monkeypatch.setattr(image, "_is_low_variance_image", lambda _: True)
    assert locator.find_all() == [(15, 24), (27, 25)]
    monkeypatch.setattr(image, "_pil_to_cv", Mock(return_value=_Array((2, 2, 3))))
    assert locator.find_all() == []


def test_find_all_candidate_cap_is_applied(monkeypatch) -> None:
    _install_numpy(monkeypatch)

    def many_where(condition):
        values = [0] * 10_001
        return _Index(values), _Index(values)

    monkeypatch.setattr(_Numpy, "where", staticmethod(many_where))
    cv2 = _CV2(result=_Scores())
    monkeypatch.setattr(image, "_CV2_ERROR", None)
    monkeypatch.setattr(image, "_cv2", cv2)
    locator = image.ImageLocator("template", threshold=0.8)
    monkeypatch.setattr(locator, "_load_template", Mock(return_value=_Array((2, 2, 3))))
    monkeypatch.setattr(image, "_pil_to_cv", Mock(return_value=_Array((20_000, 20_000, 3))))
    monkeypatch.setattr(image, "_grab", Mock(return_value=object()))
    monkeypatch.setattr(image, "_grab_origin", Mock(return_value=(0, 0)))
    monkeypatch.setattr(image, "_is_low_variance_image", lambda _: False)
    points = locator.find_all()
    assert len(points) == 1


def test_click_double_click_wait_exists_and_element(monkeypatch) -> None:
    locator = image.ImageLocator("button.png")
    monkeypatch.setattr(locator, "find", Mock(side_effect=[(4, 5), None, (6, 7), None]))
    with (
        patch("pywinauto.mouse.click") as click,
        patch("pywinauto.mouse.double_click") as double_click,
    ):
        locator.click()
        with pytest.raises(RuntimeError, match="not found"):
            locator.double_click()
        locator.double_click()
        with pytest.raises(RuntimeError):
            locator.click()
    assert click.call_args == call(coords=(4, 5))
    assert double_click.call_args == call(coords=(6, 7))

    locator.find = Mock(side_effect=[None, (8, 9)])
    monkeypatch.setattr(image.time, "monotonic", Mock(side_effect=[0, 0.1, 1]))
    monkeypatch.setattr(image.time, "sleep", Mock())
    assert locator.wait_for(timeout=0.5) == (8, 9)
    locator.find = Mock(return_value=None)
    monkeypatch.setattr(image.time, "monotonic", Mock(side_effect=[0, 1]))
    with pytest.raises(WaitTimeoutError, match="not found"):
        locator.wait_for(timeout=0.5)
    locator.find = Mock(return_value=(1, 2))
    assert locator.exists() is True
    locator.find = Mock(return_value=None)
    assert locator.exists() is False
    locator.wait_for = Mock(return_value=(1, 2))
    assert locator.exists(timeout=1) is True
    locator.wait_for.side_effect = WaitTimeoutError("no")
    assert locator.exists(timeout=1) is False
    locator.find_with_size = Mock(return_value=(10, 20, 4, 6))
    assert locator.as_element().rectangle().left == 8
    locator.find_with_size.return_value = None
    assert locator.as_element() is None


def test_screen_convenience_methods_and_ocr_retries(monkeypatch) -> None:
    screenshot = Image.new("RGB", (10, 10), (10, 20, 30))
    monkeypatch.setattr(image, "_grab", Mock(return_value=screenshot))
    assert image.Screen.screenshot((1, 2, 3, 4)) is screenshot
    assert image.Screen.pixel_color(4, 5) == (10, 20, 30)
    locator_type = Mock()
    locator_type.return_value.find.return_value = (7, 8)
    monkeypatch.setattr(image, "ImageLocator", locator_type)
    assert image.Screen.find_image("x.png", 0.7, (1, 2, 3, 4)) == (7, 8)
    locator_type.assert_called_once_with("x.png", 0.7)

    tess = Mock()
    tess.image_to_string.return_value = "OCR"
    monkeypatch.setattr(image, "_require_tesseract", lambda: tess)
    assert image.Screen.text() == "OCR"
    tess.Output.DICT = "dict"
    tess.image_to_data.side_effect = [
        {"text": ["noise"], "left": [0], "top": [0], "width": [1], "height": [1]},
        {"text": ["Save"], "left": [20], "top": [10], "width": [10], "height": [6]},
    ]
    monkeypatch.setattr(image, "_grab_origin", lambda region: (100, 200))
    assert image.Screen.find_text("av", region=(100, 200, 300, 400), scales=(1, 2)) == (112, 206)
    tess.image_to_data.side_effect = [
        {"text": ["noise"], "left": [0], "top": [0], "width": [1], "height": [1]}
    ]
    assert image.Screen.find_text("missing", scales=(1,)) is None


def test_image_element_geometry_wait_and_text_limitations() -> None:
    from dolphin_desktop._image import _ImageElement

    element = _ImageElement(10, 20, tw=4, th=6)
    rect = element.rectangle()
    assert (rect.left, rect.top, rect.right, rect.bottom) == (8, 17, 12, 23)
    assert element.wait("visible") is element
    assert element.is_visible() and element.is_enabled()
    with pytest.raises(NotImplementedError):
        element.set_edit_text("text")


def test_image_locator_actions_proxy_and_timeout(monkeypatch) -> None:
    import dolphin_desktop._image as image

    locator = image.ImageLocator("button.png", region=(1, 2, 30, 40))
    locator._find_impl = Mock(return_value=(11, 12, 6, 8))

    assert locator.find() == (11, 12)
    assert locator.find((5, 6, 20, 21)) == (11, 12)
    assert locator.find_with_size() == (11, 12, 6, 8)
    assert locator.exists() is True

    element = locator.as_element()
    assert element is not None
    rect = element.rectangle()
    assert (rect.left, rect.top, rect.right, rect.bottom) == (8, 8, 14, 16)

    with patch("pywinauto.mouse.click") as click, patch("pywinauto.mouse.double_click") as double:
        locator.click()
        locator.double_click(region=(7, 8, 9, 10))
    click.assert_called_once_with(coords=(11, 12))
    double.assert_called_once_with(coords=(11, 12))

    locator._find_impl = Mock(return_value=None)
    assert locator.as_element() is None
    assert locator.exists() is False
    with pytest.raises(RuntimeError, match="not found"):
        locator.click()
    from dolphin_desktop._exceptions import WaitTimeoutError

    with pytest.raises(WaitTimeoutError):
        locator.wait_for(timeout=0)


def test_screen_ocr_and_image_convenience_wrappers(monkeypatch) -> None:
    import dolphin_desktop._image as image

    screenshot = Image.new("RGB", (10, 10), (10, 20, 30))
    tess = SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=Mock(return_value="screen text"),
        image_to_data=Mock(
            return_value={
                "text": ["ignore", "Save"],
                "left": [0, 2],
                "top": [0, 3],
                "width": [1, 4],
                "height": [1, 4],
            }
        ),
    )
    monkeypatch.setattr(image, "_grab", Mock(return_value=screenshot))
    monkeypatch.setattr(image, "_grab_origin", lambda region: (100, 200))
    monkeypatch.setattr(image, "_require_tesseract", lambda: tess)

    assert image.Screen.screenshot((1, 2, 3, 4)) is screenshot
    assert image.Screen.pixel_color(4, 5) == (10, 20, 30)
    assert image.Screen.text() == "screen text"
    assert image.Screen.find_text("save", (100, 200, 110, 210), scales=(1, 2)) == (104, 205)
    assert image.Screen.find_text("missing", scales=(1,)) is None

    fake_locator = Mock()
    fake_locator.find.return_value = (7, 8)
    locator_type = Mock(return_value=fake_locator)
    monkeypatch.setattr(image, "ImageLocator", locator_type)
    assert image.Screen.find_image("save.png", 0.9, (1, 2, 3, 4)) == (7, 8)
    locator_type.assert_called_once_with("save.png", 0.9)
    fake_locator.find.assert_called_once_with((1, 2, 3, 4))


def test_image_rect_and_variance_helpers_are_headless() -> None:
    from dolphin_desktop._image import _ImageRect

    rect = _ImageRect(1, 2, 6, 8)
    assert (rect.left, rect.top, rect.right, rect.bottom) == (1, 2, 6, 8)
