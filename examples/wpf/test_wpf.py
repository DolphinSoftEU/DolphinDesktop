"""Examples: WPF application automation.

Demonstrates AutomationId-based selectors, combo box, list, and check box.
Build WpfSampleApp with build.ps1 first. To use your own application, set
DOLPHIN_WPF_APP and adapt the sample selectors.
"""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def app(launch):
    command = os.environ.get("DOLPHIN_WPF_APP")
    if not command:
        executable = (
            Path(__file__).parent
            / "WpfSampleApp"
            / "bin"
            / "Debug"
            / "net10.0-windows"
            / "WpfSampleApp.exe"
        )
        if not executable.is_file():
            pytest.skip("Build the WPF sample first: ./examples/wpf/build.ps1 (.NET 10 SDK)")
        command = f'"{executable}"'
    return launch(command, timeout=15)


def test_fill_form(app):
    win = app.window(title_re=".*Customer.*")

    # Text fields — AutomationId is the most stable WPF selector
    win.get_by_automation_id("txtFirstName").type_text("Jane")
    win.get_by_automation_id("txtLastName").type_text("Doe")
    win.get_by_automation_id("txtEmail").type_text("jane@example.com")

    # ComboBox
    win.get_by_automation_id("cmbStatus").select_item("Active")

    # CheckBox
    win.get_by_automation_id("chkNewsletter").check()

    # Save
    win.get_by_automation_id("btnSave").click()

    # Verify
    status = win.get_by_automation_id("lblStatus").text()
    assert "Saved" in status


def test_list_items(app):
    win = app.window(title_re=".*Product List.*")

    # Get all list items
    items = win.get_by_role("List").locator(control_type="ListItem").all()
    assert len(items) > 0

    # Click the first item
    items[0].click()
    detail = win.get_by_automation_id("lblSelected").text()
    assert detail == items[0].text()


def test_tree_navigation(app):
    win = app.window(title_re=".*File Browser.*")

    tree = win.get_by_role("Tree")

    # Expand a node
    node = tree.locator(control_type="TreeItem", title="Documents")
    node.expand()

    # Find a child
    child = node.locator(control_type="TreeItem", title="Reports")
    child.select()

    win.get_by_automation_id("lblCurrentPath").wait_for_text(
        "Documents\\Reports", contains=False, timeout=5
    )
