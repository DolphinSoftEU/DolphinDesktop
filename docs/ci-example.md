# Windows UI workflow example

This workflow belongs in your automation project. It runs tests against an
installed Dolphin preview and your prepared application; it is separate from
the library's own unit/component CI.

## Prepare the runner

Use a logged-in Windows account with an unlocked interactive desktop for
physical input and screenshots. Install the target application and its runtime,
then verify the environment with `dolphin doctor`. Keep tests and the AUT at
compatible Windows integrity levels.

The example selects a self-hosted Windows runner with the custom label
`desktop-ui`. Assign that label to the prepared agent or change the label to
match your environment. See [GitHub runner labels](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/use-in-a-workflow)
and [Microsoft's interactive-agent guidance](https://learn.microsoft.com/azure/devops/pipelines/agents/agents?view=azure-devops#interactive-vs-service).
Run this job only for code trusted to access that machine.

## Workflow

Save as `.github/workflows/desktop-tests.yml` in your test repository:

```yaml
name: Desktop tests

on:
  push:
    branches: [main, master]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: desktop-ui-${{ github.repository }}
  cancel-in-progress: false

jobs:
  desktop:
    runs-on: [self-hosted, windows, desktop-ui]
    env:
      DOLPHIN_VIDEO: "off"
      DOLPHIN_TRACE: on-failure
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
        with:
          persist-credentials: false
      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: "3.13"
      - name: Install the documented preview
        run: |
          python -m venv .venv
          .\.venv\Scripts\python.exe -m pip install "dolphin-desktop[pytest] @ git+https://github.com/DolphinSoftEU/DolphinDesktop.git@testpypi-preview"
      - name: Check environment
        run: .\.venv\Scripts\dolphin.exe doctor
      - name: Run desktop tests sequentially
        run: >-
          .\.venv\Scripts\python.exe -m pytest tests/ -q --tb=short
          --dolphin-screenshot-on-fail
          --dolphin-html=dolphin-report.html
          --junitxml=test-results.xml
      - name: Upload test evidence
        if: always()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: desktop-evidence
          path: |
            test-results.xml
            dolphin-report.html
            dolphin-screenshots/
            dolphin-traces/
          if-no-files-found: warn
          retention-days: 7
```

For a reproducible run, replace `testpypi-preview` with a reviewed commit SHA
or install an approved wheel. The branch requirement above follows the current
preview; it is not a dependency lock. See [Installation](installation.md#preview-installation).

The concurrency group serializes this workflow's runs within the repository.
Reserve the same desktop across other workflows too. Use `pytest-xdist` only
for hermetic tests or separately verified desktop isolation; independent Python
test functions can still compete for the same mouse, keyboard and clipboard.

## Add only the selected stack's prerequisites

- **CDP:** add the `[cdp]` extra and enable the AUT's debug endpoint. Electron,
  CEF and WebView2 bring their own browser engine. Download Playwright Chromium
  only when your tests launch that browser themselves.
- **Java / Oracle Forms:** configure a trusted absolute `JAVA_HOME` containing
  the JAB DLL and switch tool. Prepare JAB before the test run; see
  [Java](guides/java.md) and [Oracle Forms](guides/oracle-forms.md).
- **Mainframe:** install wc3270 only for `s3270`; native TN5250 needs no emulator.
  Configure verified TLS and required host access. Missing hosts and transport
  errors must not be turned into a passing required job.
- **SAP / Office:** use dedicated prepared application environments and accounts
  or profiles. The Python extras do not install those applications.
- **Vision:** install `[vision]` and Tesseract for OCR, with matching DPI/theme
  data for image matching.

## Hidden desktop smoke tests

`dolphin-run` creates a separate hidden Windows desktop and controls the command's
process tree. Physical input and visible-desktop capture still depend on the
Windows input desktop. Keep the visible workflow above for those operations.

After validating a programmatic subset against your AUT:

```powershell
.\.venv\Scripts\dolphin-run.exe .\.venv\Scripts\python.exe -m pytest tests/smoke/ -q --dolphin-backend=uia
```

Choose operations such as `invoke()` and `set_value()` only where the target
provider exposes their required patterns. See [Headless Mode](tutorials/headless-mode.md).
Custom markers and directories in a consumer project must be declared by that
project; `not integration` is not a general synonym for unit tests.

## Optional video

Install an external `ffmpeg` executable and set `DOLPHIN_FFMPEG` or PATH before
enabling `DOLPHIN_VIDEO=keepfailedonly`. Add `dolphin-videos/` to the artifact
paths. The `fast` extra installs `mss` for faster trace screenshots, not a video
encoder. Review artifact access and retention for the data shown by your AUT.
