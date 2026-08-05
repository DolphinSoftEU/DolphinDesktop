# Exceptions

Every exception the library raises on its own behalf derives from
`DolphinError`, so one `except DolphinError` catches all of them.

---

::: dolphin_desktop._exceptions.DolphinError

::: dolphin_desktop._exceptions.ElementNotFoundError

::: dolphin_desktop._exceptions.AmbiguousMatchError

::: dolphin_desktop._exceptions.WaitTimeoutError

::: dolphin_desktop._exceptions.ApplicationError

::: dolphin_desktop._exceptions.WindowNotFoundError

::: dolphin_desktop._exceptions.AliasNotFoundError

::: dolphin_desktop._exceptions.UnsupportedPatternError

::: dolphin_desktop._exceptions.UnsupportedCapabilityError

## Stack-specific errors

These live next to their adapter but share the same base, so
`except DolphinError` catches them like everything else.

::: dolphin_desktop._delphi.DelphiError

::: dolphin_desktop._mainframe.MainframeError

::: dolphin_desktop._oracle_forms.OracleFormsError

::: dolphin_desktop._cdp.CDPStalePageError

::: dolphin_desktop._qt_inject.QtAgentInjectError

::: dolphin_desktop._qt_inject.QtAgentRpcError

::: dolphin_desktop._qt_inject.QtAgentTimeoutError

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

`UnsupportedPatternError` is what the programmatic action family raises
instead of silently falling back to a physical click, so it is the signal
to switch to input simulation:

```python
from dolphin_desktop import UnsupportedPatternError

try:
    row.expand()                 # ExpandCollapse pattern
except UnsupportedPatternError:
    row.double_click()           # the control does not implement it
```

## Exception hierarchy

```
DolphinError
|-- ElementNotFoundError
|-- AmbiguousMatchError
|-- WaitTimeoutError
|-- ApplicationError
|-- WindowNotFoundError
|-- AliasNotFoundError
|-- UnsupportedPatternError
|-- UnsupportedCapabilityError
|-- DelphiError            (Delphi / VCL)
|-- MainframeError         (3270 / 5250)
|-- OracleFormsError       (Oracle Forms)
|-- CDPStalePageError      (Electron / CEF)
|-- QtAgentInjectError     (Qt agent — injection refused or failed)
`-- QtAgentRpcError        (Qt agent — protocol / transport failure)
    `-- QtAgentTimeoutError    (recoverable — the connection stays usable)
```

The two Qt agent errors also inherit from `RuntimeError`, which they raised
exclusively before 0.2.0, so code that already catches `RuntimeError` keeps
working.

`QtAgentTimeoutError` is the one Qt agent failure that is **not** terminal:
the request is abandoned but the connection remains usable, so a wedged Qt
event loop (a native modal dialog, say) does not cost you the agent.

```python
from dolphin_desktop import QtAgentTimeoutError, QtAgentRpcError

try:
    value = widget.get_property("text")
except QtAgentTimeoutError:
    value = widget.get_property("text")   # the app was just busy; retry
except QtAgentRpcError:
    agent = app.qt_agent                  # genuine desync — rebuilt on access
```
