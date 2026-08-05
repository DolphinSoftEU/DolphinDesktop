# Support matrix (stack × backend × mode)

The authoritative table of **what dolphin_desktop covers, and how**.
Read this before deciding whether a given stack is in scope, which
backend to target, and where the sharp edges are.

Companion pages:

* [backend-capabilities.md](./backend-capabilities.md) — the
  `Capability` vocabulary that this matrix maps onto.
* Per-stack deep dives live under `docs/guides/` — SAP, Qt, Delphi,
  Java, image-based, mainframe, Oracle Forms, PowerBuilder and more.

## Legend — backends

| Backend id       | Underlying tech                                              | Registered as               |
| ---------------- | ------------------------------------------------------------ | --------------------------- |
| `uia`            | Microsoft UI Automation (via pywinauto)                      | `UIABackend`                |
| `win32`          | Win32 messaging (via pywinauto)                              | `Win32Backend`              |
| `qt`             | UIA with a Qt-aware locator wrapper (+ optional agent DLL)   | `QtBackend`                 |
| `image`          | Template matching + OCR (opencv + tesseract)                 | `ImageBackend`              |
| `cdp`            | Chrome DevTools Protocol via Playwright                      | `CDPBackend` marker → `CDPSession` |
| `sap`            | SAP GUI Scripting COM engine                                 | `SapBackend` marker → `SapGui` |
| `mainframe`      | s3270 subprocess **or** HLLAPI DLL binding                   | `MainframeBackend` marker → `MainframeTerminal` |
| `delphi`         | UIA + `TComponent.Name → AutomationId` name resolution       | `DelphiBackend` marker → `DelphiApp` |
| `java`           | Java Access Bridge (JAB) — Swing / Oracle Forms / JavaFX     | `JavaBackend` marker → `OracleFormsApp` / `JavaAccessBridge` |

Marker backends surface in `list_backends()` and publish the same
`Capability` set the fronting facade offers, but direct backend
calls redirect the caller to the facade via
`UnsupportedCapabilityError` — the real automation surface lives on
`Desktop.sap()`, `Desktop.launch_electron_cdp()`,
`Desktop.launch_delphi()`, `Desktop.mainframe()`,
`Desktop.launch_java()` / `Desktop.launch_oracle_forms()`.

## The matrix

Each row: **primary backend** (the recommended path), any
**alternative** (fallback / mode toggle), the supported **modes**,
the headline **capabilities** it exposes (against the
`Capability` vocabulary — see full set in
[backend-capabilities.md](./backend-capabilities.md)), and known
**limitations**.

### Native Windows apps (WPF, WinForms, UWP)

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | `uia`                                                              |
| Alt           | `win32` for legacy MFC / VB6 apps that misreport UIA               |
| Modes         | Attached (running app), Launch (spawn + wait), Hidden desktop      |
| Capabilities  | LOCATE, GET_TREE, READ_TEXT, READ_STATE, CLICK, DOUBLE_CLICK, RIGHT_CLICK, HOVER, DRAG, TYPE_TEXT, PRESS_KEY, SCROLL, INVOKE, TOGGLE, EXPAND, COLLAPSE, SELECT, SET_VALUE, SCREENSHOT |
| Limitations   | Custom-paint controls (`Ownerdraw`, DirectComposition) opaque under UIA — fall back to `image`. |

### SAP GUI (SAP GUI for Windows, SAP GUI Scripting)

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | `sap` (marker → `SapGui`, `SapSession`, `SapLocator`)              |
| Alt           | `uia` if SAP GUI Scripting is disabled by the SAP admin (heavily degraded — only screenshot / coarse click work) |
| Modes         | Attach to running SAP GUI; multiple sessions per connection        |
| Capabilities  | LOCATE, GET_TREE, READ_TEXT, READ_STATE, CLICK, DOUBLE_CLICK, RIGHT_CLICK, TYPE_TEXT, PRESS_KEY, INVOKE, TOGGLE, SELECT, SET_VALUE, SCREENSHOT |
| Limitations   | HOVER not exposed by SAP GUI Scripting; DRAG not published (SAP DnD is component-specific — no generic verb); SCROLL is grid-scoped (ALV grid `FirstVisibleRow` or `VerticalScrollbar.Position`), not a mouse-wheel event. Use `SapLocator.scroll_to_row(n)` on grids. |

