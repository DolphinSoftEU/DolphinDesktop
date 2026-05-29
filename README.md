# Dolphin Desktop

Windows desktop test automation with lazy locators, auto-waiting, and pytest integration.

Dolphin Desktop helps Python tests launch, find, and interact with Windows desktop applications through Microsoft UI Automation, Win32 automation, image matching, and selected integrations such as Java Access Bridge and Office COM.

```bash
pip install dolphin-desktop
```

```python
from dolphin_desktop import Desktop

desktop = Desktop(backend="uia")
app = desktop.launch("notepad.exe")
win = app.window(class_name="Notepad")

editor = win.get_by_role("Document")
editor.click()
editor.type_text("Hello, Dolphin!")

assert "Hello, Dolphin!" in editor.text()

app.kill()
```

## What It Provides

- Lazy locators that resolve only when an action or query runs.
- Auto-waiting for element actions and assertions.
- A pytest plugin with `desktop` and `launch` fixtures.
- UIA and Win32 backends for Windows desktop applications.
- Image-based fallback selectors with the `vision` extra.
- Trace, screenshot, video, recorder, spy, and project scaffold commands.

## Quickstart

```bash
pip install dolphin-desktop pytest
dolphin doctor
dolphin init my-tests --yes
cd my-tests
pytest tests/ -v
```

The default scaffold creates a Notepad test and a small Page Object under `objects/`.

## Documentation

The documentation site is built with MkDocs from the `docs/` directory:

```bash
uv run mkdocs serve
```

Key pages:

- `docs/installation.md`
- `docs/quickstart.md`
- `docs/tutorials/first-test.md`
- `docs/reference/index.md`
- `docs/reference/cli.md`

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions.

## Contact

Maintained by DolphinSoft Kamil Gluszek.

- Website: <https://dolphinsoft.pl>
- Email: <kontakt@dolphinsoft.pl>

## License

[Apache License 2.0](LICENSE)
