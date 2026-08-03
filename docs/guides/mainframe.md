# Mainframe terminal (3270 / 5250) automation

dolphin_desktop drives IBM mainframe (**3270**, z/OS TSO/CICS) and
midrange (**5250**, IBM i) sessions through three interchangeable
backends:

| Backend | Command | When to use |
|---|---|---|
| `s3270` (default) | Spawns `ws3270` / `s3270` subprocess | Open-source, 3270 hosts. CI-friendly. Requires wc3270 / x3270 package installed. |
| `tn5250` | Pure-Python TN5250 client (no extra dependency) | IBM i / AS400 hosts (pub400.com, banks, ERP). Real TN5250 negotiation, not NVT fallback. |
| `hllapi` | Attaches to a running enterprise emulator via EHLLAPI DLL | Production: IBM PCOMM, Attachmate/Rocket Reflection, Micro Focus RUMBA. |

All three backends implement the same `MainframeTerminal` surface — user
code is portable between them.

## Quick start

```python
from dolphin_desktop import Desktop, AID

with Desktop().mainframe(host="tso.host.example", session_type="3270") as term:
    term.wait_ready()
    term.field_after("USERID").type_text("MYUSER")
    term.field_after("PASSWORD").type_text("s3cret")
    term.press(AID.ENTER)
    term.wait_change()
    assert term.screen().contains("READY")
```

## Install

### s3270 backend (open source, default)

**Windows** — download the wc3270 portable ZIP:

```powershell
# from a browser or:
Invoke-WebRequest `
  https://downloads.sourceforge.net/project/x3270/x3270/4.5ga5/wc3270-4.5ga5-noinstall-64.zip `
  -OutFile $env:TEMP\wc3270.zip
Expand-Archive $env:TEMP\wc3270.zip -DestinationPath "$env:LOCALAPPDATA\wc3270"
```

The `_find_s3270()` probe auto-detects `%LOCALAPPDATA%\wc3270\ws3270.exe`
and the standard Program Files paths. Pass `ws3270_path=` explicitly to
`Desktop.mainframe()` for non-standard installs.

**Linux/macOS** — dolphin itself runs on Windows only; use these
instructions to prepare a remote/CI host or to work with the
pure-protocol pieces. Install via package manager:

```bash
# Debian/Ubuntu
sudo apt install x3270

