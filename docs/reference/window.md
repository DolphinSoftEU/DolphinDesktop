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
