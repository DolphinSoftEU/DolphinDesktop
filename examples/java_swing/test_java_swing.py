"""Examples: Java Swing application automation.

Drives ``SwingDemo.java`` (in this directory) through the Java Access
Bridge: the app is started with the pytest plugin's ``launch`` fixture
and its controls are located by JAB role + accessible name.
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
    """Fill both fields, press Sign In, read the status label back."""
    win = swing_app.window(class_name="SunAwtFrame")

    win.get_by_role("text", name="UserField").type_text("admin")
    win.get_by_role("password text", name="PassField").type_text("secret")
    win.get_by_role("push button", name="Sign In").click()

    msg = win.get_by_role("label", name="StatusLabel").text()
    assert "Signed in" in msg
    assert "user=admin" in msg


def test_list_selection(swing_app):
    """Select an entry in the Hobbies JList.

    The demo has no label mirroring the selection, so this covers the
    select call itself.
    """
    win = swing_app.window(class_name="SunAwtFrame")

    lst = win.get_by_role("list", name="HobbiesList")
    lst.select_item("Cycling")


def test_check_box(swing_app):
    """The Remember-me checkbox toggles both ways."""
    win = swing_app.window(class_name="SunAwtFrame")

    chk = win.get_by_role("check box", name="Remember me")
    chk.check()
    assert chk.is_checked()

    chk.uncheck()
    assert not chk.is_checked()
