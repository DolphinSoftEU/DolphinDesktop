"""Tests for ImageLocator, Screen, window.image() and image failover chain."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from dolphin_desktop import ImageLocator, Screen
from dolphin_desktop._image import _ImageElement

# _ImageElement unit tests (no cv2 required)


class TestImageElement:
    def test_rectangle_reflects_template_size(self):
        el = _ImageElement(cx=100, cy=200, tw=40, th=20)
        rect = el.rectangle()
        assert rect.left == 100 - 20
        assert rect.top == 200 - 10
        assert rect.right == rect.left + 40
        assert rect.bottom == rect.top + 20

    def test_booleans(self):
        el = _ImageElement(50, 50)
        assert el.is_visible() is True
        assert el.is_enabled() is True

    def test_window_text_returns_empty(self):
        assert _ImageElement(0, 0).window_text() == ""

    def test_get_value_raises(self):
        with pytest.raises(AttributeError):
            _ImageElement(0, 0).get_value()

    def test_set_edit_text_raises(self):
        with pytest.raises(NotImplementedError):
            _ImageElement(0, 0).set_edit_text("x")

    def test_wait_returns_self(self):
        el = _ImageElement(10, 20)
        assert el.wait("visible") is el

    def test_scroll_into_view_is_noop(self):
        # A template match has no scrollable container: unlike wait(), the call
        # returns nothing at all rather than a chainable element.
        assert _ImageElement(0, 0).scroll_into_view() is None

    def test_click_input_delegates_to_mouse(self):
        el = _ImageElement(cx=300, cy=400)
        with patch("pywinauto.mouse.click") as mock_click:
            el.click_input()
            mock_click.assert_called_once_with(coords=(300, 400))

    def test_type_keys_forwards_kwargs_to_send_keys(self):
        """Dropping with_spaces turns 'hello world' into 'helloworld'."""
        el = _ImageElement(cx=5, cy=6)
        with (
            patch("pywinauto.mouse.click"),
            patch("pywinauto.keyboard.send_keys") as mock_send,
        ):
            el.type_keys("hello world", with_spaces=True, pause=0.05)
        mock_send.assert_called_once_with("hello world", with_spaces=True, pause=0.05)

    def test_type_keys_ignores_wrapper_only_kwargs(self):
        el = _ImageElement(cx=5, cy=6)
        with (
            patch("pywinauto.mouse.click"),
            patch("pywinauto.keyboard.send_keys") as mock_send,
        ):
            el.type_keys("x", with_spaces=True, set_foreground=True)
        mock_send.assert_called_once_with("x", with_spaces=True)

    def test_double_click_input_delegates_to_mouse(self):
        el = _ImageElement(cx=10, cy=20)
        with patch("pywinauto.mouse.double_click") as mock_dc:
            el.double_click_input()
            mock_dc.assert_called_once_with(coords=(10, 20))

    def test_right_click_input_delegates_to_mouse(self):
        el = _ImageElement(cx=5, cy=6)
        with patch("pywinauto.mouse.right_click") as mock_rc:
            el.right_click_input()
            mock_rc.assert_called_once_with(coords=(5, 6))


class TestImageElementFocusClick:
    """A second click between select-all and typing collapses the selection."""

    def test_type_keys_alone_still_focuses(self):
        el = _ImageElement(cx=5, cy=6)
        with patch("pywinauto.mouse.click") as click, patch("pywinauto.keyboard.send_keys"):
            el.type_keys("abc")
        click.assert_called_once_with(coords=(5, 6))

    def test_type_keys_after_set_focus_does_not_click_again(self):
        el = _ImageElement(cx=5, cy=6)
        with patch("pywinauto.mouse.click") as click, patch("pywinauto.keyboard.send_keys"):
            el.set_focus()
            el.type_keys("abc")
        assert click.call_count == 1

    def test_set_text_types_into_a_live_selection(self):
        """``^a`` then a click would insert instead of replacing."""
        from dolphin_desktop._locator import Locator

        el = _ImageElement(cx=5, cy=6)
        events: list[str] = []

        with (
            patch.object(Locator, "_resolve", return_value=el),
            patch("pywinauto.mouse.click", lambda **kw: events.append("click")),
            patch(
                "pywinauto.keyboard.send_keys",
                lambda keys, **kw: events.append(f"keys:{keys}"),
            ),
            patch(
                "dolphin_desktop._locator._send_keys",
                lambda keys, **kw: events.append(f"keys:{keys}"),
            ),
        ):
            Locator(MagicMock(), title="Field").set_text("hello")

        assert events == ["click", "keys:^a", "keys:hello"]


# ImageLocator — missing cv2 raises clearly


class TestImageLocatorMissingCv2:
    def test_find_raises_runtime_error_without_cv2(self, monkeypatch):
        from dolphin_desktop import _image

        monkeypatch.setattr(_image, "_CV2_ERROR", ImportError("no cv2"))
        monkeypatch.setattr(_image, "_cv2", None)

        loc = ImageLocator("nonexistent.png")
        with pytest.raises(RuntimeError, match="opencv-python"):
            loc.find()

    def test_find_all_raises_without_cv2(self, monkeypatch):
        from dolphin_desktop import _image

        monkeypatch.setattr(_image, "_CV2_ERROR", ImportError("no cv2"))
        monkeypatch.setattr(_image, "_cv2", None)

        loc = ImageLocator("nonexistent.png")
        with pytest.raises(RuntimeError, match="opencv-python"):
            loc.find_all()

    def test_missing_cv2_reported_before_numpy_is_imported(self, monkeypatch):
        """The install hint must survive a base install, where numpy is absent too.

        Simulating the absence rather than requiring it keeps the guarantee
        under test on developer machines, which have the vision extra —
        the real gap only ever showed up on CI.
        """
        from dolphin_desktop import _image

        monkeypatch.setattr(_image, "_CV2_ERROR", ImportError("no cv2"))
        monkeypatch.setattr(_image, "_cv2", None)

        with pytest.raises(RuntimeError, match="opencv-python"):
            ImageLocator("nonexistent.png")._load_template()


# Screen — OCR missing tesseract raises clearly


def test_screen_text_raises_without_tesseract(monkeypatch):
    from dolphin_desktop import _image

    monkeypatch.setattr(_image, "_TESS_ERROR", ImportError("no pytesseract"))
    monkeypatch.setattr(_image, "_pytesseract", None)

    with pytest.raises(RuntimeError, match="pytesseract"):
        Screen.text()


def test_screen_find_image_raises_without_cv2(monkeypatch):
    from dolphin_desktop import _image

    monkeypatch.setattr(_image, "_CV2_ERROR", ImportError("no cv2"))
    monkeypatch.setattr(_image, "_cv2", None)

    with pytest.raises(RuntimeError, match="opencv-python"):
        Screen.find_image("nonexistent.png")


# ImageLocator — constructor / API surface


def test_image_locator_default_threshold():
    loc = ImageLocator("tmpl.png")
    assert loc._threshold == 0.85


def test_image_locator_custom_threshold():
    loc = ImageLocator("tmpl.png", 0.95)
    assert loc._threshold == 0.95


def test_image_locator_default_scales():
    loc = ImageLocator("tmpl.png")
    assert loc._scales == [1.0]


def test_image_locator_custom_scales():
    loc = ImageLocator("tmpl.png", scales=[0.8, 1.0, 1.2])
    assert loc._scales == [0.8, 1.0, 1.2]


def test_image_locator_region_stored():
    loc = ImageLocator("tmpl.png", region=(10, 20, 300, 400))
    assert loc._region == (10, 20, 300, 400)


def test_image_locator_template_path_is_path_object():
    loc = ImageLocator("some/path/tmpl.png")
    assert isinstance(loc._template_path, Path)


# ImageLocator — find() with mocked cv2


@pytest.fixture()
def tmp_template(tmp_path: Path) -> Path:
    """Create a tiny real PNG template file."""
    p = tmp_path / "tmpl.png"
    Image.new("RGB", (10, 10), color=(255, 0, 0)).save(str(p))
    return p


def test_find_returns_none_below_threshold(tmp_template: Path):
    try:
        import cv2 as real_cv2  # noqa: F401
    except ImportError:
        pytest.skip("cv2 not installed")

    loc = ImageLocator(str(tmp_template), threshold=0.99)
    with patch("dolphin_desktop._image._grab") as mock_grab:
        mock_grab.return_value = Image.new("RGB", (800, 600))
        result = loc.find()
    assert result is None


def test_exists_without_match_returns_false(tmp_template: Path):
    try:
        import cv2 as _cv  # noqa: F401
    except ImportError:
        pytest.skip("cv2 not installed")

    loc = ImageLocator(str(tmp_template), threshold=0.99)
    with patch("dolphin_desktop._image._grab") as mock_grab:
        mock_grab.return_value = Image.new("RGB", (800, 600))
        assert loc.exists() is False


# Multi-monitor capture


class TestGrabCoversAllMonitors:
    """Without ``all_screens`` Pillow returns the primary monitor only."""

    def test_full_screen_grab_covers_the_virtual_desktop(self):
        from PIL import ImageGrab

        from dolphin_desktop._image import _grab

        with patch.object(ImageGrab, "grab") as grab:
            _grab()
        assert grab.call_args.kwargs == {"bbox": None, "all_screens": True}

    def test_region_grab_covers_the_virtual_desktop(self):
        from PIL import ImageGrab

        from dolphin_desktop._image import _grab

        with patch.object(ImageGrab, "grab") as grab:
            _grab((-1920, 0, -1620, 300))
        assert grab.call_args.kwargs == {"bbox": (-1920, 0, -1620, 300), "all_screens": True}


# Virtual-screen origin — a full-desktop grab does not start at (0, 0)


_ORIGIN = (-1920, -200)


@pytest.fixture()
def virtual_origin():
    """Pretend a second monitor sits left of and above the primary one."""
    with patch("dolphin_desktop._image._virtual_origin", return_value=_ORIGIN) as origin:
        yield origin


class TestVirtualScreenOrigin:
    """Pillow crops an explicit bbox absolutely but not a whole-desktop grab.

    With ``bbox=None`` pixel (0, 0) of the returned image is
    ``(SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN)``, so an image-space match has to
    be shifted by that origin to become a screen coordinate.
    """

    def _locator(self, tmp_path: Path, threshold: float = 0.9) -> ImageLocator:
        path = tmp_path / "tile.png"
        _gradient_tile().save(str(path))
        return ImageLocator(str(path), threshold=threshold)

    def _screen(self, origin: tuple[int, int]) -> Image.Image:
        screen = Image.new("RGB", (400, 300), (17, 34, 51))
        screen.paste(_gradient_tile(), origin)
        return screen

    def test_find_returns_a_screen_coordinate(self, tmp_path: Path, virtual_origin):
        pytest.importorskip("cv2")

        loc = self._locator(tmp_path)
        with patch("dolphin_desktop._image._grab", return_value=self._screen((100, 50))):
            point = loc.find()

        cx, cy = _centre((100, 50))
        assert point == (_ORIGIN[0] + cx, _ORIGIN[1] + cy)

    def test_click_targets_the_screen_coordinate(self, tmp_path: Path, virtual_origin):
        """A 1920 px error here puts the click on the wrong monitor."""
        pytest.importorskip("cv2")

        loc = self._locator(tmp_path)
        with (
            patch("dolphin_desktop._image._grab", return_value=self._screen((100, 50))),
            patch("pywinauto.mouse.click") as click,
        ):
            loc.click()

        cx, cy = _centre((100, 50))
        click.assert_called_once_with(coords=(_ORIGIN[0] + cx, _ORIGIN[1] + cy))

    def test_find_all_returns_screen_coordinates(self, tmp_path: Path, virtual_origin):
        pytest.importorskip("cv2")

        loc = self._locator(tmp_path)
        with patch("dolphin_desktop._image._grab", return_value=self._screen((40, 30))):
            points = loc.find_all()

        cx, cy = _centre((40, 30))
        assert points == [(_ORIGIN[0] + cx, _ORIGIN[1] + cy)]

    def test_explicit_region_is_not_shifted_by_the_origin(self, tmp_path: Path, virtual_origin):
        """Pillow already crops a bbox absolutely — adding the origin doubles it."""
        pytest.importorskip("cv2")

        loc = self._locator(tmp_path)
        with patch("dolphin_desktop._image._grab", return_value=self._screen((40, 30))):
            point = loc.find(region=(500, 700, 900, 1000))

        cx, cy = _centre((40, 30))
        assert point == (500 + cx, 700 + cy)

    def _tesseract(self) -> MagicMock:
        tess = MagicMock()
        tess.image_to_data.return_value = {
            "text": ["Login"],
            "left": [10],
            "top": [20],
            "width": [40],
            "height": [10],
        }
        return tess

    def test_find_text_returns_a_screen_coordinate(self, virtual_origin):
        with (
            patch("dolphin_desktop._image._require_tesseract", return_value=self._tesseract()),
            patch("dolphin_desktop._image._grab", return_value=Image.new("RGB", (400, 300))),
        ):
            point = Screen.find_text("Login")

        assert point == (_ORIGIN[0] + 30, _ORIGIN[1] + 25)

    def test_find_text_with_region_is_not_shifted_twice(self, virtual_origin):
        with (
            patch("dolphin_desktop._image._require_tesseract", return_value=self._tesseract()),
            patch("dolphin_desktop._image._grab", return_value=Image.new("RGB", (400, 300))),
        ):
            point = Screen.find_text("Login", region=(500, 700, 900, 1000))

        assert point == (530, 725)

    def test_pixel_color_always_passes_a_bbox(self):
        """The bbox path is already absolute, so it must stay origin-free."""
        with patch("dolphin_desktop._image._grab") as grab:
            grab.return_value = Image.new("RGB", (1, 1), (1, 2, 3))
            assert Screen.pixel_color(-1800, -100) == (1, 2, 3)
        assert grab.call_args.args[0] == (-1800, -100, -1799, -99)


# Template loading — non-ASCII paths


class TestTemplateLoading:
    def test_non_ascii_path_loads(self, tmp_path: Path):
        """cv2.imread's ANSI fopen cannot open this path and returns None."""
        pytest.importorskip("cv2")

        path = tmp_path / "szablon-日本語-Ωμ.png"
        Image.new("RGB", (8, 12), color=(3, 200, 90)).save(str(path))

        tmpl = ImageLocator(str(path))._load_template()
        assert tmpl.shape[:2] == (12, 8)

    def test_missing_file_still_raises_file_not_found(self, tmp_path: Path):
        pytest.importorskip("cv2")

        loc = ImageLocator(str(tmp_path / "absent.png"))
        with pytest.raises(FileNotFoundError, match="Template image not found"):
            loc._load_template()

    def test_undecodable_file_is_not_reported_as_missing(self, tmp_path: Path):
        pytest.importorskip("cv2")

        path = tmp_path / "broken.png"
        path.write_bytes(b"not an image")
        with pytest.raises(ValueError, match="could not be decoded"):
            ImageLocator(str(path))._load_template()


