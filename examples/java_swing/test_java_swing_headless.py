"""Java Swing headless-safe programmatic-action example.

Drives a real compiled Swing demo (``SwingDemo.class``) end-to-end
using only the headless-safe primitives — no mouse cursor, no
keystrokes — so the same test file passes both in a visible desktop
and under ``dolphin-run`` on a hidden desktop.

* ``.invoke()`` on buttons
* ``.toggle()`` on checkboxes
* ``.select()`` on radio buttons
* ``.set_value(text)`` on text fields

To run:

    # Visible mode (dev box)
    pytest examples/java_swing/test_java_swing_headless.py -v

    # Headless mode
    dolphin-run pytest examples/java_swing/test_java_swing_headless.py -v

Skips cleanly when ``SwingDemo.class`` is not present. Rebuild with:

    cd examples/java_swing
    javac SwingDemo.java
"""

from __future__ import annotations

import pytest

from dolphin_desktop import (
    Desktop,
    JavaAccessBridge,
    UnsupportedPatternError,
    dirname,
    path_exists,
    path_join,
)

pytestmark = pytest.mark.integration


_HERE = dirname(__file__)


def _demo_compiled() -> bool:
    return path_exists(path_join(_HERE, "SwingDemo.class"))


@pytest.fixture(scope="module")
def swing_demo():
    """Module-scoped Swing demo — launched once for the whole suite.

    Uses ``Desktop().launch_java`` directly (not the ``launch`` fixture,
    which is function-scoped and would restart the JVM per test).
    Cleans up at module teardown.
    """
    if not _demo_compiled():
        pytest.skip(
            "SwingDemo.class not built — run `javac SwingDemo.java` "
            "inside examples/java_swing/ first"
        )
    if JavaAccessBridge.java_home() is None:
        pytest.skip("Java not found — install a JDK 8+ to run the Swing demo")

    JavaAccessBridge.ensure_enabled()

    desktop = Desktop()
    cmd = f'java -Djava.accessibility=true -cp "{_HERE}" SwingDemo'
    app = desktop.launch_java(cmd, timeout=20, startup_delay=2.0)
    try:
        # Opt out of dolphin's per-test PID reaping — module scope needs it.
        try:
            app.detach()
        except Exception:
            pass
        win = app.window(title="Java Swing Demo", timeout=15)
        yield app, win
    finally:
        try:
            app.kill()
        except Exception:
            pass


def test_set_value_writes_username_without_keystrokes(swing_demo):
    """`set_value` uses JAB `setTextContents` — no keyboard events, works
    when the JVM is not focused."""
    _, win = swing_demo
    field = win.get_by_role("edit", name="UserField")
    field.set_value("alice")
    field.wait_for_text("alice", contains=False, timeout=5)


def test_set_value_replaces_previous_content(swing_demo):
    _, win = swing_demo
    field = win.get_by_role("edit", name="UserField")
    field.set_value("first")
    field.wait_for_text("first", contains=False, timeout=5)
    field.set_value("second")
    field.wait_for_text("second", contains=False, timeout=5)


def test_toggle_flips_checkbox_via_jab(swing_demo):
    """`toggle` uses JAB `doAccessibleActions("toggle" | "click")` — no
    mouse cursor."""
    _, win = swing_demo
    chk = win.get_by_role("check box", name="Remember me")
    initial = chk.is_checked()
    chk.toggle()
    chk.wait_for_checked(checked=not initial, timeout=5)


def test_select_picks_radio_button_without_mouse(swing_demo):
    """`select` on a JRadioButton fires the ButtonGroup's state change
    without any cursor movement."""
    _, win = swing_demo
    win.get_by_role("radio button", name="Premium").select()
    win.get_by_role("radio button", name="Standard").wait_for_checked(checked=False, timeout=5)


def test_invoke_fires_button_default_action(swing_demo):
    """`invoke` on a JButton fires the ActionListener without any mouse
    events."""
    _, win = swing_demo
    status = win.get_by_role("label", name="StatusLabel")

    # Deterministic starting state — Clear button changes status to "Cleared."
    win.get_by_role("push button", name="Clear").invoke()
    status.wait_for_text("Cleared", source="description", timeout=5)

    win.get_by_role("edit", name="UserField").set_value("bob")
    win.get_by_role("push button", name="Sign In").invoke()

    status.wait_for_text("user=bob", source="description", timeout=5)


def test_invoke_clear_button_resets_form(swing_demo):
    _, win = swing_demo
    user = win.get_by_role("edit", name="UserField")
    user.set_value("someone")
    user.wait_for_text("someone", contains=False, timeout=5)

    win.get_by_role("push button", name="Clear").invoke()
    user.wait_for_text("", contains=False, timeout=5)


def test_full_signup_flow_via_programmatic_only(swing_demo):
    """End-to-end workflow using only programmatic (headless-safe)
    methods against a real Swing app."""
    _, win = swing_demo
    status = win.get_by_role("label", name="StatusLabel")

    # Clear via the JButton (invoke — no mouse)
    win.get_by_role("push button", name="Clear").invoke()
    status.wait_for_text("Cleared", source="description", timeout=5)

    # Set text field via JAB setTextContents (no keystrokes).
    # NOTE: PassField is a JPasswordField which JAB publishes under the
    # role "password text" (not in the default _CT_TO_JAB map); skipped
    # here so this scenario stays focused on the core primitives.
    user = win.get_by_role("edit", name="UserField")
    user.set_value("carol")
    user.wait_for_text("carol", contains=False, timeout=5)

    # Toggle the checkbox via TogglePattern / JAB toggle action (no mouse)
    win.get_by_role("check box", name="Remember me").toggle()

    # Select the radio button via SelectionItemPattern / JAB click (no mouse)
    win.get_by_role("radio button", name="Premium").select()

    # Submit via InvokePattern / JAB click (no mouse)
    win.get_by_role("push button", name="Sign In").invoke()

    # Final assertion — wait until the status label carries every
    # expected substring. Programmatic actions are deterministic on
    # the JVM's EventQueue so one wait covers all of them.
    status.wait_for_text("user=carol", source="description", timeout=5)
    final = status.description()
    assert "tier=Premium" in final
    assert "remember=true" in final


def test_invoke_on_label_raises_unsupported_pattern(swing_demo):
    """The status label is a JLabel — no click action, no InvokePattern.
    Must raise a clean UnsupportedPatternError, not silent-fallback to
    click_input (which would crash under dolphin-run)."""
    _, win = swing_demo
    with pytest.raises(UnsupportedPatternError):
        win.get_by_role("label", name="StatusLabel").invoke()


def test_set_value_on_label_raises_unsupported_pattern(swing_demo):
    """The status label is a JLabel — no AccessibleEditableText,
    `setTextContents` fails cleanly.

    (We can't use `.toggle()` on a JButton for this test because
    :meth:`JABLocator.toggle` deliberately falls back to the ``"click"``
    AccessibleAction when the JVM does not publish a separate
    ``"toggle"`` action — many Swing L&Fs collapse the two, and users
    reasonably expect `.toggle()` on any clickable to invert its state.
    Read-only JLabel gives us a clean negative case instead.)"""
    _, win = swing_demo
    with pytest.raises(UnsupportedPatternError):
        win.get_by_role("label", name="StatusLabel").set_value("nope")
