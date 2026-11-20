# Changelog

All notable changes to `dolphin-desktop` are documented in this file.
Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Planned release: **0.2.0**. Changes are relative to the public 0.1.0 release.

### Added

* **Programmatic actions and waiters:** `Locator.invoke()`, `toggle()`,
  `expand()`, `collapse()`, `select()` and `set_value()` use accessibility
  patterns without physical mouse or keyboard input. Unsupported patterns raise
  `UnsupportedPatternError`. Corresponding actions are available on Java and
  Delphi controls. `wait_for_text()` and `wait_for_checked()` support waiting
  for control state without fixed sleeps.
* **Qt 5 / Qt 6 agent and selectors:** Qt detection, objectName selectors and
  an injected x64 agent extend existing UIA support with QObject properties
  and methods, QML scene graphs and QGraphicsView items. Public `WidgetElement`,
  `QmlElement` and `GraphicsViewElement` wrappers expose the agent API.
  UIA tree paths support nested Qt rows, including scrolling and expansion.
  Agent calls are serialized across threads; `QtAgentTimeoutError`,
  `QtAgentClient.reattach()` and `Application.reset_qt_agent()` provide recovery.
* **SAP GUI Scripting:** `SapGui`, `SapConnection`, `SapSession` and
  `SapLocator` provide COM-based session discovery, transactions, control
  interaction, grid access, status checks and waits. SAP-specific spy helpers
  inspect controls and generate selectors. `keyboard_login()` requires verified
  writable UIA login fields and aborts on focus loss; SAP GUI 800 screens that
  do not expose those fields require Scripting login.
* **Delphi / VCL and Lazarus / LCL:** `Desktop.launch_delphi()` /
  `attach_delphi()`, `DelphiApp`, `DelphiForm` and `DelphiComponent` wrap controls
  exposed through UIA/MSAA. Name selectors require an application-supplied UIA
  provider publishing component names as AutomationIds; stock VCL does not
  supply that mapping, and Dolphin includes no VCL Name provider or RTTI agent.
  **PowerBuilder:** `Desktop.launch_powerbuilder()` supports Appeon applications
  through UIA, with OCR fallback for DataWindow content.
* **Electron / CEF over CDP:** `Desktop.launch_electron_cdp()`,
  `launch_cef_cdp()` and `CDPSession` provide DOM locators, shadow-DOM and frame
  access, network routing, downloads, popups, dialogs and console capture.
  `playwright_compat` provides a synchronous Playwright-style adapter with
  millisecond timeouts and `first` / `last` properties. Sessions on one thread
  share a Playwright driver while retaining independent connections; closing a
  page affects only that page. Explicitly selected closed pages raise
  `CDPStalePageError`, while a self-closing captured popup returns to its opener.
* **Mainframe terminals:** `Desktop.mainframe()` exposes s3270, native TN5250
  and HLLAPI through `MainframeTerminal`, with field detection, code pages,
  cursor/field/text waits, tracing and independent sessions. TN3270/TN5250
  support verified TLS via `tls=True`, with `tls_cafile` / `tls_context` where
  supported. Remote plaintext requires `allow_plaintext=True`; port 992 alone
  does not enable TLS. HLLAPI requires an absolute `hllapi_dll_path`.
  The native TN5250 write/submit path remains unverified against live IBM i
  hosts; use an enterprise emulator with HLLAPI for write-critical flows.
* **Oracle Forms:** `Desktop.launch_oracle_forms()` / `attach_oracle_forms()`
  and the `OracleFormsApp` facade provide block, item, menu and LOV navigation,
  status waits and PC-style `fmrpcweb.res` key bindings. Direct Java Access
  Bridge operations are preferred; modifier shortcuts require foreground focus.
* **PowerPoint COM:** `PowerPointApp` supports presentations, slide ordering
  and size, shapes, embedded pictures, text and character-range formatting,
  paragraphs, tables, charts, notes, hyperlinks and PDF export. Mixed text
  formatting returns `None`; text ranges use validated 1-based indices.
