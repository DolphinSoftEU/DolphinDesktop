# Window

`Window` wraps a top-level window and provides factory methods for creating `Locator` objects.

---

::: dolphin_desktop._window.Window

---

## Locator factory methods

```python
# By UIA control type (role)
win.get_by_role("Button")
win.get_by_role("Edit", name="Username")

# By visible text / label
win.get_by_title("Save")
win.get_by_text("Submit")        # alias for get_by_title

# By AutomationId (most stable)
win.get_by_automation_id("btnSave")

# By Win32 class name
win.get_by_class("TButton")

# Arbitrary pywinauto criteria
win.locator(control_type="Button", title="OK")
win.locator(title_re=".*Save.*")
win.locator(auto_id="btnSave", found_index=0)
```

## Window actions

```python
win.maximize()
win.minimize()
win.restore()
win.close()
win.move(x=100, y=100)
win.resize(width=800, height=600)
win.focus()
win.wait_for_close(timeout=10)
```

## Screenshots

```python
img = win.screenshot()                  # PIL.Image
img = win.screenshot("output.png")      # save to file
```
