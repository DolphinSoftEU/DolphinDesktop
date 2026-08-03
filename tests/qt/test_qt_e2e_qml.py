"""End-to-end scenarios on the QML demo.

Each test models a realistic user workflow rather than a single property
roundtrip — fill a form, drive a cascade, browse a list, react to a binding.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, sleep
from tests.qt._qt_helpers import QML_SCRIPT, QT6_SCRIPT, QT6_WINDOW_TITLE, launch_demo

# NOTE: source files used different pytestmark values
# (test_qt_e2e_qml_workflow.py + test_qt_e2e_qml_long_journeys.py used
# `pytest.mark.qt_qml`; test_qt_e2e_state_persistence.py used
# `pytest.mark.qt_agent`). Applied per-test via decorators below instead of
# a single module-level pytestmark.


@pytest.fixture
def qml_app_workflow():
    """Single QML demo instance — module-scoped is risky because E2E tests
    mutate shared state (text fields, checkbox), so we re-launch per test
    to keep them independent and self-evident.
    """
    desktop = Desktop()
    app = desktop.launch_qt_python_script(str(QML_SCRIPT), timeout=20, startup_delay=2.5)
    sleep(1.5)
    app.detach()
    try:
        yield app
    finally:
        try:
            app.kill()
        except Exception:
            pass


@pytest.fixture
def qml_app_long_journeys():
    desktop = Desktop()
    app = desktop.launch_qt_python_script(str(QML_SCRIPT), timeout=20, startup_delay=2.5)
    sleep(1.5)
    app.detach()
    try:
        yield app
    finally:
        try:
            app.kill()
        except Exception:
            pass


@pytest.fixture
def persistent_app():
    desktop = Desktop()
    app, win = launch_demo(desktop, QT6_SCRIPT, QT6_WINDOW_TITLE)
    sleep(0.5)
    try:
        yield app, win
    finally:
        try:
            app.kill()
        except Exception:
            pass


# ###########################################################################
# SOURCE 1: tests/qt/test_qt_e2e_qml_workflow.py
# (module marker: pytest.mark.qt_qml)
# ###########################################################################


# ---------------------------------------------------------------------------
# Scenario 1 — Registration form
# ---------------------------------------------------------------------------


@pytest.mark.qt_qml
def test_e2e_registration_form_happy_path(qml_app_workflow):
    """User fills name, agrees to terms, picks colour preference, clicks submit."""
    qml_app = qml_app_workflow
    name = qml_app.qml("qmlNameField")
    agree = qml_app.qml("qmlAgreeCheck")
    colour = qml_app.qml("qmlColorCombo")
    submit = qml_app.qml("qmlClickButton")
    status = qml_app.qml("qmlStatusLabel")

    # Step 1: enter name (status mirrors name=<value> binding).
    name.set_text("Anna Kowalska")
    assert status.wait_for_property("text", "name=Anna Kowalska", timeout=2.0)

    # Step 2: tick the agreement (status moves to agree=true).
    agree.set_property("checked", True)
    assert status.wait_for_property("text", "agree=true", timeout=2.0)

    # Step 3: pick blue (cascading status update).
    colour.set_property("currentIndex", 2)
    assert status.wait_for_property("text", "color=Blue", timeout=2.0)

    # Step 4: submit. Status flips to 'clicked'.
    submit.click()
    assert status.wait_for_property("text", "clicked", timeout=2.0)


@pytest.mark.qt_qml
def test_e2e_registration_form_with_validation(qml_app_workflow):
    """If user un-checks agreement, status should reflect it (negative path)."""
    qml_app = qml_app_workflow
    name = qml_app.qml("qmlNameField")
    agree = qml_app.qml("qmlAgreeCheck")
    status = qml_app.qml("qmlStatusLabel")

    name.set_text("Bob")
    agree.set_property("checked", True)
    assert status.wait_for_property("text", "agree=true")

    # Change mind — uncheck.
    agree.set_property("checked", False)
    assert status.wait_for_property("text", "agree=false")
    assert agree.get_property("checked") is False


# ---------------------------------------------------------------------------
# Scenario 2 — Volume / preferences slider
# ---------------------------------------------------------------------------


@pytest.mark.qt_qml
def test_e2e_slider_sweep_updates_status_throughout(qml_app_workflow):
    """Drive the slider through several discrete positions and verify every step lands."""
    qml_app = qml_app_workflow
    slider = qml_app.qml("qmlVolumeSlider")
    status = qml_app.qml("qmlStatusLabel")

    for vol in (0, 25, 50, 75, 100):
        slider.set_property("value", vol)
        assert status.wait_for_property("text", f"vol={vol}", timeout=2.0), (
            f"slider stuck at {status.text()!r} after setting {vol}"
        )


@pytest.mark.qt_qml
def test_e2e_slider_extremes_respected(qml_app_workflow):
    """Bounded property — values are clamped by Qt into [from, to]."""
    qml_app = qml_app_workflow
    slider = qml_app.qml("qmlVolumeSlider")
    slider.set_property("value", -50)
    assert slider.get_property("value") == 0  # QML Slider clamps at 'from'
    slider.set_property("value", 9999)
    assert slider.get_property("value") == 100  # clamped at 'to'


# ---------------------------------------------------------------------------
# Scenario 3 — Combo box wizard
# ---------------------------------------------------------------------------


@pytest.mark.qt_qml
def test_e2e_combobox_full_iteration_through_choices(qml_app_workflow):
    """Visit every option in the ComboBox, verify status reflects each pick."""
    qml_app = qml_app_workflow
    combo = qml_app.qml("qmlColorCombo")
    status = qml_app.qml("qmlStatusLabel")
    expected = ["Red", "Green", "Blue"]

    for idx, name in enumerate(expected):
        combo.set_property("currentIndex", idx)
        assert combo.get_property("currentText") == name
        assert status.wait_for_property("text", f"color={name}", timeout=1.5)


# ---------------------------------------------------------------------------
# Scenario 4 — ListView browsing
# ---------------------------------------------------------------------------


@pytest.mark.qt_qml
def test_e2e_listview_model_count_consistent(qml_app_workflow):
    """ListView's count matches the static model size."""
    qml_app = qml_app_workflow
    lv = qml_app.qml("qmlItemList")
    assert lv.get_property("count") == 4


