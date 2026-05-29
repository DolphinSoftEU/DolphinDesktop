# Dialogs

`FileDialog` and `MessageBox` handle the most common Windows system dialogs.

---

## FileDialog

::: dolphin_desktop._dialogs.FileDialog

### Usage

```python
from dolphin_desktop import FileDialog

# Wait for a dialog to appear (Open or Save As)
dlg = FileDialog.wait_for(timeout=10)

# Set the file path and confirm
dlg.set_path(r"C:\Users\me\Documents\report.xlsx")
dlg.confirm()

# Or cancel
dlg.cancel()
```

### Notes

- `FileDialog.wait_for()` detects dialogs by Win32 class `#32770` or title pattern
  (handles both English and Polish Windows).
- Uses `win32gui.EnumWindows` internally to find visible dialog windows.
- `set_path()` first tries the filename Edit control; falls back to `Ctrl+L` (address bar).

---

## MessageBox

::: dolphin_desktop._dialogs.MessageBox

### Usage

```python
from dolphin_desktop import MessageBox

# Wait for a MessageBox
mb = MessageBox.wait_for()

# Read the message text
print(mb.text())

# Click buttons
mb.click_ok()
mb.click_cancel()
mb.click_yes()
mb.click_no()
mb.click("Retry")   # any button by title
```
