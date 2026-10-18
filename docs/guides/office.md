# Office

Dolphin automates Excel, Word, PowerPoint and classic Outlook through COM wrappers.
These helpers require the corresponding Microsoft Office applications and pywin32 on Windows.

## Supported applications

The dedicated Office COM API exports `ExcelApp`, `WordApp`, `PowerPointApp`
and `OutlookApp` from `dolphin_desktop`. Outlook requires classic Outlook with
an existing configured MAPI profile; new Outlook is outside this COM API.
Ribbon controls and dialogs still use the generic `Desktop` UIA/Win32 API.
See the [Support Matrix](../support-matrix.md#office-com-excel-word-powerpoint-classic-outlook).

## Macro security

`ExcelApp.open()` and `WordApp.open()` default to `allow_macros=False`.
They set `AutomationSecurity=3` immediately before opening the file to disable
VBA macros, then restore the previous value in `finally`, including when opening
fails. Excel also sets `AskToUpdateLinks=False` and opens with `UpdateLinks=0`;
Word sets `Options.UpdateLinksAtOpen=False` to prevent automatic OLE link
updates and restores its previous value in `finally`. Enabling macros does
not enable these external link updates.
See Microsoft's [UpdateLinksAtOpen documentation](https://learn.microsoft.com/en-us/office/vba/api/word.options.updatelinksatopen).

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

The real Office suite generates harmless marker-only macro documents and links
to controlled local Excel/Word documents in pytest's temporary directory:

```powershell
uv run pytest tests/office -q -rs --junitxml artifacts/KAN-574-office-retest.xml
```

It requires registered Excel/Word COM servers. Creating macro fixtures also
requires **existing** trusted access to the VBA project object model; when that
is blocked, the macro cases report skips without changing security settings.
Each macro case first opens a separate copy with `allow_macros=True` and requires
the marker to change. Only then does it open a fresh copy with
`allow_macros=False` and assert that the marker stays unchanged. If policy blocks
the positive control, the entire case is skipped; the default open cannot be
reported as a security pass. The local external-link cases do not require VBA
project access. The Word case also explicitly updates the field afterwards to
prove that the source is reachable and changed. A skip is not evidence that the
corresponding security criterion passed.

The mandatory CI unit job runs the COM stand-in tests with `pytest -m unit`.
It does not run this `system` / `integration` suite or verify the real Office
macro engine. Record a separate Windows/Office run, listing passed, failed and
skipped cases; see the [KAN-574 retest record](https://github.com/DolphinSoftEU/DolphinDesktop/blob/master/SecurityTasks/KAN-574-retest.md).

For a real Office retest, use an isolated Windows account with reviewed `.xlsm`
and `.docm` fixtures whose `Workbook_Open` / `Document_Open` handlers only write
a fixed marker in the document (for example, cell A1 / document text). First
open a separate trusted copy with `allow_macros=True` and require the marker to
change, then open a fresh copy with defaults and require it to stay unchanged.
Skip the entire scenario if the positive control is blocked. For Excel, use a
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

## PowerPoint

```python
from dolphin_desktop import PowerPointApp

with PowerPointApp.create(visible=False) as ppt:
    presentation = ppt.active_presentation
    slide = presentation.add_slide()  # blank layout, appended at the end
    caption = slide.add_textbox("Quarterly report")
    caption.text = "Quarterly report — reviewed"
    presentation.save_as("report.pptx")
    presentation.export_pdf("report.pdf")

with PowerPointApp.open("report.pptx", visible=False) as ppt:
    print(ppt.active_presentation.slide(1).shape(1).text)
```

Slide and shape indexes are 1-based; shapes can also be selected by name.
`open()` disables VBA macros by default and restores the previous
`AutomationSecurity` setting after opening, including on failure. As with the
other Office wrappers, `allow_macros=True` is an explicit opt-in for trusted
files; Office policies still apply. This is not a sandbox for linked content.
Opening a presentation already open in PowerPoint raises `RuntimeError` so the
wrapper cannot accidentally take ownership of the operator's document.

PowerPoint is a shared COM server. `connect()` attaches to its active
presentation; `open()` / `create()` expose the presentation they opened/created
even when it has no window. In shared mode, `quit()` and context exit close only
those owned presentations and release COM references, preserving the process. Save explicitly or use
`quit(save_changes=True)` to retain changes. Failed saves/closes raise and keep
the affected presentation available for retry.

`export_pdf()` uses PowerPoint's `ExportAsFixedFormat` with PDF output. Real
acceptance tests must parse the resulting PDF and verify page count/content,
not just check COM arguments or the file extension.

## Classic Outlook

```python
from dolphin_desktop import OutlookApp, OutlookMail

with OutlookApp.start() as outlook:  # starts or reuses classic Outlook
    draft = outlook.create_mail(subject="Local report", body="Review the attachment.")
    draft.add_attachment("report.pdf")
    draft.save_as("report.msg")  # local file only; does not send

with OutlookApp.start() as outlook:
    mail = outlook.open_item("report.msg")
    assert isinstance(mail, OutlookMail)
    print(mail.subject, mail.body)
    mail.attachment(1).save_as("report-copy.pdf")
    print(outlook.default_folder("inbox").item_count)
```

`connect()` requires an already running classic Outlook session. `start()` may
reuse the operator's session; neither method logs on/off or configures profiles.
`start()` checks registered MAPI profiles before COM activation, then validates
the active profile and accessible stores. Missing/unusable profiles raise
`RuntimeError`. Missing folders/items raise `LookupError`.
Folders support `item(1)` and `folder("Child")`; supported default folder names
are `inbox`, `drafts`, `sent`, `deleted`, `calendar`, or an `OlDefaultFolders`
integer. Items other than mail and appointments raise `TypeError` on lookup.

Drafts created by this `OutlookApp` support `move(folder)` and `delete()`.
`move()` updates the existing wrapper to the COM object returned by Outlook;
`find_item(entry_id)` can verify its absence/presence in source/destination
folders. Folder lookup recognizes owned drafts by EntryID **and** StoreID.
Pre-existing messages, sent messages and locally opened MSG files cannot be
moved/deleted by this API. Destinations must come from the creating facade.

`delete()` moves an owned draft to its store's Deleted Items folder. A repeated
call raises instead of silently deleting permanently. For UAT data cleanup,
`delete(permanent=True)` explicitly removes only an owned draft already there.

`create_appointment(subject=...)` returns a calendar item with editable `start`
and `end` Python datetimes (Outlook local time), `location`, `subject` and `body`.
It can be saved to a local MSG file or explicitly persisted to the calendar
with `save()`. Creating/saving appointments does not send invitations.

Mail supports `to`, `cc`, `bcc`, `subject`, `body`, `html_body` and attachments.
`save()` stores a draft; only an explicit `mail.send()` sends a message using
Outlook's configured account. Outlook security prompts/policies remain active.
Context exit and `quit()` discard unsaved changes to only the items created or
opened here; saved drafts/appointments remain saved. Items retrieved from
existing folders are not automatically closed. Shared Outlook sessions are
preserved. Failed saves/closes raise and retain the affected items for retry.

## Exclusive Office sessions for UAT

`PowerPointApp.create/open(..., exclusive=True)` and
`OutlookApp.start(exclusive=True)` require **no existing** process of that Office
application. Use a dedicated test account without concurrent Office launches.
This mode verifies the new process's image/creation time and retains its process
handle. `owns_process` and `process_id` expose the resulting ownership.

After COM activation, exclusive startup polls process visibility for up to
five seconds, servicing COM messages between attempts. Multiple candidates or
a failed identity check stop initialization immediately. Failed activation,
visibility timeout or ownership verification reports observed PIDs and a final
snapshot of possible orphaned processes. A failed snapshot is reported as
unknown, not as absence. No unverified process is claimed or terminated;
inspect the reported processes before retrying. Office may still start after
the timeout, so a diagnostic snapshot without candidates cannot prove that no
process will appear later.

After document/item cleanup, `quit()` calls `Application.Quit()`, releases COM
references and waits for that process handle to signal exit while pumping COM
messages. A shutdown timeout raises and can be retried; processes are never
force-killed. Foreign open presentations block exclusive PowerPoint shutdown.
Objects from a closed facade are no longer usable. Shared mode (the default)
and `connect()` never acquire process ownership or quit the operator's session.

The DESKTOP-240/241 UAT scenarios live in the separate `dolphin-desktop.ta`
repository, under `tests/zt_16_office_com`. They distinguish pre-existing and
owned processes and validate PDF content independently. Outlook draft UAT
requires `DOLPHIN_OUTLOOK_UAT=1`, a configured nonproduction profile/mailbox,
and `DOLPHIN_OUTLOOK_UAT_FOLDER` naming a controlled subfolder of Drafts. Its
synthetic drafts are moved, soft-deleted, verified and permanently cleaned up;
the scenarios never send mail. The missing-profile case uses a Windows account
without configured profiles and verifies no Outlook process was started.

Run the suite from that repository against the built/installed wheel:

```powershell
python -m pytest tests/zt_16_office_com -q -rs -o junit_family=legacy --junitxml artifacts/office-com.xml
```

Repeat on Office 2019 and Microsoft 365, x86 and x64, recording product/version/
bitness per run. A successful run on one installation does not establish PASS
for another, and skipped profile-dependent scenarios do not count as PASS.

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
