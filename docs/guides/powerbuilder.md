# PowerBuilder automation

PowerBuilder is Appeon's (formerly Sybase's) rapid-application-
development stack, still widely deployed in banks, insurance, and
government back-office. dolphin-desktop's story depends on which
runtime the target app uses:

| PowerBuilder generation | Runtime | dolphin support | Extra install |
|---|---|---|---|
| **PowerBuilder 2019 / 2022 / 2025** (Appeon) | Appeon runtime | ✅ UIA for window chrome + standard controls (verified live on PB 2025); DataWindow interior may be opaque — see below | none (base install); `[vision]` for DataWindow OCR |
| **PowerBuilder Classic** (up to 12.6, pre-Appeon) | Proprietary framework | ⚠️ Chrome-only (menus, top-level buttons); DataWindow grids opaque | image-based fallback (`[vision]` extra) |

## Modern PowerBuilder (2019+ / Appeon)

Since taking over PowerBuilder, Appeon has kept the runtime current on
modern Windows, and the standard controls expose UIA accessibility
metadata: buttons, text fields, checkboxes, combo boxes and window
chrome resolve through the same locators as any native Windows app
(verified live on a PowerBuilder 2025 runtime).

```python
from dolphin_desktop import Desktop

with Desktop().launch_powerbuilder(r"C:\path\to\pb_app.exe") as app:
    win = app.window(title_re=".*Customer Master.*")
    win.wait_until_ready(timeout=10)
    win.edit(name="EdtCustomerNo").set_text("10500")
    win.button(name="BtnSearch").click()
```

`Desktop.launch_powerbuilder()` is a **semantic wrapper** over
`Desktop.launch()` — same UIA backend, same locator surface. The
wrapper exists so:

* Tests self-document intent (`launch_powerbuilder(...)` reads better
  than `launch(...)`).
* Future PB-specific launch prep (PBNI hook injection, PowerScript
  recorder handshake) has a stable API to hang off.

### Common Appeon PB control classes

| PowerBuilder control | UIA class name (typical) | Locator hint |
|---|---|---|
| `SingleLineEdit` | `Edit` | `.edit(name="EdtName")` |
| `CommandButton` | `Button` | `.button(name="BtnSave")` |
| `CheckBox` | `CheckBox` | `.check_box(name="ChkActive")` |
| `RadioButton` | `RadioButton` | `.radio_button(name="RadPremium")` |
| `DropDownListBox` | `ComboBox` | `.combo_box(name="CmbCountry")` |
| `ListBox` | `List` | `.list_box(name="LstItems")` |
| `Tab` | `Tab` | `.tab(name="TabDetails")` |
| **DataWindow** | `Table` (with grid rows) | `.locator(control_type="Table")` |

DataWindow exposure varies by deployment: some Appeon-runtime apps
expose grid rows via the standard UIA GridPattern (like a WPF
`DataGrid`), but this is **not guaranteed** — the live-verified
PowerBuilder 2025 ModernUI demo renders its DataWindows as opaque
`pbdw` panes with no children beyond the scrollbar. Check your target
with `dolphin spy`; when the interior is opaque, use the OCR /
ImageLocator recipe from the fallback section below — it works the
same on both generations.

## Native window classes and the DataWindow fallback

Both generations share PB's native window class hierarchy, which
surfaces only the outer chrome to Windows automation. Classes observed
on a live PB 2025 runtime (older versions use `PBWindow<version>` /
`PBDW<version>` naming):

* Frame / sheet windows: `FNWND3xx` — visible, titled, activatable
* Userobjects / panes: `FNUDO3xx`
* DataWindows: `pbdw` — the grid interior is drawn on an internal
  buffer and is invisible to UIA; only the scrollbar has elements
* RibbonBar: `FNRIB3xx` — tab panes carry names, inner buttons opaque
* Buttons / edits placed on windows: standard Win32 classes, fully
  reachable (`.button(name=...)`, `.edit(...)`)

**dolphin's practical answer when the DataWindow is opaque:**

* Top-level chrome (window title, menu bar, standalone buttons) via
  UIA works.
