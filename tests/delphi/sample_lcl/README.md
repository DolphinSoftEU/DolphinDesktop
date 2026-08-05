# sample_lcl — Lazarus/LCL demo app for dolphin's Delphi backend tests

A Lazarus (LCL) project that mirrors what a real Delphi VCL sign-up
form looks like: `TEdit` for name/age, `TCheckBox`, `TRadioButton`
pair, `TComboBox`, `TListBox`, `TPageControl`, `TStringGrid`,
`TButton` triggers, `TLabel` status line. Every component has an
Object-Inspector `Name` matching what the pytest suite in
`tests/delphi/` expects.

## Compile

Install Lazarus 4.x (bundled with Free Pascal, ~450 MB) from
<https://www.lazarus-ide.org/>, then from this directory:

```powershell
lazbuild sample_lcl.lpi
```

Produces `sample_lcl.exe` in the same folder. That's the binary the
pytest fixture launches.

## Why LCL, not real Delphi

Lazarus's LCL is Free Pascal's open-source implementation of the same
VCL API. Class names (`TButton`, `TEdit`, …), the property model
(`Text`, `Caption`, `Checked`), and the runtime message pump match
real Delphi 1:1 for the standard controls dolphin cares about — the
same `TComponent.Name → AutomationId` mapping applies to both.

The one systemic difference: Delphi 10.4+ ships an
`UIA property provider` that publishes AutomationId; Lazarus 4.x does
not (yet). Dolphin's Delphi backend accounts for that — it tries
AutomationId first, then falls back to `class_name + caption` which
Lazarus does publish.

## What the sample proves

* `TButton` invocation (BtnSave, BtnClear, BtnAppendLog)
* `TEdit` set_text / get_text (EdtName, EdtAge)
* `TMemo` line collection (MemoLog)
* `TCheckBox` toggle (ChkActive)
* `TRadioButton` pair select (RadStandard / RadPremium)
* `TComboBox` select-by-index / by-text (CmbCountry)
* `TListBox` pick (LstHobbies)
* `TPageControl` tab navigation (TabDetails / TabAdvanced)
* `TStringGrid` cell read (Grid)
* `TLabel` status assertion (LblStatus)

The full round-trip suite lives at
`tests/delphi/test_sample_lcl.py`.
