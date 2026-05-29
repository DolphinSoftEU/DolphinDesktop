# Locator

`Locator` is the core of Dolphin's API. It represents a **lazy** reference to a UI element -
the element is not searched until an action or query method is called.

---

::: dolphin_desktop._locator.Locator

---

## Actions

```python
loc.click()
loc.double_click()
loc.right_click()
loc.type_text("hello world")
loc.set_text("hello world")     # direct value set, no keystrokes
loc.clear()
loc.press_key("{ENTER}")
loc.press_key("^a")             # Ctrl+A
loc.select_item("Option A")     # ComboBox / ListBox
loc.check()
loc.uncheck()
loc.focus()
loc.select_text()               # Ctrl+A on the element
loc.scroll_into_view()
```

## Mouse actions

```python
loc.hover()
loc.scroll("up", amount=3)
loc.scroll("down", amount=3)
loc.drag_to(target_locator, duration=0.5, button="left")
```

## Queries

```python
loc.text()          # str - element text / value
loc.value()         # str - value property
loc.is_visible()    # bool
loc.is_enabled()    # bool
loc.is_checked()    # bool
loc.exists()        # bool - True if element is found (no wait)
loc.count()         # int - number of matching elements
loc.bounding_box()  # dict with x, y, width, height
loc.get_attribute("AutomationId")
```

## Waiting

```python
loc.wait_for(state="exists", timeout=10)
loc.wait_for(state="visible", timeout=10)
loc.wait_for(state="enabled", timeout=10)
loc.wait_until_hidden(timeout=10)
loc.wait_until_enabled(timeout=10)
```

## Collections

```python
# All direct children matching the locator
items = loc.all()             # depth=None means direct children
items = loc.all(depth=3)      # descendants up to depth 3

# Nth item (0-based)
third = loc.nth(2)

# Count
n = loc.count()
```

## Chaining

```python
# Find a descendant inside this locator
inner = loc.locator(control_type="Button", title="OK")

# Change timeout (returns new locator, does not mutate)
slow_loc = loc.timeout(30)
```

## Fallback selectors

```python
loc = win.locator(
    auto_id="btnSubmit",
    fallback=[
        {"auto_id": "btnSave"},
        {"title": "Submit"},
    ],
    image_fallback="templates/submit.png",
)
```

## Screenshot

```python
img = loc.screenshot()              # PIL.Image of this element
img = loc.screenshot("elem.png")    # save to file
```
