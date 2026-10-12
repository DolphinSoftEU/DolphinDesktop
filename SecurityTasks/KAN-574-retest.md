# KAN-574 — Office security retest

Run date: **2026-10-11**, Europe/Warsaw. Tests ran against the local source tree
with the review fixes applied on top of commit
`8c45b8e6e304778b3b10015c08cd338584a09829` (`Fix KAN-574`).

Environment:

- Windows 11, build 26200 (`Windows-11-10.0.26200-SP0`).
- Python 3.12.13, pywin32 311.
- Installed Excel: version 16.0, build 20430.0.
- Installed Word: version 16.0, build 16.0.20430.
- Word `Options.UpdateLinksAtOpen` was `True`; it was still `True` after the run.
- VBA project access was blocked in both applications. No Trust Center,
  organization policy or registry settings were weakened to create fixtures.

Commands from the repository root:

```powershell
.venv\Scripts\python.exe -m pytest tests/framework/test_office_com.py tests/framework/test_office.py -q
.venv\Scripts\python.exe -m pytest tests/office -q -rs --junitxml artifacts/KAN-574-office-retest.xml
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\mypy.exe src/
```

Results:

| Suite / scenario | Passed | Failed / errors | Skipped |
| --- | ---: | ---: | ---: |
| COM stand-ins and wrapper unit tests | 75 | 0 | 0 |
| Real Office suite | 2 | 0 | 2 |
| Excel cached external-link value | 1 | 0 | 0 |
| Word cached OLE LINK field value | 1 | 0 | 0 |
| Excel macro: positive control followed by default block | 0 | 0 | 1 |
| Word macro: positive control followed by default block | 0 | 0 | 1 |

The Office suite completed in 11.90 seconds. Ruff lint, formatting and mypy
passed (43 source files checked by mypy).

The real Excel test saved a cached link to a local workbook, changed the source
while the target was closed and verified that `ExcelApp.open()` retained the
cached value. The real Word test did the same with an automatically updated OLE
`LINK Word.Document.12` field to a local `.docx`. `WordApp.open()` retained its
cached text; explicitly updating the field afterwards produced the new source
text, proving that the link was reachable and not merely broken. All test
documents lived in pytest temporary directories, and private COM instances
were closed without saving the opened test documents.

Both macro cases were skipped during fixture creation because Office reported
`Programmatic access to Visual Basic Project is not trusted`. Each combined test
requires a harmless macro to run on an opt-in control copy **before** testing
default blocking on a separate fresh copy. A policy block skips the entire
case, so a macro blocked solely by Office policy cannot produce a passing
default-block test.

**Evidence limits:** this run verifies real Excel and Word link behavior and the
COM wrapper security contract. It does **not** verify real VBA macro blocking or
opt-in execution; those cases still need a run with reviewed macro fixtures and
a passing positive control. XLM behavior is simulated in unit tests and described
in the guide, not verified against a real XLM document.

**CI scope:** `.github/workflows/ci.yml` runs the mandatory `pytest -m unit` job.
The Office suite is classified as `system` / `integration`, requires locally
installed Office and is not part of that gate. A green CI result is not evidence
of real Office macro execution/blocking. This record supplies the separate
Windows/Office run and preserves the distinction between passed and skipped.
