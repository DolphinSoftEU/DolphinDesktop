# GitHub Actions

GitHub-hosted Windows runners provide an interactive session, so start with plain pytest.

## Minimal Workflow

```yaml title=".github/workflows/tests.yml"
name: Desktop Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: windows-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install
        run: pip install dolphin-desktop pytest

      - name: Check Dolphin environment
        run: dolphin doctor

      - name: Run tests
        run: pytest tests/ -v --dolphin-backend=uia
```

## Upload Failure Artifacts

```yaml
      - name: Install
        run: pip install "dolphin-desktop[fast]" pytest

      - name: Run tests
        env:
          DOLPHIN_TRACE: on-failure
          DOLPHIN_VIDEO: keepfailedonly
        run: pytest tests/ -v --dolphin-screenshot-on-fail

      - name: Upload traces
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: dolphin-traces
          path: dolphin-traces/
          if-no-files-found: ignore

      - name: Upload videos
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: dolphin-videos
          path: dolphin-videos/
          if-no-files-found: ignore

      - name: Upload screenshots
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: dolphin-screenshots
          path: dolphin-screenshots/
          if-no-files-found: ignore
```

`DOLPHIN_VIDEO` above only produces MP4s if an `ffmpeg` binary is on the
runner's `PATH` (or pointed at by `DOLPHIN_FFMPEG`). Without it, recording is
skipped silently and the upload step finds nothing, so confirm ffmpeg is
present on your runner image before relying on the videos. The `fast` extra
installs `mss` for quicker trace screenshots; it does not encode video.

## Headless Smoke Job

Use `dolphin-run` only for tests you have verified on a hidden desktop:

```yaml
  smoke-headless:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install dolphin-desktop pytest
      - run: dolphin-run pytest tests/smoke/ -v --dolphin-backend=uia
```

Read [Headless Mode](../tutorials/headless-mode.md) before moving a full suite to `dolphin-run`.