* Interior of DataWindows requires **image-based fallback** —
  install the `vision` extra and use `ImageLocator`:

  ```python
  pip install "dolphin-desktop[vision]"
  ```

  ```python
  from dolphin_desktop import Desktop, ImageLocator

  win = Desktop().launch_powerbuilder(r"C:\path\to\legacy_pb.exe").window(...)
  # Screen-relative image match for DataWindow cell content
  save_btn = ImageLocator("templates/pb/save_button.png", threshold=0.9)
  save_btn.click()
  ```

  `ImageLocator` is the actionable wrapper — `click()` / `double_click()`
  act on the current match (raising if the template is not on screen),
  and `wait_for(timeout=...)` polls until it appears. `Screen.find_image()`
  is the one-shot alternative and returns bare `(x, y)` coordinates; to
  type after an image click, use `Keyboard`.

  For **text** the DataWindow renders (cell values, dropdown rows,
  filter combos), OCR closes the gap without any template files —
  requires a [Tesseract](https://github.com/tesseract-ocr/tesseract)
  install alongside the `vision` extra:

  ```python
  from dolphin_desktop import Keyboard, Mouse, Screen

  bb = win.locator(class_name="pbdw", found_index=1).bounding_box()
  region = (bb["left"], bb["top"], bb["right"], bb["bottom"])

  pt = Screen.find_text("Alberta", region=region)   # OCR-locate the cell
  Mouse.double_click(*pt)                           # select its content
  Keyboard.type("Alaska")                           # type into the cell
  Keyboard.press("{TAB}")                           # commit
  assert "Alaska" in Screen.text(region=region)     # OCR read-back
  ```

  `Screen.find_text` automatically retries the capture upscaled (2×, 3×)
  when nothing matches at native resolution, so the small fonts DataWindow
  grids use resolve reliably. Note the `found_index=` — a window usually
  hosts several `pbdw` controls, and a bare `class_name="pbdw"` locator
  raises `AmbiguousMatchError`.

* Full field-level automation would require **PBNI** (PowerBuilder
  Native Interface) or a PowerScript instrumentation script inside
  the PB IDE. Both need an Appeon licence and are outside the
  0.2.0 scope. Tracked as follow-up (v0.3+).

## Which version am I targeting?

Check the exe's manifest:

```powershell
[System.Diagnostics.FileVersionInfo]::GetVersionInfo("C:\path\to\pb_app.exe") | Format-List *
```

* ProductName containing "PowerBuilder 2019" / "2022" / "2025" →
  **Appeon runtime** — use `Desktop.launch_powerbuilder()` and standard
  UIA locators.
* ProductName containing "PowerBuilder 12" / "11" / "10" →
  **classic** — chrome via UIA, DataWindows via `ImageLocator`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `.window(title=...)` times out | Splash / login window steals focus | Chain `title_re=".*Login.*"` first, sign in, then `title_re=".*Main.*"` |
| DataWindow cells invisible to `.locator(control_type="Table")` | Classic PB runtime | Use `ImageLocator` — this is a runtime limitation, not a dolphin bug |
| Menu items unreachable | PB uses its own drawing for popup menus | Use `Keyboard.type("%F", escape=False)` to open the File menu via mnemonic, then navigate with arrows / `{ENTER}` |
| Custom controls (Foundation Class, DevExpress-for-PB) not detected | Third-party libs paint their own UI | Fall back to `ImageLocator`; consider `dolphin spy` to check what UIA does see |
| Buttons don't respond to `.click()` | PB Classic button uses BM_CLICK but not the UIA Invoke pattern | `.click()` already sends a real mouse click; if the control ignores it, fall back to `ImageLocator` and click the button by its pixels |

## Scope decision

* **In 0.2.0:** semantic wrapper `Desktop.launch_powerbuilder()`,
  documentation of standard-controls path for Appeon PB, image-based
  fallback path for classic PB. **No proprietary framework
  introspection.**
* **Deferred to v0.3+:** PBNI-based DataWindow row/column extraction
  (requires Appeon licence + trial account for verification).
