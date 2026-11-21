"""Template matching and OCR on a visible Windows desktop.

Install the vision extra; OCR also requires the Tesseract executable.
Capture your own Paint template and set DOLPHIN_BRUSH_TEMPLATE to its path.
"""

import os
import re
import time
import uuid
from pathlib import Path

import pytest

from dolphin_desktop import ImageLocator, Mouse, Screen, find_pid_by_image_name

pytestmark = pytest.mark.integration


def test_image_locator_click(desktop):
    pytest.importorskip("cv2")
    template = Path(
        os.environ.get(
            "DOLPHIN_BRUSH_TEMPLATE", str(Path(__file__).with_name("templates") / "brush_tool.png")
        )
    )
    if not template.is_file():
        pytest.skip(
            "Set DOLPHIN_BRUSH_TEMPLATE to a template captured with dolphin spy --image-pick"
        )
    if find_pid_by_image_name("mspaint.exe") is not None:
        pytest.skip("Close existing Paint windows before running this example")
    with desktop.launch("mspaint.exe", timeout=10) as app:
        app.window(title_re=".*Paint.*").focus()
        brush = ImageLocator(template, threshold=0.85)
        brush.wait_for(timeout=5)
        brush.click()


@pytest.fixture
def ocr_region(desktop, tmp_path):
    tesseract = pytest.importorskip("pytesseract")
    try:
        tesseract.get_tesseract_version()
    except tesseract.TesseractNotFoundError:
        pytest.skip("Install Tesseract-OCR and put its executable on PATH")
    if find_pid_by_image_name("notepad.exe") is not None:
        pytest.skip("Close existing Notepad windows before running this example")
    path = tmp_path / f"ocr-{uuid.uuid4().hex[:8]}.txt"
    path.write_text("OCR test string 12345", encoding="utf-8")
    with desktop.launch(f'notepad.exe "{path}"', wait_for_idle=False) as app:
        window = app.window(
            class_name="Notepad", title_re=f".*{re.escape(path.stem)}.*", timeout=15
        )
        editor = window.get_by_role("Document")
        if not editor.exists():
            editor = window.get_by_role("Edit")
        editor.wait_for_text("OCR test string", timeout=5)
        window.focus()
        box = window.bounding_box()
        yield (box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"])
    deadline = time.monotonic() + 3
    while find_pid_by_image_name("notepad.exe") is not None and time.monotonic() < deadline:
        time.sleep(0.1)


def test_screen_ocr(ocr_region):
    text = Screen.text(region=ocr_region)
    assert "OCR test string" in text or "12345" in text


def test_screen_find_text(ocr_region):
    position = Screen.find_text("12345", region=ocr_region)
    assert position is not None, "The expected text was not found by OCR"
    Mouse.click(*position)
