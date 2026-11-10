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

Lazarus LCL shares many VCL control APIs, but its accessibility tree is
not evidence for Delphi VCL behavior. These tests use UIA-visible captions,
indices and adjacency. They do not prove `TComponent.Name` lookup.
Neither a Delphi version nor an LCL component Name guarantees a matching
UIA AutomationId. See `../test_real_vcl.py` for the separate official
DelphiVCL runtime regression and `docs/guides/delphi.md` for prerequisites.

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
