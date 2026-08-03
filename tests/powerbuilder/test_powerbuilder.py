"""PowerBuilder backend tests.

PowerBuilder 2019+ (Appeon runtime) exposes standard UIA properties,
and ``Desktop.launch_powerbuilder`` is a semantic wrapper over ``launch()``.
That splits the coverage in two:

* The wrapper's full code path (launch -> window -> locators -> type ->
  read back -> attach) is exercised against a plain UIA application that is
  always present on Windows — it runs everywhere, without a PB licence.
* PB-specific behaviour (DataWindow grids, PB window classes ``FNWND*`` /
  ``W-*``) can only be proven against a real PB executable — those tests
  are opt-in via the ``DOLPHIN_PB_APP`` environment variable and skip
  otherwise.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, env_var

pytestmark = pytest.mark.integration


@pytest.fixture
def pb_launched_app():
    """App launched through the PowerBuilder path, killed on teardown."""
    desktop = Desktop()
    app = desktop.launch_powerbuilder("notepad.exe", timeout=15)
    try:
        win = app.window(class_name="Notepad", timeout=15)
        win.wait_until_ready(timeout=10)
        yield app, win
    finally:
        try:
            app.kill()
        except Exception:
            pass


def test_pb_launch_produces_visible_window(pb_launched_app):
    _app, win = pb_launched_app
    assert win.is_visible()
    assert win.exists()
    assert win.title() != ""


def test_pb_type_and_read_back(pb_launched_app):
    """Type into the editor through UIA and read the value back.

    Win11 Notepad's editor is a Document/RichEdit control, the same UIA
    shape a PB MultiLineEdit exposes — text() goes through the clipboard
    fallback exactly as it would for a PB control that returns empty
    window_text().
    """
    _app, win = pb_launched_app
    editor = win.locator(control_type="Document")
    editor.type_text("PB smoke 123test123")
    # No wait_for_text here: text() on a Document control reads via the
    # select-all + clipboard fallback, which is destructive — polling it
    # while trailing keystrokes are still in the input queue interleaves
    # Ctrl+A with the typed characters. One read after type_text returns
    # is race-free.
    value = editor.text()
    assert "123test123" in value, f"expected typed text in editor, got {value!r}"


def test_pb_attach_to_running_process(pb_launched_app):
    """A second Desktop attaches to the running process by PID.

    This is the standard scenario for long-lived PB clients: the app is
    already open on the operator's desk and the test connects to it
    instead of launching its own copy.
    """
    app, win = pb_launched_app
    attached = Desktop().connect(process=app.process_id, timeout=10)
    win2 = attached.window(class_name="Notepad", timeout=10)
    assert win2.is_visible()
    assert win2.title() == win.title()


def test_pb_window_ops(pb_launched_app):
    """maximize/restore/focus must round-trip on a PB-path window."""
    _app, win = pb_launched_app
    win.maximize()
    win.restore()
    win.focus()
    assert win.is_active()


# ---------------------------------------------------------------------------
# Real-PowerBuilder tests — opt-in, need an actual PB executable
# ---------------------------------------------------------------------------

_PB_APP = env_var("DOLPHIN_PB_APP")
_PB_PID = env_var("DOLPHIN_PB_PID")

skip_no_pb_pid = pytest.mark.skipif(
    not _PB_PID,
    reason="set DOLPHIN_PB_PID to the PID of a running PowerBuilder app",
)


@skip_no_pb_pid
def test_pb_real_attach_readonly():
    """Attach to a live PB app and verify UIA exposure — read-only.

    Safe to run on an operator's desktop: no clicks, no typing, no focus
    stealing. Verified against Appeon's ModernUI demo (PowerClient): the
    main FNWND3 window, standard Button/Edit controls resolve; DataWindow
    (class ``pbdw``) resolves as an opaque Pane. Note that repeated classes
    like ``pbdw`` need ``found_index`` — on a bare class_name match
    exists() answers True and actions raise AmbiguousMatchError.
    """
    app = Desktop().connect(process=int(_PB_PID), timeout=10)
    win = app.window(title_re=".+", timeout=10)
    assert win.is_visible()
    assert win.title() != ""


skip_no_pb = pytest.mark.skipif(
    not _PB_APP,
    reason="set DOLPHIN_PB_APP to a PowerBuilder exe path to run real-PB tests",
)


@skip_no_pb
def test_pb_real_app_launches_and_exposes_uia():
    """Launch the real PB app and verify its main window speaks UIA."""
    desktop = Desktop()
    app = desktop.launch_powerbuilder(_PB_APP, timeout=30)
    try:
        win = app.window(title_re=".+", timeout=30)
        win.wait_until_ready(timeout=15)
        assert win.is_visible()
        assert win.title() != ""
    finally:
        app.kill()


@skip_no_pb
def test_pb_real_app_controls_reachable():
    """At least one interactive control of the real PB app resolves via UIA.

    Appeon PB 2019+ maps buttons/edits to standard UIA control types; if
    nothing resolves, the app is likely classic (pre-Appeon) PB and needs
    the ImageLocator fallback documented in docs/guides/powerbuilder.md.
    """
    desktop = Desktop()
    app = desktop.launch_powerbuilder(_PB_APP, timeout=30)
    try:
        win = app.window(title_re=".+", timeout=30)
        win.wait_until_ready(timeout=15)
        found = any(
            win.locator(control_type=ct).exists()
            for ct in ("Button", "Edit", "Document", "Table", "DataGrid", "Pane")
        )
        assert found, "no standard UIA control types resolved — classic PB?"
    finally:
        app.kill()
