# Full CI workflow example

This page walks through a working GitHub Actions setup that runs your
`dolphin-desktop` tests on every push and pull request.

> **Windows-only runners.** dolphin_desktop drives Windows APIs (UIA,
> Win32, JAB, HLLAPI, SAP GUI COM). The workflow targets
> `windows-latest`; Linux/macOS runners will not work for the
> automation tests. Non-automation unit tests (mainframe TN5250 parser,
> EBCDIC translation, path helpers) run on any OS.

## Minimum viable workflow

Save as `.github/workflows/tests.yml`:

```yaml
name: tests

on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  windows:
    runs-on: windows-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
      fail-fast: false

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip

      - name: Install dolphin-desktop with extras
        run: |
          python -m pip install --upgrade pip
          pip install "dolphin-desktop[cdp,vision,pytest]"

      - name: Install Playwright browser (for CDP tests)
        run: playwright install --with-deps chromium

      - name: Environment probe
        run: dolphin doctor

      - name: Run unit tests
        run: pytest tests/ -q --tb=short --timeout=60 --dolphin-screenshot-on-fail

      - name: Upload artifacts on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: pytest-artifacts-${{ matrix.python-version }}
          path: |
            .pytest_cache/
            dolphin-screenshots/
            dolphin-traces/
            **/*.html
          if-no-files-found: ignore
          retention-days: 7
```

## Full workflow with UI automation on a hidden desktop

UIA and pywinauto require an interactive desktop session. On CI
runners the "session" is a hidden desktop — dolphin's runner integrates
via the `dolphin-run` command. Use it for tests marked as
`integration`:

```yaml
      - name: Run UI automation tests on hidden desktop
        run: dolphin-run pytest tests/ -m integration --tb=short --timeout=120
```

`dolphin-run`:

* Creates a `DolphinHidden` Windows desktop.
* Reparents the test process to it.
* Launches all `Desktop.launch(...)` targets on the same hidden desktop
  so their windows are invisible to the runner display but fully
  responsive to UIA / Win32 automation.

Without `dolphin-run`, tests that assert on visible geometry or
foreground focus will race with GitHub's screen-lock timer.

## Testing individual stacks in CI

### Electron / CEF

Runs cleanly on any Windows GitHub runner — Playwright ships its own
Chromium.

```yaml
      - name: Install Chromium
        run: playwright install --with-deps chromium

      - name: Run CDP tests
        run: pytest tests/electron/ -q
```

### Mainframe 3270 (needs ws3270)

Cache the wc3270 install between runs:

```yaml
      - name: Cache wc3270
        id: wc3270-cache
        uses: actions/cache@v4
        with:
          path: ${{ env.LOCALAPPDATA }}\wc3270
          key: wc3270-4.5ga5

      - name: Install wc3270 (portable)
        if: steps.wc3270-cache.outputs.cache-hit != 'true'
        run: |
          $url = "https://downloads.sourceforge.net/project/x3270/x3270/4.5ga5/wc3270-4.5ga5-noinstall-64.zip"
          $zip = "$env:TEMP\wc3270.zip"
          Invoke-WebRequest $url -OutFile $zip
          Expand-Archive $zip -DestinationPath "$env:LOCALAPPDATA\wc3270"

      - name: Run mainframe tests
        run: pytest tests/mainframe/ -q
```

### Mainframe 5250 / IBM i

Pure Python — no external install. Add a job step to skip against
pub400 when the runner has no outbound network:

```yaml
      - name: Run TN5250 tests
        run: pytest tests/mainframe/ -q
        continue-on-error: true  # public IBM i hosts may be unreachable from CI
```

### Java / Oracle Forms

The JDK on GitHub's `windows-latest` includes JAB. Enable it once:

```yaml
      - name: Enable Java Access Bridge
        run: jabswitch /enable

      # If your suite ships a Java fixture app, compile it first:
      - name: Compile Java test fixtures
        run: javac --release 21 tests/fixtures/MySwingApp.java

      - name: Run Java / Oracle Forms tests
        run: pytest tests/oracle_forms/ -q
```

### SAP GUI

