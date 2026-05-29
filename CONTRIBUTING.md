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
4. Open a PR against `main` - CI will run the full matrix automatically.

## Architecture

See [docs/architecture.md](docs/architecture.md) for a breakdown of the library's design.
