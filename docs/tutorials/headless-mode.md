# Headless Mode

Dolphin can run a command on a hidden Windows desktop with `dolphin-run`.

```bash
dolphin-run pytest tests/ -v
```

The wrapper creates a desktop named `DolphinHidden`, sets `DOLPHIN_HEADLESS=1` for the child process, starts the command there, waits for it to finish, and closes its desktop handle.

## When To Use It

Use `dolphin-run` for test subsets that have been checked on a hidden desktop, especially smoke tests that mostly launch, inspect, read state, and collect artifacts.

For many end-to-end suites, the safer default is a normal interactive Windows runner:

```bash
pytest tests/ -v
```

GitHub Actions `windows-latest` and many self-hosted Windows agents already provide an interactive session. Start there before adding hidden-desktop execution.

## Activation Paths

Run the whole test command on the hidden desktop:

```bash
dolphin-run pytest tests/ -v --dolphin-backend=uia
```

Force a `Desktop` instance to use hidden mode:

```python
from dolphin_desktop import Desktop

desktop = Desktop(hidden=True)
```

Use visible mode even when `DOLPHIN_HEADLESS` is set:

```python
desktop = Desktop(hidden=False)
```

## Important Limitations

Hidden desktops are a Windows isolation primitive. They are not the same as a browser's headless mode.

Known limitations:

- MSIX, UWP, Microsoft Store apps, and some system apps may launch through broker processes on the default desktop instead of `DolphinHidden`.
- Mouse-based actions can fail when Windows refuses to move the physical cursor on a non-input desktop.
- UAC prompts appear on the secure desktop and cannot be automated by Dolphin.
- Clipboard behavior can be surprising because the window station and desktop focus both matter.

Targeted keyboard shortcuts are supported by ``Locator.press_key(...)``. On a
hidden desktop Dolphin briefly makes ``DolphinHidden`` the input desktop,
sends the sequence through the normal Windows keyboard path, and restores the
previous desktop. If the desktop cannot be switched, Dolphin falls back to
window-targeted messages for controls that support them. Global
``Keyboard.press(...)`` still requires an interactive input desktop because it
has no target window.

If a test uses many physical mouse actions, run it on a visible interactive desktop unless you have verified that the target app and actions work under `dolphin-run`.

## Headless-Safe API — `invoke()` and Friends

Under the hood, `Locator.click()`, `.hover()`, `.drag_to()`, and other
"input-simulation" actions call pywinauto's `click_input()`, which
uses `win32api.SetCursorPos` to move the physical cursor. That
requires an **input desktop** — a desktop currently attached to the
hardware mouse/keyboard — which `DolphinHidden` is not. Under
`dolphin-run` those calls explode with `pywintypes.error: (2,
'SetCursorPos', ...)`.

Dolphin exposes a parallel family of **programmatic** actions that
dispatch the same UI event **without ever moving the mouse**. They
route through Windows UI Automation patterns (or the equivalent Java
Access Bridge actions on Swing) — the same API a screen reader uses.

| Physical (needs input desktop) | Programmatic (headless-safe)      | UIA pattern                        |
|-------------------------------|-----------------------------------|------------------------------------|
| `.click()` on a button        | `.invoke()`                       | `InvokePattern`                    |
| `.click()` on a checkbox      | `.toggle()`                       | `TogglePattern`                    |
| `.click()` on a radio button  | `.select()`                       | `SelectionItemPattern`             |
| `.click()` on a list item     | `.select()`                       | `SelectionItemPattern`             |
| `.click()` on a tab           | `.select()`                       | `SelectionItemPattern`             |
| `.click()` on a tree expander | `.expand()` / `.collapse()`       | `ExpandCollapsePattern`            |
| `.click()` on a combo arrow   | `.expand()` / `.collapse()`       | `ExpandCollapsePattern`            |
| `.type_text("hello")`         | `.set_value("hello")`             | `ValuePattern`                     |

Same method names on `JABLocator` (Java Swing) — they route through
JAB's `doAccessibleActions("click" / "toggle" / "expand" / …)` and
`setTextContents`.

### When to pick which

**`click()` / `type_text()` — input simulation**

- Fires real hover/mouseover handlers, keyboard-down handlers, focus-lost
  events. What a human would trigger.
- Better for **E2E** — catches bugs where hover state matters, real-
  time input validation runs on every keystroke, focus behaviour is
  under test.
- Requires a **real desktop** (dev box, headed CI runner, RDP session
  that stays connected). Fails under `dolphin-run`.

**`invoke()` / `toggle()` / `set_value()` — programmatic**

- Fires only the "action" event — the button's `OnClick`, the
  checkbox's state-change, the text field's `Text` property. No hover,
  no keystrokes, no focus dance.
