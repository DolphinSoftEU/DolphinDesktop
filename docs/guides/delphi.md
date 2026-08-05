# Delphi / VCL (and Lazarus / LCL)

dolphin-desktop drives Delphi VCL and Lazarus LCL native Windows apps
through UIA + a Delphi-aware locator that maps the developer-assigned
``TComponent.Name`` to the UIA AutomationId. Standard VCL controls
(`TButton`, `TEdit`, `TMemo`, `TCheckBox`, `TComboBox`, `TListBox`,
`TPageControl`, `TStringGrid`, `TListView`, `TTreeView`) are covered
in 0.2.0. Third-party libraries (DevExpress, TMS, EhLib) will land in
a follow-up (v0.3+) via an injected Delphi RTTI agent DLL.

## Install

No extra install — the Delphi backend rides on the base UIA layer.

```powershell
pip install dolphin-desktop pytest
```

## Quick start

```python
from dolphin_desktop import Desktop

with Desktop().launch_delphi(r"C:\path\to\vcl_app.exe") as app:
    form = app.form(name="MainForm")   # TForm.Name from Object Inspector
    form.wait_ready(timeout=10)

    form.component(name="EdtName", cls="TEdit").set_text("Alice")
    form.component(name="EdtAge",  cls="TEdit").set_text("30")
    form.component(name="ChkActive", cls="TCheckBox").check()
    form.component(name="CmbCountry", cls="TComboBox").select("Poland")
    form.component(name="BtnSave", cls="TButton").click()

    assert form.component(name="LblStatus", cls="TLabel").text().startswith("Saved")
```

## API surface

### `DelphiApp`

| Method | Purpose |
|---|---|
| `form(name=None, title=None, title_re=None, timeout=15)` | Resolve a top-level `TForm`. Prefer `name=` (matches `TForm.Name` in the Object Inspector). |
| `forms()` | Enumerate every top-level `TForm` in the process. |
| `application` | The underlying `Application` for teardown (`app.application.detach()` to opt out of pytest's PID reaper). |
| `close()` / context manager | Kill the app. |

### `DelphiForm`

| Method | Purpose |
|---|---|
| `component(name, cls=None, timeout=10)` | Resolve a child component. `name` = `TComponent.Name`; `cls` is an optional Delphi class hint (`"TButton"`, `"TEdit"`). |
| `components(cls=None)` | Enumerate every direct-child component, optionally filtered by class. |
| `wait_ready(timeout=15)` | Block until the form window is visible. |
| `title()` | Return the form caption. |
| `pywinauto` | Escape hatch — underlying pywinauto window wrapper. |

### `DelphiComponent`

**State reads:** `text()`, `is_visible()`, `is_enabled()`,
`is_checked()`, `bounding_box()`, `lines()`, `items()`,
`item_count()`, `cell(row=, col=)`.

**Actions:** `click()`, `double_click()`, `right_click()`, `focus()`,
`set_text(text)`, `type_text(text, clear=True)`, `toggle()`,
`check()`, `uncheck()`, `select(item)` (index or string).

**Escape hatch:** `pywinauto` returns the underlying pywinauto
wrapper for advanced UIA calls dolphin does not expose.

## Locator resolution — how names map

On Delphi 10.4+ (RAD Studio) the VCL registers UIA property providers
so `TComponent.Name → UIA AutomationId`. dolphin's Delphi backend
tries in this order:

1. **`AutomationId == name`** — the preferred selector on Delphi 10.4+
   and modern Lazarus builds.
2. **`class_name == cls AND AutomationId == name`** — narrows when
   two controls share a name across sibling forms.
3. **`class_name == cls AND title == name`** — fallback for Lazarus
   (LCL) which does not currently publish AutomationId for standard
   controls; the caption is what UIA can see instead.
4. **`title == name`** — last resort by window text.

Passing `cls=` is optional but recommended — it makes lookups faster
and unambiguous.

## Recommended patterns

### Page objects for each form

Same shape as everywhere else in dolphin:

```python
class SignOnPage:
    def __init__(self, form):
        self._form = form
        self._user = form.component(name="EdtName", cls="TEdit")
        self._pass = form.component(name="EdtPassword", cls="TEdit")
        self._btn  = form.component(name="BtnSignIn", cls="TButton")

    def sign_in(self, user: str, password: str) -> None:
        self._user.set_text(user)
        self._pass.set_text(password)
        self._btn.click()
```

### Enumerating components at runtime

```python
buttons = form.components(cls="TButton")
for b in buttons:
    print(b.name, b.text())
```

Useful during a test-development session — combine with
`dolphin spy --delphi --pid <pid>` for a full tree dump.

## Delphi vs. Lazarus / LCL

Both compile to Windows-native HWND-based apps. dolphin's tests use
Lazarus because it's the free, open-source counterpart — the LCL API
is a 1:1 rewrite of VCL with the same class names (`TButton`, `TEdit`,
…), the same property model (`Text`, `Caption`, `Checked`), and the
same message pump.

The one systemic difference:

| | Delphi 10.4+ (VCL) | Lazarus 4.x (LCL) |
|---|---|---|
| `TComponent.Name` → UIA `AutomationId` | ✅ auto | ❌ falls back to caption match |
| Standard control class names | `TButton` etc. | `TButton` etc. (identical) |
| MSAA (older path) | ✅ | ✅ |

Dolphin's locator handles both — pass `cls=` and things work
regardless of the compiler.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ElementNotFoundError: Delphi component 'Button1' not found` | The developer renamed / didn't set `Name` in Object Inspector | `dolphin spy --delphi --pid <pid>` to enumerate real names |
| Component found but `.click()` does nothing | UIA Invoke pattern not implemented for this control | Falls through to mouse `click_input()` automatically; if RDP is active use `.pywinauto.click_input()` |
| `TStringGrid` cells return empty | Lazarus LCL does not surface grid cells via UIA GridPattern | Use `dolphin spy --delphi` to check; fall back to `ImageLocator` or `.pywinauto` escape |
| DevExpress / TMS control invisible | Third-party libs draw custom canvases | Requires the RTTI agent DLL (v0.3+). For 0.2.0 use `ImageLocator` fallback for those regions |
| `.set_text()` on TEdit does not persist | pywinauto's `set_edit_text` fails on some Lazarus builds | Falls back to click + Ctrl+A + type sequence; if that also fails, use `.pywinauto.type_keys("^a{DELETE}text", with_spaces=True)` |
| `.forms()` returns duplicates | Modal dialog inheritance from main form | Filter by `class_name` on the pywinauto wrapper first |
| App detects "test framework" and refuses to launch | Some banking apps ship anti-automation checks | Requires per-app carve-out — outside dolphin's scope |

## What's next

* **v0.3+**: Delphi RTTI injected agent DLL for full third-party
  coverage (DevExpress, TMS, EhLib). Same architecture as the Qt
  agent we already ship.
* **v0.3+**: Delphi IDE plugin for recorder / spy handshake (record
  actions directly from the RAD Studio Object Inspector).

## See also

* [PowerBuilder](powerbuilder.md) — sibling stack, similar architecture.
* `tests/delphi/sample_lcl/` — working Lazarus source
  the pytest suite exercises against.
