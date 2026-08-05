# Keyboard

`Keyboard` sends keys to the currently focused window.

::: dolphin_desktop._keyboard.Keyboard

## Usage

```python
from dolphin_desktop import Keyboard

Keyboard.press("^s")          # Ctrl+S
Keyboard.press("{ENTER}")     # Enter
Keyboard.type("Hello World")  # plain text
Keyboard.type("100% (net)")   # metacharacters typed literally
Keyboard.type("%F", escape=False)  # opt out: send Alt+F as a key sequence
Keyboard.hotkey("ctrl", "c")  # Ctrl+C
Keyboard.hotkey("win", "e")   # Win+E
```

`Keyboard.type()` types literal text — `+ ^ % ~ ( ) { }` are escaped for you.
Pass `escape=False` when the argument is a pywinauto key sequence rather than
text to type.

`Keyboard.hotkey()` accepts the modifiers `ctrl`, `shift`, `alt` and `win`,
followed by a single character or a key name (`enter`, `f4`, `delete`).

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
