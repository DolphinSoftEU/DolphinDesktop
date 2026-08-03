# SAP GUI for Windows

dolphin can automate classic SAP GUI for Windows through the SAP GUI Scripting
COM API. This is separate from the `uia` and `win32` backends because SAP exposes
stable component IDs and session objects through its own automation model.

## Requirements

- SAP GUI for Windows is installed and running.
- SAP GUI Scripting is enabled in the local SAP GUI client options
  (Options → Accessibility & Scripting → Scripting → *Enable scripting*).
- SAP GUI Scripting is allowed by the target SAP system: profile
  parameter `sapgui/user_scripting = TRUE` (set it in **RZ11** for the
  running instance, or in the instance profile via RZ10 to make it
  survive a restart). Without it the scripting engine still attaches and
  lists the connection, but the connection exposes **zero sessions** —
  which is the usual reason a working setup suddenly finds nothing.
- Tests run on Windows with `pywin32` available.

### The scripting-security notification

By default SAP GUI raises a modal notification the first time a script
attaches ("A script is trying to attach to SAP GUI"). It is modal to the
scripting call, so an unattended run stalls behind it.

`SapGui.scripting_security_handler()` starts a background thread that
confirms exactly that dialog — start it **before** connecting, since the
dialog blocks the attach itself:

```python
stop = SapGui.scripting_security_handler()
try:
    sap = SapGui.connect(timeout=30)
    ...
finally:
    stop.set()
```

To silence the prompts for good on a dedicated test machine, clear
*Notify when a script attaches to SAP GUI* and *Notify when a script
opens a connection* in the same SAP GUI options page.

## Basic Usage

```python
from dolphin_desktop import SapGui

sap = SapGui.connect()
session = sap.session(connection=0, session=0)

session.transaction("SE16")
session.find_by_id("wnd[0]/usr/ctxtDATABROWSE-TABLENAME").set_text("T000")
session.find_by_id("wnd[0]/tbar[1]/btn[8]").click()

assert session.find_by_id("wnd[0]/sbar").text() is not None
```

You can also connect through `Desktop` when a test already uses Dolphin desktop
fixtures:

```python
def test_sap_from_desktop(desktop):
    sap = desktop.sap()
    session = sap.session()
    session.transaction("VA03")
```

## Locators

SAP component IDs are the preferred selector. They are the same IDs visible in
SAP GUI Scripting recordings and usually look like `wnd[0]/usr/...`.

```python
customer = session.find_by_id("wnd[0]/usr/ctxtVBAK-KUNNR")
customer.set_text("100000")
assert customer.value() == "100000"
```

`SapLocator` is lazy: dolphin does not query SAP until an action or assertion is
called. Actions wait for `session.Busy` to become false and retry until the
configured Dolphin timeout expires.

## Finding Component IDs (Spy)

The regular `uia`/`win32` Object Spy cannot see SAP component IDs — SAP GUI is
opaque to the UIA tree. Use the SAP-aware spy instead, which drives SAP GUI
Scripting directly and emits `find_by_id(...)` locators.

```bash
# Print the SAP component tree of the active session
dolphin spy --sap

# Pick a control interactively (Ctrl+Click highlights and captures it)
dolphin spy --sap --pick

# Select a non-default connection/session, limit depth, or emit JSON
dolphin spy --sap --connection 0 --session 0 --depth 4
dolphin spy --sap --json
```

The headless Python API mirrors the desktop spy:

```python
import dolphin_desktop.spy as spy

tree = spy.sap_inspect()                 # {"schema_version": 1, "root": {...}}
print(spy.format_sap_tree(tree["root"]))

picked = spy.sap_pick()
# {"schema_version": 1, "status": "ok",
#  "selector": {"id": "wnd[0]/usr/ctxtVBAK-KUNNR"}, "message": ""}
if picked["status"] == "ok":
    sel = picked["selector"]
```

Each node exposes the session-relative `id` (the locator Dolphin uses), the
absolute `full_id`, plus `name`, `type`, `text`, `bounding_box`, and
`changeable`. The `id` is the most stable selector, so `sap_pick()` returns it
whenever a control exposes one.

