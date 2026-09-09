# Changelog

All notable changes to `dolphin-desktop` are documented in this file.
Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

* Hidden-desktop `Locator.press_key()` now reports a clear `DolphinError`
  instead of using system-wide modifier events when the hidden desktop cannot
  be activated. Hidden physical-input errors are also classified only for
  `SetCursorPos` failures, and `Locator.text()` preserves intentional trailing
  blank lines returned by UIA `TextPattern`.

## [0.2.0] — 2026-08-05

### Added

* **Programmatic action API + auto-waiting waiters**:
  `Locator.invoke()`, `toggle()`, `expand()`, `collapse()`, `select()`,
  `set_value()` route through UIA patterns instead of mouse/keyboard
  events — headless-safe under `dolphin-run`. Same surface on
  `JABLocator` (Java Swing) via `doAccessibleActions` and
  `setTextContents`, plus on `DelphiComponent` for VCL/LCL. Every
  method raises `UnsupportedPatternError` cleanly instead of silently
  falling back to a physical click. Auto-waiting
  `Locator.wait_for_text()`, `wait_for_checked()` (and JAB / Delphi
  counterparts) replace the `sleep(N); assert ... in .text()`
  antipattern. See `docs/tutorials/headless-mode.md`.

* **Delphi / VCL + PowerBuilder**: `Desktop.launch_delphi()`
  / `attach_delphi()` return a `DelphiApp` facade with `DelphiForm` +
  `DelphiComponent` — Delphi-aware locator maps `TComponent.Name` to
  UIA `AutomationId` on Delphi 10.4+ and falls back to
  `(class_name, caption)` on Lazarus / LCL. Standard controls covered:
  `TButton`, `TEdit`, `TMemo`, `TCheckBox`, `TRadioButton`,
  `TComboBox`, `TListBox`, `TPageControl`, `TStringGrid`, `TListView`,
  `TLabel`. `Desktop.launch_powerbuilder()` semantic wrapper for
  Appeon PB 2019+. Sample Lazarus project + round-trip suite
  in `tests/delphi/`. Third-party VCL libraries
  (DevExpress, TMS) and classic PowerBuilder DataWindow introspection
  deferred to v0.3+.
* **Electron / CDP**: `Desktop.launch_electron_cdp()` +
  `Desktop.launch_cef_cdp()` (Steam, Spotify), `CDPSession`,
  `CDPLocator`, `CDPFrameLocator`, `CDPRoute`,
  `CDPRequest`, `CDPDownload`. Shadow-DOM piercing, network route
  interception, download / popup capture, expose_function bridge.
  Full parity with the native UIA Locator surface.
* **Mainframe**: `Desktop.mainframe()` with three backends
  under one `MainframeTerminal` API — `s3270` (subprocess wrap of
  ws3270), `tn5250` (pure-Python native TN5250 client, no NVT
  fallback), `hllapi` (ctypes binding to `EHLAPI32.DLL` for PCOMM /
  Attachmate / Rocket). Auto field detection via `MainframeTerminal.fields()`,
  rich waits (`wait_for_cursor`, `wait_for_field`, `wait_for_text`,
  generic `wait_for`), trace mode, codepage parameter, multi-session
  isolation.
* **Oracle Forms**: `Desktop.launch_oracle_forms()` /
  `attach_oracle_forms()`, `OracleFormsApp` with block / item / menu /
  LOV navigation, function-key shortcuts (`enter_query`,
  `execute_query`, `save`, `next_block`, `previous_block`
  …), `wait_for_status()` for status-line assertions. Uses direct JAB
  API (setTextContents, getAccessibleTextRange, doAccessibleActions,
  requestFocus) — works from background test runs, RDP sessions, and
  CI hosts where SetForegroundWindow is blocked.
* **Public helpers** for the autonomous-library contract: `which`,
  `tcp_reachable`, `http_ok`, `sleep`, `monotonic`, `dirname`,
  `path_join`, `path_exists`, `path_basename`, `remove_file`,
  `temp_file`, `tempdir`, `env_var`, `b64encode`, `b64decode`,
  `counter`, `start_thread`, `add_import_path`, `find_pid_by_image_name`,
  `is_cdp_available`, `cdp_install_hint`.
* **Getting Started** guide (`docs/getting-started.md`) — the shortest
  path from clean venv to a green test for every stack.
* **Native TN5250** WriteToDisplay parser now aligns pixel-perfect on
  pub400.com sign-on: inline attribute bytes (0x20-0x3F) and SF
  attribute bytes each correctly consume one screen cell.
* **`AmbiguousMatchError`** — locator criteria matching more than one
  element now raise a dedicated error naming the fix (`found_index=N`
  or tighter criteria) instead of falling through fallbacks and
  misreporting "not found". `exists()` answers `True` for an ambiguous
  match — the element is there, at least twice. Found live on a
  PowerBuilder window hosting two same-class DataWindow panes.
* **`Screen.find_text` retries upscaled** — when OCR finds nothing at
  native resolution the capture is re-run at 2× and 3× magnification
  (coordinates mapped back to screen space), because the small fonts of
  combo lists and grid cells sit below Tesseract's reliable glyph size.
  Opt out with `scales=(1,)`.
