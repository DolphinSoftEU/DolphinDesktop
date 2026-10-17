# Contributing to Dolphin

Thank you for your interest in contributing!

## Prerequisites

- Windows 10 / 11 or Windows Server 2022+
- Python 3.11-3.13
- [uv](https://docs.astral.sh/uv/) package manager

## Setup

```bash
git clone https://github.com/DolphinSoftEU/DolphinDesktop.git
cd DolphinDesktop

# install all dependencies including dev extras
uv sync --group dev

# install pre-commit hooks
uv run pre-commit install
```

## Running Tests

Unit and non-integration tests (no real apps required):

```bash
uv run pytest -m "not integration"
```

Full integration test suite (requires Windows desktop session):

```bash
uv run pytest
```

With coverage:

```bash
uv run pytest --cov=dolphin_desktop --cov-report=term-missing
```

## Minimum dependency verification (KAN-581)

CI and release run a separate Windows matrix for Python 3.11, 3.12 and 3.13,
with the base package, each extra independently (including the aggregate `all`
extra), and all extras together. Each job builds a wheel using
locked build tools, reads its `Requires-Dist` metadata, resolves the lowest
compatible direct dependencies with binary distributions, then installs the
hashed resolution in a clean environment. Transitive dependencies use the
resolver's latest compatible versions. This tests a compatible minimum set,
not every possible combination allowed by the dependency ranges.

The checks include dependency consistency, import of the installed wheel,
imports of the selected optional integrations, execution of the installed
`dolphin --help` and `dolphin info backends` commands, and an explicit check of
the `dolphin-desktop` pytest entry point whenever the `pytest` extra is selected,
and a strict `pip-audit` of the resolved set. This audit is independent of the
existing full-lockfile audit; their vulnerability counts must not be added.
Build, security and docs tooling remain controlled by `uv.lock`.

To reproduce one matrix entry on Windows (use a fresh checkout or set
`UV_PROJECT_ENVIRONMENT` to a dedicated tool environment):

```powershell
uv sync --locked --only-group build --only-group security --python 3.13
uv run --no-sync python -m build --no-isolation --wheel --outdir artifacts/minimum/dist
$wheel = (Get-ChildItem artifacts/minimum/dist/*.whl).FullName
uv run --no-sync python .github/scripts/check_minimum_dependencies.py --wheel $wheel --output artifacts/minimum/evidence --all-extras
```

Replace `--all-extras` with `--extra <name>` for an individual extra (`fast`,
`vision`, `cdp`, `docs`, `telemetry`, `pytest`, or `all`); omit both for the base
profile. The `all` extra selects only its wheel requirements, including any
self-references; it does not implicitly include `docs` or `telemetry`. Repeat with
Python 3.11 and 3.12. Each profile resolves independently, preventing an
unrelated extra from raising a shared dependency's minimum during the check.
CI uploads `minimum-dependencies-<python>-<profile>` even on failure, containing
the wheel, direct requirements, hashed resolution, command log, audit JSON and
a success result with interpreter version and wheel SHA-256. A missing success
result indicates an incomplete or failed check; inspect the command log.
After dependency/resolver changes, rerun this matrix and the full-lockfile
security gate. The wheel and reports let a tester reproduce the installation
without relying on metadata from an unrelated environment.

## Code Style

This project uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting, and [mypy](https://mypy-lang.org/) for type checking.

```bash
# lint
uv run ruff check .

# format
uv run ruff format .

# type check
uv run mypy src/
```

Pre-commit hooks run these automatically on every commit.

## Submitting a Pull Request

1. Fork the repository and create a feature branch.
2. Add tests for any new functionality.
3. Ensure `ruff check .`, `ruff format --check .`, and `mypy src/` all pass.
4. Open a PR against `master` - CI will run the full matrix automatically.

## Running the sample suites

`tests/` ships runnable integration suites for every stack
(`tests/framework` is the headless unit suite; `tests/qt`,
`tests/delphi`, `tests/electron`, `tests/steam`, `tests/mainframe`,
`tests/oracle_forms` target real or mock apps). These are the closest
thing to end-to-end validation:

```bash
# Mainframe (mock TN3270 + pub400 s3270 + native TN5250 + HLLAPI ctypes fake)
uv run pytest tests/mainframe/ -q

# Oracle Forms mock (compile the Java Swing mock first)
javac --release 21 tests/oracle_forms/OracleFormsMock.java
uv run pytest tests/oracle_forms/ -q

# Steam CEF (needs Steam running with -cef-enable-debugging on port 8080)
uv run pytest tests/steam/ -q

# Electron / VS Code (needs VS Code installed at %LOCALAPPDATA%)
uv run pytest tests/electron/ -q
```

If a suite can't run in your environment (no VS Code, no wc3270, no
Steam), it will `pytest.skip` cleanly — you do not need every stack
locally to contribute.

## Conventions

Key rules the codebase leans on:

* **Type hints on every public function.**
* **Docstrings on every public class + method** — one-line summary,
  then Args / Returns / Raises. Users see these in IDE tooltips.
* **No stdlib imports in `tests/**/test_*.py`** — the autonomous-
  library contract. Add a wrapper helper to `dolphin_desktop` first,
  then use the public export. See `dirname`, `path_join`,
  `start_thread`, `tcp_reachable`, `which` for the pattern.
* **No raw `time.sleep()` in tests** — use `sleep()` from
  `dolphin_desktop` and prefer poll-based `wait_for_*` primitives.
* **Small, focused commits** — rebase noisy history before opening
  the PR.

## Adding a new backend

If you're adding support for a new application stack (say, Delphi VCL
or Chromium apps with custom CDP quirks), follow the pattern the
existing stacks established:

1. **Module** `src/dolphin_desktop/_<stack>.py` — public classes
   modeled after `_cdp.py`, `_oracle_forms.py`, `_mainframe.py`.
2. **Factory** on `Desktop` (`launch_<stack>`, `attach_<stack>`).
3. **Register** the backend id in `_backend.py` so
   `list_backends()` surfaces it.
4. **Exports** in `__init__.py` (both the import list and `__all__`).
5. **Sample** `tests/<stack>/` with a mock target (and, where useful,
   a sample app under `examples/`) plus a real-target test that skips
   gracefully when unavailable.
6. **Docs** `docs/<stack>.md` — install, quick start, API surface,
   backend selection, troubleshooting.
7. **Changelog** entry under `[Unreleased]` in `CHANGELOG.md`.

## Reporting bugs

Please include:

* Windows version (`winver`)
* Python version (`python --version`)
* dolphin_desktop version (`python -c "import dolphin_desktop; print(dolphin_desktop.__version__)"`)
* `dolphin doctor` output
* Minimal reproducer (a short pytest file that hits the bug)
* Actual output vs. expected
* If applicable: `dolphin spy` output for the failing UI

## Security issues

Please **do not** file public issues for security problems. Email
`kontakt@dolphinsoft.pl` with details. You'll get an acknowledgement
within 48h and a fix window we agree on before public disclosure.

## Code of Conduct

Everyone participating in dolphin-desktop follows the
[Code of Conduct](CODE_OF_CONDUCT.md). Report issues to
`kontakt@dolphinsoft.pl`.

## License

By contributing, you agree that your contributions will be licensed
under the [Apache-2.0 License](LICENSE) that covers the project.

## Architecture

See [docs/architecture.md](docs/architecture.md) for a breakdown of the library's design.
