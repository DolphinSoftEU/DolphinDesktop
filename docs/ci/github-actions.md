# GitHub Actions

Start with plain pytest on a Windows runner. Tests that send physical input or
capture pixels require an unlocked interactive desktop and the target app's
prerequisites. Verify those conditions for your runner image; use a prepared
self-hosted desktop when the application or session cannot be provided by a
hosted runner. See the [full workflow example](../ci-example.md).

## Minimal Workflow

```yaml title=".github/workflows/tests.yml"
name: Desktop Tests

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  test:
    runs-on: windows-latest

    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
        with:
          persist-credentials: false

      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: "3.11"

      - name: Install
        run: python -m pip install "dolphin-desktop[pytest]==0.2.0"

      - name: Check Dolphin environment
        run: dolphin doctor

      - name: Run tests
        run: pytest tests/ -v --dolphin-backend=uia
```

## Upload Failure Artifacts

```yaml
      - name: Install
        run: python -m pip install "dolphin-desktop[fast,pytest]==0.2.0"

      - name: Run tests
        env:
          DOLPHIN_TRACE: on-failure
          DOLPHIN_VIDEO: keepfailedonly
        run: pytest tests/ -v --dolphin-screenshot-on-fail

      - name: Upload traces
        if: failure()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: dolphin-traces
          path: dolphin-traces/
          if-no-files-found: ignore

      - name: Upload videos
        if: failure()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: dolphin-videos
          path: dolphin-videos/
          if-no-files-found: ignore

      - name: Upload screenshots
        if: failure()
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
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
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5
        with:
          persist-credentials: false
      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: "3.11"
      - run: python -m pip install "dolphin-desktop[pytest]==0.2.0"
      - run: dolphin-run pytest tests/smoke/ -v --dolphin-backend=uia
```

Read [Headless Mode](../tutorials/headless-mode.md) before moving a full suite to `dolphin-run`.
