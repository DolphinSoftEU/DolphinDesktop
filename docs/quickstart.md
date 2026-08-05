# Quickstart

This path takes you from a clean Python environment to a generated Notepad test.

## 1. Install

```bash
pip install dolphin-desktop pytest
```

Check the environment:

```bash
dolphin doctor
```

The required dependencies should be installed and UIA access should be available.

## 2. Generate A Project

```bash
dolphin init my-tests
```

When prompted for a template, press Enter to accept `standard`.

For a non-interactive command, use:

```bash
dolphin init my-tests --yes
```

The default scaffold creates:

```text
my-tests/
|-- conftest.py
|-- pyproject.toml
|-- objects/
|   |-- __init__.py
|   `-- notepad_page.py
`-- tests/
    |-- __init__.py
    `-- test_sample.py
```

`conftest.py` is intentionally small. Dolphin's pytest plugin is loaded from the package entry point and provides the `desktop` and `launch` fixtures.

## 3. Run The Sample

```bash
cd my-tests
pytest tests/ -v
```

The generated test launches Notepad, wraps the main window in a Page Object, types text, and reads it back:

```python title="tests/test_sample.py"
import pytest

from objects.notepad_page import NotepadPage

pytestmark = pytest.mark.integration


def test_notepad_with_page_object(launch):
    """Open Notepad via page object; type text, then verify it can be read back."""
    app = launch("notepad.exe")
    win = app.window(class_name="Notepad")
    page = NotepadPage(win)

    page.type_text("Hello, Dolphin!")
    assert "Hello, Dolphin!" in page.read_text()
```

The Page Object created by the scaffold is:

```python title="objects/notepad_page.py"
from __future__ import annotations

from dolphin_desktop import Window


class NotepadPage:
    def __init__(self, window: Window) -> None:
        self._win = window
        self._editor = window.get_by_role("Document")

    def type_text(self, text: str) -> "NotepadPage":
        self._editor.click()
        self._editor.type_text(text)
        return self

    def clear(self) -> "NotepadPage":
        self._editor.clear()
        return self

    def read_text(self) -> str:
        return self._editor.text()
```

## What Happened

1. `launch("notepad.exe")` starts Notepad through the pytest fixture.
2. `app.window(class_name="Notepad")` waits for a matching top-level window.
3. `window.get_by_role("Document")` creates a lazy locator for the editor.
4. `click()`, `type_text()`, and `text()` resolve the locator when called.
5. The `launch` fixture kills the launched application during teardown.

## Next

Continue with [Tutorial: First Test](tutorials/first-test.md) to write the same test by hand, or open the [API Reference](reference/index.md) for generated signatures.