### Qt 5 / Qt 6 (QWidget + QML)

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | `qt` (UIA-derived, Qt-aware locator)                               |
| Alt           | `uia` — bypass the Qt wrapper if the app uses only classic widgets |
| Alt           | Agent DLL — injected Qt agent (requires the Qt agent DLL, bundled with the library) for QML / `QGraphicsView` custom-paint |
| Modes         | Requires `QT_ACCESSIBILITY=1` in target process env                |
| Capabilities  | Same as UIA (full accessibility set)                               |
| Limitations   | Widgets rendered on a `QGraphicsView` custom scene are opaque under UIA until the agent DLL is loaded. Set `objectName` (dotted path) + `accessibleName` for stable selectors — objectName becomes UIA AutomationId, accessibleName becomes UIA Name. |

### Electron / CEF / WebView2 (Chromium-based)

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | `cdp` (marker → `CDPSession` via Playwright)                       |
| Alt           | `uia` for the Chromium OS-level frame (window title, tray menu)    |
| Modes         | Launch (`--remote-debugging-port`), Attach (existing port), Emulated (Playwright headless mode for CI) |
| Capabilities  | Full accessibility set + SCREENSHOT + DRAG + SCROLL                |
| Limitations   | Target app must start with `--remote-debugging-port=NNNN` (or be launched via `Desktop.launch_electron_cdp` which passes it). CEF apps without the flag exposed require `image` fallback. |

### Delphi / VCL (RAD Studio) and Lazarus / LCL

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | `delphi` (marker → `DelphiApp` on UIA)                             |
| Alt           | `win32` — for pre-VCL-10.4 Delphi that does not publish `TComponent.Name` as UIA AutomationId |
| Alt           | `image` — Ownerdraw controls (custom TCanvas paint)                |
| Modes         | Launch, Attach, Hidden desktop                                     |
| Capabilities  | Full accessibility set + SCREENSHOT + DRAG + SCROLL                |
| Limitations   | Lazarus LCL does not publish AutomationId — falls back to class + role matching. VCL 10.4+ works out-of-the-box; earlier VCL requires the `win32` alt. |

### PowerBuilder (2019+ Appeon)

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | `uia` (semantic wrapper: `Desktop.launch_powerbuilder`)            |
| Alt           | `win32` for pre-2019 PowerBuilder Classic                          |
| Modes         | Launch, Attach                                                     |
| Capabilities  | Full accessibility set for standard controls                       |
| Limitations   | DataWindow interior may be custom-painted and opaque to UIA — check with `dolphin spy`; fall back to `image` / OCR for those. |

### Java Swing / JavaFX / Oracle Forms 12c

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | `java` (marker → `JavaAccessBridge` / `OracleFormsApp`)            |
| Alt           | `uia` — JavaFX 8+ apps that set `-Djavafx.accessible=true` register in UIA |
| Modes         | Requires JAB enabled (`jabswitch /enable` — Windows switch style) and `WindowsAccessBridge-64.dll` reachable on PATH |
| Capabilities  | Full accessibility set + SCREENSHOT + SCROLL                       |
| Limitations   | JAB does not expose a native drag pattern (DRAG omitted from `java` cap set). Oracle Forms applet in a browser needs `-Djavafx.accessible=true` on the JRE and the browser applet plugin. |

### Mainframe TN3270 (z/OS, CICS)

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | `mainframe` — `backend="s3270"` (spawns `ws3270.exe`)              |
| Alt           | `mainframe` — `backend="hllapi"` (PCOMM / Rumba / Attachmate DLL)  |
| Modes         | Direct TN3270 connect (s3270); attach to running emulator (HLLAPI) |
| Capabilities  | LOCATE (field addressing), READ_TEXT (screen buffer), READ_STATE, TYPE_TEXT, PRESS_KEY (AID keys) |
| Limitations   | No CLICK / HOVER / DRAG — terminals have no such concept. No SCREENSHOT (the text buffer *is* the image; use `.text()`). No INVOKE / TOGGLE / SET_VALUE (no programmatic patterns). |

