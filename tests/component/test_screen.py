"""Component checks for the public global :class:`Screen` facade."""

from __future__ import annotations

import pytest
from PIL import Image

from dolphin_desktop import Screen

pytestmark = pytest.mark.windows_component


def test_screen_screenshot_returns_a_real_pil_image() -> None:
    image = Screen.screenshot()

    assert isinstance(image, Image.Image)
    assert image.width > 0
    assert image.height > 0


def test_screen_screenshot_region_has_the_requested_size() -> None:
    image = Screen.screenshot(region=(0, 0, 32, 24))

    assert image.size == (32, 24)


def test_screen_pixel_color_returns_an_rgb_tuple() -> None:
    color = Screen.pixel_color(0, 0)

    assert isinstance(color, tuple)
    assert len(color) == 3
    assert all(0 <= channel <= 255 for channel in color)
