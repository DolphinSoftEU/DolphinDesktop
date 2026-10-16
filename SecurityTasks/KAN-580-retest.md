# KAN-580 — local retest (2026-10-16)

Read the Jira description and comment 11899 (2026-10-04). The comment identified
the missing smoke before publication. The current KAN-579 implementation already
smoked the CI wheel; this change also makes a successful local-wheel smoke an
explicit dependency of both Release publishing jobs.

## Changes

- Release downloads the immutable CI artifact, verifies its anchored manifest
  and distribution hashes, then smokes that exact wheel on Windows before either
  publishing job can start. Distributions are not rebuilt in Release.
- All CI/local/TestPyPI wheel smoke tests install runtime dependencies exported
  from `uv.lock`, with hashes enforced, from PyPI alone. They then install the
  local wheel using `--no-deps --no-index`, check dependency consistency, verify
  both installed/imported versions, and run both CLI commands outside the checkout.
  Inherited pip configuration and extra-index settings are disabled.
- TestPyPI download remains a separate `--no-deps` invocation using only TestPyPI,
  now restricted to wheels. Before any pip call, including the diagnostic index
  query, the PowerShell step clears inherited `PIP_*` settings and disables pip
  configuration files with `PIP_CONFIG_FILE` pointing to the null device.
  Its SHA256 comparison still precedes installation.
- Development preview versions encode the originating CI run/attempt in both
  wheel metadata and `__version__`. Release derives the same version from the
  selected CI attempt. Tagged final/RC versions remain explicit. Existing-version
  upload conflicts remain fatal; there is no `skip-existing` override.

## Verification

- `uv run --locked --group dev pytest tests/framework/test_ci_security.py
  tests/framework/test_release_provenance.py tests/framework/test_release_preview.py
  -q`: **176 passed**, including smoke-gate bypasses, immediate failure propagation,
  index isolation, hashed dependency commands, preview run/retry uniqueness and
  exact CI identity propagation.
- `ruff check .`, `ruff format --check .`, and `mypy src/`: passed.
- Built an isolated copy of the real source as wheel and sdist after assigning
  CI run `12345678901`, attempt `2`: version `0.2.0.dev12345678901000002`.
  Both passed `twine check`. The checked-out source version and lock were not changed.
- Ran the real smoke helper on that wheel on Windows with Python 3.12.10 and
  3.13.13: hashed PyPI dependency installation, offline/no-deps wheel installation,
  `pip check`, version/import checks, `dolphin --help`, and `dolphin info backends`
  all passed in clean temporary virtual environments.
- Executed the workflow's actual PowerShell SHA256 comparison locally: identical
  files passed, and changed wheel bytes were rejected with a nonzero exit code.
- Follow-up regression executes the actual download PowerShell step with a Python
  invocation recorder. Both uppercase and lowercase `PIP_EXTRA_INDEX_URL`,
  `PIP_FIND_LINKS`, `PIP_INDEX_URL`, and a custom pip config are overridden before
  every pip call; only the explicit TestPyPI index remains. Both cases passed.
  A negative control using the original step without cleanup failed as expected.

No hosted workflow or package upload was performed. GitHub and Trusted Publisher
administrative prerequisites documented for KAN-579 still apply; local tests do
not establish their server-side configuration.