**SAP GUI is not installable on public runners.** Test SAP against your
own self-hosted runner with SAP GUI + Scripting enabled. Skip on
public runners with a marker:

```python
# conftest.py
import pytest
from dolphin_desktop import Desktop

def pytest_collection_modifyitems(config, items):
    try:
        Desktop().sap(timeout=1)
        sap_available = True
    except Exception:
        sap_available = False
    skip_sap = pytest.mark.skip(reason="SAP GUI not available on this runner")
    for item in items:
        if "sap" in item.keywords and not sap_available:
            item.add_marker(skip_sap)
```

## Artifact capture on failure

dolphin's pytest plugin writes traces on failure by default
(`--dolphin-trace=on-failure`); screenshots require the opt-in
`--dolphin-screenshot-on-fail` flag on the pytest invocation. Video
recording (`--dolphin-video`, default `keepfailedonly`) additionally
needs an `ffmpeg` binary on the runner's PATH (or `DOLPHIN_FFMPEG`) —
without it, no MP4s are produced. Wire GitHub artifacts to keep the
outputs for 7 days:

```yaml
      - name: Upload traces
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: dolphin-traces-${{ matrix.python-version }}
          path: |
            dolphin-traces/
            dolphin-screenshots/
          if-no-files-found: ignore
          retention-days: 7
```

To keep videos too, install ffmpeg on the runner and add
`dolphin-videos/` to the `path:` list.

## Full example — everything wired

```yaml
name: full-tests

on:
  push:
    branches: [main, master]
  pull_request:
  workflow_dispatch:

jobs:
  windows:
    runs-on: windows-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
      fail-fast: false

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip

      - name: Install dolphin-desktop with extras
        run: |
          python -m pip install --upgrade pip
          pip install "dolphin-desktop[cdp,vision,pytest]"

      - name: Install Playwright browsers
        run: playwright install --with-deps chromium

      - name: Enable Java Access Bridge
        run: jabswitch /enable

      - name: Cache wc3270
        id: wc3270-cache
        uses: actions/cache@v4
        with:
          path: ${{ env.LOCALAPPDATA }}\wc3270
          key: wc3270-4.5ga5

      - name: Install wc3270
        if: steps.wc3270-cache.outputs.cache-hit != 'true'
        run: |
          Invoke-WebRequest "https://downloads.sourceforge.net/project/x3270/x3270/4.5ga5/wc3270-4.5ga5-noinstall-64.zip" -OutFile "$env:TEMP\wc3270.zip"
          Expand-Archive "$env:TEMP\wc3270.zip" -DestinationPath "$env:LOCALAPPDATA\wc3270"

      # Only needed if your suite ships a Java fixture app:
      - name: Compile Java test fixtures
        run: javac --release 21 tests/fixtures/MySwingApp.java

      - name: Environment probe
        run: dolphin doctor

      - name: Unit tests
        run: pytest tests/ -m "not integration" -q --tb=short --timeout=60

      - name: Integration tests (on hidden desktop)
        run: dolphin-run pytest tests/ -m integration --tb=short --timeout=180

      - name: Per-stack suites
        run: pytest tests/ -q --tb=short --timeout=180 --dolphin-screenshot-on-fail

      - name: Upload artifacts on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: pytest-artifacts-${{ matrix.python-version }}
          path: |
            .pytest_cache/
            dolphin-screenshots/
            dolphin-traces/
          if-no-files-found: ignore
          retention-days: 7
```

## Cost + timing notes

* GitHub-hosted `windows-latest` runners are ~2× more expensive per
  minute than `ubuntu-latest`. Budget accordingly for a matrix of
  three Python versions.
* Playwright's Chromium install adds ~200 MB per run — cache it or
  gate it behind a `paths:` filter so unrelated changes do not
  reinstall.
* dolphin's UI tests average 10–30 s each on a cold runner. Split
  large suites with `pytest -n auto` (`pytest-xdist`) — every test is
  independent unless it opts into a module-scoped fixture.

## Local reproducibility

Run the same steps locally to reproduce a CI failure:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install "dolphin-desktop[cdp,vision,pytest]"
playwright install chromium
jabswitch /enable
dolphin doctor
pytest -q
```
