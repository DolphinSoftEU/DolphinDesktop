"""Examples: automating Windows Notepad.

Works on Windows 10 and Windows 11 (single-instance tab model).
"""

import os
import tempfile

import pytest

from dolphin_desktop import Desktop, Keyboard

pytestmark = pytest.mark.integration


@pytest.fixture
def notepad_file(desktop: Desktop):
    """Launch Notepad with a temp file; yield the Window; clean up."""
    f = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, prefix="dolphin_ex_")
    f.close()
    path, basename = f.name, os.path.basename(f.name)

    app = desktop.launch(f'notepad.exe "{path}"', wait_for_idle=False)
    win = app.window(class_name="Notepad")

    # Click the tab for our file (Windows 11 single-instance)
    try:
        win.locator(control_type="TabItem", title_re=f".*{basename}.*").click()
    except Exception:
        pass

    yield win, basename

    try:
        Keyboard.press("^s")
        import time

        time.sleep(0.2)
        Keyboard.press("^w")
    except Exception:
        pass
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_type_and_read(notepad_file):
    win, _ = notepad_file
    editor = win.get_by_role("Document")

    editor.click()
    editor.type_text("Hello, Dolphin!")

    assert "Hello, Dolphin!" in editor.text()


def test_clear(notepad_file):
    win, _ = notepad_file
    editor = win.get_by_role("Document")

    editor.click()
    editor.type_text("some text to clear")
    editor.clear()

    assert editor.text() == ""


def test_multiline(notepad_file):
    win, _ = notepad_file
    editor = win.get_by_role("Document")

    editor.click()
    editor.type_text("Line 1")
    editor.press_key("{ENTER}")
    editor.type_text("Line 2")

    text = editor.text()
    assert "Line 1" in text
    assert "Line 2" in text


def test_keyboard_shortcut_select_all(notepad_file):
    win, _ = notepad_file
    editor = win.get_by_role("Document")

    editor.click()
    editor.type_text("select me")
    editor.select_text()  # Ctrl+A

    # Overwrite selected text
    editor.type_text("replaced")
    assert "replaced" in editor.text()
