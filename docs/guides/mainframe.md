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
from dolphin_desktop import Desktop, AID, Secret

with Desktop().mainframe(host="tso.host.example", port=992, session_type="3270", tls=True) as term:
    term.wait_ready()
    term.field_after("USERID").type_text("MYUSER")
    term.field_after("PASSWORD").type_text(Secret("s3cret"))
    term.press(AID.ENTER)
    term.wait_change()
    assert term.screen().contains("READY")
```

Two habits worth adopting from the start:

* **`tls=True`** — a terminal session carries the sign-on in the clear
  otherwise (see [Transport security](#transport-security)).
* **`Secret(...)`** around a password — marks the value for Dolphin's
  diagnostic redaction while sending the actual value to the host.

## Transport security

### Migrating from the previous API

This release changes `Desktop.mainframe()` transport defaults and arguments.
Replace `tls_ca_file=` with `tls_cafile=`. Use the certificate's DNS name as
`host`; `server_hostname=` is no longer accepted. `insecure_tls=` is also
removed: unverified TLS is unavailable, and an intentional remote plaintext
session must use `allow_plaintext=True`. Existing remote plaintext calls need
that explicit opt-in or `tls=True`. Port 992 does not enable TLS by itself.

A 3270/5250 session sends the user id and password in the same
unencrypted byte stream as the rest of the screen; neither EBCDIC nor
Telnet provides confidentiality. dolphin therefore **refuses a plaintext
connection to a remote host** and asks you to choose:

```python
# Verified TLS — certificate chain AND host name are checked. No fallback:
# a failed handshake raises, it never silently drops to plaintext.
term = desktop.mainframe(host="mf.example", port=992, session_type="3270", tls=True)

# Native TN5250 with an explicit trusted CA bundle.
term = desktop.mainframe(host="ibmi.example", port=992, session_type="5250",
                         backend="tn5250", tls=True,
                         tls_cafile=r"C:\ca\corp-root.pem")

# Native TN5250 with a caller-built context (must still verify the peer).
import ssl
ctx = ssl.create_default_context(cafile=r"C:\ca\corp-root.pem")
term = desktop.mainframe(host="ibmi.example", port=992, session_type="5250",
                         backend="tn5250", tls=True, tls_context=ctx)

# An SSH/stunnel tunnel terminated on localhost is exempt (loopback).
term = desktop.mainframe(host="127.0.0.1", port=3271, session_type="3270")

