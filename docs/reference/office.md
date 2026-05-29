# Excel / Word (Office COM)

Office automation via `win32com.client`. Requires Microsoft Office to be installed.

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
