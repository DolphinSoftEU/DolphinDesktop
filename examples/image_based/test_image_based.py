"""Examples: image-based automation fallback.

Demonstrates ImageLocator (template matching) and Screen (OCR).
Requires: pip install dolphin-desktop[vision]
"""

import pytest

try:
    import cv2  # noqa: F401

    HAS_VISION = True
except ImportError:
    HAS_VISION = False

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_vision():
    if not HAS_VISION:
        pytest.skip(
            "dolphin-desktop[vision] not installed - run: pip install dolphin-desktop[vision]"
        )


def test_image_locator_click(launch):
    """Click an element located by template matching."""
    from dolphin_desktop import Desktop, ImageLocator

    desktop = Desktop()
    app = desktop.launch("mspaint.exe", timeout=10)
    app.window(title_re=".*Paint.*")

    # Wait for a known UI element to appear (template must exist)
    # Capture templates with: dolphin spy --image-pick --output-dir examples/image_based/templates/
    brush_btn = ImageLocator(
        "examples/image_based/templates/brush_tool.png",
        threshold=0.85,
    )

    if not brush_btn.exists():
        pytest.skip("Template not found — run dolphin spy --image-pick to capture it")

    brush_btn.click()
    app.kill()


def test_screen_ocr(launch):
    """Read text from the screen using OCR."""
    import os
    import tempfile

    from dolphin_desktop import Desktop, Screen

    # Open Notepad with known text
    f = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
    f.write("OCR test string 12345")
    f.close()

    desktop = Desktop()
    app = desktop.launch(f'notepad.exe "{f.name}"', wait_for_idle=False)
    win = app.window(class_name="Notepad")

    bb = win.bounding_box()
    region = (
        int(bb["x"]),
        int(bb["y"]),
        int(bb["width"]),
        int(bb["height"]),
    )

    text = Screen.text(region=region)
    assert "OCR test string" in text or "12345" in text

    app.kill()
    os.unlink(f.name)


def test_screen_find_text(launch):
    """Find the screen coordinates of text using OCR."""
    from dolphin_desktop import Desktop, Mouse, Screen

    desktop = Desktop()
    app = desktop.launch("calc.exe", timeout=10)
    win = app.window(title_re=".*Calculator.*")

    bb = win.bounding_box()
    region = (int(bb["x"]), int(bb["y"]), int(bb["width"]), int(bb["height"]))

    x, y = Screen.find_text("5", region=region) or (None, None)
    if x is not None:
        Mouse.click(x, y)

    app.kill()