# find_text upscale retries — small fonts need magnification for Tesseract


class TestFindTextUpscale:
    def _tess_mock(self, results):
        tess = MagicMock()
        tess.Output.DICT = "DICT"
        tess.image_to_data.side_effect = results
        return tess

    @staticmethod
    def _data(words, boxes):
        return {
            "text": words,
            "left": [b[0] for b in boxes],
            "top": [b[1] for b in boxes],
            "width": [b[2] for b in boxes],
            "height": [b[3] for b in boxes],
        }

    def test_found_at_native_scale_returns_screen_coords(self):
        tess = self._tess_mock([self._data(["", "Alaska"], [(0, 0, 0, 0), (40, 80, 20, 10)])])
        with (
            patch("dolphin_desktop._image._require_tesseract", return_value=tess),
            patch("dolphin_desktop._image._grab") as grab,
            patch("dolphin_desktop._image._grab_origin", return_value=(100, 200)),
        ):
            grab.return_value = Image.new("RGB", (300, 300))
            pt = Screen.find_text("alaska", region=(100, 200, 400, 500))
        assert pt == (100 + 40 + 10, 200 + 80 + 5)
        assert tess.image_to_data.call_count == 1

    def test_miss_at_native_scale_retries_upscaled_and_maps_back(self):
        miss = self._data(["", "noise"], [(0, 0, 0, 0), (1, 1, 1, 1)])
        hit = self._data(["", "Alaska"], [(0, 0, 0, 0), (40, 80, 20, 10)])
        tess = self._tess_mock([miss, hit])
        with (
            patch("dolphin_desktop._image._require_tesseract", return_value=tess),
            patch("dolphin_desktop._image._grab") as grab,
            patch("dolphin_desktop._image._grab_origin", return_value=(100, 200)),
        ):
            grab.return_value = Image.new("RGB", (300, 300))
            pt = Screen.find_text("Alaska", region=(100, 200, 400, 500))
        # Hit came from the 2x pass: box centre (50, 85) maps back to (25, 42).
        assert pt == (100 + 25, 200 + 42)
        assert tess.image_to_data.call_count == 2
        second_img = tess.image_to_data.call_args_list[1].args[0]
        assert second_img.size == (600, 600)

    def test_scales_single_entry_disables_retries(self):
        miss = self._data(["", "noise"], [(0, 0, 0, 0), (1, 1, 1, 1)])
        tess = self._tess_mock([miss])
        with (
            patch("dolphin_desktop._image._require_tesseract", return_value=tess),
            patch("dolphin_desktop._image._grab") as grab,
            patch("dolphin_desktop._image._grab_origin", return_value=(0, 0)),
        ):
            grab.return_value = Image.new("RGB", (50, 50))
            pt = Screen.find_text("Alaska", scales=(1,))
        assert pt is None
        assert tess.image_to_data.call_count == 1


