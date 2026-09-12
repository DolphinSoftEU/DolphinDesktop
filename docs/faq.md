# FAQ

## Does Dolphin work on macOS or Linux?

No. The documented runtime target is Windows desktop automation. The package has placeholder backend classes for future or plugin use, but Dolphin's implemented product path depends on Windows UIA, Win32, COM, and pywin32.

## Which Python versions are supported?

For the 0.2.0 release, the supported range is Python 3.11–3.13. The package
metadata rejects Python 3.14 and newer until a release explicitly adds support.

## Do I have to use pytest?

No. `Desktop`, `Application`, `Window`, and `Locator` are normal Python objects. Pytest is recommended because the package provides fixtures, CLI options, retries, traces, screenshots, videos, and cleanup.

## Why does `dolphin init my-tests` ask for a template?

Without `--yes` or `--template`, the command is interactive. Press Enter to use the default `standard` template, or run:

```bash
dolphin init my-tests --yes
```

## The `desktop` or `launch` fixture is missing.

Install Dolphin in the same environment that runs pytest:

```bash
pip install dolphin-desktop pytest
```

The plugin is loaded through the `pytest11` package entry point. You do not need to import it in `conftest.py`.

## My locator times out, but I can see the element.

Use the spy tool to inspect the actual UIA tree:

```bash
dolphin spy --window "App Title" --depth 3
dolphin spy --pick
```

The visible label may not be the UIA title, and the control type may differ from what you expect. For example, Windows Notepad exposes the editor as `Document`.

## Which selector should I prefer?

Prefer selectors in this order when the app exposes them:

1. `get_by_automation_id(...)`
2. `get_by_role(..., name=...)`
3. `get_by_title(...)` or `get_by_text(...)`
4. `get_by_class(...)`
5. `window.image(...)` as a fallback

## `type_text()` enters the wrong characters.

Try `set_text()` for controls that support direct value assignment:

```python
field.set_text("text")
```

If the control requires keyboard input, use a small pause:

```python
field.type_text("text", pause=0.05)
```

## `text()` returns an empty string.

Some controls do not expose text through UIA. Dolphin attempts a clipboard-based fallback for text reading. If that also fails, the control may not support accessible text retrieval and may need a different selector or image/OCR fallback.

## The application runs as Administrator.

Run the test process as Administrator too. Windows blocks lower-integrity processes from automating elevated windows.

## Can Dolphin interact with UAC prompts?

No. UAC prompts run on the secure desktop and are intentionally isolated from normal desktop automation.

## Should I use `dolphin-run` for all CI tests?

Use it when you need Dolphin to create a hidden Windows desktop:

```bash
dolphin-run pytest tests/ -v
```

Some Store/UWP/MSIX apps and mouse-heavy tests still require a visible interactive desktop. See [Headless Mode](tutorials/headless-mode.md).

## Where are traces and videos written?

By default:

- Traces: `dolphin-traces`
- Videos: `dolphin-videos`
- Screenshots on failure: `dolphin-screenshots`

Open the latest trace with:

```bash
dolphin trace view --last
```
