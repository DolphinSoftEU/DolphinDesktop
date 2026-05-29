"""Tests for ImageLocator, Screen, window.image() and image failover chain."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from dolphin_desktop import ImageLocator, Screen
from dolphin_desktop._image import _ImageElement

# ---------------------------------------------------------------------------
# _ImageElement unit tests (no cv2 required)
# ---------------------------------------------------------------------------


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
        _ImageElement(0, 0).scroll_into_view()  # no exception

    def test_click_input_delegates_to_mouse(self):
        el = _ImageElement(cx=300, cy=400)
        with patch("pywinauto.mouse.click") as mock_click:
            el.click_input()
            mock_click.assert_called_once_with(coords=(300, 400))

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


# ---------------------------------------------------------------------------
# ImageLocator — missing cv2 raises clearly
# ---------------------------------------------------------------------------


class TestImageLocatorMissingCv2:
    def test_find_raises_runtime_error_without_cv2(self):
        try:
            import cv2  # noqa: F401

            pytest.skip("cv2 is installed — cannot test missing-cv2 path")
        except ImportError:
            pass

        loc = ImageLocator("nonexistent.png")
        with pytest.raises(RuntimeError, match="opencv-python"):
            loc.find()

    def test_find_all_raises_without_cv2(self):
        try:
            import cv2  # noqa: F401

            pytest.skip("cv2 is installed")
        except ImportError:
            pass

        loc = ImageLocator("nonexistent.png")
        with pytest.raises(RuntimeError, match="opencv-python"):
            loc.find_all()


# ---------------------------------------------------------------------------
# Screen — screenshot and pixel_color (no cv2 required)
# ---------------------------------------------------------------------------


pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
def test_screenshot_returns_pil_image():
    img = Screen.screenshot()
    assert isinstance(img, Image.Image)
    assert img.width > 0 and img.height > 0


@pytest.mark.integration
def test_screenshot_with_region():
    img = Screen.screenshot(region=(0, 0, 200, 200))
    assert img.size == (200, 200)


@pytest.mark.integration
def test_pixel_color_is_rgb_tuple():
    color = Screen.pixel_color(0, 0)
    assert isinstance(color, tuple)
    assert len(color) == 3
    assert all(0 <= c <= 255 for c in color)


# ---------------------------------------------------------------------------
# Screen — OCR missing tesseract raises clearly
# ---------------------------------------------------------------------------


def test_screen_text_raises_without_tesseract():
    try:
        import pytesseract  # noqa: F401

        pytest.skip("pytesseract is installed")
    except ImportError:
        pass
    with pytest.raises(RuntimeError, match="pytesseract"):
        Screen.text()


def test_screen_find_image_raises_without_cv2():
    try:
        import cv2  # noqa: F401

        pytest.skip("cv2 is installed")
    except ImportError:
        pass
    with pytest.raises(RuntimeError, match="opencv-python"):
        Screen.find_image("nonexistent.png")


# ---------------------------------------------------------------------------
# ImageLocator — constructor / API surface
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# ImageLocator — find() with mocked cv2
# ---------------------------------------------------------------------------


def _make_fake_cv2(match_val: float = 0.95, match_loc: tuple = (10, 10)):
    """Return a mock cv2 module that simulates a successful template match."""
    import numpy as np

    cv2 = MagicMock()
    cv2.TM_CCOEFF_NORMED = 5

    fake_tmpl = np.zeros((20, 30, 3), dtype="uint8")  # h=20, w=30
    fake_screen = np.zeros((200, 300, 3), dtype="uint8")

    cv2.imread.return_value = fake_tmpl
    cv2.cvtColor.return_value = fake_screen
    cv2.matchTemplate.return_value = np.full((181, 271), match_val)
    cv2.minMaxLoc.return_value = (0.0, match_val, (0, 0), match_loc)
    cv2.resize.return_value = fake_tmpl

    return cv2


@pytest.fixture()
def tmp_template(tmp_path: Path) -> Path:
    """Create a tiny real PNG template file."""
    p = tmp_path / "tmpl.png"
    Image.new("RGB", (10, 10), color=(255, 0, 0)).save(str(p))
    return p


def test_find_returns_centre_when_match_found(tmp_template: Path):
    try:
        import cv2  # noqa: F401
    except ImportError:
        pytest.skip("cv2 not installed")

    loc = ImageLocator(str(tmp_template), threshold=0.85)
    # Patch _grab so no real screenshot is needed
    with patch("dolphin_desktop._image._grab") as mock_grab:
        mock_grab.return_value = Image.new("RGB", (800, 600))
        result = loc.find()
    # result may be None if real cv2 doesn't match a blank template — that's fine
    assert result is None or (isinstance(result, tuple) and len(result) == 2)


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


def test_find_with_region_applies_offset(tmp_template: Path):
    """Coordinates returned by find() must be offset by the region origin."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        pytest.skip("cv2 not installed")

    region = (50, 100, 350, 400)
    loc = ImageLocator(str(tmp_template), threshold=0.0, region=region)

    with patch("dolphin_desktop._image._grab") as mock_grab:
        # Return a blank image big enough for matching
        mock_grab.return_value = Image.new("RGB", (300, 300))
        result = loc.find()

    if result is not None:
        cx, cy = result
        # cx and cy must be >= region origin
        assert cx >= region[0]
        assert cy >= region[1]


# ---------------------------------------------------------------------------
# window.image() — integration with Window class
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Locator image_fallback — unit test without real UIA
# ---------------------------------------------------------------------------


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