* **Locator criteria aliases** — `automation_id=`, `role=` and `name=`
  are accepted everywhere criteria are (constructor, chained
  `locator()`, `fallback=` lists) and normalize to `auto_id` /
  `control_type` / `title`, matching the YAML Object Repository
  dialect. Previously the natural spellings travelled into pywinauto
  as unknown kwargs and dissolved into a silent "not found".
  Conflicting alias + canonical values raise `ValueError`.

### Changed — breaking

These change the behavior of existing calls; read them before upgrading.

* **`get_attribute()` raises `AttributeError` for a name the element does
  not publish**, instead of returning `None`. This affects the UIA
  `Locator`, `SapLocator` and `JABLocator`. A misspelled name — including
  the PascalCase UIA spelling `"AutomationId"` where pywinauto publishes
  `automation_id` — used to be indistinguishable from an attribute that
  was genuinely empty, so an assertion against it passed without ever
  reading the UI. The message lists the names that *are* available. Pass a
  second argument to opt back into the old behavior:
  `get_attribute("AutomationId", None)`.

* **`spy.pick()` and `spy.sap_pick()` return a versioned dict** instead of
  a bare `list[dict]` / selector dict, and no longer print the locator's
  verification status (the interactive banner still goes to stdout).
  `{"schema_version", "status", "chain"|"selector", "message"}` — `status`
  is `ok` / `repaired` / `repaired_flat` / `unverified` / `unreachable` /
  `cancelled` (`no_session` for SAP). Previously a caller could not tell a
  verified locator from one flagged "chain does NOT resolve to the picked
  element", because that warning only went to stdout.
* **`spy.inspect()` and `spy.sap_inspect()` are bounded by default**
  (`depth=12`, `max_children=200`); pass `None` for either to restore the
  old unlimited walk. Unbounded introspection of a Chromium accessibility
  tree took minutes and could end in `RecursionError`. The result gained a
  `"limits"` key so a consumer can tell a complete tree from a capped one.
* **`Application.close()` no longer terminates a process dolphin did not
  start.** It now honors `_owns_process` like `kill()` already did, and
  both log a warning naming the PID instead of silently doing nothing.
* **`check()` / `uncheck()` / `is_checked()` raise `UnsupportedPatternError`**
  when the toggle state cannot be read, instead of assuming "unchecked" —
  which made `uncheck()` a silent no-op and could invert `check()`.
* **A desktop-wide window match in an unrelated process now raises**
  `WindowNotFoundError` instead of returning that window bound to this
  `Application`. Binding a stranger's window meant every later click,
  keystroke and Qt-agent call was aimed at the wrong program.
* **Java windows reject criteria the Access Bridge cannot express.**
  `window.locator(auto_id=...)` on a `SunAwtFrame` used to drop every
  unsupported criterion and match the first node in the tree; it now
  raises, as do `get_by_automation_id` / `get_by_class` /
  `get_by_object_name` on a Java window.
* **JAB role names are no longer shadowed by the UIA alias table.**
  `role="text"` now reaches a `JTextField` (it used to search for labels)
  and `role="window"` a `JWindow` (used to mean `JFrame`); use
  `role="label"` / `role="frame"` for the old meanings.
* **`Keyboard.hotkey()` validates its arguments.** No arguments, a
  modifier with no key, or more than one non-modifier key now raise
  `ValueError` rather than silently sending nothing or sending the extra
  keys unmodified.
* **`config()` rejects out-of-range values** (`timeout`, `poll_interval`,
  `retry_count` must be non-negative; `video_fps` must be 1–30), and a
  malformed `DOLPHIN_*` environment variable now warns and falls back to
  the default instead of making `import dolphin_desktop` raise.
* **The recorder emits Tab and Enter as `press_key(...)`** instead of
  folding `{TAB}`/`{ENTER}` into the surrounding `type_text`, and redacts
  any field UI Automation cannot classify. Generated code now quotes
  values with `repr()` and escapes `title_re` patterns.
* **Screenshot and video filenames carry a pid and uuid suffix.** Two
  parametrized tests sharing their first 80 nodeid characters used to
  overwrite each other's artifacts.
* **`CDPSession` raises `DolphinError` after `close()`** from the fourteen
  methods that reach the browser or context, instead of leaking
  Playwright's `TargetClosedError`.
* **`switch_to_page()` raises `DolphinError` when a registered route
  cannot be re-armed** on the incoming context, rather than silently
  leaving the session with no interception while `unroute()` still
  reports success.
* **Delphi `set_text()` raises `DelphiError`** when its last-resort
  keystroke path fails, matching `click()` / `double_click()` /
  `right_click()`, instead of surfacing the raw pywinauto exception.
* **`ExcelApp.quit(save_changes=True)` / `WordApp.quit(save_changes=True)`
  raise** when a workbook or document could not be saved. The instance is
  still shut down first — the failure is simply no longer silent.