# macOS
brew install x3270
```

### HLLAPI backend (enterprise emulators)

The emulator vendor ships an `EHLAPI32.DLL` (or `PCSHLL32.DLL` for IBM
PCOMM). dolphin_desktop auto-loads any of these:

| DLL name | Vendor |
|---|---|
| `PCSHLL32.DLL` | IBM Personal Communications (PCOMM) |
| `EHLAPI32.DLL` | Attachmate / Rocket Reflection |
| `WHLAPI32.DLL` | Older Rocket versions |
| `PCSHLL.DLL` | Legacy 16-bit shims |

If none load, pass `hllapi_dll_path=r"C:\path\to\your\DLL"`. The DLL
must be a **32-bit or 64-bit build matching the Python interpreter**
(a 64-bit Python cannot load a 32-bit DLL). PCOMM historically ships
32-bit only — use a 32-bit Python for those installs.

## API surface

### `MainframeTerminal`

| Method | Purpose |
|---|---|
| `connect(host, port, session_type, timeout)` | Open the session. Called automatically by `Desktop.mainframe(connect=True)`. |
| `disconnect()` | Idempotent teardown. |
| `is_connected()` | Session-live probe. |
| `screen() -> TerminalScreen` | Fresh snapshot of the presentation space. |
| `text() -> str` | Convenience — `screen().text()`. |
| `field(row=, col=, length=) -> TerminalField` | Locator for a positional field. |
| `field_after(label, length=20, row=None) -> TerminalField` | Locator immediately after `label` — skips separator chars. |
| `type_text(text)` | Type at the current cursor position. |
| `press(aid)` | Send an AID key (`"Enter"`, `"PF3"`, `"Clear"` …). Use the `AID` class or raw strings. |
| `move_cursor(row, col)` | Move the input cursor. **Only works in true 3270/5250 mode** (see NVT note below). |
| `wait_ready(timeout)` | Block until the keyboard unlocks. |
| `wait_change(timeout)` | Block until the host writes to the screen. |
| `wait_for_text(needle, timeout, row=)` | Poll until `needle` appears. |
| `is_keyboard_locked()` | True while the host is still writing. |
| context manager | `with desktop.mainframe(...) as term:` auto-disconnects. |

### `TerminalScreen`

Immutable 24×80 or 43×80 grid snapshot.

| Property / method | Purpose |
|---|---|
| `rows`, `cols` | Grid size. |
| `cursor -> (row, col)` | 1-indexed input cursor position. |
| `text()` | All rows joined by `\n`. |
| `line(row)` | Full text of one row. |
| `text_at(row, col, length)` | Substring — never IndexErrors past line end. |
| `contains(needle, row=None)` | Case-sensitive substring search. |
| `find(needle) -> (row, col) \| None` | First occurrence, 1-indexed. |

### `TerminalField`

Bound to a specific `(row, col, length)`.

| Method | Purpose |
|---|---|
| `read()` | Right-stripped current value. |
| `type_text(text, clear=True)` | Move cursor to field, clear it, type. |

### `AID`

| Attribute | AID key |
|---|---|
| `AID.ENTER` | Enter |
| `AID.CLEAR` | Clear |
| `AID.PA1` / `PA2` / `PA3` | Program Attention keys — **3270 only**. The `s3270` and `hllapi` backends send them; the native `tn5250` backend raises, because 5250 has no equivalent key to substitute. |
| `AID.pf(n)` | `"PF1"` .. `"PF24"` |

## pub400.com — public IBM i demo host

pub400.com is a public **IBM i** (formerly AS/400) host on port 23. It
accepts free sign-ups and is safe for automation experiments.

```python
with Desktop().mainframe(host="pub400.com", session_type="3270") as term:
    term.wait_ready()
    print(term.text())
```

## Native TN5250 backend status

The pure-Python TN5250 backend (`backend="tn5250"`) provides:

* **Correct TN5250 telnet negotiation** (BINARY + EOR + TERMINAL-TYPE as
  IBM-3477-FC + NEW-ENVIRON). No NVT fallback.
* **Complete WriteToDisplay parser**: SBA, IC, RA, SF orders, plus
  inline display-attribute bytes (0x20-0x3F range), each of which
  consumes one screen cell.
* **Start-Field parsing** with correct writable classification: an SF
  order marks an INPUT field by definition (5250 protocol); only the
  bypass bit (FFW hi 0x80) marks a display-only field. Pub400's user
  and password inputs are correctly detected as writable.
* **Screen buffer read** with pixel-perfect alignment on pub400's
  sign-on: `Your user name:` is contiguous on row 5 col 1,
  `Password (max. 128):` on row 6 col 1, etc.

The read side — screens, fields, cursor — is production-ready.

**Known limitations:**

* **The write/submit path is unverified against live IBM i hosts.**
  Client-to-host input records (RIF response with typed field data)
  build correctly at the byte level and follow RFC 1205 (GDS record
  type, opcode, payload structure), but live hosts such as pub400 do
  not visibly react to Enter/PF keys — there may be a flags or
  Save/Restore Screen precondition specific to IBM i's TN5250 server
  that the current implementation does not satisfy. For write-critical
  5250 flows, use the `hllapi` backend with an enterprise emulator.
* **No Write Structured Field (WSF) support** — Query, Set Reply Mode,
  Read Partition Query all fall through as unhandled. This means
  advanced Forms features (queries at cursor position, dynamic menus)
  are not driven, but they are not required for the standard sign-on
  and menu-navigation flow.

## pub400 through the s3270 backend — NVT fallback caveat

IBM i speaks **TN5250** natively. ws3270 only speaks **TN3270**, so
the negotiation degrades to **NVT** (raw telnet). In NVT mode:

* Reading the screen works — `screen()`, `text()`, `field_after()`,
  `contains()`, `find()` all behave normally.
* Writing single characters works — the host echoes them back and the
  cursor advances.
* **Positional `move_cursor`, field-level `TerminalField.type_text`,
  and AID keys (Enter/PF) do NOT trigger form submissions** — pub400
  ignores them because there is no 5250 record-descriptor to respond
  to.

For field-level 5250 **read** automation against pub400 use the shipped
**native TN5250 backend** (`backend="tn5250"` — see
[Native TN5250 backend status](#native-tn5250-backend-status) above);
for write-critical 5250 flows use the `hllapi` backend with an
enterprise emulator, since the native backend's write/submit path is
unverified against live IBM i hosts.
Against a real 3270 host (z/OS TSO, CICS) all positional operations
work through s3270 — the NVT degradation is pub400-specific.

The test suite in `tests/mainframe/pub400/` exercises
what NVT-mode *does* support:

```
test_ws3270_installed                          PASS
test_session_is_connected                      PASS
test_screen_size                               PASS
test_sign_on_screen_contains_pub400_banner     PASS
test_sign_on_screen_has_user_prompt            PASS
test_cursor_starts_in_user_field               PASS
test_field_after_user_name_label               PASS
test_screen_snapshots_are_stable               PASS
test_typed_char_is_echoed                      PASS
test_terminal_screen_line_and_text_at          PASS
test_terminal_screen_find                      PASS
```

## Backend selection

```python
# Default — s3270 subprocess (3270 hosts)
term = desktop.mainframe(host="host.example", session_type="3270")