- Better for **smoke tests + state assertions** — runs anywhere,
  including `dolphin-run` and CI hosts without a display.
- Fails with `UnsupportedPatternError` if the element does not
  implement the required UIA pattern (no silent fallback to
  mouse — the difference between "programmatic worked" and "we
  moused instead" MUST stay visible in the test).

### Choosing at the test level

Concrete guidance for the common cases:

```python
# Smoke test — assertion-only, needs to run under dolphin-run
def test_sign_in_updates_status(desktop):
    app = desktop.launch("myapp.exe")
    win = app.window(title="Login")

    win.edit(auto_id="txtUser").set_value("alice")           # no keystrokes
    win.edit(auto_id="txtPassword").set_value("secret")
    win.button(name="Sign In").invoke()                       # no mouse

    # Auto-waits — no sleep(), no assumption about handler timing.
    win.get_by_automation_id("status").wait_for_text("Signed in as alice")


# Full E2E — hover effects and keystroke handlers under test
def test_sign_in_full_ux(desktop):
    app = desktop.launch("myapp.exe")
    win = app.window(title="Login")

    user = win.edit(auto_id="txtUser")
    user.click()                                              # focus via mouse
    user.type_text("alice")                                   # real keystrokes
    win.edit(auto_id="txtPassword").type_text("secret")
    win.button(name="Sign In").click()                        # real mouse

    win.get_by_automation_id("status").wait_for_text("Signed in as alice")
```

Two suites for one workflow is fine — the E2E flavour catches "our
button's hover effect broke", the smoke flavour catches "the login
API stopped working" and runs on every commit under `dolphin-run`.

### Auto-waiting counterparts — no `sleep()`

Programmatic actions still race the app's event queue: `.invoke()`
returns as soon as the UIA dispatch is queued, not when the action
handler has finished. The naive fix is `sleep(N)`; dolphin's
supported fix is the `wait_for_*` family — polls the same state a
manual assertion would, returns the moment it matches, times out
with the current value embedded in the error:

| Assertion pattern                            | Replace with                                                       |
| -------------------------------------------- | ------------------------------------------------------------------ |
| `sleep(0.5); assert "X" in loc.text()`       | `loc.wait_for_text("X")` (substring, default)                     |
| `sleep(0.5); assert loc.text() == "X"`       | `loc.wait_for_text("X", contains=False)` (exact)                  |
| `sleep(0.5); assert re.match(pat, loc.text())` | `loc.wait_for_text(text_re=pat)`                                |
| `sleep(0.5); assert loc.is_checked()`        | `loc.wait_for_checked()`                                          |
| `sleep(0.5); assert not loc.is_checked()`    | `loc.wait_for_checked(checked=False)`                             |

Same methods on `JABLocator` — plus an extra `source="description"`
kwarg for the Swing-specific case where a `JLabel` publishes its
dynamic value through `AccessibleContext.setAccessibleDescription`
(as the bundled Swing demo does):

```python
status = win.get_by_role("label", name="Status")
win.get_by_role("push button", name="Save").invoke()
status.wait_for_text("Saved", source="description", timeout=5)
```

`sleep()` is not banned outright — it stays available for real-time
delays (rate-limited APIs, animations that must run for a fixed
duration). But if the test is waiting for a UI state change, the
`wait_for_*` primitive is the right tool.

### Error surface — `UnsupportedPatternError`

Every programmatic method raises
`dolphin_desktop.UnsupportedPatternError` when the target
does not implement the required pattern. The error message names
the pattern, the alternative primitive to try, and the physical
fallback — no need to look anything up:

```
UnsupportedPatternError: invoke() called on an element that does not
expose InvokePattern (wrapper 'UIAWrapper' has no 'invoke' method)
  hint: for checkboxes use .toggle(); for radio buttons / list items
  use .select(); for tree nodes use .expand() / .collapse(); for headed
  E2E fallback use .click()
```

Same shape on `JABLocator` when the JVM does not expose the required
`AccessibleAction`.

## pytest Flag

The pytest plugin also exposes `--dolphin-headless`:

```bash
pytest tests/ -v --dolphin-headless
```

This creates `Desktop(hidden=True)` through the fixture. For full UIA visibility of hidden-desktop windows, prefer running the entire command through `dolphin-run`.

## Skipping Tests In Headless Mode

```python
import os
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("DOLPHIN_HEADLESS") == "1",
    reason="This test requires the visible desktop",
)
```

## Artifacts

Artifacts still use the normal pytest options:

```bash
dolphin-run pytest tests/ -v --dolphin-trace=always --dolphin-video=keepfailedonly
```

Traces go to `dolphin-traces/`, videos to `dolphin-videos/`, and screenshots to `dolphin-screenshots/` when enabled.

Next: [CI Setup](../ci/index.md).