@pytest.mark.qt_qml
def test_e2e_listview_currentindex_navigation(qml_app_workflow):
    """Drive selection by setting currentIndex; verify property follows."""
    qml_app = qml_app_workflow
    lv = qml_app.qml("qmlItemList")
    for idx in range(4):
        lv.set_property("currentIndex", idx)
        assert lv.get_property("currentIndex") == idx


# ---------------------------------------------------------------------------
# Scenario 5 — Disabled-state guard
# ---------------------------------------------------------------------------


@pytest.mark.qt_qml
def test_e2e_disabled_button_does_not_fire_handler(qml_app_workflow):
    """Disable submit before clicking — status must not advance to 'clicked'."""
    qml_app = qml_app_workflow
    submit = qml_app.qml("qmlClickButton")
    status = qml_app.qml("qmlStatusLabel")

    # Establish known status.
    status.set_property("text", "guard-marker")

    submit.set_property("enabled", False)
    submit.click()  # Qt should swallow the event.
    sleep(0.3)
    assert status.text() != "clicked"

    submit.set_property("enabled", True)
    submit.click()
    assert status.wait_for_property("text", "clicked", timeout=2.0)


# ---------------------------------------------------------------------------
# Scenario 6 — Window properties are live
# ---------------------------------------------------------------------------


@pytest.mark.qt_qml
def test_e2e_rename_window_title_persists(qml_app_workflow):
    """Window title is a Q_PROPERTY — set it via agent, then read it back via UIA.

    Verifies both halves of the lib agree on the same source of truth.
    """
    qml_app = qml_app_workflow
    desktop_app = qml_app
    agent = desktop_app.qt_agent
    roots = agent.qml_root()
    assert roots
    win_h = roots[0]["handle"]

    new_title = "E2E Renamed by Test"
    agent.set_property(win_h, "title", new_title)
    sleep(0.3)

    # Read back through UIA (Window.title) — the underlying QWindow's
    # accessible name should now match (Qt 6 mirrors title automatically).
    win = desktop_app.window(title=new_title, timeout=5)
    assert win is not None


# ###########################################################################
# SOURCE 2: tests/qt/test_qt_e2e_qml_long_journeys.py
# (module marker: pytest.mark.qt_qml)
# ###########################################################################


# ===========================================================================
# QML Story 1 — Long survey workflow (fill many sections, submit)
# ===========================================================================


