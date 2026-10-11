# Office

Dolphin automates Excel and Word through COM wrappers. These helpers require Microsoft Office and pywin32 on Windows.

## Macro security

`ExcelApp.open()` and `WordApp.open()` default to `allow_macros=False`.
They set `AutomationSecurity=3` immediately before opening the file to disable
VBA macros, then restore the previous value in `finally`, including when opening
fails. Excel also sets `AskToUpdateLinks=False` and opens with `UpdateLinks=0`;
enabling macros does not enable external link updates.

!!! warning "Macro execution requires explicit trust"

    `allow_macros=True` selects `msoAutomationSecurityLow` (`1`). VBA macros can
    execute arbitrary code with your account's permissions. Use this opt-in only
    with trusted documents. Office policies, Protected View and other security
    controls can still prevent execution; Dolphin does not bypass them.

```python
from dolphin_desktop import ExcelApp, WordApp

# Explicitly permit macros only in files whose code you trust.
with ExcelApp.open("trusted.xlsm", allow_macros=True) as xl:
    print(xl.active_sheet.name)

with WordApp.open("trusted.docm", allow_macros=True) as wd:
    print(wd.active_document.name)
```

`AutomationSecurity=3` does **not** disable Excel 4.0/XLM macros; Excel can still
display a security prompt for them. Enforce XLM restrictions through Office
policy and never approve active content from an untrusted file. See Microsoft's
[AutomationSecurity documentation](https://learn.microsoft.com/en-us/office/vba/api/excel.application.automationsecurity).
`UpdateLinks=0` controls workbook external links, not every data connection or
other type of active content. These wrappers are not a document sandbox.

The security setting applies to the file opened by `open()` and is restored
afterwards. `connect()` does not change security or disable macros in documents
already open. Documents remain editable because the API supports modifying and
saving them; read-only mode would not itself prevent macro execution.

### Security regression checks

Run the deterministic COM stand-in tests without Office or real macros:

```powershell
uv run pytest tests/framework/test_office_com.py tests/framework/test_office.py -q
```

The synthetic auto-open macro only increments an in-memory counter. Tests cover
default blocking, explicit opt-in, link arguments, restoration after errors,
rejected security settings, a simulated policy block and an XLM prompt.
These checks verify the wrapper contract, not the Office macro engine.

The real Office suite generates harmless marker-only macro documents and a link
to a controlled local workbook in pytest's temporary directory:

```powershell
uv run pytest tests/office -q -rs
```

It requires registered Excel/Word COM servers. Creating macro fixtures also
requires **existing** trusted access to the VBA project object model; when that
is blocked, the macro cases report skips without changing security settings.
An opt-in case also reports a skip if Office still blocks execution. The local
external-link case does not require VBA project access. A skip is not evidence
that the corresponding security criterion passed.

For a real Office retest, use an isolated Windows account with reviewed `.xlsm`
and `.docm` fixtures whose `Workbook_Open` / `Document_Open` handlers only write
a fixed marker in the document (for example, cell A1 / document text). Open a
fresh copy with defaults and assert that the marker stays unchanged. A separate
trusted-copy run with `allow_macros=True` should change it if policy permits;
record a policy block separately from successful execution. For Excel, use a
link to a controlled local workbook with a changed value and verify that the
cached value is retained on open. Test XLM separately under a policy that blocks
XLM. Never weaken Trust Center or organization policies to make a test pass.

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

!!! note "`open()` and `connect()` own different things"

    `open()` starts a **private** Excel instance, so leaving the `with` block
    closes the workbooks it opened and quits that instance. `connect()`
    attaches to the operator's running Excel — leaving the block closes
    nothing and quits nothing, because those workbooks are not yours. The
    same rule applies to `WordApp`.

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

A connected instance is left running when the block exits — see the note
above.

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
