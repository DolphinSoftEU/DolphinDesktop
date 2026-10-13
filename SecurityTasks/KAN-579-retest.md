# KAN-579 — implementation and local verification

Date: 2026-10-13. Jira was read with the description and comment field;
the issue had zero comments. Changes are local and have not been published.

## Implemented

- Production accepts only a matching final version tag. Dispatching from
  `master`, preview, or an arbitrary branch cannot publish to PyPI.
- Release checks source ancestry, active review/check rulesets, independent
  approval of the reviewed PR head and exact merge result, restricted tag
  creation and immutable update/deletion rules, and independent environment
  approval without administrator bypass or broad ref policies.
- The latest push CI run on the approved branch must match the release SHA
  and succeed. Every required Windows, package and security job must succeed
  in its latest attempt; PR/fork runs and skipped jobs are rejected.
- CI builds wheel/sdist only after required jobs, checks metadata and installs
  the wheel in a fresh Windows venv for import/CLI smoke. It records both file
  hashes and the SHA/run/attempt identity. Release reuses this artifact without
  rebuilding and verifies its immutable ID, API archive digest and manifest.
- Environment approval comes after preparation. Immediately before upload,
  publishing verifies the anchored manifest hash and both distribution hashes.
  Replacing both a wheel and its manifest cannot satisfy the anchored hash.

## Validation performed

- Full focused run of `test_ci_security.py` and `test_release_provenance.py`:
  **106 passed**. Subsequently added tag-creation and anchored-manifest tests;
  final targeted run (`-k 'tag or verified_ci_artifact or publish_cannot'`):
  **10 passed, 52 deselected**. The current focused suite contains 109 tests;
  the three additions passed in that final run.
- Repository-wide Ruff lint and format checks: passed.
- `mypy src/`: passed for 43 files.
- `git diff --check`: passed.
- Live read-only calls through the new validators rejected `master` for missing
  mandatory review, `testpypi-preview` for missing protection, the release tag
  policy for absent tag rulesets, and both environments for administrator bypass.

## External acceptance criteria still open

Authenticated GitHub inspection confirmed `admin=false`, `maintain=false`,
`push=true`. `master` has only an active deletion rule, with required checks off;
`testpypi-preview` is unprotected. No active tag ruleset exists. `pypi` and
`testpypi` have the sole reviewer `DolphinSoftEU`, self-review prevention disabled,
administrator bypass enabled, and no deployment ref restrictions.

An authorized attempt to apply independent approval/ref restrictions and disable
administrator bypass on `pypi` returned **HTTP 403: Must have admin rights to
Repository**. No settings were changed by that request. Continuing the remaining
administration requests with this credential cannot complete the configuration.

An administrator must apply the exact rulesets/environment configuration in
[RELEASING.md](../RELEASING.md), verify owner/repository/workflow/environment
bindings in the PyPI and TestPyPI Trusted Publisher accounts, and record evidence.
No index administrator session is available to verify those bindings here.

A hosted rejection run and a successful protected-source preparation run remain
required. Local tests and live rejection checks do not establish a successful
hosted deployment. Under the inspected settings the new release gate blocks
publication. KAN-579 is therefore not claimed fully closed.
