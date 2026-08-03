# Image-Based Fallback

Image matching is the fallback path for controls that UIA and Win32 cannot see. It uses OpenCV template matching and optional OCR through Tesseract.

## Install

```bash
pip install "dolphin-desktop[vision]"
```

OCR also requires the Tesseract executable to be installed separately.

## Capture A Template

```bash
dolphin spy --image-pick --output-dir templates
```

Use Ctrl+Click in the picker to save a PNG template.

## Use `ImageLocator`

```python
from dolphin_desktop import ImageLocator

button = ImageLocator("templates/submit.png", threshold=0.9)
button.wait_for(timeout=10)
button.click()
```

Find without clicking:

```python
point = button.find()
if point is not None:
    x, y = point
```

Find all matches:

```python
for x, y in button.find_all():
    print(x, y)
```

## Scope To A Window

```python
win.image("templates/submit.png", confidence=0.9).click()
```

`window.image(...)` restricts matching to the window bounding box when Dolphin can read it.

## Locator Fallback

Use a normal selector first and an image only as the last resort:

```python
from dolphin_desktop import ImageLocator

win.locator(
    auto_id="btnSubmit",
    fallback=[{"title": "Submit", "control_type": "Button"}],
    image_fallback=ImageLocator("templates/submit.png", threshold=0.9),
).click()
```

## OCR And Screen Helpers

```python
from dolphin_desktop import Screen

img = Screen.screenshot()
img.save("screen.png")

text = Screen.text()
point = Screen.find_text("Submitted")
```

## Tips

- Keep templates small and specific.
- Capture templates at the same DPI and theme used by CI.
- Prefer UIA or Win32 selectors when available.
- Use OCR for broad verification, not as the first choice for precise controls.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ImportError: cv2` | vision extra not installed | `pip install "dolphin-desktop[vision]"` |
| `TesseractNotFoundError` | Tesseract exe not on PATH | Install from <https://github.com/UB-Mannheim/tesseract/wiki> and add `C:\Program Files\Tesseract-OCR` to PATH |
| Template matches wrong region | Threshold too permissive | Raise `threshold` towards 0.95 (default 0.85) and re-capture at exact DPI |
| Match works locally, fails in CI | DPI / theme / font-hinting difference | Capture templates on the CI runner OS (use a screenshot from a failed CI run), commit both variants, and pick between them with `platform.system()` / your own CI env var |
| Match works with English UI, fails in localised builds | UI theme swapped icon | Capture per-locale templates in `templates/<locale>/` and pick the directory from your own locale setting |
| OCR returns garbled Unicode | Wrong language pack | `Screen.text()` uses Tesseract's default language — install the language pack you need and set the `TESSDATA_PREFIX` / `tesseract` language configuration for the process |
| `Screen.find_image()` extremely slow (>2 s) | Full-screen template matching | Constrain the search: `Screen.find_image(template, region=(x1, y1, x2, y2))` |
| Templates rot with UI updates | Long-lived tests | Inspect drift with `dolphin selfheal-stats`, then re-capture the templates it reports |
