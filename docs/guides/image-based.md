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
