"""Examples: WPF application automation.

Demonstrates AutomationId-based selectors, combo box, list, and check box.
Replace 'MyWpfApp.exe' with an actual WPF app path.
"""

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def app(launch):
    return launch("MyWpfApp.exe", timeout=15)


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
    items = win.get_by_role("List").get_by_role("ListItem").all()
    assert len(items) > 0

    # Click the first item
    items[0].click()
    detail = win.get_by_automation_id("lblSelected").text()
    assert detail == items[0].text()


def test_tree_navigation(app):
    win = app.window(title_re=".*File Browser.*")

    tree = win.get_by_role("Tree")

    # Expand a node
    node = tree.get_by_role("TreeItem", name="Documents")
    node.click()

    # Find a child
    child = node.get_by_role("TreeItem", name="Reports")
    child.double_click()

    assert win.get_by_automation_id("lblCurrentPath").text() == "Documents\\Reports"
