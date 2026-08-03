# Embedded web apps — UIA, CDP fallback, Shadow DOM

Windows apps built on Chromium (Electron, CEF), WebView2, or Internet
Explorer host web content inside a native shell. Two orthogonal problems
show up when trying to automate them via UIA:

| Case | UIA reach | Why |
|---|---|---|
| Electron with `--force-renderer-accessibility` | full DOM | accessibility tree built on demand |
| Electron without the flag (packaged apps) | native frame only | renderer never publishes an a11y tree |
| Shadow DOM (any host) | often hidden | closed shadow roots are not exposed to platform a11y |

Where UIA is blind, dolphin_desktop exposes a **CDP fallback**: attach to
the Chromium runtime's Chrome DevTools Protocol port and drive the DOM
directly. This section explains when to reach for it, how to install it,
and what to expect.

## When to use CDP

Use CDP when **any** of these apply:

1. Target is a packaged Electron app you cannot relaunch with
   `--force-renderer-accessibility` (Spotify, Steam, VS Code out of the box).
2. Target uses Shadow DOM (open OR closed roots aside from cases below).
3. UIA reports a `Pane` where DOM elements should be.
4. `is_visible()` is unreliable because the app defers a11y-tree build-out.

If the app happily exposes elements to UIA, stay on UIA — it is faster
and has no extra dependency.

## Install

```powershell
pip install "dolphin-desktop[cdp]"
playwright install chromium
```

The `[cdp]` extra pulls in Playwright; `playwright install chromium`
downloads a browser used for Playwright's protocol implementation.
Attempting to use CDP without the extra raises a `RuntimeError`
with these exact install instructions (never a bare `ImportError`).

## Quick start

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app, cdp = desktop.launch_electron_cdp(
    r'"C:\Program Files\Microsoft VS Code\Code.exe" --no-sandbox',
    debug_port=9222,
)

# Same 5 methods as the UIA Locator:
cdp.locator(".monaco-workbench").wait_for(state="visible")
cdp.locator("#status-bar").is_visible()
title = cdp.locator(".title").text()
cdp.locator("button.primary").click()
cdp.locator("input[name=username]").type_text("alice")

cdp.close()
app.kill()
```

`launch_electron_cdp` returns `(Application, CDPSession)`:

* `Application` is the same object as any other launched process — use
  `app.kill()`, `app.detach()`, `app.window(...)` for the native shell.
* `CDPSession` is your DOM-side entry point.

## Full API surface

`CDPLocator` mirrors `dolphin_desktop.Locator` so tests port between UIA
and CDP without renaming methods:

| Category | Methods |
|---|---|
| Mouse | `click(modifiers=, position=, button=, force=)`, `double_click`, `right_click`, `hover`, `drag_to` |
| Keyboard | `focus`, `press_key`, `type_text`, `set_text`, `clear` |
| Form | `check`, `uncheck`, `select_option(value=/label=/index=)`, `set_input_files` |
| Layout | `scroll_into_view`, `bounding_box` |
| State | `text`, `inner_html`, `value`, `get_attribute`, `is_visible`, `is_enabled`, `is_checked`, `exists`, `count` |
| Multi-match | `nth(i)`, `first()`, `last()`, `all()` |
| Narrowing | `locator(sub)`, `filter(has_text=/has=/has_not=/has_not_text=)` |
| A11y selectors | `get_by_role`, `get_by_label`, `get_by_text`, `get_by_placeholder`, `get_by_title`, `get_by_alt_text`, `get_by_test_id` |
| Wait | `wait_for(state=visible/hidden/attached/detached)` |
| Debug | `screenshot(path=None)` — returns a PIL image |
| Escape hatch | `element_handle()` — raw Playwright handle for advanced use |

`CDPSession` extras (page-level, not tied to a locator):

| Method | Purpose |
|---|---|
| `evaluate(js, *args)` | Run JavaScript in the page |
| `press_key(key)` | Global shortcut (Ctrl+Shift+P, Escape) |
| `screenshot(path=None)` | Full-page PIL image |
| `wait_for_load_state(state, timeout)` | Wait for `load`/`domcontentloaded`/`networkidle` |
| `wait_for_url(pattern, timeout)` | Wait until the URL matches |
| `current_url()` | Read the current URL |
| `reload(timeout)` | Reload the current page |
| `set_default_timeout(seconds)` | Change Playwright's default action timeout |
| `frame_locator(selector)` | Reach into an ``<iframe>`` |
| `accept_dialogs()` / `dismiss_dialogs()` | Auto-handle `alert/confirm/prompt` |
| `console_messages()` / `clear_console_messages()` | Capture / reset console log |
| `cookies()` / `set_cookies([...])` / `clear_cookies()` | Read + write browser cookies |
| `local_storage_get/set/clear` / `session_storage_get/set/clear` | Storage helpers |
| `route(pattern, handler)` / `unroute(pattern)` | Network interception (mock API responses) |
| `expect_response(url_or_predicate, timeout)` | Context manager: capture a response |
| `expect_request(url_or_predicate, timeout)` | Context manager: capture a request |
| `expect_download(timeout)` | Context manager: catch the next file download |
| `expect_popup(timeout)` | Context manager: catch the next new window (auto-switches) |
| `add_init_script(js)` | Run JS on every page BEFORE its own scripts (mock `Date.now`, feature flags) |
| `get_by_role/label/text/placeholder/title/alt_text/test_id(...)` | A11y-first page-level selectors |
| `pages()` / `switch_to_page(i)` | Multi-window Electron |
| `close()` / context-manager | Detach cleanly |

`CDPRoute` (yielded to route handlers) has `respond(status, body, ...)`,
`respond_json(payload)`, `pass_through()`, `abort(reason)`.
`CDPRequest` (readonly view) exposes `url`, `method`, `headers`,
`resource_type`, `post_data`, `post_data_json`.
`CDPDownload` has `suggested_filename`, `save_as(path)`, `path()`,
`cancel()`, `delete()`.

`CDPFrameLocator` (returned from `session.frame_locator(sel)`) chains
`locator(sub)`, `get_by_role`, `get_by_text`, `get_by_label` to reach
into iframe DOM.

All exceptions surface as **dolphin's** `ElementNotFoundError` and
`WaitTimeoutError` — user code catches the same types whether the
backend is UIA or CDP.

## Attaching to an already-running app

If you started Electron manually (or a fixture is passing you a running
PID) use `CDPSession.connect(endpoint)`:

```python
from dolphin_desktop import CDPSession