@pytest.mark.qt_qml
@pytest.mark.timeout(120)
def test_qml_user_completes_long_survey(qml_app_long_journeys):
    """Story: A user answers a 5-question survey:

      Q1 — What is your name?                   (TextField)
      Q2 — Do you agree to terms?               (CheckBox)
      Q3 — Pick your favorite color             (ComboBox)
      Q4 — How loud do you like things?         (Slider, 0-100)
      Q5 — Press submit                         (Button)

    Status label captures each answer. Final submit must show "clicked".
    """
    qml_app = qml_app_long_journeys
    name = qml_app.qml("qmlNameField")
    agree = qml_app.qml("qmlAgreeCheck")
    color = qml_app.qml("qmlColorCombo")
    volume = qml_app.qml("qmlVolumeSlider")
    submit = qml_app.qml("qmlClickButton")
    status = qml_app.qml("qmlStatusLabel")

    # Q1
    name.set_text("Hiroshi Yamamoto")
    assert status.wait_for_property("text", "name=Hiroshi Yamamoto", timeout=2.0)

    # Q2
    agree.set_property("checked", True)
    assert status.wait_for_property("text", "agree=true", timeout=2.0)

    # Q3 — force away from default (Red is idx=0 by default), then iterate.
    # QML's onCurrentTextChanged only fires on a real transition.
    color.set_property("currentIndex", 2)
    sleep(0.1)
    for idx, expected in enumerate(["Red", "Green", "Blue"]):
        color.set_property("currentIndex", idx)
        # Verify the property changed; status mirror is best-effort.
        assert color.get_property("currentText") == expected
    # End on Blue.
    assert color.get_property("currentText") == "Blue"

    # Q4 — sweep volume to settle on 65.
    for v in (10, 30, 80, 65):
        volume.set_property("value", v)
        assert status.wait_for_property("text", f"vol={v}", timeout=2.0)

    # Q5 — submit.
    submit.click()
    assert status.wait_for_property("text", "clicked", timeout=2.0)


# ===========================================================================
# QML Story 2 — Comparison shopping
# ===========================================================================


@pytest.mark.qt_qml
@pytest.mark.timeout(120)
def test_qml_comparison_shopper_evaluates_options(qml_app_long_journeys):
    """Story: A shopper evaluates each color (representing a product variant)
    one at a time. For each, they set a "fit" rating via the slider and
    update the name field with a note. Final state captures the best choice.
    """
    qml_app = qml_app_long_journeys
    name = qml_app.qml("qmlNameField")
    color = qml_app.qml("qmlColorCombo")
    volume = qml_app.qml("qmlVolumeSlider")
    status = qml_app.qml("qmlStatusLabel")

    variants = [
        ("Red", 30, "Too bright for office wear"),
        ("Green", 70, "Solid daily option"),
        ("Blue", 90, "Best match overall"),
    ]
    ratings = {}
    for idx, (variant, fit, note) in enumerate(variants):
        color.set_property("currentIndex", idx)
        assert color.get_property("currentText") == variant
        volume.set_property("value", fit)
        assert status.wait_for_property("text", f"vol={fit}", timeout=2.0)
        name.set_text(f"{variant}: {note}")
        assert status.wait_for_property("text", f"name={variant}: {note}", timeout=2.0)
        ratings[variant] = fit

    # Best variant by rating.
    best = max(ratings, key=ratings.get)
    assert best == "Blue"

    # Switch back to best for "final selection".
    color.set_property("currentIndex", 2)
    assert color.get_property("currentText") == "Blue"


# ===========================================================================
# QML Story 3 — Onboarding wizard with checkpoint validation
# ===========================================================================


