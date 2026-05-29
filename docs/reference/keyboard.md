# Keyboard

`Keyboard` sends keys to the currently focused window.

::: dolphin_desktop._keyboard.Keyboard

## Usage

```python
from dolphin_desktop import Keyboard

Keyboard.press("^s")          # Ctrl+S
Keyboard.press("{ENTER}")     # Enter
Keyboard.type("Hello World")  # plain text
Keyboard.hotkey("ctrl", "c")  # Ctrl+C
```

## Key Notation

`Keyboard.press()` uses pywinauto `send_keys` notation.

| Notation | Key |
| --- | --- |
| `{ENTER}` | Enter |
| `{TAB}` | Tab |
| `{ESC}` | Escape |
| `{BACK}` | Backspace |
| `{DELETE}` | Delete |
| `{UP}`, `{DOWN}`, `{LEFT}`, `{RIGHT}` | Arrow keys |
| `{HOME}`, `{END}` | Home / End |
| `{F1}` ... `{F12}` | Function keys |
| `^` | Ctrl modifier |
| `%` | Alt modifier |
| `+` | Shift modifier |
