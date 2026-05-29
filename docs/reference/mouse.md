# Mouse

`Mouse` performs screen-coordinate mouse actions.

::: dolphin_desktop._mouse.Mouse

## Usage

```python
from dolphin_desktop import Mouse

Mouse.move(100, 200)
Mouse.click(100, 200)
Mouse.click(100, 200, button="right")
Mouse.right_click(100, 200)
Mouse.double_click(100, 200)

Mouse.press(100, 200, button="left")
Mouse.move(300, 200)
Mouse.release(300, 200, button="left")

Mouse.scroll(100, 200, wheel_dist=3)   # positive scrolls up
Mouse.scroll(100, 200, wheel_dist=-3)  # negative scrolls down
```

Prefer `Locator.click()`, `Locator.hover()`, and `Locator.drag_to()` when you can identify an element. Use `Mouse` for low-level coordinate workflows.