* **`playwright_compat` timeouts are now milliseconds**, matching
  Playwright, across every `timeout=` parameter in the module
  (including ones forwarded through `**kwargs` by `click`, `dblclick`,
  `hover`, `screenshot`). They were previously interpreted as seconds,
  so ported code passing Playwright's `timeout=5000` waited ~83 minutes
  instead of 5 seconds. Effective default durations are unchanged.
* **`playwright_compat`: `Locator.first` and `.last` are properties**,
  not methods, matching Playwright's sync API. `page.locator("li").first`
  no longer returns a bound method. `nth()` remains a method.
* **`playwright_compat`: `Page.close()` closes only its own page**
  instead of tearing down the whole `CDPSession` and browser. Use
  `browser.close()` for session teardown.
* **`Keyboard.type()` and `Locator.type_text()` escape literal text**
  by default: `+ ^ % ~ ( ) { }` are now typed as characters instead of
  being interpreted as pywinauto modifiers. `type("100% done")` no
  longer sends Alt+Space and opens the window's system menu. Pass
  `escape=False` for the old key-sequence behavior (`Keyboard.press`
  and `Locator.press_key` remain key-sequence APIs and are unaffected).
* **`OracleFormsKey` now follows one key-binding family end to end**
  (PC-style, `fmrpcweb.res`). The previous table mixed two families and
  contained a destructive collision: `DUPLICATE_RECORD` was `Shift+F6`,
  the *Delete Record* key, so asking to duplicate a record deleted it.
  The clear block was also shifted by one row, making `CLEAR_BLOCK` send
  Clear Form. Changed: `CLEAR_RECORD` `Shift+F5`→`Shift+F4`,
  `CLEAR_BLOCK` `Shift+F7`→`Shift+F5`, `CLEAR_FORM` `Shift+F8`→
  `Shift+F7`, `DUPLICATE_RECORD` `Shift+F6`→`F4`, `NEXT_RECORD` /
  `PREVIOUS_RECORD` `Down`/`Up`→`Shift+Down`/`Shift+Up` (bare arrows are
  the *other* family's convention), `NEXT_BLOCK`/`PREVIOUS_BLOCK`
  `Ctrl+`→`Shift+Page_Down`/`Up`, `EXIT`/`CANCEL_QUERY`→`Ctrl+Q`. Added
  `COUNT_QUERY`. **Adopt the table as a whole** — a single entry borrowed
  from the other family maps to a different Forms function. See
  `OracleFormsKey` for how to check yours with Ctrl+K.
* **Removed `OracleFormsKey.FIRST_RECORD` / `LAST_RECORD` and
  `OracleFormsApp.first_record()` / `last_record()`** — the Forms key
  table has no such functions. They were bound to F11/F12, which on a
  stock-file host is *Enter Query*, so `first_record()` could silently
  put the block into query mode.
* **Mainframe HLLAPI PF keys corrected.** The mnemonic table was off by
  nine: `PF1` emitted `@a` (PF10) and `PF3` — the near-universal Exit
  key — emitted `@c` (PF12). Every PF key now sends the correct AID.
  `PF13`-`PF24` were emitting mnemonics that do not exist; out-of-range
  keys now raise `MainframeError`.
* **Logger hierarchy is `dolphin_desktop`, not `dolphin`.** All emitters
  already logged under `dolphin_desktop.*`, so `DOLPHIN_LOG_LEVEL`,
  `--dolphin-desktop-log-level` and `config(log_level=…)` configured a
  logger nothing wrote to, and the advertised secret redaction never
  applied to a single line the library emitted. Both work now.
  `get_logger()` returns the `dolphin_desktop` logger.
* **`Tab.select_tab()` raises `ElementNotFoundError`** when no tab
  matches, instead of silently doing nothing and returning success.
* **Delphi `click()` / `double_click()` / `right_click()` raise**
  `DelphiError` when every strategy failed, instead of reporting
  success for a click that never happened.
* **`JABLocator.do_action()` no longer substitutes** the component's
  first available action for the requested one, so `expand()` on a
  button raises `UnsupportedPatternError` instead of clicking it.
* **SAP session-loss is no longer swallowed.** `is_busy()`,
  `status_message()`, `title()`, `current_transaction()` and
  `system_info()` raise `ApplicationError` when the session is gone
  rather than returning `False` / empty values — so `wait_until_ready()`
  and `assert_no_error()` fail instead of green-lighting a dead session.
* **Qt agent refuses cross-architecture injection.** Only 64-bit Qt
  targets are supported (the bundled agent DLLs are x64-only); a 32-bit
  target now raises instead of crashing the application under test.
* **`CDPSession` raises `CDPStalePageError`** when a page you explicitly
  selected via `switch_to_page()` or `expect_popup()` has closed, rather
  than silently retargeting to a different window. Auto-selection is
  unchanged when no explicit selection was made.
* **Oracle Forms modifier shortcuts require foreground focus.**
  `Shift+F7` and friends were delivered via `PostMessage`, which does
  not set the target thread's keyboard state, so they arrived as the
  unmodified key (`CLEAR_BLOCK` acted as `ENTER_QUERY`). They now fall
  through to the foreground input path.

### Changed

* Python 3.11+ required (was 3.10+).
* JABLocator.text / .value / .click / .set_text now prefer direct JAB
  APIs (getAccessibleTextRange, doAccessibleActions, setTextContents)
  over pywinauto mouse+keyboard replay. The keyboard fallback path is
  retained but only fires when the direct API is missing.
* `wait_change` on the mainframe API tolerates disconnect during wait
  — a host closing the socket after an AID key (PF3=Exit) no longer
  raises MainframeError.
* **Element resolution is roughly 4× faster** (measured 3.0 s → 0.75 s per
  `Locator` resolve against a small window). Resolution used to cost seven
  full UIA tree scans: `Window.locator()` spent one deciding whether the
  window was a Java Swing frame — an answer fixed for the lifetime of an
  HWND, now cached — and pywinauto's `wait("exists visible")` spent three
  more per poll, one for `exists`, one to re-resolve for `is_visible()`,
  and one for a wrapper the caller discarded. A single scan answers both
  questions, leaving two per resolve.

### Deprecated

None.

### Removed

* **The `video` extra.** It installed `mss`, which speeds up the per-step
  trace screenshots and has nothing to do with MP4 recording — that is done
  by an external `ffmpeg` binary no wheel can provide. The name promised
  something it never delivered. `mss` is still available under `fast`.
  Upgrading with `dolphin-desktop[video]` pinned does not fail: pip warns
  that the extra is unknown and installs the base package, after which
  screenshots fall back to `PIL.ImageGrab` — correct, just slower.
* **The `dev` extra.** The development toolchain lives in
  `[dependency-groups]`, which is what CI and `CONTRIBUTING.md` use
  (`uv sync --group dev`). The extra was a second copy that nothing
  referenced and that had already fallen two packages behind.

### Fixed

* **`_trace.list_runs()` leaked a sqlite connection** for every trace
  directory whose `trace.db` failed to open or query. The `except` that
  keeps the scan going past a corrupt or locked database skipped the
  `close()` on the line below it, so the connection survived until
  garbage collection.
* **DelphiComponent.is_checked**: LCL exposes MSAA `State` as an
  **integer bitfield** (STATE_SYSTEM_CHECKED = 0x10), not a
  comma-separated word list. Now reads the bit directly; the
  cascade to `is_selected()` (which LCL reports for focused rows)
  is removed — that cascade silently made `uncheck()` idempotent-
  toggle back on.
* **JABLocator.is_visible / is_enabled / is_checked**: substring
  matching against `states_en_US` replaced with word-boundary set
  membership. Latent — Java's built-in AccessibleState constants
  happen not to include "unchecked"/"disabled"/"invisible", but any
  future L&F publishing a state containing our substring would have
  silently flipped every predicate.
* **DelphiComponent.text() cascade**: fallback to `element_info.name`
  removed. On Delphi VCL 10.4+ that attribute is the developer-
  assigned `TComponent.Name` (a stable AutomationId), not the
  widget's actual text. Latent under LCL (info.name is empty), would
  have exploded on real Delphi VCL.
* **Empty-substring wait guards** across every `wait_for_*` /
  `assert_*` that took a substring argument:
  - `Locator.wait_for_text("", contains=True)` → ValueError
    (was: immediate false-positive match)
  - `JABLocator.wait_for_text("", contains=True)` → ValueError
  - `DelphiComponent.wait_for_text("", contains=True)` → ValueError
  - `MainframeTerminal.wait_for_text("")` → ValueError
  - `CDPSession.wait_for_text("")` → ValueError (whitespace-only
    text too, since it would otherwise build a match-everything selector)
  - `OracleFormsApp.wait_for_status("")` → ValueError
  - `SapSession.wait_for_title("", exact=False)` → ValueError
  - `SapSession.assert_field_value("", partial=True)` → ValueError

  Exact-empty match (`contains=False`) stays supported for
  wait-until-field-is-cleared scenarios.
* **`text()` None-safety**: `Locator.wait_for_text` and
  `DelphiComponent.wait_for_text` now coerce `.text() → None` to
  `""` so `text in None` cannot raise `TypeError` on transient
  wrapper edge cases.
* **`ImageLocator.wait_for`**: raised `RuntimeError` instead of
  `WaitTimeoutError`. Callers catching `WaitTimeoutError` uniformly
  silently missed image-locator misses. Now consistent with every
  other `wait_for_*` in the library.
* **pytest `launch` fixture leaked processes**: teardown loop
  aborted after one failed `app.kill()`, leaking every
  subsequently-launched AUT in the same test. Now wraps each kill
  in try/except.
* **Application.kill / close order-of-operations**: PIDs were
  discarded from the session reaper BEFORE `_app.kill()`. If
  pywinauto kill raised, the process leaked as an unreachable zombie
  — the reaper no longer knew about it. Kill now runs first; PIDs
  are discarded only after the attempt.


* Qt Creator (and any other pre-existing user process the tests connect
  to) is no longer killed by dolphin's per-test PID reaping — call
  `app.detach(session=True)` in the fixture.
* Steam CEF sessions no longer die between tests in a module-scoped
  fixture — call `app.detach()`.
* Non-Windows import no longer fails silently — emits a RuntimeWarning
  pointing at the supported-platform table.

*Processes and windows*

* `attach_delphi()` (and every other `attach_*` factory) no longer
  registers the target PID for the pytest teardown reaper, which was
  force-terminating already-running applications dolphin never
  launched — taking unsaved user data with them.
* `Application`'s desktop-wide window fallback no longer adopts an
  unrelated process that happens to match the window criteria, and no
  longer registers it for killing. Adoption now requires the same
  executable image or a child process.
* The SAP scripting-security auto-dismiss worker no longer clicks
  OK/Allow in *every* application's dialogs. It now requires the SAP
  product name in the caption, the `#32770` dialog class and an
  SAP-owned process.

*Selectors and actions*

* The last-resort tree-walk fallback refuses to match when the selector
  carries criteria it cannot evaluate (`auto_id`, `title_re`, `class_name`,
  …). It previously matched on control type alone, so a stale `auto_id`
  clicked the first button of that type in the window. `found_index` is
  honoured by the walk rather than disqualifying it, so `.nth(n)` keeps the
  same fallbacks as the locator it was derived from.
* `Locator.timeout()` and `.nth()` preserve the locator subclass and all
  locator state, so `menu(...).item(...).click(timeout_ms=…)` no longer
  loses the menu-opening resolver.
* `RadioButton.select()` uses SelectionItemPattern instead of a toggle,
  which raised on standard WPF/WinForms/Qt radio buttons; `is_checked()`
  now reports selection state instead of always `False`.
* `Keyboard.hotkey()` emits real virtual-key codes rather than Unicode
  packet events, so `hotkey("ctrl", "c")` actually triggers Copy. Added
  Win-key support.
* File-dialog path entry escapes the typed path, so
  `C:\Program Files (x86)\…` no longer loses its parentheses.

*Enterprise adapters*

* SAP `get_selected_rows()` expands range syntax (`"3,5-8,14"`), which
  it previously discarded, returning an empty list for contiguous
  selections.
* Java Access Bridge contexts are released instead of leaked — a
  polling `wait_for_*` was leaking thousands of JNI global references
  per minute inside the target JVM.
* `JABLocator.exists()` accepts `timeout=` like every sibling adapter;
  `OracleFormsLov.is_open()` raised `TypeError` on every call because
  of the mismatch.
* Oracle Forms role lookups resolve JAB role names, so `status_line()`
  and `wait_for_status()` work on forms whose status bar is not named
  `StatusLine`. An unmappable role now raises instead of being silently
  dropped from the match.
* Mainframe s3270: stopped sending the `L:` TLS-tunnel prefix for 5250
  sessions; ReadBuffer parsing handles multi-attribute `SF(...)` tokens
  (emitted by the default color model) and no longer counts zero-width
  `SA(...)` tokens as screen cells.
* Mainframe EHLLAPI: presentation-space position is passed in the
  4th parameter as the API requires, screen size is parsed as binary
  16-bit fields (a 27×132 session was silently read as 24×80), and
  literal `@` in typed text is escaped so it cannot inject key
  mnemonics.
* Mainframe TN5250 field attributes decode against the actual bit
  layout — every field previously reported itself as unprotected, and
  password fields were not flagged hidden.

*Test artifacts and tracing*

* Failure-artifact capture can no longer escape the pytest hookwrapper
  as an INTERNALERROR that masks the original test failure, and long
  parametrized node ids no longer overflow MAX_PATH.
* Traces are finalized for setup errors, not only for the call phase —
  previously the sqlite handle leaked and the artifact was missing for
  exactly the failures that need it most.
* Trace run directories are collision-free under retries and
  pytest-xdist; run lookups are deterministic instead of `LIMIT 1` over
  an ambiguous set.
* Tracing failures (locked database, cross-thread use, disk full) are
  logged rather than raised — a trace write can no longer fail a test
  whose UI action succeeded.
* Under pytest-xdist each worker writes `dolphin-report-<id>.html`
  instead of all workers racing on one file that ended up holding a
  single worker's subset.
* The recorder synthesizes double-clicks from consecutive button-down
  events; low-level hooks never receive `WM_LBUTTONDBLCLK`, so the
  upgrade path was dead code and every double-click replayed as two
  clicks.

*Clipboard and images*

* `Clipboard` retries `OpenClipboard` with backoff instead of failing
  immediately when another process holds it — the common case right
  after the application under test finishes its own copy.
* CF_DIB→BMP conversion computes the pixel offset from the DIB header
  instead of hardcoding 54, which returned shifted or garbage images
  for `BI_BITFIELDS`, palettized and V4/V5 bitmaps.
* Image-fallback typing honors `with_spaces`, so a self-healed
  `type_text("hello world")` no longer types `helloworld`.

*Security and privacy*

* Qt agent: bounded RPC read timeout (a wedged Qt event loop hung the
  test process forever), response-id validation (a desynchronized
  stream silently returned the previous call's data), buffered reads
  with a size cap, and verification that the named pipe is served by
  the injected process. Remote memory is no longer freed while the
  remote thread may still be reading it — that freeing was itself a way
  to crash the application under test.
* Telemetry is hardened before a DSN is ever configured: Sentry default
  integrations are off, local variables are not collected, and events
  that do not originate in dolphin are dropped. Previously any
  `logging.error()` anywhere in the host process would have been
  uploaded once telemetry was enabled.
* `dolphin record` states up front that it captures every keystroke on
  the desktop when no `--app` filter is given, filters out-of-scope
  windows before converting keystrokes to text, and redacts password
  fields on a best-effort basis.

* **Excel and Word no longer close the operator's own documents.**
  `ExcelApp.open()` / `WordApp.open()` attached to an already-running
  Office instance via the running-object table, and `quit()` then called
  `Quit()` on it with `DisplayAlerts` suppressed — silently discarding
  every unsaved edit the user had open. They now start a private instance
  and close only what they opened; `connect()` never quits anything.
* **`Keyboard.hotkey()` with the Win key is exception-safe.** The key was
  pressed and released inside one pywinauto sequence with no `finally`, so
  a `KeyboardInterrupt` or a blocked `SendInput` between the two left the
  Windows key logically held down for the rest of the desktop session.
* **`Keyboard.hotkey()` sends real virtual-key codes for punctuation.**
  Only letters and digits were fixed in the first pass; `Ctrl+-`,
  `Ctrl+=`, `Ctrl+,` and `Win+.` still went out as Unicode packets that no
  application matches against its accelerators.
* **`playwright_compat` locators follow their own page.** Page adapters
  were rebound correctly, but the locators they returned resolved lazily
  against whichever page was selected last, so a locator from page 0 acted
  on page 1. The test fake hid this by capturing the page eagerly.
* **The recorder's password redaction survives a Tab.** The password flag
  was sampled once per typing run and `Tab` did not end a run, so the
  canonical username-Tab-password login flow recorded the password in
  plain text. Focus is now sampled per keystroke and an unclassifiable
  field is redacted rather than recorded.
* **A wedged Qt agent is recoverable.** An RPC timeout marked the client
  permanently broken and `Application` cached it forever, so a Qt event
  loop blocked behind a modal dialog cost you the agent until the app was
  killed. Timeouts now raise `QtAgentTimeoutError` and leave the
  connection usable. `QtAgentClient.reattach()` rebuilds a genuinely failed
  connection in place; `Application.reset_qt_agent()` drops the cached
  client so the next `qt_agent` access builds a fresh one.
* **A self-closing popup no longer bricks the CDP session.** After
  `expect_popup()` the session pinned that page; when the popup closed
  itself — the normal OAuth flow — every later `page` access raised. It
  now falls back to the opener, and `reset_page_selection()` clears a pin
  explicitly.
* **`unroute()` removes every handler for a pattern**, and routes move
  with the active context instead of staying armed on the old one.
* **Console and dialog handlers follow page switches**, so
  `console_messages()` keeps recording and `accept_dialogs()` keeps
  working after a switch — previously Playwright auto-dismissed dialogs on
  the unlistened page despite the configured policy.
* **A shared `QtAgentClient` is thread-safe**; the round trip is now
  locked instead of the invariant being pushed onto callers.
* **Clipboard, drag and JAB resource fixes.** `Locator.clear()` could
  leave the Windows clipboard open for the whole process, blocking every
  other application on the machine; `drag_to()` could leave the mouse
  button latched down desktop-wide; and locators from `JABLocator.all()`
  never released their Java contexts.
* **`get_by_object_name(...).exists()` works.** A zero timeout made the
  poll loop skip its body entirely, so it always answered `False`; four
  other `wait_*` loops had the same shape.
* **Screenshots span every monitor.** All seven capture paths passed
  `all_screens=False`, so anything on a secondary display came back wrong
  or black.
* **Image templates load from non-ASCII paths**, and `find_all()`
  suppresses overlapping matches instead of returning hundreds of hits per
  occurrence.
* **`dolphin-run` reports a real exit code** rather than 259 when waiting
  on the child fails, and quotes arguments by the Windows rule.
* **Tracing distinguishes degraded from failed.** A lost sqlite write left
  a passing test rendered as a red FAIL; swallowed errors are now logged
  at warning level and surface as a distinct badge.
* **Generated code is syntactically valid.** The spy and the recorder
  interpolated element text into Python source unescaped, so an element
  named `C:\Users\foo` produced a `SyntaxError`.
* **`sap_pick()` no longer stalls SAP GUI** — the hit-test rebuilt the
  whole component tree over COM on every 50 ms poll.

* **`Keyboard.hotkey("win", …)` reaches the shell again.** Splitting the
  Win-key hold into three calls left the payload a bare character, which
  pywinauto injects as a Unicode packet no accelerator table matches — so
  `Win+E` opened the Start menu instead of Explorer.
* **The Qt agent survives a timeout that lands mid-reply.** The consumed
  prefix of a half-arrived response was discarded, so the tail parsed as a
  malformed line and permanently broke a connection `QtAgentTimeoutError`
  exists to keep usable. `reattach()` also pins the target pid now, so it
  cannot reconnect to a recycled process.
* **Recovery preempts a wedged Qt agent.** `close()` and `reattach()` no
  longer queue behind a `_send` blocked writing to a full pipe buffer.
* **The recorder types into the control that actually had focus.** After a
  bare Tab, Enter or shortcut the pending selector was never cleared, so
  the next typed run was attributed to the previous field — the generated
  script put the password in the username box.
* **Image matching returns true screen coordinates** on a multi-monitor
  desktop whose virtual origin is not `(0, 0)`; the round-two
  `all_screens=True` fix had made full-desktop grabs report coordinates
  offset by the virtual origin. `find_all()` on a flat background no
  longer takes minutes.
* **Crash dumps cover attached applications.** The UIA capture was scoped
  to processes dolphin launched, so the SAP / mainframe / Oracle Forms
  workflows — which attach — got an empty tree.
* **Secret redaction cannot be bypassed** by a module that builds its
  logger with the stdlib `getLogger` directly.
* **Java Access Bridge contexts are released exactly once.** A walk that
  raised while descending a matched node released the same context twice,
  which the JVM reports as `DeleteGlobalRef on invalid reference`.
* **Excel and Word no longer leave an orphaned instance** when the
  document fails to open, and a failed save during
  `quit(save_changes=True)` is reported instead of swallowed.
* **TN5250 `SF` orders without a field format word no longer appear as
  writable fields,** and an implausible field length is clamped to the
  screen.
* **`OracleFormsWindow.title()` returns the title** instead of always `""`.
* **`dolphin trace view` on a directory without a database** reports it
  instead of creating an empty `trace.db` and dying on a raw sqlite error.
* **A deleted object-repository YAML no longer wedges its whole level,**
  and `parent:` is rejected at load with the file named rather than
  failing deep inside a locator call.
* **`dolphin init --template sap` and `--template qt` generate code that runs.**
  Both scaffolds referenced methods that do not exist, so a new project's
  first test failed with `AttributeError`.
* **`dolphin-run` restores a caller-set `DOLPHIN_HEADLESS`** instead of
  unconditionally removing it.
* **`Mouse.click(..., button=…)` rejects an unknown button** instead of
  moving the cursor and silently clicking nothing, and `Clipboard.set_text`
  restores the previous contents if the write fails.
* **A video that ffmpeg could not finalise is no longer reported as a
  successful artifact,** and the reason ffmpeg refused to start is logged
  instead of discarded.

* **Java Access Bridge text is no longer silently truncated or holed.**
  `get_text_range` asked for more than the 10 240-byte wire packet holds,
  and advanced its cursor by the requested length rather than the length
  returned — a short read left a gap mid-string with nothing to signal it.
  `set_text_contents` now rejects input above the 1 023-character limit
  instead of writing a truncated value, the action buffer is sized to the
  32 the bridge accepts, and `get_value()` calls the export that exists.
* **Multi-level menus stay open.** `window.menu("View").item("Zoom")
  .item("100%")` focused its immediate parent before clicking, which
  re-ran the parent's own opening click and toggled the menu shut. Focus
  now goes to the root window, which is what physical input needs anyway.
* **`Locator.is_visible()` agrees with `click()`.** Dropping the
  TreeWalker fallback for speed made elements only that walk can see
  report invisible, so `wait_until_hidden()` returned immediately on a
  control that was plainly on screen.
* **A CDP page that auto-heals rebinds its handlers in the right order,**
  matching the initial selection path; `expect_popup()` no longer swallows
  the error raised when routes cannot be re-armed on the new page.
* **Oracle Forms ships one key family, not a blend of two.** The map mixed
  PC-style and terminal-style bindings, so `duplicate_record` sent the
  same key as `delete_record` and the PF keys were off by nine. Entries
  that could not be verified against `fmrpcweb.res` are marked as such,
  and a failure to foreground the Forms window is logged rather than
  swallowed — every modifier shortcut otherwise lands on whatever window
  has focus.
* **The recorder emits runnable scripts.** An action recorded without a
  usable selector produced `win.locator()  # no selector found.click()`,
  commenting out the line it was appended to. AltGr characters (Polish
  `ą`, German `€`, French `@`) are recorded as text rather than as the
  Ctrl+Alt shortcut Windows reports them to be.
* **TN5250 rejects `PF25` and above** instead of running off the end of
  the PF block onto `Clear`.

* **The TN5250 command table uses the real 5250 command codes.** Clear
  Format Table is `0x50`, not `0x41`, and it was being swallowed as a read
  command — so nothing but Clear Unit ever retired a panel's fields. A
  repaint left the previous screen's fields in place, `field_after()`
  matched a stale entry, and the typed value went to coordinates that
  belonged to the old panel. Read Immediate (`0x72`) and Read Screen
  Immediate (`0x62`) are also recognised now, and correctly as commands
  that carry no control characters — a Write-To-Display sharing the record
  with one of them used to be lost.
* **The TN5250 input record sends the cursor before the AID,** as the
  reference client does. Emitting the AID first made byte 0 of every
  record the AID code where the host expects a row — `0xF1` for Enter
  reads as row 241 — so IBM i never answered. The write path remains
  unverified against a live host, and `send_aid()` now says so rather
  than attributing the silence to a missing WSF handshake.
* **`press("PA1")` on a 5250 session raises** instead of silently sending
  PF1, which on IBM i is Help. There is no nearest equivalent to
  substitute: PA1–PA3 are 3270 keys.
* **The recorder replays what was recorded.** Only Tab and Enter ended a
  run of typed text, so Backspace, Delete, the arrows, Home/End, PgUp/PgDn
  and F1–F12 were folded into it — and `type_text` escapes braces, so one
  corrected typo replayed as the literal characters `{BACKSPACE}`.
  `Ctrl+Alt+Q` no longer records as `Ctrl+Q` (Quit in most editors), plain
  `Alt+F` is no longer dropped entirely, and Shift now survives in
  shortcuts like `Ctrl+Shift+Home`. AltGr is distinguished from Ctrl+left
  Alt by the side of the key, so a Polish or German user recording
  `Ctrl+Alt+S` gets the shortcut rather than `type_text("ś")`.
* **Read-only queries never drive input.** `is_checked()`, `text()`,
  `value()`, `bounding_box()` and `get_attribute()` resolved the same way
  an action does, so on a menu item they clicked the parent menu open —
  and `wait_for_checked()` polls `is_checked()` every 150 ms, clicking the
  menubar several times a second while waiting.
* **A pointer action no longer dismisses the popup it is aiming into.**
  Focusing the root window before every click closed the transient popups
  that window owns, so clicking an item in a combo-box dropdown or a
  context menu dismissed it first. An already-active window is now left
  alone.
* **`JABLocator.focus()` asks for focus instead of pressing the button.**
  It was an alias for `click()`, so focusing a Swing button dispatched its
  action — and `OracleFormsItem.type_text(text, clear=False)` focuses first,
  so it pressed every button it was asked to type into. A bridge build that
  exports no `requestFocus` still falls back to the activating path, with a
  warning, since there is no other way to move focus there. `value()` now
  consults AccessibleValue
  before falling through to the text read, so a JSlider or JScrollBar —
  which publishes one — no longer reaches the last-resort path that clicks
  the widget's centre, dragging the thumb to the midpoint and returning the
  clipboard instead of the value it was asked for.
* **Oracle Forms reports an undelivered keystroke.** `PostMessage` signals
  failure by returning zero rather than raising, so a form at a different
  integrity level received nothing while the call reported success and
  skipped its own fallback. The navigation keys also carry `KF_EXTENDED`
  now, without which AWT read each one as its numpad twin. A refused
  foreground activation is logged instead of passing silently.

* **A newline or tab in typed text is no longer dropped.**
  `type_text("line1\nline2")` wrote `line1line2` and reported success:
  pywinauto discards a literal `\n` or `\t` unless told otherwise, and
  escaping removes its `~` newline alias, so no path survived. Every
  typing site now passes the same flags `Keyboard.type()` already did.
* **A test whose teardown fails is no longer reported as passed** in
  `dolphin-report.html`. pytest exited non-zero while the row showed
  green, because the outcome had been committed during the call phase.
  The report header carries an `Errors` count as well, so a teardown
  failure changes the headline and not just one row.
* **A trace store that cannot be opened disables tracing** instead of
  turning every test into a setup error. Construction created directories
  and a sqlite file outside the guard that keeps the rest of tracing
  observational, so a read-only workspace or a path over MAX_PATH failed
  the whole run with nothing naming tracing as the cause.
* **Failure text is redacted before it is stored or published.** The
  pytest `longrepr` went verbatim into `trace.db`, `trace.html` and the
  Allure attachment — and under `pytest -l` it carries the failing frame's
  locals, so a password in scope was published while logging the same
  string would have masked it.
* **Redaction was rebuilt around the shapes credentials actually take.**
  It now finds the keyword inside a compound identifier, so
  `AWS_SECRET_ACCESS_KEY=…` and `private_key=…` are masked rather than
  printed verbatim; it absorbs any auth scheme, not just `Bearer` and
  `Basic`, which previously masked the scheme name and published the
  credential after it; it follows a quoted value to its closing quote, so a
  passphrase with spaces or a password containing a comma goes whole; and it
  covers `pwd`, `passphrase`, `credential`, `signature` and `sessionid`.
  Python literals are left alone, because masking them turned an assertion
  diff into two identical sides. An unquoted value containing a semicolon is
  masked only up to it — there the semicolon really is the delimiter.
  Redaction remains best-effort; see `SECURITY.md`.

See `SECURITY.md` for the local control channels the library opens and
what failure artifacts capture.

## [0.1.0] — 2026-02-11

Initial public release. UIA + Win32 + image backends, Qt widget
support, SAP GUI Scripting, Java Access Bridge basics, pytest plugin
with screenshots / video / trace / retries, `dolphin init` CLI.