### Mainframe TN5250 (IBM i / AS/400)

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | `mainframe` — pure-Python TN5250 (`backend="tn5250"`, no NVT fallback) |
| Alt           | HLLAPI if the site uses PCOMM/Rumba (same cap set)                 |
| Modes         | Direct connect                                                     |
| Capabilities  | Same as TN3270                                                     |
| Limitations   | Read-side full; write-side WSF handshake missing on some IBM i releases. |

### Office (Excel / Word / PowerPoint / Outlook)

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | Office COM automation (in-process, not a `Backend` registry entry) |
| Alt           | `uia` for the ribbon / task-pane UI when COM cannot reach a dialog |
| Modes         | Launch, Attach                                                     |
| Capabilities  | LOCATE (workbook / worksheet / range), READ_TEXT, TYPE_TEXT (cell values), SET_VALUE, INVOKE (macros), SCREENSHOT via UIA |
| Limitations   | Office COM is a domain API — not exposed via the generic `Backend` interface. Uses its own `ExcelApp.open(path)` / `ExcelApp.connect()` / `WordApp` facades. Modeless dialogs (e.g. AutoCorrect prompts) still routed through UIA. |

### Image fallback

| Field         | Value                                                              |
| ------------- | ------------------------------------------------------------------ |
| Primary       | `image` — template matching (opencv) + OCR (tesseract via `[vision]` extra) |
| Alt           | none — this is the "last-resort" backend when accessibility fails  |
| Modes         | Full screen, region, per-window                                    |
| Capabilities  | LOCATE (via template), CLICK, DOUBLE_CLICK, RIGHT_CLICK, HOVER, TYPE_TEXT (focus + Keyboard), PRESS_KEY, SCREENSHOT |
| Limitations   | No GET_TREE, no READ_STATE, no INVOKE / TOGGLE / EXPAND / COLLAPSE / SELECT / SET_VALUE — pixel matching has no accessibility patterns. `SCROLL` and `DRAG` not published (pyautogui-level scroll is not integrated as a first-class verb). |

## Cross-cutting mode notes

* **Hidden mode** — every UIA-backed row supports the hidden-desktop
  session mode (`Desktop(hidden=True)`), used to avoid stealing
  focus during CI runs. Does not apply to `cdp` (Playwright has its
  own headless mode) or `mainframe` (terminals are headless by
  definition).
* **Attach vs Launch** — every row supports attach-to-running.
  `mainframe` HLLAPI is *attach-only* (the emulator process owns the
  session).
* **Multi-monitor / DPI** — `image` and `screenshot`-based verbs
  respect per-monitor DPI awareness set by the target process.

## Out-of-scope: dead technologies (intentional non-support)

The following are **deliberately unsupported**. They are listed
here so their absence reads as a decision, not an oversight.

### Adobe Flash / Flex (AIR)

* **Adobe end-of-life 2020-12-31** — Flash Player removed from
  every major browser; Flash Player itself blocks execution as of
  the January 2021 kill-switch. AIR moved to Harman with a paid
  licence and near-zero user base.
* **Automation surface never existed** — Flex's browser-based
  automation bindings relied on the Flash Player plugin
  intercepting JS-to-ActionScript bridges. That plugin is gone.
* **Dolphin decision** — no `flash` / `flex` backend will ship. Migrate
  legacy Flex apps to Angular/React (CDP-backed) or a
  Delphi/VCL desktop port; both are in-scope.

### Microsoft Silverlight

* **Microsoft end-of-life 2021-10-12** — pulled from Windows
  Update; unsupported on any current browser (last surviving path
  was IE11 mode in Edge Legacy, itself EOL 2022-06-15).
