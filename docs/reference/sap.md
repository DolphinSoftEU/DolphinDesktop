# SAP GUI for Windows

SAP GUI automation via SAP GUI Scripting COM. Requires SAP GUI for Windows with
scripting enabled.

---

## SapGui

::: dolphin_desktop._sap.SapGui

---

## SapConnection

::: dolphin_desktop._sap.SapConnection

---

## SapSession

::: dolphin_desktop._sap.SapSession

---

## SapLocator

::: dolphin_desktop._sap.SapLocator

---

## Quick reference

```python
from dolphin_desktop import SapGui

sap = SapGui.connect()
session = sap.session()

session.transaction("SE16")
session.find_by_id("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").set_text("T000")
session.find_by_id("wnd[0]/tbar[1]/btn[8]").click()

assert session.find_by_id("wnd[0]/sbar").text() is not None
```

See the [SAP GUI guide](../guides/sap.md) for usage notes and requirements.
