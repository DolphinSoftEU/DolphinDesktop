# Migration guide

Coming to dolphin-desktop with an existing test suite? This guide covers
the migration dolphin is built for — from **pywinauto**, the library it
extends — plus the general pattern that makes any migration mechanical.

## From pywinauto

The closest migration there is: dolphin builds on pywinauto, so the same
UIA backend resolves your selectors — criteria like `auto_id`, `title`,
`control_type` and `class_name` port **unchanged**. What you gain on top:
lazy locators with auto-waiting (no `.wait("visible")` boilerplate),
pytest fixtures (`desktop`, `launch`) with process cleanup, traces /
video / screenshots on failure, self-healing `fallback` selectors, and
headless-safe programmatic actions (`invoke()`, `set_value()`).

### Launch + attach

| pywinauto | dolphin-desktop |
|---|---|
| `Application(backend="uia").start("app.exe")` | `Desktop().launch("app.exe")` |
| `Application(backend="uia").connect(title="X")` | `Desktop().connect(title="X")` |
| `Application().connect(process=1234)` | `Desktop().connect(process=1234)` |
| `app.kill()` | `app.kill()` — or let the `launch` fixture reap it |

### Find + interact

| pywinauto | dolphin-desktop |
|---|---|
| `app.window(title="Main")` | `app.window(title="Main")` |
| `dlg.child_window(auto_id="btnOK", control_type="Button")` | `win.locator(auto_id="btnOK", control_type="Button")` — or `win.button(auto_id="btnOK")` |
| `ctrl.click_input()` | `loc.click()` — physical; prefer `loc.invoke()` (UIA pattern, headless-safe) |
| `ctrl.type_keys("hi", with_spaces=True)` | `loc.type_text("hi")` — or `loc.set_value("hi")` (programmatic) |
| `ctrl.window_text()` | `loc.text()` |
| `ctrl.wrapper_object()` | not needed — locators resolve lazily on each action |

### Waits and timing

| pywinauto | dolphin-desktop |
|---|---|
| `dlg.wait("visible", timeout=10)` | built into every action; explicit: `loc.wait_for(state="visible", timeout=10)` |
| `wait_until(10, 0.5, lambda: ...)` | `loc.wait_for_text("...")`, `wait_for_checked()`, `wait_until_hidden()` |
| `timings.Timings.slow()` | global timeout: `--dolphin-timeout` / `DOLPHIN_TIMEOUT` |
| `ElementAmbiguousError` | `AmbiguousMatchError` — same fix: `found_index=N` or tighter criteria |

### Global input

| pywinauto | dolphin-desktop |
|---|---|
| `from pywinauto.keyboard import send_keys` | `from dolphin_desktop import Keyboard` — `Keyboard.press("^s")`, `Keyboard.type("hi")` |
| `from pywinauto import mouse` | `from dolphin_desktop import Mouse` — `Mouse.click(x, y)` |

Migration can be incremental: because the criteria dialect is identical,
port one test file at a time and both suites keep passing. Criteria also
accept the friendlier aliases `automation_id=`, `role=` and `name=`
(normalized to `auto_id` / `control_type` / `title`), so half-remembered
pywinauto spellings keep working.

## Web-style tests over CDP

If your existing suite drives browsers or Electron apps through the
`playwright` package, dolphin's CDP surface uses the same package
underneath and follows its API shape (`locator()`, `get_by_role()`,
`wait_for_selector()`, `route()`, `expect_response()`), so tests port
with mostly-mechanical substitution. There is also a dedicated
compatibility layer, `dolphin_desktop.playwright_compat` — see the
[Playwright compatibility reference](reference/playwright-compat.md).

## Pattern: Page Objects everywhere

The biggest lift in any migration is switching from "raw script" to
Page Objects. dolphin's scaffold shows the pattern:

```python
# objects/login_page.py
class LoginPage:
    def __init__(self, window):
        self._win = window
        self._user = window.edit(auto_id="user")
        self._pass = window.edit(auto_id="password")
        self._submit = window.button(name="Sign In")

    def sign_in(self, user: str, password: str) -> None:
        self._user.set_text(user)
        self._pass.set_text(password)
        self._submit.click()
```

Once the Page Objects are in place, the rest of a migration is usually
a mechanical find-and-replace of locator syntax. If you prefer keeping
identifiers out of code entirely, the YAML
[Object Repository](tutorials/object-repository.md) plays the same
role declaratively.

## Getting help

* [FAQ](faq.md) — pitfalls, gotchas, and common tricks
* [GitHub Discussions](https://github.com/DolphinSoftEU/DolphinDesktop/discussions)
* [Bug report template](https://github.com/DolphinSoftEU/DolphinDesktop/issues/new?template=bug_report.yml)

If you're stuck on a specific migration, open a Discussion with the
source snippet and we'll help write the dolphin equivalent.
