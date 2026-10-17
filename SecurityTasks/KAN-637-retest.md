# KAN-637 — bounding-box consumer contract

The 2026-10-05 Jira comment was reproduced against the current source: the
DESKTOP-054 position/attribute scenario failed because it required the removed
`left`, `top`, `right`, `bottom` keys. The public contract already established in
the library, reference pages and breaking-change entry is exactly
`{x, y, width, height}` in absolute screen coordinates.

Consumer changes are in the adjacent `dolphin-desktop.ta` checkout and must be
delivered together with this repository's documentation/regression changes:

* DESKTOP-054 checks the exact four keys, integer coordinates, positive size,
  containment in the window and an unchanged interaction counter.
* DESKTOP-021 and `test_support/real_window.py` compare the four-key result with
  native Win32 geometry, including physical-to-logical coordinate conversion.
* DESKTOP-056 derives right/bottom edges for clipping and screenshot assertions.
* DESKTOP-057 compares `x`/`y` after movement; DESKTOP-058 derives mouse centers
  from `x`/`y` and dimensions.

Source regressions cover hidden/disabled elements, negative screen coordinates,
zero-size rectangles, and real UIA window/element geometry without interaction.
No runtime API change is needed.

## Verification

Run from `DolphinDesktop` with its development environment:

```powershell
.venv/Scripts/python.exe -m pytest tests/framework/test_locator.py tests/framework/test_window.py tests/framework/test_delphi.py tests/framework/test_java.py tests/framework/test_oracle_forms.py -q
.venv/Scripts/python.exe -m pytest tests/component/test_locator_windows.py -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy src/
```

All 338 selected wrapper unit tests and six real UIA component tests passed.
Ruff lint/format and mypy passed.
From `dolphin-desktop.ta`, using the source checkout's development Python and
`-p no:dolphin-desktop`, the complete DESKTOP-021/054/056/057/058 plus DESKTOP-172
selection finished with **14 passed, 1 skipped**. An earlier screenshot run
failed its pixel-color assertion; the isolated rerun and final combined run
passed, so that initial desktop-capture failure was transient.

DESKTOP-172 remains **unverified across DPI levels**. Its approved-runtime
preflight skips because `DOLPHIN_DESKTOP_WHEEL` and `DOLPHIN_DESKTOP_VERSION`
are absent. `DOLPHIN_DESKTOP_172_DPI_CONFIG` is also absent. This test currently
requires an approved `0.2.0.dev6` wheel; it must be run in its designated UAT
environment with a scaling controller and fixture for 100%, 125% and 150%.
The real UIA component run at the current desktop DPI does not replace that UAT.
