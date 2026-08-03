# Oracle Forms automation

dolphin_desktop automates Oracle Forms Java clients — the Java Web
Start applet, Forms Standalone Launcher (FSAL), or a plain Java main
class — through the Java Access Bridge (JAB). The public surface
speaks Forms concepts (**blocks**, **items**, **function keys**,
**LOV**, **status line**) rather than raw Swing components, so tests
port cleanly across Forms 10g / 11g / 12c and remain readable.

## What works

* **Direct JAB API** for reads and writes. `type_text()` uses
  `setTextContents`; `text()` / `value()` use `getAccessibleTextRange`;
  buttons and menu items invoke via `doAccessibleActions`. Zero mouse
  cursor movement, zero global keyboard capture — the tests run
  reliably from background processes, RDP sessions, and CI machines
  where `SetForegroundWindow` is blocked by the OS.
* **Function keys** (F1–F12 + Shift/Ctrl/Alt combinations) delivered
  via `PostMessage(WM_KEYDOWN)` to the target HWND. Works without
  foregrounding the window; hits the Swing `InputMap`/`ActionMap`
  chain the way a real user's keystroke would.
* **Menu bar** navigation via ALT+first-letter mnemonic and JAB action
  invocation on the resolved menu item.
* **List of Values (F9)** wrapper — `.lov().select("SMITH")` opens
  and completes the LOV popup.
* **Status line** reader — pulls the current text from
  `AccessibleContext.getAccessibleDescription()` (the standard place
  Oracle Forms publishes dynamic status text).

## Quick start

```python
from dolphin_desktop import Desktop, OracleFormsKey

app = Desktop().launch_oracle_forms(
    jnlp="http://forms.example.com/forms/frmservlet?config=hr",
)
form = app.form()
form.wait_ready(timeout=30)

# Type into a block item, run the query, save.
app.block("EMPLOYEES").item("EMPNO").type_text("7369")
app.execute_query()             # F8
assert "Record 1" in app.status_line()
app.save()                      # F10 (see caveat below)

# LOV
app.list_of_values()            # F9
app.lov().select("JONES")

# Menu bar
app.menu("Query").select("Enter")
```

## Function-key reference

!!! danger "This table is an all-or-nothing choice"

    The Forms runtime loads **exactly one** key-binding resource file and
    every key comes from it. The values below follow the **PC-style**
    family (`fmrpcweb.res`). A host running the *stock* `fmrweb.res` uses a
    different family — and there, these bindings mean:

    * `duplicate_record()` → **Exit**, discarding uncommitted changes
    * `insert_record()` → **Clear Record**
    * `delete_record()` → **Duplicate Record**
    * `clear_form()` → **Next Primary Key**

    Press **Ctrl+K** ("Show Keys") in the running form to see what your
    runtime actually loaded. If it does not match, override the **whole**
    block — never a single entry, because a borrowed entry silently maps to
    a different Forms function.

| Attribute | Key | Oracle Forms action |
|---|---|---|
| `HELP` | F1 | Help topic |
| `COUNT_QUERY` | Shift+F2 | Count matching records ¹ |
| `DUPLICATE_ITEM` | F3 | Copy item from previous record ¹ |
| `DUPLICATE_RECORD` | F4 | Copy the previous record |
| `CLEAR_RECORD` | Shift+F4 | Clear current record |
| `CLEAR_BLOCK` | Shift+F5 | Clear all records in block ¹ |
| `INSERT_RECORD` | F6 | Insert new record |
| `DELETE_RECORD` | Shift+F6 | Delete current record |
| `ENTER_QUERY` | F7 | Enter query mode |
| `CLEAR_FORM` | Shift+F7 | Clear all records in form ¹ |
| `EXECUTE_QUERY` | F8 | Run the query |
| `LIST_OF_VALUES` | F9 | Open LOV popup |
| `SAVE` / `COMMIT` | F10 | Save changes |
| `NEXT_ITEM` | Tab | Move to next item |
| `PREVIOUS_ITEM` | Shift+Tab | Move to previous item |
| `NEXT_RECORD` | Shift+Down | Move to next record |
| `PREVIOUS_RECORD` | Shift+Up | Move to previous record |
| `NEXT_BLOCK` | Shift+Page_Down | Move to next block ¹ |
| `PREVIOUS_BLOCK` | Shift+Page_Up | Move to previous block ¹ |
| `CLEAR_ITEM` | Ctrl+U | Clear current item |
| `EXIT` / `CANCEL_QUERY` | Ctrl+Q | Exit form / leave query mode |