* **Classic Outlook COM:** `OutlookApp` supports mail, recipients, attachments,
  folders, appointments and local MSG persistence using an existing MAPI
  profile. Owned drafts support move/delete with verified outcomes; ambiguous
  permanent-delete outcomes raise an error. Shared sessions and existing items
  are preserved on context exit. PowerPoint and Outlook also support explicit
  exclusive process ownership and graceful exit verification.
* **Locator aliases and ambiguity reporting:** `automation_id`, `role` and
  `name` normalize to `auto_id`, `control_type` and `title`, including in chained
  selectors and fallbacks. Conflicting values raise `ValueError`.
  `AmbiguousMatchError` reports non-unique matches; use tighter criteria or
  `found_index`. `exists()` returns `True` for an ambiguous match.
* **Backend capabilities:** public capability descriptions and
  `UnsupportedCapabilityError` distinguish unsupported backend operations from
  missing elements or unsupported patterns. Pytest's `--dolphin-backend` accepts
  registered and entry-point backends, including registrations in nested
  `conftest.py`, as well as `auto`.
* **OCR and helpers:** `Screen.find_text()` retries at 2× and 3× scale, mapping
  results back to screen coordinates (`scales=(1,)` disables retries).
  Public helpers cover paths, temporary files, environment variables, process
  discovery, HTTP/TCP checks, base64, timing and threads. A Getting Started
  guide and stack-specific guides document installation and prerequisites.

### Changed — breaking

* **Bounds:** `Locator.bounding_box()`, `Window.bounding_box()` and JAB bounds
  return `{x, y, width, height}`. Replace `left` / `top` with `x` / `y` and
  derive `right` / `bottom` by adding width / height. Delphi uses the same shape.
* **Missing attributes:** `get_attribute()` raises `AttributeError` for unknown
  names. Pass a default, such as `get_attribute("optional_name", None)`, to
  retain the previous behavior.
* **Spy results:** `spy.pick()` returns a versioned dictionary containing
  `schema_version`, `status`, `chain` and `message` instead of a bare list.
  `spy.inspect()` defaults to `depth=12` and `max_children=200` and reports
  traversal limits; pass `None` to remove either limit.
* **Process ownership:** `Application.close()` leaves connected, unowned
  processes running. Window discovery rejects unrelated processes with
  `WindowNotFoundError` instead of adopting a matching foreign window.
* **Java configuration and selectors:** set an absolute `JAVA_HOME`; JAB loads
  only from its `bin` directory. Unsupported Java selector criteria raise
  instead of being ignored. JAB `role="text"` means a text field and
  `role="window"` means a window; use `label` / `frame` for those roles.
* **Text input:** `Keyboard.type()` and `Locator.type_text()` escape literal
  SendKeys metacharacters by default. Pass `escape=False` for key sequences,
  or use `Keyboard.press()` / `Locator.press_key()`.
* **Validation and state errors:** `Keyboard.hotkey()` rejects missing keys,
  modifier-only calls and multiple non-modifier keys. `check()`, `uncheck()` and
  `is_checked()` raise `UnsupportedPatternError` when state cannot be read.
  `Tab.select_tab()` raises `ElementNotFoundError` for a missing tab.
* **Configuration:** `config()` validates ranges; `video_fps` must be an integer
  from 1 to 30, `retry_count` a non-negative integer, and timeout / poll interval
  finite and non-negative. Invalid marker
  `video_mode` values raise `ValueError` before recording starts. Malformed
  `DOLPHIN_*` environment values warn and fall back to defaults.
* **Recorder scope and lifecycle:** choose `--app`, `--pid`, or explicitly
  `--global` (`global_recording=True` in Python). Application filters bind to one
  process. Each `Recorder` permits one start attempt; create a new instance to
  record again. Stop recording before calling `actions()` or `generate_code()`.
  Unknown and password controls are masked; plaintext capture is limited to
  stable native Windows Edit controls. Navigation and shortcuts are emitted as
  separate key actions rather than embedded in typed text.
* **Office ownership and macros:** `ExcelApp.open()` and `WordApp.open()` start
  private instances; `connect()` leaves existing documents and applications
  untouched on exit. Macros are disabled by default; trusted VBA requires
  explicit `allow_macros=True`. `quit(save_changes=True)` reports save errors
  after shutting down the owned instance.
