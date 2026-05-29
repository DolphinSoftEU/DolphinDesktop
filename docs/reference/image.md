# ImageLocator / Screen

Image-based element finding and full-screen operations. Requires `pip install dolphin-desktop[vision]`.

---

## ImageLocator

::: dolphin_desktop._image.ImageLocator

### Usage

```python
from dolphin_desktop import ImageLocator

btn = ImageLocator("templates/ok_button.png", threshold=0.9)

btn.exists()              # bool - found on screen right now
btn.wait_for(timeout=10)  # wait until it appears
btn.click()
btn.double_click()

# Find all occurrences
matches = btn.find_all()
for x, y in matches:
    print(f"({x}, {y})")

# Find one
match = btn.find()
if match is not None:
    x, y = match
    print(f"({x}, {y})")
```

---

## Screen

::: dolphin_desktop._image.Screen

### Usage

```python
from dolphin_desktop import Screen

# Full screenshot
img = Screen.screenshot()
img.save("screen.png")

# Region screenshot (left, top, right, bottom)
img = Screen.screenshot(region=(0, 0, 800, 600))

# Pixel colour
r, g, b = Screen.pixel_color(100, 200)

# OCR - all visible text
text = Screen.text()
text = Screen.text(region=(0, 50, 1920, 100))

# Find text - returns (cx, cy) of first matching word
point = Screen.find_text("Submit")
if point is not None:
    x, y = point
    from dolphin_desktop import Mouse
    Mouse.click(x, y)

# Shortcut
match = Screen.find_image("templates/btn.png")
```
