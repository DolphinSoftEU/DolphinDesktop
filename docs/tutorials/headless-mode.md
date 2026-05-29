# Headless Mode

Dolphin can run a command on a hidden Windows desktop with `dolphin-run`.

```bash
dolphin-run pytest tests/ -v
```

The wrapper creates a desktop named `DolphinHidden`, sets `DOLPHIN_HEADLESS=1` for the child process, starts the command there, waits for it to finish, and closes its desktop handle.

## When To Use It

Use `dolphin-run` for test subsets that have been checked on a hidden desktop, especially smoke tests that mostly launch, inspect, read state, and collect artifacts.

For many end-to-end suites, the safer default is a normal interactive Windows runner:

```bash
pytest tests/ -v
```

GitHub Actions `windows-latest` and many self-hosted Windows agents already provide an interactive session. Start there before adding hidden-desktop execution.

## Activation Paths

Run the whole test command on the hidden desktop:

```bash
dolphin-run pytest tests/ -v --dolphin-backend=uia
```

Force a `Desktop` instance to use hidden mode:

```python
from dolphin_desktop import Desktop

desktop = Desktop(hidden=True)
```

Use visible mode even when `DOLPHIN_HEADLESS` is set:

```python
desktop = Desktop(hidden=False)
```

## Important Limitations

Hidden desktops are a Windows isolation primitive. They are not the same as Selenium-style browser headless mode.

Known limitations:

- MSIX, UWP, Microsoft Store apps, and some system apps may launch through broker processes on the default desktop instead of `DolphinHidden`.
- Mouse-based actions can fail when Windows refuses to move the physical cursor on a non-input desktop.
- UAC prompts appear on the secure desktop and cannot be automated by Dolphin.
- Clipboard behavior can be surprising because the window station and desktop focus both matter.

If a test uses many physical mouse actions, run it on a visible interactive desktop unless you have verified that the target app and actions work under `dolphin-run`.

## pytest Flag

The pytest plugin also exposes `--dolphin-headless`:

```bash
pytest tests/ -v --dolphin-headless
```

This creates `Desktop(hidden=True)` through the fixture. For full UIA visibility of hidden-desktop windows, prefer running the entire command through `dolphin-run`.

## Skipping Tests In Headless Mode

```python
import os
import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("DOLPHIN_HEADLESS") == "1",
    reason="This test requires the visible desktop",
)
```

## Artifacts

Artifacts still use the normal pytest options:

```bash
dolphin-run pytest tests/ -v --dolphin-trace=always --dolphin-video=keepfailedonly
```

Traces go to `dolphin-traces/`, videos to `dolphin-videos/`, and screenshots to `dolphin-screenshots/` when enabled.

Next: [CI Setup](../ci/index.md).