* **Pytest plugin name:** the entry point is `dolphin-desktop`; update explicit
  plugin loading to `-p dolphin-desktop`.

### Changed

* Supported Python versions are 3.11–3.13 (`>=3.11,<3.14`). Windows remains the
  supported automation platform. Wheel metadata restricts `pywin32` and
  `comtypes` to Windows; importing elsewhere emits a `RuntimeWarning`.
* Dependency minimums include Pillow 13.0.0, comtypes 1.4.8,
  opencv-python 4.10.0.84, mkdocs-material 9.7.7 and sentry-sdk 2.8.0.
  The pytest extra requires `pytest>=9.0.3,<10`.
* Java text, value and action operations prefer direct Access Bridge APIs over
  clipboard and physical input, retaining fallbacks where the API is unavailable.
* Locator resolution reduces repeated UIA scans and caches Java-window
  detection. Ordered selector and image fallbacks share the action deadline and
  record successful self-healing, including beneath resolved wrappers.
* Screenshot, video and trace paths use opaque names to avoid collisions and
  exposing sensitive parameter values. JUnit properties retain screenshot,
  trace and video paths through teardown, including retained passing videos.

### Removed

* The `video` extra; MP4 recording requires an external `ffmpeg` executable.
  Install the `fast` extra for accelerated screenshots with `mss`.
* The `dev` extra; use `uv sync --group dev` for the development toolchain.

### Fixed

* **UIA locators:** tree-walk fallbacks honor supported criteria and indices,
  verify uniqueness even when the initial search returns a partial match, and
  reject incomplete scans. Hidden-but-live elements resolve through `exists()`
  and presence waits. `is_visible()` uses the same fallback coverage as actions.
  Cloning via `timeout()` / `nth()` preserves specialized locator state, and
  locators returned by `all()` support further child searches. Unsupported
  search criteria propagate errors instead of becoming empty results.
* **Win32 controls:** typed shortcuts and `control_type` selectors match classic,
  WinForms, VB6 and VCL classes. Tree paths, list contents, tab selection and
  native menus use Win32 controls directly. `all()`, `count()` and negative
  indices apply the same criteria as individual lookup. Unsupported UIA-only
  types raise `UnsupportedCapabilityError`; text and scroll queries avoid
  pywinauto's extra child-window waits. Empty Edit/Static text reads preserve
  focus and clipboard contents.
* **Menus and focus:** native menu clicks use correct coordinates on scaled
  displays, open closed ancestors within one deadline, and validate item identity,
  enabled state and hit window before sending input. Nested menus stay open;
  already-active windows are not refocused in ways that dismiss owned popups.
  Read-only locator queries do not open menus or send input.
* **Control input:** radio buttons use selection patterns; literal typing
  preserves spaces, newlines, tabs and punctuation, including file-dialog paths.
  Hotkeys use virtual-key codes, support Win-key combinations and release held
  modifiers on errors. Drag operations release the mouse button on failure,
  and `Mouse.click()` rejects unknown buttons before moving the cursor.
* **Processes and hidden desktops:** teardown continues after individual cleanup
  failures, reaps owned applications after fixture finalizers and preserves
  connected applications. Hidden launches clean up owned descendants on normal
  owner exit or hard termination. Windows 11 Notepad launch handoff binds only
  verified new descendants. Concurrent hidden runners avoid desktop-creation
  races and share console interrupt handling. `dolphin-run` preserves command
  exit codes and caller environment, quotes Windows arguments correctly, and
  bounds interrupt/termination cleanup before releasing desktop handles.
  `launch_qt()` passes Qt environment variables only to the child process.
* **Recorder:** generated code quotes selectors and text safely, preserves
  double-clicks, AltGr text and modifier combinations, and assigns typing to
  the control that had focus at capture time. Shutdown drains queued events;
  hook, worker and cleanup failures are reported before output is generated.
  Callback failures still forward input and cannot interrupt remaining cleanup.
* **Java resources:** Access Bridge contexts are released exactly once,
  including for enumerated locators and failed tree walks. `exists(timeout=...)`
  accepts an explicit timeout, state checks use complete state names, and
  `focus()` requests focus when supported instead of activating the control.