`status` is `"ok"`, `"cancelled"` (Esc) or `"no_session"`; `selector` is empty
for anything but `"ok"`. Check it rather than assuming a pick succeeded —
`sap_inspect()` carries the same `schema_version`, so a tool consuming either
can detect a format change.

## Transactions And Keys

`transaction("SE16")` writes `/nSE16` to the OK Code field and sends Enter.

For lower-level key handling, use SAP virtual keys:

```python
session.send_vkey("ENTER")
session.send_vkey("F8")
session.find_by_id("wnd[0]/usr/ctxtFIELD").press_key("F4")
```

Common aliases include `ENTER`, `F3`/`BACK`, `F8`/`EXECUTE`, `F11`/`SAVE`, and
`F12`/`CANCEL`.

## Raw COM Access

When a SAP control needs a method that Dolphin does not wrap yet, resolve the
component and use the underlying COM object:

```python
grid = session.find_by_id("wnd[0]/usr/cntlGRID1/shellcont/shell").raw
grid.currentCellRow = 0
grid.selectedRows = "0"
```

This is useful for ALV grids and custom SAP controls. Dedicated high-level grid
helpers are intentionally outside the first SAP support version.

## Running the bundled SAP suite

`tests/sap/` drives a live system and keeps every credential out of the
repository — it reads them from the environment and skips when they are
missing:

| Variable | Meaning |
|---|---|
| `DOLPHIN_SAP_USER` | logon user |
| `DOLPHIN_SAP_PASSWORD` | logon password |
| `DOLPHIN_SAP_CLIENT` | client / mandant, e.g. `001` |
| `DOLPHIN_SAP_LANG` | logon language (default `EN`) |
| `DOLPHIN_SAP_CONNECTION` | SAP Logon entry to open when no session is running |

```powershell
$env:DOLPHIN_SAP_USER = "..."; $env:DOLPHIN_SAP_PASSWORD = "..."
$env:DOLPHIN_SAP_CLIENT = "001"
pytest tests/sap -v
```

The fixtures attach to a running SAP GUI and sign in **only** when the
session sits on the logon screen, so an already-signed-in session is used
as-is and never disturbed. Every test in the suite is display-only.

## Notes

- A runnable example that attaches to an already-open SAP GUI session
  lives at `examples/sap_gui/test_sap_gui.py`.
- SAP GUI Scripting is session-global; avoid running SAP GUI tests in parallel
  against the same desktop session.
- If `SapGui.connect()` fails, confirm that SAP GUI is already running and that
  scripting is enabled on both the client and server.
- SAP Business Client and browser-based SAP apps are not covered by this API.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `SapConnection: could not attach to running SAP GUI` | SAP GUI is not running, or scripting is disabled on the client | Launch SAP Logon → Options → Accessibility → Scripting → Enable scripting; uncheck "Notify when a script attaches" and "Notify when a script opens a connection" |
| `SapGui.connect(): timeout waiting for GuiApplication` | Multiple SAP GUI processes; COM binds to the wrong one | Close every SAP GUI window, relaunch the one you want tested, then run the test |
| `permission denied` on `sap.connections()` | Server-side scripting disabled | Have Basis enable in RZ11: `sapgui/user_scripting = TRUE`, `sapgui/user_scripting_disable_recording = FALSE` |
| `SapLocator` returns `None` for a visible field | Field is inside a nested `GuiShell` tree | Use the full ID from the SAP recorder — dolphin's fuzzy matching does not cross shell boundaries |
| Test kills the user's SAP session on teardown | The SAP GUI process is on dolphin's per-test kill list | dolphin only reaps processes it launched, so attach rather than launch SAP GUI; if you did launch it, call `app.detach(session=True)` on that `Application` |
| `cell_value()` returns empty for numeric cells | SAP represents empty and zero differently per column | Read the column with `cell_value(row, column)` and compare against `""` explicitly rather than truthiness |
| Text shows Unicode boxes | Windows locale lacks the SAP session codepage | Install the matching Windows language pack or use `session.system_info()["language"]` to detect and skip |
