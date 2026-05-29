# Native Windows Apps

Use this guide for Win32, WPF, WinForms, MFC, Delphi/VCL, and similar Windows desktop applications.

## Choose A Backend

| App type | Start with | Notes |
| --- | --- | --- |
| WPF | `uia` | Prefer `AutomationId` selectors when developers set them |
| WinForms | `uia` | Use `win32` for older controls that expose poor UIA data |
| Modern Win32 | `uia` | Inspect with `dolphin spy` before choosing selectors |
| MFC, Delphi/VCL, VB6 | `win32` | Class names are often more useful than UIA roles |

```python
from dolphin_desktop import Desktop

desktop = Desktop(backend="uia")
```

For legacy applications:

```python
desktop = Desktop.for_legacy_apps()
```

## WPF / WinForms Example

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch("MyApp.exe")
win = app.window(title_re=".*My Application.*")

win.get_by_automation_id("txtUsername").type_text("admin")
win.get_by_automation_id("txtPassword").type_text("secret")
win.get_by_automation_id("btnSignIn").click()

assert win.get_by_automation_id("lblStatus").text() == "Signed in"
```

## Legacy Win32 Example

```python
from dolphin_desktop import Desktop

desktop = Desktop(backend="win32")
app = desktop.launch("LegacyApp.exe")
win = app.window(title="Main Window")

win.get_by_class("Edit").type_text("hello")
win.get_by_class("Button").click()
```

## Inspect The UI Tree

```bash
dolphin spy --window "My Application" --depth 3
dolphin spy --pick
```

Prefer stable selectors:

1. `get_by_automation_id(...)`
2. `get_by_role(..., name=...)`
3. `get_by_title(...)`
4. `get_by_class(...)`

## Common Dialogs

```python
from dolphin_desktop import FileDialog, MessageBox

win.get_by_title("Open").click()
dlg = FileDialog.wait_for(timeout=10)
dlg.set_path(r"C:\Temp\input.txt")
dlg.confirm()

win.get_by_title("Delete").click()
msg = MessageBox.wait_for(timeout=10)
msg.click_yes()
```

## Tips

- Avoid title-only selectors for localized applications.
- If the application runs as Administrator, run the test process as Administrator too.
- If UIA shows little useful information, try `--dolphin-backend=win32` or `Desktop.for_legacy_apps()`.