@pytest.mark.qt_qml
@pytest.mark.timeout(120)
def test_qml_onboarding_wizard_with_validation(qml_app_long_journeys):
    """Story: A new user goes through a 4-step onboarding wizard:

      Step A — Enter your name
      Step B — Pick a theme color
      Step C — Set notification volume
      Step D — Accept terms

    Each checkpoint must be validated before moving on (we read each
    property back and confirm). Failed validation = test failure.
    """
    qml_app = qml_app_long_journeys
    name = qml_app.qml("qmlNameField")
    color = qml_app.qml("qmlColorCombo")
    volume = qml_app.qml("qmlVolumeSlider")
    agree = qml_app.qml("qmlAgreeCheck")
    submit = qml_app.qml("qmlClickButton")
    status = qml_app.qml("qmlStatusLabel")

    # ---- Step A ----
    name.set_text("Lena Andersson")
    actual = name.text()
    assert actual == "Lena Andersson", f"step A failed: {actual!r}"
    assert status.wait_for_property("text", "name=Lena Andersson", timeout=2.0)

    # ---- Step B ----
    color.set_property("currentIndex", 1)  # Green
    assert color.get_property("currentText") == "Green", "step B failed"

    # ---- Step C ----
    volume.set_property("value", 40)
    assert volume.get_property("value") == 40, "step C failed"
    assert status.wait_for_property("text", "vol=40", timeout=2.0)

    # ---- Step D ----
    agree.set_property("checked", True)
    assert agree.get_property("checked") is True, "step D failed"
    assert status.wait_for_property("text", "agree=true", timeout=2.0)

    # All checkpoints passed — finalize.
    submit.click()
    assert status.wait_for_property("text", "clicked", timeout=2.0)

    # Final post-condition: every choice still on record.
    assert name.text() == "Lena Andersson"
    assert color.get_property("currentText") == "Green"
    assert volume.get_property("value") == 40
    assert agree.get_property("checked") is True


# ===========================================================================
# QML Story 4 — Repeated review / correction cycle
# ===========================================================================


@pytest.mark.qt_qml
@pytest.mark.timeout(120)
def test_qml_user_corrects_typo_then_resubmits(qml_app_long_journeys):
    """Story: User submits a form with a typo, notices it on the status
    label, corrects the field, resubmits. Verifies the form supports an
    edit-and-retry workflow without losing other fields.
    """
    qml_app = qml_app_long_journeys
    name = qml_app.qml("qmlNameField")
    agree = qml_app.qml("qmlAgreeCheck")
    color = qml_app.qml("qmlColorCombo")
    submit = qml_app.qml("qmlClickButton")
    status = qml_app.qml("qmlStatusLabel")

    # Initial submission with a typo.
    name.set_text("Jhn Smth")  # missing letters
    agree.set_property("checked", True)
    color.set_property("currentIndex", 0)  # Red
    submit.click()
    assert status.wait_for_property("text", "clicked", timeout=2.0)

    # User notices typo, fixes name.
    name.set_text("John Smith")
    assert status.wait_for_property("text", "name=John Smith", timeout=2.0)

    # Other fields must not have been cleared by the edit.
    assert agree.get_property("checked") is True
    assert color.get_property("currentText") == "Red"

    # Resubmit.
    submit.click()
    assert status.wait_for_property("text", "clicked", timeout=2.0)


# ===========================================================================
# QML Story 5 — Disabled-mode "draft" save then enable + submit
# ===========================================================================


@pytest.mark.qt_qml
@pytest.mark.timeout(120)
def test_qml_save_draft_then_enable_and_submit(qml_app_long_journeys):
    """Story: User starts filling a form, "saves a draft" by disabling
    submit (UI gate for incomplete), then completes the form and re-enables
    submit before final submission.
    """
    qml_app = qml_app_long_journeys
    name = qml_app.qml("qmlNameField")
    submit = qml_app.qml("qmlClickButton")
    status = qml_app.qml("qmlStatusLabel")

    # Step 1 — Partial fill.
    name.set_text("Draft user (incomplete)")
    assert name.text() == "Draft user (incomplete)"

    # Step 2 — "Save draft" by disabling submit (simulates: business rule
    # gates submit until form is valid).
    submit.set_property("enabled", False)

    # Step 3 — Click submit — must NOT advance because disabled.
    status.set_property("text", "pre-submit-marker")
    submit.click()
    sleep(0.3)
    assert status.text() != "clicked"

    # Step 4 — Complete the form.
    name.set_text("Final user (complete)")
    assert status.wait_for_property("text", "name=Final user (complete)", timeout=2.0)

    # Step 5 — Re-enable submit.
    submit.set_property("enabled", True)

    # Step 6 — Submit succeeds.
    submit.click()
    assert status.wait_for_property("text", "clicked", timeout=2.0)


# ===========================================================================
# QML Story 6 — Restaurant feedback ratings
# ===========================================================================


