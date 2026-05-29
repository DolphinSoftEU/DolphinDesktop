# Tracing And Debugging

Dolphin's pytest plugin can record a trace for each test. A trace stores action metadata, screenshots for failed steps or all steps depending on mode, and UIA tree data for errors.

## Trace Modes

Tracing is controlled by `--dolphin-trace` or `DOLPHIN_TRACE`.

| Value | Behavior |
| --- | --- |
| `on-failure` | Default. Keep traces for failed tests and discard passing runs. |
| `always` | Keep traces for every test. |
| `off` | Disable tracing. |

Examples:

```bash
pytest tests/ -v --dolphin-trace=always
set DOLPHIN_TRACE=always
pytest tests/ -v
```

Trace runs are written under `dolphin-traces/` by default.

## View Traces

```bash
dolphin trace list
dolphin trace view --last
dolphin trace view RUN_ID
```

Use `--dir PATH` if your project writes traces somewhere else:

```bash
dolphin trace list --dir artifacts/traces
dolphin trace view --last --dir artifacts/traces
```

## Video Recording

Video recording uses `--dolphin-video` or `DOLPHIN_VIDEO`.

```bash
pip install "dolphin-desktop[video]"
pytest tests/ -v --dolphin-video=keepfailedonly
```

| Value | Behavior |
| --- | --- |
| `off` | No recording. |
| `keepfailedonly` | Default. Record tests, keep MP4 files only for failures. |
| `keepall` | Keep MP4 files for every test. |

Videos are written to `dolphin-videos/` by default. `ffmpeg` must be available for MP4 encoding.

## Screenshots On Failure

```bash
pytest tests/ -v --dolphin-screenshot-on-fail
```

Screenshots are written to `dolphin-screenshots/` and attached to the pytest report.

## Fallback HTML Report

When `allure-pytest` is not installed, Dolphin writes `dolphin-report.html` at session finish. Set a custom path with:

```bash
pytest tests/ -v --dolphin-html artifacts/dolphin-report.html
```

## What To Check First

When a selector fails:

1. Open the latest trace with `dolphin trace view --last`.
2. Check the failed action selector and error message.
3. Compare the UIA tree against your locator criteria.
4. Use `dolphin spy --pick` to confirm the current selector.

Next: [Fallback Selectors](fallback-selectors.md) and [CI Setup](../ci/index.md).