cdp = CDPSession.connect("http://127.0.0.1:9222")
cdp.locator("#login").click()
cdp.close()
```

## CEF apps (Steam, Spotify, some launchers)

CEF (Chromium Embedded Framework) speaks the **same** Chrome DevTools
Protocol as Electron — once the debug port is up, `CDPSession` and
`CDPLocator` work identically. Only the launch flag differs:

| Runtime | Flag | Default port |
|---|---|---|
| Electron | `--remote-debugging-port=9222` | caller picks |
| Steam | `-cef-enable-debugging` (single dash) | pinned to `8080` |
| Other CEF | usually `--remote-debugging-port=N` | caller picks |

Use `Desktop.launch_cef_cdp`:

```python
from dolphin_desktop import Desktop

app, cdp = Desktop().launch_cef_cdp(
    r'"C:\Program Files (x86)\Steam\steam.exe"',
    # debug_port=8080,                         # default — Steam hardcodes this
    # debug_flag="-cef-enable-debugging",      # default — Steam-specific
)

# Steam splits UI across many CEF pages (main, library, friends, overlay).
# Find the one you want by URL substring:
for i, p in enumerate(cdp.pages()):
    if "library" in p.url.lower():
        cdp.switch_to_page(i)
        break

owns_cs = "Counter-Strike" in cdp.evaluate("() => document.body.innerText")
```

**Steam gotchas** (see also
`tests/steam/test_steam_cdp.py`):

* Launching a second `steam.exe` normally forwards to the running client
  **without** re-processing CLI args — so the debug flag is silently
  dropped. Kill the existing Steam first, or start Steam manually with
  `-cef-enable-debugging` and use `CDPSession.connect("http://127.0.0.1:8080")`.
* Steam signs you out when killed. The test fixture defaults to
  connect-only for this reason; opt in to auto-launch with
  `DOLPHIN_STEAM_ALLOW_LAUNCH=1`.
* Library DOM is only rendered once the Library tab has been opened at
  least once. On a cold start `document.body.innerText` on the library
  page can be empty.

## Shadow DOM piercing

Playwright's CSS engine auto-pierces **open** shadow roots — descendant
combinators work through the shadow boundary. For clarity and to match
the explicit CDP contract, dolphin also supports the `>>` sub-locator
combinator:

```python
# both of these locate the same button inside an open shadow root
cdp.locator("#host #button-inside-shadow")
cdp.locator("#host >> #button-inside-shadow")
```

**Closed shadow roots** are not reachable via CDP — Chromium refuses to
serialise them even to devtools. There is no workaround: the app owner
has to expose an API or open the root. Document this at the boundary
where your test starts, so downstream engineers know to fall back to
image-based verification.

## Multi-window / multi-context Electron

Electron apps often open helper windows (settings, popouts). The
`CDPSession.pages()` method returns every open page across every browser
context:

```python
pages = cdp.pages()
print([p.url for p in pages])
cdp.switch_to_page(1)          # subsequent locator() calls target that page
```

If the active page dies (window closed, hot-reload), `session.page`
transparently picks the next durable page. This is what keeps
module-scoped fixtures alive across many tests.

## Limitations vs. UIA

| Concern | UIA | CDP |
|---|---|---|
| Latency | fast (in-process) | ~5–20 ms per action (out-of-process WebSocket) |
| Native menus / chrome | full | none (renderer only) |
| Shadow DOM (open) | usually invisible | reachable |
| Shadow DOM (closed) | invisible | invisible |
| Requires app change | no | must start with `--remote-debugging-port` |
| Extra dependency | no | `playwright` (~200 MB with Chromium install) |
| Runs offline | yes | yes |

Use both in the same test when you need to: UIA drives the native
window frame (File → Open, menu bar, hover a system tray icon), CDP
drives the DOM (form fills, click through a Shadow DOM component).

## Common failure modes

**`RuntimeError: Electron CDP debug port 9222 did not become live within 15s`**
: The app never opened the port. Verify by manually running the same
  command and visiting `http://127.0.0.1:9222/json/version` in a browser.
  Packaged Electron sometimes strips `--remote-debugging-port` — check
  the app's own docs.

**`TargetClosedError` on every action after the first**
: You launched the app but pytest killed it between tests. Fix: call
  `app.detach()` after `launch_electron_cdp` in a module-scoped fixture
  so the plugin skips per-test PID reaping.

**`CDP connect_over_cdp('http://…') failed: connect ECONNREFUSED`**
: Port is up but Chromium hasn't finished binding. Bump `timeout=` on
  the `launch_electron_cdp` call.

## See also

* `tests/electron/test_vscode_cdp.py` — full
  integration test suite (workbench interaction + Shadow DOM piercing).
* `docs/guides/electron.md` — Electron via plain UIA when it does work.
* `docs/guides/image-based.md` — last-resort fallback when nothing else sees the
  element.
