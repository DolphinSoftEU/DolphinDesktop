# Playwright compatibility layer

Playwright-shaped facade over dolphin's CDP session. Use to migrate
existing Playwright tests to Electron / CEF targets that dolphin
handles better.

::: dolphin_desktop.playwright_compat
    options:
      show_root_heading: false
      show_source: false
      members_order: source
      filters: ["!^_", "sync_playwright"]

## Example

```python
from dolphin_desktop.playwright_compat import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    page = browser.contexts[0].pages[0]
    page.locator("#login").click()
    page.locator("input[name=user]").fill("alice")
    page.locator("button[type=submit]").click()
    assert page.locator(".welcome").is_visible()
    browser.close()
```

## Not implemented

The following Playwright APIs are **not** available through the
compat layer — dolphin does not have equivalents:

* Firefox / WebKit browser types (dolphin is Chromium-only).
* `chromium.launch()` — dolphin never spawns a standalone Chromium;
  use `Desktop().launch_electron_cdp(...)` for Electron apps or
  `connect_over_cdp(url)` for a running one.
* Async API (`async_playwright`) — dolphin's CDP surface is sync-only;
  calling it raises `NotImplementedError` with a migration hint.
* Trace viewer / video recording APIs — dolphin has its own trace /
  video capture (see the pytest plugin options).
* Multi-context / storage-state persistence — Electron apps run
  under a single default context.

Calls to these attributes raise `NotImplementedError` with a
pointer to the dolphin equivalent.

The same explicit exception is used for `browser.new_context()`,
`context.new_page()`, `context.storage_state()`, `context.tracing` and
`page.video`.  These names are exposed so an accidentally ported Playwright
test fails with a useful compatibility message rather than an
`AttributeError`.
