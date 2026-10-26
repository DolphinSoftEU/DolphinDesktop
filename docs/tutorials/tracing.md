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

`RUN_ID` is the name of a trace-run directory under the trace directory, or a
path to that directory; it is not the SQLite `runs.id` value. `--last` selects
the run with the newest recorded start time. A run with status `RUNNING` is an
incomplete trace (for example, after an interrupted test) and its viewer shows
an unknown duration. Directories without a readable `trace.db`, including a
corrupted trace copy, are skipped by `trace list`; `trace view` reports an
error and does not create a viewer for them.

The viewer checks v1 table names, column types, NOT NULL and primary key
constraints, the steps-to-runs foreign key target, and stored column types before
rendering. It does not compare every DDL property, such as AUTOINCREMENT or
foreign key deletion actions. Invalid values (including nonfinite numbers) produce an
`Invalid trace.db` error. Screenshot references must be flat filenames inside
`screenshots/`; unsafe paths and links outside that directory are omitted.
Generated HTML includes a Content Security Policy allowing only the fixed viewer
script. UIA tree depths must be integers; displayed indentation is limited to
32 levels. Malformed trees are displayed as escaped JSON text.
When publishing traces received from others, serve them from a separate
origin without application cookies or access to other sensitive reports.

Use `--dir PATH` if your project writes traces somewhere else:

```bash
dolphin trace list --dir artifacts/traces
dolphin trace view --last --dir artifacts/traces
```

## 9.2 — Nagrywanie wideo

Video recording supports the `off`, `keepfailedonly` and `keepall` modes. The
requested frame rate is controlled with `config(video_fps=...)` or
`DOLPHIN_VIDEO_FPS` and must be an integer from 1 through 30. In
`keepfailedonly`, a successful test's recording is removed while a failed
test's recording is retained; `keepall` retains both, and `off` creates no
recording. Encoding requires the external `ffmpeg` binary (and `ffprobe` for
validation); it is not installed by a Python extra.

Video recording is on by default (`keepfailedonly`) and is controlled
with `--dolphin-video` or `DOLPHIN_VIDEO`. It requires an external
`ffmpeg` binary on `PATH` (or pointed to by `DOLPHIN_FFMPEG`); without
ffmpeg, recording is silently skipped. No extra can change that — ffmpeg is
a binary, not a wheel. (The `fast` extra installs `mss`, which speeds up the
per-step trace screenshots below, not video encoding.)

```bash
pytest tests/ -v --dolphin-video=keepfailedonly
```

| Value | Behavior |
| --- | --- |
| `off` | No recording. |
| `keepfailedonly` | Default. Record tests, keep MP4 files only for failures. |
| `keepall` | Keep MP4 files for every test. |

Videos are written to `dolphin-videos/` by default. `ffmpeg` is a separate binary install and must be available for MP4 encoding.

## Screenshots On Failure

```bash
pytest tests/ -v --dolphin-screenshot-on-fail
```

Screenshots are written to `dolphin-screenshots/` and attached to the pytest report.

## 9.3 — Warstwa stabilności

Diagnostic collection is best-effort. A failure in
`--dolphin-screenshot-on-fail` — for example because the desktop is locked or
screen capture is unavailable — is logged as a warning and cannot replace the
original test exception, change its exit status, or create a pytest
`INTERNALERROR`.

The same rule applies to trace, video, Allure, and fallback HTML collection.
The original failure remains available in the terminal and JUnit/XML report;
captured stdout/stderr and diagnostic logs remain available after secret
redaction. A failed screenshot capture does not leave a partial PNG or a
misleading artifact link.

For Allure, redaction runs before result/container serialization, covering test
and fixture exception messages, tracebacks, nested steps and parameters or
labels named by sensitive keys such as `pin`. Captured stdout/stderr is masked
before attachment. This applies with automatic or explicit plugin loading;
Allure remains an optional dependency. Test statuses and attachment references
are preserved.

Sensitive parameter values are also removed from Allure display names with
default pytest IDs or custom `ids=`. Pytest collection IDs and live verbose
output are unchanged; use non-sensitive `ids=` when sharing them.

Direct `allure.attach()` and `allure.attach.file()` calls bypass Dolphin's text
redaction. Their caller must sanitize the body or file first. For example:

```python
import allure
from dolphin_desktop import Secret

allure.attach(f"pin={Secret(pin)}", name="diagnostics", attachment_type=allure.attachment_type.TEXT)
```

The formatted text contains `pin=***`. Attaching the raw value or a file that
contains it remains outside automatic redaction, even if the value was
previously registered through `Secret`.

Redaction applies to text, not screenshot or video pixels. A captured desktop
can show credentials or other private information, including in windows outside
the application under test. Limit access to these artifacts and set CI
retention to the shortest period needed; delete local copies after debugging.

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