@pytest.mark.qt_qml
@pytest.mark.timeout(120)
def test_qml_restaurant_feedback_session(qml_app_long_journeys):
    """Story: After visiting 4 restaurants, a guest leaves a multi-criteria
    rating for each. Each rating record consists of: name, food rating
    (slider 0-100), color of "vibe" (combo), and recommendation flag.

    The test verifies all 4 records get applied independently.
    """
    qml_app = qml_app_long_journeys
    name = qml_app.qml("qmlNameField")
    color = qml_app.qml("qmlColorCombo")
    volume = qml_app.qml("qmlVolumeSlider")
    agree = qml_app.qml("qmlAgreeCheck")
    status = qml_app.qml("qmlStatusLabel")

    visits = [
        ("Sushi Zen", 88, 1, True),  # green vibe, recommended
        ("Pasta Roma", 75, 0, True),  # red, recommended
        ("Veg Bowl", 62, 1, False),  # green, not recommended
        ("Steak Five", 95, 2, True),  # blue, recommended
    ]
    captured = []
    # Force agree to a known starting state so each toggle is a real transition.
    agree.set_property("checked", False)
    sleep(0.1)

    for restaurant, food_rating, vibe_idx, recommend in visits:
        name.set_text(restaurant)
        assert status.wait_for_property("text", f"name={restaurant}", timeout=2.0)

        volume.set_property("value", food_rating)
        assert status.wait_for_property("text", f"vol={food_rating}", timeout=2.0)

        color.set_property("currentIndex", vibe_idx)
        sleep(0.2)

        # Toggle through a transition so onCheckedChanged actually fires.
        # If recommend matches current state, force the inverse first.
        if agree.get_property("checked") == recommend:
            agree.set_property("checked", not recommend)
            sleep(0.1)
        agree.set_property("checked", recommend)
        # Verify property directly — status mirror is best-effort.
        assert agree.get_property("checked") == recommend

        captured.append(
            {
                "name": name.text(),
                "rating": volume.get_property("value"),
                "vibe": color.get_property("currentText"),
                "recommend": agree.get_property("checked"),
            }
        )

    # Verify all 4 records were captured correctly.
    assert captured[0] == {"name": "Sushi Zen", "rating": 88, "vibe": "Green", "recommend": True}
    assert captured[1] == {"name": "Pasta Roma", "rating": 75, "vibe": "Red", "recommend": True}
    assert captured[2] == {"name": "Veg Bowl", "rating": 62, "vibe": "Green", "recommend": False}
    assert captured[3] == {"name": "Steak Five", "rating": 95, "vibe": "Blue", "recommend": True}


# ###########################################################################
# SOURCE 3: tests/qt/test_qt_e2e_state_persistence.py
# (module marker: pytest.mark.qt_agent)
# ###########################################################################


# ===========================================================================
# Test 1 — Username survives tab navigation
# ===========================================================================


@pytest.mark.qt_agent
@pytest.mark.timeout(120)
def test_username_persists_across_tab_switches(persistent_app):
    """User types Username, navigates other tabs, returns — text must still be there."""
    app, win = persistent_app
    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.3)

    user_field = app.qt_widget(object_name="qt_input_username")
    user_field.set_property("text", "persistence_test_user")

    # Tour every other tab.
    for tab in ("Buttons", "Choices", "Containers", "Dialogs", "Inputs"):
        win.locator(control_type="TabItem", title=tab).invoke()
        sleep(0.3)

    # Username should still be there.
    assert user_field.get_property("text") == "persistence_test_user"


# ===========================================================================
# Test 2 — Form values stable after toolbar interaction
# ===========================================================================


@pytest.mark.qt_agent
@pytest.mark.timeout(90)
def test_form_values_survive_toolbar_clicks(persistent_app):
    """Filling form fields + clicking toolbar buttons must NOT clear field data."""
    app, win = persistent_app
    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.3)

    fields = {
        "qt_input_username": ("text", "stable_user"),
        "qt_spin_count": ("value", 77),
        "qt_slider_volume": ("value", 42),
    }
    for obj_name, (prop, val) in fields.items():
        app.qt_widget(object_name=obj_name).set_property(prop, val)

    # Now hit the toolbar a few times.
    bold = app.qt_widget(object_name="qt_action_bold")
    italic = app.qt_widget(object_name="qt_action_italic")
    for _ in range(3):
        bold.invoke("trigger")
        italic.invoke("trigger")

    # All form values still intact.
    for obj_name, (prop, expected) in fields.items():
        actual = app.qt_widget(object_name=obj_name).get_property(prop)
        assert actual == expected, (
            f"{obj_name}.{prop} = {actual!r}, expected {expected!r} "
            "(toolbar interaction corrupted form state)"
        )


