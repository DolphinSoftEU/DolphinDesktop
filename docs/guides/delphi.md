# Delphi / VCL (and Lazarus / LCL)

Dolphin drives controls exposed by the application's Windows UIA/MSAA tree.
A Delphi compiler version does **not** guarantee that `TForm.Name` or
`TComponent.Name` appears as UIA `AutomationId`. The earlier claim that
VCL 10.4+ automatically provides this mapping was incorrect (KAN-761).

## Install and prerequisites

```powershell
pip install dolphin-desktop pytest
```

* Windows and a desktop session appropriate to the operations being tested.
* The target must expose each required control and action through UIA.
  Inspect the actual target with `dolphin spy --delphi --pid <pid>`.
* **Lookup by Delphi Name requires an application-supplied UIA provider**
  returning the exact `TForm.Name` / `TComponent.Name` as `AutomationId` on
  each required element. Setting Name in Object Inspector alone is insufficient.
  Verify both the form and its children; a provider on one does not cover all.
* Dolphin supplies no VCL Name provider, injected RTTI agent, configuration
  switch or turnkey provider installation. Stock VCL Name lookup is outside
  the current support contract. Do not assume that upgrading Delphi enables it.
  Applications requiring it need a separately implemented and validated provider.
* Stock Delphi 12 VCL exposed by official `delphivcl==1.0.7` has an empty form
  AutomationId and numeric control IDs instead of component names. Its
  non-windowed `TLabel` may be absent from UIA entirely. A missing element cannot
  be recovered by `name=`, `near_label=` or switching to generic `win32`.

[Official DelphiVCL bindings](https://github.com/Embarcadero/DelphiVCL4Python)
provide a real VCL runtime; a Lazarus sample is separate evidence and cannot
establish Delphi VCL accessibility support.

## Quick start for UIA-visible controls

Choose selectors from the inspected application. For example, a known form
with one Edit and a Save button can use:

```python
from dolphin_desktop import Desktop

with Desktop().launch_delphi(r"C:\path\to\vcl_app.exe") as app:
    form = app.form(title="Customer details")
    form.wait_ready(timeout=10)
    form.component(cls="TEdit", index=0).set_text("Alice")
    form.component(cls="TButton", title="Save").click()
```

Indices depend on the target's tree order and captions on its language/state.
This is a UIA workflow, **not** proof of lookup by Delphi Name. Only when an
application's verified provider publishes `MainForm`, `EdtName`, and `BtnSave`
as AutomationIds can the corresponding `name=` selectors resolve those IDs.

## API surface

### `DelphiApp`

| Method | Purpose |
|---|---|
| `form(name=None, title=None, title_re=None, timeout=15)` | Resolve a top-level `TForm`. `name=` matches a provider-published UIA AutomationId; use `title=` for captions. |
| `forms()` | Enumerate every top-level `TForm` in the process. |
| `application` | The underlying `Application` for teardown (`app.application.detach()` to opt out of pytest's PID reaper). |
| `close()` / context manager | Kill the app. |

### `DelphiForm`

| Method | Purpose |
|---|---|
| `component(name, cls=None, timeout=10)` | Resolve a child component. `name` = provider-published UIA ID (legacy caption fallback); `cls` is an optional Delphi class hint (`"TButton"`, `"TEdit"`). |
| `components(cls=None)` | Enumerate UIA-visible descendant components, optionally filtered by class. |
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

## Selector resolution

`app.form(name=...)` queries UIA `auto_id` only. It does not read `TForm.Name`.
`title=` and `title_re=` explicitly select window captions.

`form.component(name=...)` first queries UIA `auto_id` with a mapped UIA
control type (if known), then without that type. For backward compatibility it
also tries the same string as a caption, with type/class variants. A successful
caption fallback does not prove component Name exposure. Prefer `title=` for
intentional caption matching.

Explicit `near_label=`, `index=`, and `title_re=` strategies run before those
criteria variants. `near_label=` requires an actual UIA-visible label, not a
painted TLabel that is missing from the tree. `components()` enumerates the
exposed UIA descendants; its `.name` is the published AutomationId or caption,
not a read of Delphi RTTI.

## Reproducible real VCL reference

From the repository root, with the project development environment installed:

```powershell
uv pip install --python .venv/Scripts/python.exe delphivcl==1.0.7
.venv/Scripts/python.exe -m pytest tests/delphi/test_real_vcl.py -q -o junit_family=xunit1 --junitxml=artifacts/vcl-reference.xml
```

The fixture `tests/delphi/real_vcl_app.py` uses the official runtime and assigns
real component Names. It adds no UIA provider and injects no AutomationIds.
The harness launches the Python host and attaches to the actual form-owning
interpreter PID, since Windows venv launchers can create a child interpreter.
The regression checks the actual tree and diagnostic errors for missing form,
Edit and Button names. A separate reference workflow edits a real VCL Edit,
invokes Save and verifies the resulting text using the supported UIA selectors.
This separate workflow does **not** replace or pass DESKTOP-161's Name contract.
DESKTOP-161 remains unsupported on this stock runtime without a real provider.

The test skips when the optional runtime is unavailable; a skip is incomplete
real-VCL verification. Run on Windows with `delphivcl==1.0.7`. The LCL suite
`tests/delphi/test_sample_lcl.py` independently validates Lazarus behavior.

## Troubleshooting

| Symptom | Check / action |
|---|---|
| `form(name="MainForm")` or `component(name="EdtName")` fails | Inspect UIA AutomationIds, not just Object Inspector. Empty/numeric IDs do not expose Delphi names. A provider is required for that contract. |
| Component found but action fails | Inspect supported UIA patterns, visibility and enabled state. Mouse fallback needs an interactive desktop. |
| `TLabel` absent or `near_label=` fails | Non-windowed controls can be missing from UIA. The application needs accessibility support for those controls. |
| Grid cells are unavailable | UIA exposure of the grid does not guarantee GridPattern or cell access. Verify the target's patterns. |
| Third-party / owner-drawn control is invisible | Support depends on its provider. Dolphin currently supplies no Delphi RTTI agent. |

## See also

* [Support matrix](../support-matrix.md)
* [PowerBuilder](powerbuilder.md)
* `tests/delphi/sample_lcl/` — independent Lazarus sample