# find_all — non-maximum suppression


_TILE_W, _TILE_H = 40, 24


def _gradient_tile() -> Image.Image:
    """A smooth 'button'-like tile.

    Real UI widgets are low-frequency, so neighbouring offsets correlate above
    threshold too: this one produces a ~100-pixel match plateau per occurrence,
    which is exactly what non-maximum suppression has to collapse.
    """
    import numpy as np

    arr = np.zeros((_TILE_H, _TILE_W, 3), dtype=np.uint8)
    for x in range(_TILE_W):
        arr[:, x, :] = 40 + x * 4
    return Image.fromarray(arr, "RGB")


def _centre(origin: tuple[int, int]) -> tuple[int, int]:
    return origin[0] + _TILE_W // 2, origin[1] + _TILE_H // 2


class TestFindAllSuppressesOverlaps:
    """One on-screen occurrence must count as one, not as its whole match plateau."""

    @pytest.fixture(autouse=True)
    def _single_monitor(self):
        """Image space equals screen space only when the virtual origin is (0, 0)."""
        with patch("dolphin_desktop._image._virtual_origin", return_value=(0, 0)):
            yield

    def _screen(self, origins: list[tuple[int, int]], tmpl: Image.Image) -> Image.Image:
        screen = Image.new("RGB", (400, 300), (17, 34, 51))
        for origin in origins:
            screen.paste(tmpl, origin)
        return screen

    def _locator(self, tmp_path: Path, tmpl: Image.Image) -> ImageLocator:
        path = tmp_path / "tile.png"
        tmpl.save(str(path))
        return ImageLocator(str(path), threshold=0.9)

    def test_single_occurrence_yields_exactly_one_centre(self, tmp_path: Path):
        pytest.importorskip("cv2")

        tmpl = _gradient_tile()
        loc = self._locator(tmp_path, tmpl)
        with patch("dolphin_desktop._image._grab", return_value=self._screen([(100, 50)], tmpl)):
            points = loc.find_all()

        assert points == [_centre((100, 50))]

    def test_three_occurrences_yield_three_centres(self, tmp_path: Path):
        pytest.importorskip("cv2")

        tmpl = _gradient_tile()
        origins = [(10, 10), (240, 60), (120, 220)]
        loc = self._locator(tmp_path, tmpl)
        with patch("dolphin_desktop._image._grab", return_value=self._screen(origins, tmpl)):
            points = loc.find_all()

        assert sorted(points) == sorted(_centre(o) for o in origins)

    def test_region_offset_is_applied_once_per_match(self, tmp_path: Path):
        pytest.importorskip("cv2")

        tmpl = _gradient_tile()
        loc = self._locator(tmp_path, tmpl)
        with patch("dolphin_desktop._image._grab", return_value=self._screen([(40, 30)], tmpl)):
            points = loc.find_all(region=(500, 700, 900, 1000))

        cx, cy = _centre((40, 30))
        assert points == [(500 + cx, 700 + cy)]


