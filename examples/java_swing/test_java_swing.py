"""Examples: Java Swing application automation.

Demonstrates launch_java, JABLocator roles, and Java Access Bridge.
Requires Java 8+ to be installed.
"""

import pytest

from dolphin_desktop import JavaAccessBridge, dirname, path_exists, path_join

pytestmark = pytest.mark.integration

_HERE = dirname(__file__)


def test_jab_is_available():
    """Verify Java Access Bridge can be enabled."""
    jh = JavaAccessBridge.java_home()
    if jh is None:
        pytest.skip("Java not found — skipping JAB test")
    JavaAccessBridge.ensure_enabled()
    assert JavaAccessBridge.is_enabled()


@pytest.fixture
def swing_app(launch):
    if not path_exists(path_join(_HERE, "SwingDemo.class")):
        pytest.skip(
            "SwingDemo.class not built — run `javac SwingDemo.java` "
            "inside examples/java_swing/ first"
        )
    if JavaAccessBridge.java_home() is None:
        pytest.skip("Java not found")
    return launch(f'java -cp "{_HERE}" SwingDemo', timeout=20)


def test_fill_login_form(swing_app):
    win = swing_app.window(class_name="SunAwtFrame")

    win.get_by_role("text", name="Username").type_text("admin")
    win.get_by_role("password text", name="Password").type_text("secret")
    win.get_by_role("push button", name="Login").click()

    msg = win.get_by_role("label", name="Status").text()
    assert "Welcome" in msg


def test_list_selection(swing_app):
    win = swing_app.window(class_name="SunAwtFrame")

    # Select an item in a JList
    lst = win.get_by_role("list", name="Options")
    lst.select_item("Option B")

    selected = win.get_by_role("label", name="Selected").text()
    assert "Option B" in selected


def test_check_box(swing_app):
    win = swing_app.window(class_name="SunAwtFrame")

    chk = win.get_by_role("check box", name="Remember me")
    chk.check()
    assert chk.is_checked()

    chk.uncheck()
    assert not chk.is_checked()
