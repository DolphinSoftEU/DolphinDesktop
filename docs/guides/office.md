# Office

Dolphin automates Excel and Word through COM wrappers. These helpers require Microsoft Office and pywin32 on Windows.

## Excel

```python
from dolphin_desktop import ExcelApp

with ExcelApp.open("report.xlsx") as xl:
    wb = xl.active_workbook
    sheet = wb.sheet("Sheet1")

    sheet.cell(1, 1).value = "Name"
    sheet.cell(1, 2).value = "Score"
    sheet.cell(2, 1).value = "Alice"
    sheet.cell(2, 2).value = 95

    wb.save()
```

Read a range:

```python
with ExcelApp.open("report.xlsx") as xl:
    rows = xl.active_workbook.sheet("Sheet1").range("A1:B2").as_list()
```

Connect to an already running Excel instance:

```python
with ExcelApp.connect() as xl:
    print(xl.active_sheet.name)
```

## Word

```python
from dolphin_desktop import WordApp

with WordApp.open("template.docx") as wd:
    doc = wd.active_document
    doc.find_replace("{{NAME}}", "Alice")
    doc.save_as("output.docx")
```

Connect to an already running Word instance:

```python
with WordApp.connect() as wd:
    print(wd.active_document.name)
```

## Combining UI And Office Checks

```python
from dolphin_desktop import Desktop, ExcelApp, FileDialog

desktop = Desktop()
app = desktop.launch("MyDataApp.exe")
win = app.window(title="Data Export")
win.get_by_title("Export").click()

dlg = FileDialog.wait_for(timeout=10)
dlg.set_path(r"C:\Temp\export.xlsx")
dlg.confirm()

with ExcelApp.open(r"C:\Temp\export.xlsx") as xl:
    sheet = xl.active_workbook.sheet("Sheet1")
    assert sheet.cell(1, 1).text == "Name"
```

## Notes

- Context managers call `quit()` on exit.
- `ExcelApp.connect()` and `WordApp.connect()` attach to the active Office instance.
- COM automation is process-global and should not be used casually from parallel tests.
