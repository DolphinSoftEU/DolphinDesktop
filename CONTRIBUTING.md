# Contributing to Dolphin

This guide covers local development and pull requests. See
[Architecture](docs/architecture.md) for the library design and
[RELEASING.md](RELEASING.md) for publication and branch protection policies.

## Prerequisites

- Windows 10 / 11 or Windows Server 2022+ for desktop automation tests.
- Python 3.11–3.13.
- Git and [uv](https://docs.astral.sh/uv/). CI pins uv in
  [ci.yml](.github/workflows/ci.yml).
- An interactive Windows desktop for tests that send physical input or capture
  pixels. Native Qt agents require x64 target applications.

External applications and tools are needed only for the suites that use them;
see the relevant [stack guide](docs/guides/index.md).

## Setup

```powershell
git clone https://github.com/DolphinSoftEU/DolphinDesktop.git
cd DolphinDesktop

# Match the unit CI environment: development tools plus image dependencies.
uv sync --locked --group dev --extra vision
uv run --locked pre-commit install
```

`dev` is a dependency group, not a package extra. The setup above does not
install every optional integration. Add the extras/groups documented below
when working on those areas. Run commands from the repository root.

Keep `uv.lock` committed. Ordinary setup and checks use `--locked`; when
intentionally changing dependencies in `pyproject.toml`, run `uv lock`, review
both files, and sync again.

## Running tests

Start with the unit suite; it does not require a real application under test
(AUT) or an external service:

```powershell
uv run --locked pytest -m unit -q

# The same selection with coverage.
uv run --locked pytest -m unit --cov=dolphin_desktop --cov-report=term-missing
```

Tests are classified by [tests/conftest.py](tests/conftest.py):

| Category | Scope |
| --- | --- |
| `unit` | `tests/framework`, `tests/test_support`, and `tests/mainframe/hllapi_mock`; framework checks and test doubles |
| `component` | `tests/mainframe/mock_tn3270`; controlled local server/emulator |
| `windows_component` | `tests/component` and `tests/qt/test_qt_agent_v2.py`; controlled Windows processes |
| `system` | Qt, Delphi, Electron, Oracle Forms, Office and PowerBuilder application suites, except the Qt agent component suite |
| `external` | SAP, Steam, pub400 and native TN5250 suites using external systems or installed applications |

The legacy `integration` marker covers `system` and `external`. Consequently,
`-m "not integration"` also runs component tests and is not a unit-only command.
An unclassified new suite fails collection; update the category map when adding
one. `uv run --locked pytest` selects all categories and requires their combined
prerequisites.

The repository disables automatic loading of the `dolphin-desktop` pytest
entry point in `pyproject.toml`; `tests/conftest.py` loads the production plugin
after coverage starts. Keep this bootstrap for repository tests rather than
adding an explicit `-p dolphin-desktop`. Installed-package/plugin behavior is
tested separately in subprocesses and the CI compatibility matrix.

### Controlled component tests

The non-Qt component selection uses the `cdp` extra, Playwright Chromium and
wc3270's `ws3270.exe`. Install wc3270 and point the environment variable at its
actual executable:

```powershell
uv sync --locked --group dev --extra vision --extra cdp
uv run --no-sync python -m playwright install chromium
$env:DOLPHIN_WS3270_PATH = 'C:\Tools\wc3270\ws3270.exe'
uv run --no-sync pytest -m "(component or windows_component) and not qt_agent" -q
```

The path above is an example. CI provisions and verifies wc3270 before testing;
see [the Windows component job](.github/workflows/ci.yml).
`--no-sync` uses the environment prepared by the preceding locked sync.

### Desktop acceptance

Run these targeted checks when changing the corresponding feature. Use an
interactive desktop without concurrent user input:

```powershell
# Hidden launch ownership and owner-exit cleanup.
uv run --locked pytest tests/component/test_hidden_lifecycle.py -q

# Console interrupts, nested runners and concurrent hidden execution.
uv run --locked pytest tests/component/test_runner_console.py -q

# Win32 selectors, controls and physical menu clicks.
uv run --locked pytest tests/framework/test_win32_controls.py tests/component/test_win32_controls_windows.py -q

# Recorder hooks, focus transitions and record -> generate -> replay.
uv run --locked python -m tests._recorder_acceptance

# Physical menu gate; configure the local display to 125% first.
uv run --locked python -m tests._win32_scaled_acceptance
```

The recorder and scaled-menu entry points reject missing tests, skips, failures
and errors. A skipped desktop test is incomplete verification. Scaled-menu
acceptance measures the actual monitor scale; local runs do not change display
settings. CI also requires passing owner-lifecycle, recorder, Qt agent and
console scenarios rather than accepting an all-skipped run.

Recorder evidence defaults to `artifacts/recorder-acceptance.xml`; scaled-menu
evidence, JUnit and screenshots go to `artifacts/win32-scaled/`. Both entry
points accept `--output`. CI uploads `windows-component-evidence`,
`runner-console-<python-version>` and `win32-scaled-125`; use current
[CI runs](https://github.com/DolphinSoftEU/DolphinDesktop/actions/workflows/ci.yml)
for diagnostics and retain run-specific findings in the PR.

### Qt agents and application suites

Install the locked Qt bindings before running the agent component suite:

```powershell
uv sync --locked --group dev --group qt-tests --extra vision
uv run --no-sync pytest tests/qt/test_qt_agent_v2.py -m qt_agent -q
```

For native agent changes, follow the pinned toolchain, DLL rebuild, manifest
and reproducibility procedure in
[src_cpp/qt_agent/README.md](src_cpp/qt_agent/README.md). The optional
`native-build` group requires Python 3.11.5+; this is a build-tool requirement,
not a change to the library's Python requirement.

Run the relevant application suite, such as `uv run --locked pytest tests/delphi/ -q`,
after completing its stack guide's setup. For example, the Oracle Forms Swing
mock must be compiled first:

```powershell
javac --release 21 tests/oracle_forms/OracleFormsMock.java
uv run --locked pytest tests/oracle_forms/ -q
```

That mock requires a compatible JDK, an absolute `JAVA_HOME` with JAB available,
and Java on PATH; see the [Java guide](docs/guides/java.md). Office live tests
have additional isolation/opt-in requirements documented in the
[Office guide](docs/guides/office.md). Missing optional applications can cause
local skips, while configured CI preflight/acceptance checks fail when their
required prerequisites or passing scenarios are absent. You do not need every
stack locally; report what ran, what skipped and what remains unverified.

## Code style and documentation

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src/

# Apply formatting when needed.
uv run --locked ruff format .
```

Installed pre-commit hooks run Ruff fixes/formatting on staged Python files and
mypy on `src/` when Python files change. They do not replace the full-repository
checks above. To run hooks across existing files, use
`uv run --locked pre-commit run --all-files`.

For documentation changes under `docs/` or API docstrings, build the site as CI
does:

```powershell
uv sync --locked --group dev --extra vision --extra docs
uv run --no-sync mkdocs build --strict
```

Conventions:

- Add type hints and docstrings to public functions, classes and methods.
- In stack scenario `test_*.py` files, exercise the public `dolphin_desktop` API
  instead of importing stdlib helpers directly. Framework/unit tests, fixtures
  and controlled AUT helpers may use stdlib and test doubles. Add a public
  library helper only when it belongs in the supported API.
- Prefer condition-based waits. In public API scenarios use Dolphin's `sleep()`
  only when a fixed delay is necessary.
- Add focused regression coverage for behavior changes, with test categories
  reflecting the resources the test actually uses.
- Keep commits focused and document public behavior changes in
  [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`.

## Dependency and CI verification

[CI](.github/workflows/ci.yml) runs unit tests on Python 3.11–3.13, controlled
Windows components, Qt agent reproducibility, runner and scaled-menu acceptance,
security checks, minimum-dependency checks and wheel installation checks across
Windows/Linux/macOS. It does not run every real-application or external suite.
Document relevant local application results in the PR.

The pytest compatibility matrix installs the wheel with pytest 9.0.3, 9.2.0
and the latest 9.x on Python 3.11–3.13, then runs
`tests/framework/test_terminal_redaction.py` in clean environments. Follow that
job's commands when changing pytest integration or the `>=9.0.3,<10` range.

After dependency changes, verify the full-lockfile security gate and minimum
installations. The minimum matrix covers the base package, each extra and all
extras together. It resolves the lowest compatible direct dependencies from
wheel metadata; transitive dependencies use the latest compatible resolution.
To reproduce one profile on Windows:

```powershell
uv sync --locked --group dev --group security --extra vision --python 3.13
uv run --no-sync python -m build --no-isolation --wheel --outdir artifacts/minimum/dist
$wheel = (Get-ChildItem artifacts/minimum/dist/*.whl).FullName
uv run --no-sync python .github/scripts/check_minimum_dependencies.py --wheel $wheel --output artifacts/minimum/evidence --all-extras
```

Use a fresh output directory with exactly one wheel. Replace `--all-extras` with
`--extra <name>` for one extra, or omit it for the base package; repeat with
Python 3.11 and 3.12. The `all` extra includes `cdp`, `fast`, `pytest` and
`vision`, not `docs` or `telemetry`. CI retains the wheel, resolution, audit and
command logs in `minimum-dependencies-<python>-<profile>`. The scripts and
workflow define the current matrix; avoid copying run-specific audit results
into this guide.

## Submitting a pull request

1. Fork the repository if needed and create a focused feature branch.
2. Add or update relevant tests and public documentation.
3. Run the unit, lint, format and type checks, plus affected component/application
   checks. State prerequisites, results and any skips in the PR.
4. Target the branch for the intended release: `testpypi-preview` for the preview
   line or `master` for production-bound changes; follow
   [RELEASING.md](RELEASING.md) for review and merge policy.
5. Complete the [PR template](.github/pull_request_template.md) and wait for
   current-commit CI to pass before merging.

## Adding a new backend

1. Implement `Backend` with an ID, platform, declared capabilities and clear
   unsupported-operation errors. Built-ins register in `_backend.py`; external
   packages use the `dolphin_desktop.backends` entry-point group. Follow
   [Backend capabilities](docs/backend-capabilities.md#writing-a-plugin-backend).
2. Add the stack facade and `Desktop` factories where appropriate, and export
   public names from `__init__.py` and `__all__`.
3. Add deterministic unit tests, controlled component tests where practical and
   relevant real-target scenarios. Update `tests/conftest.py` for new paths.
4. Update the [support matrix](docs/support-matrix.md), capability documentation
   and `docs/guides/<stack>.md` with prerequisites, API examples, limitations and
   troubleshooting; add navigation in `mkdocs.yml`.
5. Add a changelog entry. For bundled native code, include source, build inputs
   and artifact verification rather than shipping an unexplained binary.

## Reporting bugs and security issues

For ordinary bugs, use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml).
Include Windows/Python/Dolphin versions, `dolphin doctor` output, a minimal
reproducer, the affected backend/application and expected versus actual output.
Include relevant spy or trace output after reviewing it for sensitive data.

Report suspected vulnerabilities privately to `kontakt@dolphinsoft.pl` and
follow [SECURITY.md](SECURITY.md#reporting-a-vulnerability) for response and
disclosure policy. Do not file security-sensitive details in a public issue.

## Code of Conduct and license

Follow the [Code of Conduct](CODE_OF_CONDUCT.md); report conduct concerns to
`kontakt@dolphinsoft.pl`. Contributions are licensed under the project's
[Apache-2.0 License](LICENSE).
