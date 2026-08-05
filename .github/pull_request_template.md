<!--
Thanks for contributing. Please walk through the checklist below.
Small PRs merge fastest; multi-purpose PRs may be asked to split.
-->

## Summary

<!-- One or two sentences. What changes and why. -->

## Type of change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature causing existing API to change)
- [ ] Docs / test-only change
- [ ] New backend / stack (please read the "Adding a new backend"
      section in `CONTRIBUTING.md`)

## Which stack does this touch?

- [ ] UIA / Win32
- [ ] SAP GUI
- [ ] Qt
- [ ] Electron / CEF (CDP)
- [ ] Java Swing / Oracle Forms (JAB)
- [ ] Mainframe (3270 / 5250 / HLLAPI)
- [ ] Image / OCR
- [ ] CLI / pytest plugin / infra

## Checklist

- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] `mypy src/` passes
- [ ] Added / updated tests
- [ ] Added / updated docs
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] For a new stack: `docs/guides/<stack>.md` includes install, quick
      start, backend selection, and a Troubleshooting section
- [ ] No stdlib imports in the per-stack suites `tests/<stack>/test_*.py`
      (autonomous library contract — see `_helpers.py`)

## Test plan

<!--
How did you verify the change? Which sample suites did you run
locally, and against what environment (VS Code installed? pub400
reachable? Steam signed in? Oracle Forms deployment available?)?
-->

## Related issue

<!-- Fixes #123 -->