¹ Consistent with the PC-style family but not confirmed against a
published resource file — verify with Ctrl+K before relying on it.

There is no *First Record* or *Last Record* function in the Forms key
table, so the library exposes no binding for them. On a stock-file host
F11 is **Enter Query**, which is why a "go to first record" helper built
on F11 would silently put the block into query mode.

**Modifier shortcuts need the foreground.** `PostMessage` cannot set the
target thread's modifier state, so anything with `Shift+`/`Ctrl+` is
delivered by `SendInput` against the foreground window. Unmodified keys
(F7, F8, F9, F10) work from background runs, RDP sessions and CI hosts;
modifier combos need a usable input desktop.

Send any of these with `app.function_key(OracleFormsKey.X)` or use the
named shortcuts (`app.enter_query()`, `app.execute_query()`, …).

**F10 caveat.** Swing intercepts F10 for menu-bar activation before
the InputMap fires. In real Forms 12c the Save keybinding still works
because Forms overrides the default; in the mock we ship, F10 opens
the menu bar. If your target Forms build has the same limitation, use
`app.menu("Action").select("Save")` instead.

## Enabling the Java Access Bridge

The JAB is bundled with Adoptium and Oracle JDKs. dolphin's
`JavaAccessBridge` handles the setup automatically, or run once
by hand:

```powershell
jabswitch.exe /enable
```

The JVM the Forms client uses must be launched with
`-Djava.accessibility=true` (dolphin's `launch_oracle_forms` adds this
flag by default) so the JVM loads the JAB helper on startup.

## Backend selection

```python
# Java Web Start (real Oracle Forms deployment)
app = desktop.launch_oracle_forms(jnlp="http://forms.example.com/frmservlet")

# Forms Standalone Launcher (FSAL) — bundled JAR
app = desktop.launch_oracle_forms(jar=r"C:\forms\frmsal.jar")

# Local Java main class (used by the integration test suite)
app = desktop.launch_oracle_forms(
    main_class="OracleFormsMock",
    classpath=r"C:\path\to\classes",
)

# Attach to an already-running client — no launch
app = desktop.attach_oracle_forms(title_re=".*HR Payroll Form.*")
```

## Item locator convention

Oracle Forms items are named `BLOCK.ITEM` in the accessible tree
(`.getAccessibleContext().setAccessibleName("EMPLOYEES.EMPNO")`).
`.block("EMPLOYEES").item("EMPNO")` tries the qualified name first, then
falls back to the plain item name.

If your Forms build only publishes plain names, `.item("EMPNO")` on
the app directly works too.

## Testing your own forms

The recommended pattern:

```python
import pytest
from dolphin_desktop import Desktop

@pytest.fixture(scope="module")
def form_app():
    app = Desktop().launch_oracle_forms(jnlp=YOUR_JNLP)
    app.application.detach()  # survive per-test PID reaping
    app.form().wait_ready(timeout=30)
    yield app
    app.close()


def test_query_runs(form_app):
    form_app.block("EMPLOYEES").item("EMPNO").type_text("7369")
    form_app.execute_query()
    assert "Record 1" in form_app.status_line()
```

The `tests/oracle_forms/` directory ships a full
Swing mock and a 14-test integration suite as a reference.

## Common failure modes

**`OracleFormsError: no top-level window found`**
: The JVM launched but its window is not surfaced to pywinauto. Check
  that JAB is enabled (`jabswitch /enable`) and that
  `windowsaccessbridge-64.dll` is on PATH.

**`Oracle Forms window did not become ready`**
: JAB never established a root-context handshake. Increase
  `startup_delay` on `launch_oracle_forms`, or verify with
  `JavaAccessBridge.is_enabled()`.

**Item read returns empty string**
: The target field does not publish `AccessibleText`. Some older
  Forms builds only publish accessible name. Fall back to
  `item._jab.description()` or use image-based verification.

**Function key does not fire**
: The target Forms window may filter WM_KEYDOWN. Fall back to
  `bring_to_foreground()` + `Keyboard.type("{F8}", escape=False)`, or bind the same
  action to a menu entry and use `app.menu("Query").select("Execute")`.