class TestFindAllOnAPlateauTerminates:
    """A flat score surface makes every pixel a local maximum.

    A low-variance template over a flat background routes to the SQDIFF branch
    and clears the threshold everywhere: 800x600 yields ~420k candidates, which
    a per-candidate Python overlap scan turns into tens of seconds.
    """

    def _flat_locator(self, tmp_path: Path) -> ImageLocator:
        path = tmp_path / "flat.png"
        Image.new("RGB", (_TILE_W, _TILE_H), (200, 200, 200)).save(str(path))
        return ImageLocator(str(path), threshold=0.9)

    def test_flat_screen_completes_promptly(self, tmp_path: Path):
        import time

        pytest.importorskip("cv2")

        loc = self._flat_locator(tmp_path)
        screen = Image.new("RGB", (800, 600), (200, 200, 200))
        with patch("dolphin_desktop._image._grab", return_value=screen):
            started = time.perf_counter()
            points = loc.find_all()
            elapsed = time.perf_counter() - started

        assert elapsed < 5.0, f"find_all took {elapsed:.1f}s on a flat 800x600 region"
        assert points

    def test_candidates_are_capped(self, tmp_path: Path):
        from dolphin_desktop._image import _MAX_CANDIDATES

        pytest.importorskip("cv2")

        loc = self._flat_locator(tmp_path)
        screen = Image.new("RGB", (1920, 1080), (200, 200, 200))
        with patch("dolphin_desktop._image._grab", return_value=screen):
            points = loc.find_all()

        assert len(points) <= _MAX_CANDIDATES


