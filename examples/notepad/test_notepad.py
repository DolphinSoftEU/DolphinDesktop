"""Examples: automating Windows Notepad.

Works on Windows 10 and Windows 11 (single-instance tab model).
"""

import re
import time
import uuid

import pytest

from dolphin_desktop import Desktop, find_pid_by_image_name

pytestmark = pytest.mark.integration


@pytest.fixture
def notepad_file(desktop: Desktop, tmp_path):
    """Use a fresh Notepad process and a file owned by this example."""
    if find_pid_by_image_name("notepad.exe") is not None:
        pytest.skip("Close existing Notepad windows before running this example")
    path = tmp_path / f"dd-{uuid.uuid4().hex[:8]}.txt"
    path.write_text("", encoding="utf-8")
    with desktop.launch(f'notepad.exe "{path}"', wait_for_idle=False) as app:
        win = app.window(class_name="Notepad", title_re=f".*{re.escape(path.stem)}.*", timeout=15)
        yield win, path.name
    deadline = time.monotonic() + 3
    while find_pid_by_image_name("notepad.exe") is not None and time.monotonic() < deadline:
        time.sleep(0.1)


def _editor(win):
    document = win.get_by_role("Document")
    return document if document.exists() else win.get_by_role("Edit")


def test_type_and_read(notepad_file):
    win, _ = notepad_file
    editor = _editor(win)

    editor.click()
    editor.type_text("Hello, Dolphin!")

    assert "Hello, Dolphin!" in editor.text()


def test_clear(notepad_file):
    win, _ = notepad_file
    editor = _editor(win)

    editor.click()
    editor.type_text("some text to clear")
    editor.clear()

    assert editor.text() == ""


def test_multiline(notepad_file):
    win, _ = notepad_file
    editor = _editor(win)

    editor.click()
    editor.type_text("Line 1")
    editor.press_key("{ENTER}")
    editor.type_text("Line 2")

    text = editor.text()
    assert "Line 1" in text
    assert "Line 2" in text


def test_keyboard_shortcut_select_all(notepad_file):
    win, _ = notepad_file
    editor = _editor(win)

    editor.click()
    editor.type_text("select me")
    editor.select_text()  # Ctrl+A

    # Overwrite selected text
    editor.type_text("replaced")
    assert "replaced" in editor.text()
