# Excel / Word / PowerPoint / Outlook (Office COM)

Office automation via `win32com.client`. Requires Microsoft Office to be installed.

The public COM entry points are `ExcelApp`, `WordApp`, `PowerPointApp` and
`OutlookApp`, exported from `dolphin_desktop`. Outlook requires classic Outlook
and a configured MAPI profile. Ribbon controls and dialogs use UIA/Win32
separately; see the [Support Matrix](../support-matrix.md#office-com-excel-word-powerpoint-classic-outlook).

---

## ExcelApp

::: dolphin_desktop._office.ExcelApp

---

## ExcelWorkbook

::: dolphin_desktop._office.ExcelWorkbook

---

## ExcelSheet

::: dolphin_desktop._office.ExcelSheet

---

## ExcelCell

::: dolphin_desktop._office.ExcelCell

---

## ExcelRange

::: dolphin_desktop._office.ExcelRange

---

## WordApp

::: dolphin_desktop._office.WordApp

---

## WordDocument

::: dolphin_desktop._office.WordDocument

---

## PowerPointApp

::: dolphin_desktop._powerpoint.PowerPointApp

## PowerPointPresentation

::: dolphin_desktop._powerpoint.PowerPointPresentation

## PowerPointSlide

::: dolphin_desktop._powerpoint.PowerPointSlide

## PowerPointShape

::: dolphin_desktop._powerpoint.PowerPointShape

## PowerPointTextRange

::: dolphin_desktop._powerpoint.PowerPointTextRange

## PowerPointTable

::: dolphin_desktop._powerpoint.PowerPointTable

## PowerPointChart

::: dolphin_desktop._powerpoint.PowerPointChart

## OutlookApp

::: dolphin_desktop._outlook.OutlookApp

## OutlookMail

::: dolphin_desktop._outlook.OutlookMail
    options:
      inherited_members: true

## OutlookAppointment

::: dolphin_desktop._outlook.OutlookAppointment
    options:
      inherited_members: true

## OutlookFolder

::: dolphin_desktop._outlook.OutlookFolder

## OutlookAttachment

::: dolphin_desktop._outlook.OutlookAttachment

## Quick reference

```python
from dolphin_desktop import ExcelApp, WordApp

# Excel
with ExcelApp.open("report.xlsx") as xl:
    ws = xl.active_workbook.sheet("Sheet1")
    ws.cell(1, 1).value = "Name"
    ws.range("A2:B5").value = [["Alice", 95], ["Bob", 87], ...]
    xl.active_workbook.save()

# Word
with WordApp.open("template.docx") as wd:
    doc = wd.active_document
    doc.find_replace("{{NAME}}", "John Doe")
    doc.save_as("output.docx")
```

See the [Office automation guide](../guides/office.md) for full examples.
