# Window

`Window` wraps a top-level window and provides factory methods for creating `Locator` objects.

---

::: dolphin_desktop._window.Window

---

## Simplified XPath

`Window.find_by_xpath()` supports a strict, simplified XPath grammar for
walking the UIA element tree. It is not a full XPath engine. Leading and
trailing whitespace is ignored; whitespace elsewhere is only allowed inside a
quoted value.

```text
expression ::= segment+
segment ::= ("/" | "//") tag predicate*
tag ::= ASCII letters+ | "*"
predicate ::= "[@" attribute "=" quoted_value "]"
attribute ::= "Name" | "AutomationId" | "ClassName"
quoted_value ::= "'" value "'" | '"' value '"'
value ::= zero or more characters other than quotes or brackets
```

The supported attributes map to locator criteria as follows:

| XPath attribute | Locator criterion |
| --- | --- |
| `Name` | `title` |
| `AutomationId` | `auto_id` |
| `ClassName` | `class_name` |

Values may be empty. Segments remain lazy and can be chained:

```python
win.find_by_xpath("/Button[@Name='Save']")
win.find_by_xpath("//Edit[@AutomationId='tbSearch']")
win.find_by_xpath("//MenuBar//MenuItem[@Name='File']")
win.find_by_xpath("//Button[@Name='']")
```

Unsupported or malformed syntax raises `ValueError` before a partial locator
is created. For example, these are rejected:

```python
win.find_by_xpath("//Button[contains(@Name, 'Save')]")
win.find_by_xpath("//Button[@HelpText='Save document']")
win.find_by_xpath("//Button[1]")
win.find_by_xpath("//Button[@Name='Save'")
```

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

For WPF, WinForms, and WinUI/UWP controls, the most durable UIA contract is
the provider-published AutomationId combined with the UIA control type:

```python
save = win.locator(auto_id="btnSave", control_type="Button")
save.wait_for(state="visible")
save.wait_for(state="enabled")
```

`auto_id` is the UIA AutomationId, not a screen coordinate. A control that is
custom-rendered or does not publish an accessibility provider cannot be made
discoverable by changing the locator syntax; use the application's accessible
surface or an explicitly configured image fallback.

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

`Application.window(...)` already waits for the window to become visible. Call
`wait_until_ready()` when startup work can leave the window busy, and use
`wait_for_close()` when the scenario asserts that the window has gone away.
Window lookup does not change process ownership: an application obtained with
`Desktop.connect()` remains external and must not be killed by test cleanup.

## Screenshots

```python
img = win.screenshot()                  # PIL.Image
img = win.screenshot("output.png")      # save to file
```

Screenshot files are caller-owned artifacts. For a test, write them below
`tmp_path` and remove them from a `finally` block so pass, failure, and skip
paths leave no screenshot behind. A screenshot requires a capturable visible
desktop; hidden/headless execution is a separate contract.