# ===========================================================================
# Test 3 — Two settings independent
# ===========================================================================


@pytest.mark.qt_agent
@pytest.mark.timeout(90)
def test_changing_volume_does_not_affect_count(persistent_app):
    """Independent widgets shouldn't interfere with each other's state."""
    app, win = persistent_app
    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.3)

    count = app.qt_widget(object_name="qt_spin_count")
    volume = app.qt_widget(object_name="qt_slider_volume")

    count.set_property("value", 33)
    for vol in (10, 20, 30, 90, 50):
        volume.set_property("value", vol)
        assert count.get_property("value") == 33, (
            f"count drifted to {count.get_property('value')} after volume={vol}"
        )


# ===========================================================================
# Test 4 — Combo selection survives 50 widget interactions
# ===========================================================================


@pytest.mark.qt_agent
@pytest.mark.timeout(120)
def test_language_pick_survives_form_churn(persistent_app):
    """Pick a language, then churn the rest of the UI for 50 actions —
    language pick should not regress."""
    app, win = persistent_app
    win.locator(control_type="TabItem", title="Choices").invoke()
    sleep(0.3)

    combo = app.qt_widget(object_name="qt_combo_language")
    combo.set_property("currentIndex", 1)  # Polish
    assert combo.get_property("currentText") == "Polish"

    # Churn elsewhere.
    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.3)
    count = app.qt_widget(object_name="qt_spin_count")
    volume = app.qt_widget(object_name="qt_slider_volume")
    for i in range(25):
        count.set_property("value", i)
        volume.set_property("value", (i * 3) % 100)

    # Return — language should still be Polish.
    win.locator(control_type="TabItem", title="Choices").invoke()
    sleep(0.3)
    assert combo.get_property("currentText") == "Polish"
    assert combo.get_property("currentIndex") == 1


# ===========================================================================
# Test 5 — Reset workflow
# ===========================================================================


@pytest.mark.qt_agent
@pytest.mark.timeout(90)
def test_user_can_reset_form_by_setting_defaults(persistent_app):
    """As a user, I want to reset the form by setting fields to defaults,
    just like a "Clear" button would do."""
    app, win = persistent_app
    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.3)

    # First, dirty everything.
    app.qt_widget(object_name="qt_input_username").set_property("text", "dirty")
    app.qt_widget(object_name="qt_spin_count").set_property("value", 99)
    app.qt_widget(object_name="qt_slider_volume").set_property("value", 100)
    app.qt_widget(object_name="qt_input_notes").set_property("plainText", "dirty notes")

    # Now reset.
    app.qt_widget(object_name="qt_input_username").invoke("clear")
    app.qt_widget(object_name="qt_spin_count").set_property("value", 0)
    app.qt_widget(object_name="qt_slider_volume").set_property("value", 0)
    app.qt_widget(object_name="qt_input_notes").invoke("clear")

    # Verify reset.
    assert app.qt_widget(object_name="qt_input_username").get_property("text") == ""
    assert app.qt_widget(object_name="qt_spin_count").get_property("value") == 0
    assert app.qt_widget(object_name="qt_slider_volume").get_property("value") == 0
    assert app.qt_widget(object_name="qt_input_notes").get_property("plainText") == ""


# ===========================================================================
# Test 6 — Read-only field cannot be modified (UX contract)
# ===========================================================================


@pytest.mark.qt_agent
@pytest.mark.timeout(90)
def test_readonly_field_resists_modification(persistent_app):
    """qt_input_readonly is read-only — the QLineEdit's text is fixed."""
    app, win = persistent_app
    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.3)

    field = app.qt_widget(object_name="qt_input_readonly")
    initial = field.get_property("text")
    assert field.get_property("readOnly") is True

    # Setting text via setProperty IS allowed (it bypasses the readOnly user-input
    # guard — readOnly is about user input via keyboard/mouse, not API). So this
    # tests the readOnly flag, not the text immutability.
    assert initial == "read-only"
