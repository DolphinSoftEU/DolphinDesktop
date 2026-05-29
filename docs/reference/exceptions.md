# Exceptions

All Dolphin exceptions inherit from `DolphinError`.

---

::: dolphin_desktop._exceptions.DolphinError

::: dolphin_desktop._exceptions.ElementNotFoundError

::: dolphin_desktop._exceptions.WaitTimeoutError

::: dolphin_desktop._exceptions.ApplicationError

::: dolphin_desktop._exceptions.WindowNotFoundError

::: dolphin_desktop._exceptions.AliasNotFoundError

---

## Catching exceptions

```python
from dolphin_desktop import DolphinError, ElementNotFoundError, WaitTimeoutError

# Catch any Dolphin error
try:
    win.get_by_title("Submit").click()
except DolphinError as e:
    print(f"Dolphin error: {e}")

# Catch element not found specifically
try:
    win.get_by_automation_id("btnSubmit").timeout(5).click()
except ElementNotFoundError:
    # Fall back to image matching
    from dolphin_desktop import ImageLocator
    ImageLocator("templates/submit.png").click()

# Catch wait timeout
try:
    win.get_by_title("Loading...").wait_until_hidden(timeout=30)
except WaitTimeoutError:
    raise AssertionError("Loading never finished")
```

## Exception hierarchy

```
DolphinError
|-- ElementNotFoundError
|-- WaitTimeoutError
|-- ApplicationError
|-- WindowNotFoundError
`-- AliasNotFoundError
```
