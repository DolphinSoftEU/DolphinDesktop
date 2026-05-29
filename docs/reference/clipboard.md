# Clipboard

`Clipboard` reads and writes the Windows clipboard.

---

::: dolphin_desktop._clipboard.Clipboard

---

## Usage

```python
from dolphin_desktop import Clipboard

# Text
Clipboard.set_text("hello")
text = Clipboard.get_text()     # "hello"

# Image
img = Clipboard.get_image()     # PIL.Image or None

# Clear
Clipboard.clear()
```

## Notes

- Uses `win32clipboard` (pywin32) - Windows only.
- `Locator.text()` uses `Clipboard` internally as a fallback for controls that don't
  expose text via UIA (e.g., Windows 11 Notepad's `Document` control).
- Clipboard is process-global. Parallel tests that use the clipboard simultaneously can
  interfere with each other - avoid parallel test runs that rely on clipboard operations.