# Knowingly unencrypted (logged as a warning) — only through a channel
# you have secured by other means.
term = desktop.mainframe(host="mf.example", allow_plaintext=True)
```

The port number is **never** treated as a substitute for TLS — connecting
to port 992 without `tls=True` is still refused. For the `s3270` backend,
`tls=True` opens the emulator's supported `L:` TLS tunnel (you may also
write `host="L:mf.example"`); dolphin passes `-verifycert` explicitly and
pins `-accepthostname` to the connected host after user arguments. It rejects
switches and `-xrm` resources that disable verification or substitute another
accepted hostname (`-noverifycert`, `-noverifyhostcert`, `acceptHostname`,
`verifyHostCert`, `selfSignedOk`). The
`hllapi` backend delegates the network side to the emulator, so configure
TLS in the emulator's own session profile.

Unlike `tn5250`, where dolphin owns the TLS handshake and verifies it
directly, `s3270`/`ws3270` is an external process: dolphin can confirm the
`-cafile` flag was accepted by the build, but the certificate verification
itself happens inside the emulator, not in Python. dolphin explicitly passes
`-verifycert`, but treats `s3270` TLS as
trustworthy as the installed emulator build, not as independently verified
by dolphin — use `backend="tn5250"` when that independent verification
matters.

## Install

### s3270 backend (open source, default)

On Windows, obtain wc3270 from the [upstream x3270 project](https://x3270.bgp.nu/)
and locate its `ws3270.exe`. Pass its absolute path as `ws3270_path=` for a
non-standard installation. The library also checks PATH and well-known wc3270
installation directories.

For a reproducible build, use the archive version and SHA-256 check pinned in
the source checkout's `.github/workflows/ci.yml`. A public network host is not
needed for the controlled protocol example in `examples/mainframe/`.

TLS trust follows the installed emulator build. In particular, Windows
Schannel builds use the Windows certificate store; do not assume that passing
a PEM CA file configures the same trust behavior as native Python TN5250 TLS.
Configure the emulator's supported verified trust mechanism for the test host.

The native `tn5250` backend needs no emulator executable. Its live-host
write/submit limitation is described below.

### HLLAPI backend (enterprise emulators)

The emulator vendor ships an `EHLAPI32.DLL` (or `PCSHLL32.DLL` for IBM
PCOMM). For security, dolphin_desktop does not search CWD, the application
directory, or the ambient `PATH` for these names. Pass the vendor DLL through
`hllapi_dll_path` as an existing absolute path. This argument is required for
`backend="hllapi"`; no DLL is automatically discovered in Program Files or
System32. Missing, relative or unusable paths raise `MainframeError`:

| DLL name | Vendor |
|---|---|
| `PCSHLL32.DLL` | IBM Personal Communications (PCOMM) |
| `EHLAPI32.DLL` | Attachmate / Rocket Reflection |
| `WHLAPI32.DLL` | Older Rocket versions |

If the configured path cannot be loaded, the backend raises a clear error. The DLL
must be a **32-bit or 64-bit build matching the Python interpreter**
(a 64-bit Python cannot load a 32-bit DLL). Check the configured vendor DLL's
architecture rather than inferring it from the filename.

## API surface

### `MainframeTerminal`

| Method | Purpose |
|---|---|
| `connect(host, port, *, session_type, timeout)` | Open the session. Called automatically by `Desktop.mainframe(connect=True)`. |
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

### s3270 input safety

The `s3270` backend sends one action per line to the emulator process.  For
that reason, `host` and `type_text()` values containing carriage returns,
line feeds, NULs, or other control characters are rejected with
`MainframeError` before the process is started or stdin is written. Host
values containing s3270 action delimiters such as parentheses, semicolons,
commas, equals signs, or spaces are rejected as well. Printable quotes and
backslashes in text are escaped and remain a single literal `String(...)`
action; text such as `Quit()` is not executed as an emulator command.

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

## Choosing a live IBM i host

An IBM i host uses TN5250. A successful NVT banner through a TN3270 emulator
is not evidence of 5250 support. Select `backend="tn5250"` for the native
read/negotiation path, or an appropriate HLLAPI emulator for write-critical
flows. Configure the host, port and verified TLS trust for your environment.

For a reproducible local protocol example without an external IBM system,
use `examples/mainframe/test_mainframe.py` in the source checkout.

## Native TN5250 backend status

The pure-Python TN5250 backend (`backend="tn5250"`) implements Telnet
negotiation (BINARY, EOR, TERMINAL-TYPE and NEW-ENVIRON), WriteToDisplay
parsing for SBA/IC/RA/SF orders and inline display attributes, field detection
and screen/cursor reads. It does not use NVT as a 5250 fallback.

**Known limitations:**

* **The write/submit path is unverified against live IBM i hosts.** Unit
  checks of generated input records do not establish that a host processes
  Enter/PF keys or updates the intended fields. Use HLLAPI with a prepared
  enterprise emulator for write-critical 5250 flows and validate that setup.
* **Write Structured Field (WSF) support is absent.** Hosts requiring those
  commands need a different backend or additional implementation.

The `s3270` backend supports 3270 only. `session_type="5250"` is rejected;
select `tn5250` or HLLAPI for IBM i rather than relying on raw Telnet/NVT.

Live-host acceptance is maintained separately from the library. Controlled
local round trips remain in `tests/mainframe/mock_tn3270/` and are also used by
`examples/mainframe/test_mainframe.py`. A local protocol run does not establish
compatibility with a particular external IBM host or emulator configuration.

## Backend selection

```python
# Default — s3270 subprocess (3270 hosts)
term = desktop.mainframe(host="host.example", tls=True, session_type="3270")

# Explicit s3270 with custom binary path
term = desktop.mainframe(
    host="host.example", tls=True,
    ws3270_path=r"D:\tools\wc3270\ws3270.exe",
    model="3279-2",           # 24x80 instead of 43x80
)

# Native TN5250 — for IBM i / AS400 hosts (pub400, banks, ERP AS400)
term = desktop.mainframe(
    host="ibmi.example",
    port=992,
    session_type="5250",
    backend="tn5250",
    tls=True,
)
# Pure-Python client, zero external dependency. Negotiates real 5250
# (not NVT fallback like s3270). Parses WriteToDisplay commands into
# a 24×80 EBCDIC screen buffer with field detection.

# HLLAPI — attach to a running PCOMM session "A"
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
: Install wc3270 on Windows — see the install
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
  are unavailable; only sequential typing works. Check the host's protocol;
  a 5250-only IBM i host requires `tn5250` or HLLAPI.

## See also

* `tests/mainframe/mock_tn3270/` — controlled local protocol regressions.
* [x3270 project](https://x3270.bgp.nu/) — upstream emulator suite.
* [EHLLAPI reference (IBM)](https://www.ibm.com/docs/en/personal-communications) — HLLAPI function-code table.
