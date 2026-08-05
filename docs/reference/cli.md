# CLI Reference

Dolphin installs two console commands:

- `dolphin`: project scaffolding, diagnostics, inspection, recording, trace utilities, and backend information.
- `dolphin-run`: run another command on a hidden Windows desktop.

## `dolphin`

```text
dolphin [-h] COMMAND ...
```

Commands:

| Command | Purpose |
| --- | --- |
| `init` | Bootstrap a new Dolphin test project |
| `doctor` | Check Python, dependencies, UIA access, and environment variables |
| `spy` | Inspect a window UI tree or pick an element interactively |
| `record` | Record mouse and keyboard interactions into a Python test |
| `trace list` | List recent Dolphin trace runs |
| `trace view` | Generate and open a trace HTML viewer |
| `selfheal-stats` | Show fallback selector usage |
| `info backends` | List registered automation backends |

## `dolphin init`

```text
dolphin init [NAME] [--template {minimal,standard,enterprise}] [--yes] [--install] [--git]
```

| Option | Description |
| --- | --- |
| `NAME` | Project directory to create. If omitted, Dolphin prompts for a name. |
| `--template {minimal,standard,enterprise}` | Choose the scaffold template. |
| `--yes`, `-y` | Non-interactive defaults: `my-dolphin-tests` and `standard`. |
| `--install` | Run `pip install` for scaffold dependencies after creation. |
| `--git` | Run `git init` in the new project directory. |

Examples:

```bash
dolphin init my-tests
dolphin init my-tests --yes
dolphin init my-tests --template enterprise --yes --install --git
```

## `dolphin doctor`

```bash
dolphin doctor
```

Prints:

- Dolphin and Python versions.
- Windows platform information.
- Required and optional dependency status.
- UIA access status.
- `DOLPHIN_*` environment variables used by runtime configuration.

## `dolphin spy`

```text
dolphin spy [--window TITLE] [--exact TITLE] [--class CLASS] [--pid PID]
            [--depth N] [--json] [--pick] [--image-pick] [--output-dir DIR]
            [--backend {uia,win32}]
            [--sap] [--connection N] [--session N]
            [--jab] [--cdp URL] [--delphi]
            [--mainframe HOST:PORT] [--session-type {3270,5250}]
```

Stack-specific flags:

| Option | Description |
| --- | --- |
| `--sap` | Inspect SAP GUI via SAP GUI Scripting (emits `find_by_id` locators) |
| `--connection N` | SAP connection index for `--sap` (default: `0`) |
| `--session N` | SAP session index for `--sap` (default: `0`) |
| `--jab` | Inspect a Java Swing / Oracle Forms window via the Java Access Bridge |
| `--cdp URL` | Inspect an Electron / CEF app's DOM through the given CDP endpoint (`http://host:port`) |
| `--delphi` | Enumerate a Delphi / VCL (Lazarus) app's forms and TComponent tree via UIA |
| `--mainframe HOST:PORT` | Inspect a mainframe screen (host:port); use with `--session-type` |
| `--session-type {3270,5250}` | Mainframe session type for `--mainframe` (default: `3270`) |

Examples:

```bash
dolphin spy --window "Notepad" --depth 3
dolphin spy --window "Notepad" --json
dolphin spy --pick
dolphin spy --image-pick --output-dir templates
```

Use `--pick` to select an element interactively. Use `--image-pick` to capture a template PNG for image fallback.

## `dolphin record`

```text
dolphin record --output FILE [--app TITLE] [--backend {uia,win32}] [--func-name NAME]
```

Examples:

```bash
dolphin record --output tests/test_recorded.py
dolphin record --output tests/test_login.py --app "Login" --func-name test_login_workflow
```

The recorder prints a generated pytest function. Press `Ctrl+F12` to stop recording.

## `dolphin trace list`

```bash
dolphin trace list
dolphin trace list --last 50
dolphin trace list --dir dolphin-traces
```

Options:

| Option | Default | Description |
| --- | --- | --- |
| `--last N` | `20` | Number of recent runs to show |
| `--dir PATH` | `dolphin-traces` | Trace directory |

## `dolphin trace view`

```bash
dolphin trace view --last
dolphin trace view RUN_ID
dolphin trace view --last --dir dolphin-traces
```

`trace view` generates an HTML viewer for a trace run and opens it with the default browser.

## `dolphin selfheal-stats`

```bash
dolphin selfheal-stats
dolphin selfheal-stats --last 20
dolphin selfheal-stats --file .dolphin-selfheal.jsonl
```

Options:

| Option | Default | Description |
| --- | --- | --- |
| `--last N` | `10` | Number of recent events |
| `--file PATH` | `.dolphin-selfheal.jsonl` | Telemetry log to read |

## `dolphin info backends`

```bash
dolphin info backends
```

Lists registered backend IDs, platforms, availability, source, and description.

## `dolphin-run`

```text
dolphin-run <command> [args...]
```

Examples:

```bash
dolphin-run pytest tests/
dolphin-run pytest tests/ -k test_notepad --dolphin-backend=uia
```

`dolphin-run` creates a hidden Windows desktop named `DolphinHidden`, sets `DOLPHIN_HEADLESS=1` for the child process, runs the command there, waits for it to finish, and returns the child exit code.