# wait_for — first attempt


class TestWaitForAttemptsAtLeastOnce:
    def test_zero_timeout_still_searches(self):
        loc = ImageLocator("tile.png")
        with patch.object(ImageLocator, "find", return_value=(5, 6)) as find:
            assert loc.wait_for(timeout=0) == (5, 6)
        assert find.call_count == 1

    def test_zero_timeout_raises_only_after_looking(self):
        from dolphin_desktop import WaitTimeoutError

        loc = ImageLocator("tile.png")
        with patch.object(ImageLocator, "find", return_value=None) as find:
            with pytest.raises(WaitTimeoutError):
                loc.wait_for(timeout=0)
        assert find.call_count == 1


# window.image() — integration with Window class


def test_window_image_returns_image_locator():
    from dolphin_desktop._image import ImageLocator
    from dolphin_desktop._window import Window

    spec = MagicMock()
    spec.rectangle.return_value = MagicMock(left=10, top=20, right=110, bottom=120)
    win = Window(spec)

    loc = win.image("btn.png", confidence=0.9)
    assert isinstance(loc, ImageLocator)
    assert loc._threshold == 0.9
    assert loc._region == (10, 20, 110, 120)


def test_window_image_passes_scales():
    from dolphin_desktop._image import ImageLocator
    from dolphin_desktop._window import Window

    spec = MagicMock()
    spec.rectangle.return_value = MagicMock(left=0, top=0, right=200, bottom=100)
    win = Window(spec)

    loc = win.image("tmpl.png", scales=[0.8, 1.0, 1.2])
    assert isinstance(loc, ImageLocator)
    assert loc._scales == [0.8, 1.0, 1.2]


def test_window_image_falls_back_to_no_region_on_error():
    from dolphin_desktop._image import ImageLocator
    from dolphin_desktop._window import Window

    spec = MagicMock()
    spec.rectangle.side_effect = RuntimeError("no rect")
    win = Window(spec)

    loc = win.image("tmpl.png")
    assert isinstance(loc, ImageLocator)
    assert loc._region is None


# Locator image_fallback — unit test without real UIA


def test_locator_image_fallback_stored():
    from dolphin_desktop._locator import Locator

    img_loc = ImageLocator("btn.png")
    parent = MagicMock()
    loc = Locator(parent, title="OK", image_fallback=img_loc)
    assert loc._image_fallback is img_loc


def test_locator_timeout_clone_preserves_image_fallback():
    from dolphin_desktop._locator import Locator

    img_loc = ImageLocator("btn.png")
    parent = MagicMock()
    parent._get_spec.return_value = MagicMock()
    loc = Locator(parent, title="OK", image_fallback=img_loc)
    clone = loc.timeout(5.0)
    assert clone._image_fallback is img_loc
    assert clone._timeout == 5.0