* **Reports and traces:** capture errors preserve the original test failure.
  Setup and teardown failures produce accurate trace/HTML outcomes, including
  under xdist. SQLite handles close on failure; unavailable tracing disables
  itself or reports degradation instead of failing a successful UI action.
  Trace discovery is deterministic and viewing a missing database reports an
  error without creating a new file. Skipped reports preserve their structured
  representation and display their reasons.
* **Clipboard and images:** clipboard access retries contention and releases
  handles after failures; failed writes restore prior text. DIB conversion uses
  the actual header layout. Screenshots cover all monitors, image matches use
  virtual-desktop coordinates, and templates load from non-ASCII paths.
  `find_all()` suppresses overlapping matches and avoids flat-background stalls.
  Image waits raise `WaitTimeoutError` consistently.
* **Spy and artifacts:** picker access-denied errors propagate to the CLI with
  exit code 1, including during selector verification and image capture.
  Crash dumps include attached applications. Failed video finalization is
  reported instead of returning a successful artifact. Object-repository YAML
  deletion does not block other lookups, and invalid `parent:` entries fail at
  load with the source file identified.
* **Logging:** `DOLPHIN_LOG_LEVEL`, the pytest log-level option and
  `config(log_level=...)` configure the `dolphin_desktop` logger hierarchy used
  by library emitters. `get_logger()` returns that logger.

### Security

* **Secrets and reports:** `Secret` masks registered literal values in
  Dolphin-managed text. Structural redaction covers logging, traces,
  self-healing journals, crash-dump text, UIA dumps, Allure results and captured
  streams, JUnit XML, HTML reports and pytest terminal/collection output.
  Sensitive parameter snapshots are scoped to their test, including under
  xdist. Redaction remains best-effort: image pixels and arbitrary native
  Allure attachment bodies require caller review/sanitization; see `SECURITY.md`.
* **Qt agent:** authenticated protocol v2 uses random per-attach pipe names and
  session secrets, a logon-SID ACL, remote-client rejection and controller/server
  PID checks. Requests and responses are bounded; the agent expires its pipe
  when its controller exits. Bundled x64 DLLs are hash-verified before injection;
  C++ source, pinned build scripts and reproducibility checks are included.
* **Mainframe:** host/text validation and protocol quoting prevent s3270 command
  injection. Native TN5250 enforces record, buffering, negotiation and read
  limits. Terminal diagnostics omit typed text and raw frame payloads.
* **DLL loading:** JAB and HLLAPI use explicit absolute paths and restricted
  Windows DLL loading flags instead of searching the working directory or PATH.
* **Process identity:** cleanup anchors launched processes to creation identity
  and retained handles, avoiding termination of unrelated processes after PID
  reuse. CDP launchers reject occupied debug ports and verify listener ownership
  before connecting to the launched application.
* **Office:** opening documents disables VBA and automatic external-link updates
  by default and restores temporary security settings even after errors.
  Excel 4.0/XLM macros and other active content still require Office policy
  controls; the wrappers are not a document sandbox.
* **CLI, HTTP and trace input:** `dolphin init` confines output to the working
  directory, including through symlinks. `http_ok` restricts requests and
  redirects to HTTP(S). CDP screenshots validate format and size. Trace HTML
  validates database schema and values, confines screenshot paths and applies
  a Content Security Policy.
* **Telemetry:** default Sentry integrations and local-variable capture are
  disabled; events outside Dolphin are excluded.
* **Build and release:** GitHub Actions use pinned commits and least-privilege
  permissions. Locked dependencies and individual extras are audited; native
  build tooling requires `soupsieve>=4.1` and Python 3.11.5+ for that tool group.
  Packaging verifies native binaries and wheel provenance, and publication
  smoke tests verify the downloaded wheel hash against the tested CI artifact.

## [0.1.0] — 2026-02-11

Initial public release. UIA + Win32 + image backends, Qt widget
support, SAP GUI Scripting, Java Access Bridge basics, pytest plugin
with screenshots / video / trace / retries, `dolphin init` CLI.