# Explicit s3270 with custom binary path
term = desktop.mainframe(
    host="host.example",
    ws3270_path=r"D:\tools\wc3270\ws3270.exe",
    model="3279-2",           # 24x80 instead of 43x80
)

# Native TN5250 — for IBM i / AS400 hosts (pub400, banks, ERP AS400)
term = desktop.mainframe(
    host="pub400.com",
    port=23,
    session_type="5250",
    backend="tn5250",
)
# Pure-Python client, zero external dependency. Negotiates real 5250
# (not NVT fallback like s3270). Parses WriteToDisplay commands into
# a 24×80 EBCDIC screen buffer with field detection.

# HLLAPI — attach to a running PCOMM session "A"
term = desktop.mainframe(backend="hllapi", session_id="A")

# HLLAPI — explicit DLL
term = desktop.mainframe(
    backend="hllapi",
    session_id="A",
    hllapi_dll_path=r"C:\Program Files\IBM\Personal Communications\PCSHLL32.DLL",
)
```

The `host`, `port`, and `session_type` arguments are recorded on the
`MainframeTerminal` but ignored by the HLLAPI backend — the emulator
manages the network side there.

## Common failure modes

**`No ws3270/s3270 binary found`**
: Install wc3270 (Windows) or `x3270` (Linux/macOS) — see install
  section. Pass `ws3270_path=` for non-standard installs.

**`No HLLAPI-compatible DLL found`**
: The emulator either is not installed or exposes a differently-named
  DLL. Pass `hllapi_dll_path=` with the absolute path. Check the DLL's
  bitness matches the Python interpreter.

**`the s3270 backend speaks TN3270 only ...`**
: `session_type="5250"` on the default s3270 backend raises a
  `MainframeError` — x3270 has no 5250 support. Use
  `backend="tn5250"` for IBM i / AS/400 hosts, or
  `session_type="3270"` if the host really is a 3270 host.

**`MoveCursor() is not valid in NVT mode`**
: The host did not accept TN3270/TN3270E negotiation — connection
  fell back to plain telnet. `move_cursor()` and positional AID keys
  are unavailable; only sequential typing works. Almost always means
  the target is a 5250-only IBM i host (see pub400 caveat above).

## See also

* `tests/mainframe/pub400/` — live integration tests.
* [x3270 project](http://x3270.bgp.nu/) — upstream emulator suite.
* [EHLLAPI reference (IBM)](https://www.ibm.com/docs/en/personal-communications) — HLLAPI function-code table.