* **No modern automation path** — the `UIAutomationSilverlight`
  bridge required the Silverlight runtime, which no longer
  installs cleanly on Windows 10 22H2 / Windows 11.
* **Dolphin decision** — no `silverlight` backend. Migration path is
  Blazor WebAssembly (CDP-backed) or a WPF desktop rewrite (`uia`).

### Also deliberately out-of-scope

| Tech                         | Status               | Migration path                     |
| ---------------------------- | -------------------- | ---------------------------------- |
| Java Applets in browser      | JRE plugin removed   | JavaFX (`uia` or JNLP + `java`)    |
| Internet Explorer 11 shell   | EOL 2022-06-15       | Edge (CDP)                         |
| Windows Forms 1.x on .NET Fx | Runs but no roadmap  | WinForms .NET 6+ (`uia`)           |
| MFC via UIA proxy DLL        | Works partially      | Officially `win32` backend         |

## Capability checklist

Coverage is measured against a **fixed operation checklist**, not a
vague "the stack is supported" claim. A dolphin_desktop stack counts
as fully covered iff **every row below is supported** for that stack.

| Concept                       | dolphin API                                                |
| ----------------------------- | ---------------------------------------------------------- |
| **Locate** by role/name       | `window.get_by_role`, `Locator`, stack-specific facade     |
| **Locate** by AutomationId    | `auto_id="…"` kwarg on `Locator` / `window.edit(…)`        |
| **Locate** by image template  | `image` backend + `criteria={"template": "…"}`             |
| **Click** (left/right/middle) | `.click(button="left"\|"right"\|"middle")`                 |
| **Double-click**              | `.double_click()`                                          |
| **Hover**                     | `.hover()`                                                 |
| **Drag & drop**               | `.drag_to(target)`                                         |
| **Type text**                 | `.type_text(text)`                                         |
| **Send key**                  | `.press_key("F5")`                                         |
| **Read text**                 | `.text()` / `.get_text()`                                  |
| **Read state**                | `.is_checked()`, `.is_enabled()`, `.is_visible()`          |
| **Assertions**                | Native pytest asserts + `wait_for_*`                       |
| **Embedded web**              | `Desktop.launch_electron_cdp` for Chromium hosts           |
| **Fallback to image**         | `image` backend as first-class citizen                     |
| **Reporting** (HTML)          | Native `dolphin-report.html` + optional Allure hooks (see `docs/ci/`) |
| **Screenshots on failure**    | `--dolphin-screenshot-on-fail` pytest option, auto-attach  |
| **Video recording**           | `--dolphin-video` pytest option + `dolphin-videos/` output |
| **Retry / re-run**            | `--dolphin-retry N` pytest option (native, no plugin needed) |
| **Self-healing selectors**    | `dolphin selfheal-stats` + fuzzy locator drift             |
| **Code-generation from spy**  | `dolphin spy` / `dolphin record`                           |
| **Multi-user / RDP**          | `Desktop(hidden=True)` for parallel sessions               |

### Notable strengths

* **Electron / VS Code / Steam**: full Chromium accessibility via
  CDP, not just image matching over an opaque window.
* **Mainframe TN3270 / TN5250 / HLLAPI**: three interchangeable
  backends behind one `MainframeTerminal` API.
* **Serializable capability introspection**:
  `list_backends()` returns JSON — downstream tooling can build a
  live compatibility map without parsing docs.

### Known limitations

* **Recorder UX** — `dolphin record` is functional but minimal; no
  visual editing of recorded steps yet.
* **Object repository editor** — aliases are edited as YAML / Python
  files; there is no GUI editor.
* **Cross-platform tests** — dolphin is Windows-only by design;
  mobile and macOS are out of scope.

## When to *not* use dolphin_desktop

Honest boundaries:

* **Mobile (iOS / Android)** — use a dedicated mobile-automation framework.
* **macOS-native apps** — the `macos` backend is a reserved stub.
* **Linux X11 / Wayland** — the `linux` backend is a reserved stub.
* **Anything Flash / Silverlight-based** — see the out-of-scope
  section above.
