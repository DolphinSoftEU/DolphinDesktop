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

### Convenience controls on the win32 backend

The typed shortcuts locate their controls on `win32` too, for classic HWND
controls (MFC, VB6, Delphi/VCL) and WinForms alike:

```python
win = Desktop.for_legacy_apps().launch("LegacyApp.exe").window(title="Main Window")

win.edit().set_text("Dolphin")
win.combo_box().select_item("Premium")
win.tree().select_item_by_path("Root", "Child")
win.list_box().items()                    # ["Alpha", "Beta", ...]
win.tab().select_tab("Advanced")
win.check_box(name="Remember").exists()   # a BS_CHECKBOX, not the "Save" button
win.menu("File").item("Show dialog").click()
win.locator(title="ready", control_type="Static").wait_for()
```

The `win32` backend has no UI Automation control types. A UIA-style
`control_type` (`Edit`, `ComboBox`, `Tree`, `List`, `Button`, `CheckBox`,
`RadioButton`, `Group`, `Text`, `Tab`, `ToolBar`, `StatusBar`,
`ProgressBar`, `Slider`, `Spinner`, `Header`, `Calendar`) is matched against
pywinauto's friendly class name for the window instead. That name covers the
classic, WinForms (`WindowsForms10.EDIT.*`), VB6 and VCL class spellings, and
tells check boxes, radio buttons and group boxes apart from push buttons by
their `BS_*` style. The win32 friendly names themselves (`Static`,
`TreeView`, `ListBox`, `ListView`, `TabControl`, …) are accepted as
`control_type` too, and a dotted .NET type such as
`"System.Windows.Forms.TextBox"` keeps pywinauto's own WinForms meaning.

- `tree()` drives `SysTreeView32` nodes through tree-view messages:
  `select_item_by_path` selects the node, and the application gets the same
  selection-changed notification a click produces.
- `menu()` / `.item()` resolve a native menu bar (`HMENU`) by exact item
  text, ignoring `&` mnemonics and `\t` shortcuts. `click()` clicks the item
  with the mouse; `invoke()` sends its `WM_COMMAND` without any input. A
  WinForms `MenuStrip`/`ToolStrip` is a control, not an `HMENU`; locate it
  with the `uia` backend instead.
- A UIA type with no Win32 window class of its own (`MenuItem` outside a
  menu chain, `TreeItem`, `ListItem`, `Hyperlink`, `Pane`, `Window`, …)
  raises `UnsupportedCapabilityError` at once, from `wait_for()` and
  `exists()` alike, instead of timing out. Select such elements by
  `class_name` / `title`, or use the `uia` backend.
- `list_box()` reads `items()` and `selected_items()` from a `LISTBOX` or a
  `SysListView32` (first column of each row) through their own messages, and
  `select_item()` selects an entry.
- `tab().select_tab(name)` selects a `SysTabControl32` tab by exact text,
  ignoring an `&` mnemonic, and the application switches pages as on a click.

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
