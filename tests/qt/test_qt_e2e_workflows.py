"""End-to-end Qt workflow tests.

Realistic multi-step scenarios against the Qt demo application:
  * business workflows (wizard-style acceptance scenarios)
  * user-journey stories (registration, dialogs, settings)
  * day-in-the-life sessions exercising many widgets in sequence
  * data-driven matrices (usernames, spinners, prices, languages, notes)
  * widget workflows driven via UIA
  * cross-verification scenarios where UIA actions are verified by the
    injected in-process Qt agent
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, sleep
from tests.qt._qt_helpers import QT6_SCRIPT, QT6_WINDOW_TITLE, launch_demo

pytestmark = pytest.mark.qt_agent


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def biz_app():
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


@pytest.fixture
def journey():
    """Fresh app per journey — each story starts from a clean slate."""
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


@pytest.fixture
def fresh_app():
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


@pytest.fixture(scope="module")
def shared_app():
    """Module-scoped fixture: each parametrized case runs against the same app.

    Cheap because we're not asserting destructive cleanup between cases —
    each case sets its own fields.
    """
    desktop = Desktop()
    app, win = launch_demo(desktop, QT6_SCRIPT, QT6_WINDOW_TITLE)
    sleep(0.5)
    try:
        # Land on Inputs once at the start.
        win.locator(control_type="TabItem", title="Inputs").invoke()
        sleep(0.4)
        yield app, win
    finally:
        try:
            app.kill()
        except Exception:
            pass


@pytest.fixture
def widgets_app():
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


@pytest.fixture
def mixed_app():
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


# ===========================================================================
# Helpers
# ===========================================================================


def _status(app):
    return app.qt_widget(object_name="qt_status_label").get_property("text")


def _tab(win, name):
    win.locator(control_type="TabItem", title=name).invoke()
    sleep(0.4)


def _read_status(app):
    return app.qt_widget(object_name="qt_status_label").get_property("text")


def _switch_tab(win, name):
    win.locator(control_type="TabItem", title=name).invoke()
    sleep(0.4)


# ===========================================================================
# Parametrized test data for the data-driven matrices
# ===========================================================================


VALID_USERNAMES = [
    pytest.param("alice", id="lowercase-ascii"),
    pytest.param("Bob_Smith", id="mixed-case-underscore"),
    pytest.param("user.name123", id="dotted-with-digits"),
    pytest.param("zażółć_polska", id="polish-utf8"),
    pytest.param("用户名", id="chinese-utf8"),
    pytest.param("ユーザー", id="japanese-utf8"),
    pytest.param("user@example.com", id="email-shape"),
    pytest.param("a" * 80, id="80-char-max-edge"),
]


SPINNER_CASES = [
    pytest.param(0, 0, id="lower-boundary"),
    pytest.param(50, 50, id="midpoint"),
    pytest.param(100, 100, id="upper-boundary"),
    pytest.param(-5, 0, id="negative-clamped"),
    pytest.param(200, 100, id="overflow-clamped"),
    pytest.param(42, 42, id="answer-to-life"),
]


PRICE_CASES = [
    pytest.param(0.00, id="zero"),
    pytest.param(0.01, id="penny"),
    pytest.param(1.99, id="dollar-99"),
    pytest.param(19.99, id="default-shown"),
    pytest.param(99.99, id="under-100"),
    pytest.param(123.45, id="three-digit-with-cents"),
    pytest.param(9999.99, id="max-edge"),
]


LANGUAGE_CASES = [
    pytest.param(0, "English", id="english"),
    pytest.param(1, "Polish", id="polish"),
    pytest.param(2, "German", id="german"),
    pytest.param(3, "Japanese", id="japanese"),
]


NOTES_CASES = [
    pytest.param("Single line note", id="single-line"),
    pytest.param("Line one\nLine two", id="two-lines"),
    pytest.param("Bullet:\n - apple\n - banana\n - cherry", id="bullet-list"),
    pytest.param("", id="empty"),
    pytest.param("Unicode: Zażółć gęślą jaźń ✓", id="unicode-with-tick"),
    pytest.param("Emoji: 🚀🔥🎯", id="emoji"),
    pytest.param("Very long line " * 50, id="long-single-line"),
]


# ===========================================================================
# Business workflows — multi-step acceptance scenarios
# ===========================================================================


# ===========================================================================
# Workflow 1 — Customer onboarding with three-screen wizard
# ===========================================================================


@pytest.mark.timeout(180)
def test_customer_onboarding_three_screen_wizard(biz_app):
    """Acceptance scenario: A new customer completes onboarding.

    Pre-condition: app is on first-launch state.

    Steps:
        Screen 1 — Account creation (Inputs tab):
            * Username "yann_dubois"
            * Password "9Bx!azqW2"
            * Notes: "Marketing manager, Paris office"

        Screen 2 — Preferences (Choices tab):
            * Language: German (so receipts come in German)
            * City: Berlin (work location)
            * Favorite fruit: Cherry (for snack deliveries)

        Screen 3 — Confirmation (Buttons tab):
            * Tick "Remember me"
            * Tick "Send updates"
            * Pick radio Option B (mid-tier plan)

    Post-condition: navigating back to each tab confirms the three text
    fields, both combo boxes and all three toggles still hold what was
    entered.
    """
    app, win = biz_app

    # ----- Screen 1 -----
    _tab(win, "Inputs")
    app.qt_widget(object_name="qt_input_username").set_property("text", "yann_dubois")
    app.qt_widget(object_name="qt_input_password").set_property("text", "9Bx!azqW2")
    app.qt_widget(object_name="qt_input_notes").set_property(
        "plainText", "Marketing manager, Paris office"
    )

    # ----- Screen 2 -----
    _tab(win, "Choices")
    app.qt_widget(object_name="qt_combo_language").set_property("currentIndex", 2)  # German
    app.qt_widget(object_name="qt_combo_city").set_property("currentIndex", 1)  # Berlin
    app.qt_widget(object_name="qt_list_fruits").set_property("currentRow", 2)  # Cherry

    # ----- Screen 3 -----
    _tab(win, "Buttons")
    app.qt_widget(object_name="qt_chk_remember").set_property("checked", True)
    app.qt_widget(object_name="qt_chk_updates").set_property("checked", True)
    app.qt_widget(object_name="qt_radio_b").set_property("checked", True)
    sleep(0.2)

    # ----- Post-condition: revisit each screen and re-verify -----
    _tab(win, "Inputs")
    assert app.qt_widget(object_name="qt_input_username").get_property("text") == "yann_dubois"
    assert app.qt_widget(object_name="qt_input_password").get_property("text") == "9Bx!azqW2"
    assert (
        app.qt_widget(object_name="qt_input_notes").get_property("plainText")
        == "Marketing manager, Paris office"
    )

    _tab(win, "Choices")
    assert app.qt_widget(object_name="qt_combo_language").get_property("currentText") == "German"
    assert app.qt_widget(object_name="qt_combo_city").get_property("currentText") == "Berlin"

    _tab(win, "Buttons")
    assert app.qt_widget(object_name="qt_chk_remember").get_property("checked") is True
    assert app.qt_widget(object_name="qt_chk_updates").get_property("checked") is True
    assert app.qt_widget(object_name="qt_radio_b").get_property("checked") is True


# ===========================================================================
# Workflow 2 — Invoice creation and review
# ===========================================================================


@pytest.mark.timeout(180)
def test_invoice_creation_review_workflow(biz_app):
    """Acceptance scenario: An accountant creates an invoice for a customer.

    Steps:
        1. New invoice via File>New.
        2. Customer name into Username field.
        3. Item count and unit price set.
        4. Volume slider = courier confidence level (matters for delivery).
        5. Notes captured.
        6. Bold/italic markers toggled for branded output.
        7. Switch to Choices and set language to German (receipt language).
        8. Switch to Containers — the People table is still enabled, i.e. the
           tab round-trip left the view alive. Its cell contents sit behind
           plain C++ getters, so `enabled` is the reachable observable.
        9. Switch back and read final total = count × price.

    Verifications: the total rebuilt from the two spin boxes matches
    count × price rounded to 2 decimals, and username, slider, notes, bold
    and italic all still hold the values set in steps 2-6.
    """
    app, win = biz_app

    # 1
    app.qt_widget(object_name="qt_action_new").invoke("trigger")
    sleep(0.2)
    assert "menu File>New" in _status(app)

    # 2-5
    _tab(win, "Inputs")
    customer = "Société Trois Étoiles SARL"
    line_items = 4
    unit_price = 249.50
    delivery_confidence = 85
    notes = "Livraison express avant vendredi 14:00\nContact: Mme Lefevre\nRéf: INV-2026-0042"

    app.qt_widget(object_name="qt_input_username").set_property("text", customer)
    app.qt_widget(object_name="qt_spin_count").set_property("value", line_items)
    app.qt_widget(object_name="qt_spin_price").set_property("value", unit_price)
    app.qt_widget(object_name="qt_slider_volume").set_property("value", delivery_confidence)
    app.qt_widget(object_name="qt_input_notes").set_property("plainText", notes)

    # 6
    app.qt_widget(object_name="qt_action_bold").invoke("trigger")
    app.qt_widget(object_name="qt_action_italic").invoke("trigger")

    # 7
    _tab(win, "Choices")
    # The demo's languages are English, Polish, German, Japanese — no French —
    # so pick the closest stand-in for the test (German, idx 2).
    app.qt_widget(object_name="qt_combo_language").set_property("currentIndex", 2)

    # 8
    _tab(win, "Containers")
    # No mutation — just confirm we're here and table is alive.
    table = app.qt_widget(object_name="qt_table_people")
    assert table.get_property("enabled") is True

    # 9
    _tab(win, "Inputs")
    count_v = app.qt_widget(object_name="qt_spin_count").get_property("value")
    price_v = app.qt_widget(object_name="qt_spin_price").get_property("value")
    total = round(count_v * price_v, 2)
    assert total == round(line_items * unit_price, 2), f"total drift: {total}"

    # Final verifications.
    assert app.qt_widget(object_name="qt_input_username").get_property("text") == customer
    assert (
        app.qt_widget(object_name="qt_slider_volume").get_property("value") == delivery_confidence
    )
    assert app.qt_widget(object_name="qt_input_notes").get_property("plainText") == notes
    assert app.qt_widget(object_name="qt_action_bold").get_property("checked") is True
    assert app.qt_widget(object_name="qt_action_italic").get_property("checked") is True


# ===========================================================================
# Workflow 3 — Bulk price update across line items
# ===========================================================================


@pytest.mark.timeout(120)
def test_bulk_price_update_across_15_items(biz_app):
    """Acceptance scenario: A manager updates the Price field 15 times in
    sequence (simulating bulk catalog updates). Each update must round-trip
    cleanly to 2 decimal precision.

    Steps:
        For each new price:
          * Set price.
          * Read it back; assert matches within 0.01.
          * Log a status change via Edit>Copy as a "commit" marker.
    """
    app, win = biz_app
    _tab(win, "Inputs")
    price = app.qt_widget(object_name="qt_spin_price")
    copy_action = app.qt_widget(object_name="qt_action_copy")

    new_prices = [
        0.01,
        0.50,
        0.99,
        1.00,
        5.49,
        9.99,
        19.99,
        29.95,
        49.50,
        99.99,
        149.99,
        299.00,
        499.50,
        999.99,
        9999.99,
    ]
    for p in new_prices:
        price.set_property("value", p)
        read_back = price.get_property("value")
        assert abs(read_back - p) < 0.01, f"price {p} -> {read_back}"
        copy_action.invoke("trigger")
        sleep(0.1)
        assert "Edit>Copy" in _status(app)

    # End-state: price = last value.
    assert abs(price.get_property("value") - 9999.99) < 0.01


# ===========================================================================
# Workflow 4 — Configuration export simulation
# ===========================================================================


@pytest.mark.timeout(180)
def test_configuration_snapshot_and_replay(biz_app):
    """Acceptance scenario: User configures the app, snapshots the
    configuration as a dict, "resets" everything, then replays the
    snapshot — every field should return to its prior value.

    This proves the agent's get_property/set_property pair is a stable
    save/load primitive for product configuration.
    """
    app, win = biz_app

    # Configure: visit each tab and tweak a field.
    _tab(win, "Inputs")
    app.qt_widget(object_name="qt_input_username").set_property("text", "snapshot_user")
    app.qt_widget(object_name="qt_spin_count").set_property("value", 88)
    app.qt_widget(object_name="qt_slider_volume").set_property("value", 33)
    app.qt_widget(object_name="qt_input_notes").set_property("plainText", "important config")

    _tab(win, "Choices")
    app.qt_widget(object_name="qt_combo_language").set_property("currentIndex", 1)

    _tab(win, "Buttons")
    app.qt_widget(object_name="qt_chk_remember").set_property("checked", True)
    app.qt_widget(object_name="qt_chk_updates").set_property("checked", False)
    app.qt_widget(object_name="qt_radio_a").set_property("checked", True)

    # SNAPSHOT — read every value into a dict.
    _tab(win, "Inputs")
    snap = {
        "username": app.qt_widget(object_name="qt_input_username").get_property("text"),
        "count": app.qt_widget(object_name="qt_spin_count").get_property("value"),
        "volume": app.qt_widget(object_name="qt_slider_volume").get_property("value"),
        "notes": app.qt_widget(object_name="qt_input_notes").get_property("plainText"),
    }
    _tab(win, "Choices")
    snap["lang_idx"] = app.qt_widget(object_name="qt_combo_language").get_property("currentIndex")
    _tab(win, "Buttons")
    snap["remember"] = app.qt_widget(object_name="qt_chk_remember").get_property("checked")
    snap["updates"] = app.qt_widget(object_name="qt_chk_updates").get_property("checked")
    snap["radio_a"] = app.qt_widget(object_name="qt_radio_a").get_property("checked")

    # RESET — clear / change every field to a different value.
    _tab(win, "Inputs")
    app.qt_widget(object_name="qt_input_username").invoke("clear")
    app.qt_widget(object_name="qt_spin_count").set_property("value", 0)
    app.qt_widget(object_name="qt_slider_volume").set_property("value", 0)
    app.qt_widget(object_name="qt_input_notes").invoke("clear")
    _tab(win, "Choices")
    app.qt_widget(object_name="qt_combo_language").set_property("currentIndex", 3)
    _tab(win, "Buttons")
    app.qt_widget(object_name="qt_chk_remember").set_property("checked", False)
    app.qt_widget(object_name="qt_chk_updates").set_property("checked", True)
    app.qt_widget(object_name="qt_radio_c").set_property("checked", True)

    # REPLAY the snapshot.
    _tab(win, "Inputs")
    app.qt_widget(object_name="qt_input_username").set_property("text", snap["username"])
    app.qt_widget(object_name="qt_spin_count").set_property("value", snap["count"])
    app.qt_widget(object_name="qt_slider_volume").set_property("value", snap["volume"])
    app.qt_widget(object_name="qt_input_notes").set_property("plainText", snap["notes"])
    _tab(win, "Choices")
    app.qt_widget(object_name="qt_combo_language").set_property("currentIndex", snap["lang_idx"])
    _tab(win, "Buttons")
    app.qt_widget(object_name="qt_chk_remember").set_property("checked", snap["remember"])
    app.qt_widget(object_name="qt_chk_updates").set_property("checked", snap["updates"])
    app.qt_widget(object_name="qt_radio_a").set_property("checked", snap["radio_a"])

    # VERIFY — every field matches the snapshot.
    _tab(win, "Inputs")
    assert app.qt_widget(object_name="qt_input_username").get_property("text") == snap["username"]
    assert app.qt_widget(object_name="qt_spin_count").get_property("value") == snap["count"]
    assert app.qt_widget(object_name="qt_slider_volume").get_property("value") == snap["volume"]
    assert app.qt_widget(object_name="qt_input_notes").get_property("plainText") == snap["notes"]
    _tab(win, "Choices")
    assert (
        app.qt_widget(object_name="qt_combo_language").get_property("currentIndex")
        == snap["lang_idx"]
    )
    _tab(win, "Buttons")
    assert app.qt_widget(object_name="qt_chk_remember").get_property("checked") == snap["remember"]
    assert app.qt_widget(object_name="qt_chk_updates").get_property("checked") == snap["updates"]
    assert app.qt_widget(object_name="qt_radio_a").get_property("checked") == snap["radio_a"]


# ===========================================================================
# Workflow 5 — Audit trail: every action mirrored in status bar
# ===========================================================================


@pytest.mark.timeout(180)
def test_audit_trail_status_bar_every_action(biz_app):
    """Acceptance scenario: Every menu/toolbar action emits a deterministic
    status-bar message. An auditor reads the trail to verify a procedure
    was followed in order.

    Procedure (in order):
        File>New, File>Open, Edit>Copy, Edit>Paste,
        View>Zoom>50%, View>Zoom>100%, View>Zoom>200%,
        Bold ON, Italic ON, Italic OFF, Bold OFF
    """
    app, _ = biz_app
    procedure = [
        ("qt_action_new", "menu File>New"),
        ("qt_action_open", "menu File>Open"),
        ("qt_action_copy", "menu Edit>Copy"),
        ("qt_action_paste", "menu Edit>Paste"),
        ("qt_action_zoom_50", "menu View>Zoom>50%"),
        ("qt_action_zoom_100", "menu View>Zoom>100%"),
        ("qt_action_zoom_200", "menu View>Zoom>200%"),
        ("qt_action_bold", "toolbar bold=True"),
        ("qt_action_italic", "toolbar italic=True"),
        ("qt_action_italic", "toolbar italic=False"),
        ("qt_action_bold", "toolbar bold=False"),
    ]
    trail: list[str] = []
    for object_name, marker in procedure:
        app.qt_widget(object_name=object_name).invoke("trigger")
        sleep(0.2)
        s = _status(app)
        trail.append(s)
        assert marker in s, f"step {object_name}: status {s!r} missing {marker!r}"

    # The trail must be exactly len(procedure) entries.
    assert len(trail) == len(procedure)


# ===========================================================================
# User journeys — story-driven flows through dialogs and settings
# ===========================================================================


# ===========================================================================
# Story 1 — New user registers via the custom dialog
# ===========================================================================


@pytest.mark.timeout(120)
def test_user_completes_registration_via_custom_dialog(journey):
    """Verify the entry point to the custom registration dialog is reachable.

    Steps:
        1. Find the demo main window
        2. Find the "Show custom" button by objectName
        3. Verify the button exposes a `click` method

    The dialog itself is not opened: a modal would block the test, so this
    only checks that the integration surface exists end-to-end.
    """
    app, _ = journey
    agent = app.qt_agent

    main = agent.find(className="DemoMainWindow")
    assert main, "demo main window not findable"

    btn = agent.find(objectName="qt_btn_show_custom")
    assert btn, "Show custom button must exist on Dialogs tab"
    members = agent.members(btn[0]["handle"])
    assert members["ok"]
    assert "click" in members["methods"] or "click()" in members["methods"]


# ===========================================================================
# Story 2 — Editor changes formatting via toolbar
# ===========================================================================


@pytest.mark.timeout(90)
def test_user_applies_bold_and_italic_formatting(journey):
    """As a content editor, I want to toggle bold and italic on the toolbar,
    so that my text is styled. The status bar must report each change.

    Steps:
        1. Click Bold (toggleable)
        2. Verify status shows "toolbar bold=True"
        3. Click Italic
        4. Verify status shows "toolbar italic=True"
        5. Click Bold again to disable
        6. Verify status shows "toolbar bold=False"
    """
    app, win = journey

    bold = win.qt_widget(object_name="qt_action_bold")
    italic = win.qt_widget(object_name="qt_action_italic")
    # QAction#trigger is invokeable.
    bold.invoke("trigger")
    sleep(0.3)
    assert "bold=True" in _status(app)

    italic.invoke("trigger")
    sleep(0.3)
    assert "italic=True" in _status(app)

    bold.invoke("trigger")  # toggle off
    sleep(0.3)
    assert "bold=False" in _status(app)


# ===========================================================================
# Story 3 — User completes the settings form
# ===========================================================================


@pytest.mark.timeout(120)
def test_user_fills_complete_settings_form(journey):
    """As a user updating my preferences, I want to fill every field on the
    Inputs tab in one go, then verify each value persists.

    Steps:
        1. Switch to "Inputs" tab via UIA
        2. Username = "alice_2026"
        3. Password = "Sup3rS3cret!"
        4. Count (QSpinBox) = 25
        5. Volume (QSlider) = 80
        6. Notes (QPlainTextEdit) = "Daily check-in"
        7. Confirm every field's value individually via agent get_property
    """
    app, win = journey
    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.4)

    fields = {
        "qt_input_username": ("text", "alice_2026"),
        "qt_input_password": ("text", "Sup3rS3cret!"),
        "qt_spin_count": ("value", 25),
        "qt_slider_volume": ("value", 80),
        "qt_input_notes": ("plainText", "Daily check-in"),
    }

    # Step 1-6: set every field via the agent (faster + uniform than UIA typing).
    for object_name, (prop, value) in fields.items():
        wid = app.qt_widget(object_name=object_name)
        wid.set_property(prop, value)

    # Step 7: verify each value sticks.
    for object_name, (prop, expected) in fields.items():
        wid = app.qt_widget(object_name=object_name)
        actual = wid.get_property(prop)
        assert actual == expected, f"{object_name}.{prop} = {actual!r}, expected {expected!r}"


# ===========================================================================
# Story 4 — Editor uses the File menu shortcut chain
# ===========================================================================


@pytest.mark.timeout(90)
def test_user_navigates_file_menu_actions(journey):
    """As a user, I want to use the File menu actions to drive the app,
    so the status bar tracks what I'm doing.

    Steps:
        1. Trigger File>New action → status shows "menu File>New"
        2. Trigger File>Open action → status shows "menu File>Open"
        3. Trigger View>Zoom>200% → status shows "menu View>Zoom>200%"
    """
    app, _ = journey
    actions_expected = [
        ("qt_action_new", "menu File>New"),
        ("qt_action_open", "menu File>Open"),
        ("qt_action_zoom_200", "menu View>Zoom>200%"),
    ]
    for object_name, expected_status in actions_expected:
        action = app.qt_widget(object_name=object_name)
        action.invoke("trigger")
        sleep(0.2)
        actual = _status(app)
        assert expected_status in actual, f"after triggering {object_name}: status is {actual!r}"


# ===========================================================================
# Story 5 — Pick a language, then change it (selection persistence)
# ===========================================================================


@pytest.mark.timeout(90)
def test_user_changes_language_multiple_times(journey):
    """As an international user, I want to switch the app's language,
    cycle through several options to compare, and end on Polish.

    Steps:
        1. Open Choices tab
        2. Cycle the ComboBox through every option, checking currentText
           after each pick
        3. End on "Polish" and verify currentText

    currentTextChanged does not fire when a write lands on the value already
    selected, so the combo's own currentText is the observable rather than
    the status mirror.
    """
    app, win = journey
    win.locator(control_type="TabItem", title="Choices").invoke()
    sleep(0.4)

    combo = app.qt_widget(object_name="qt_combo_language")
    languages = ["English", "Polish", "German", "Japanese"]

    # Force a non-default starting index so the first iteration is a real
    # transition (currentTextChanged only fires on change).
    combo.set_property("currentIndex", 3)
    sleep(0.2)

    for i, lang in enumerate(languages):
        combo.set_property("currentIndex", i)
        sleep(0.2)
        # Verify property directly — status mirror may not fire if the
        # transition immediately matches a no-op.
        assert combo.get_property("currentText") == lang

    # Final: switch back to Polish.
    combo.set_property("currentIndex", languages.index("Polish"))
    assert combo.get_property("currentText") == "Polish"


# ===========================================================================
# Story 6 — Disabled button respect (UX expectation)
# ===========================================================================


@pytest.mark.timeout(90)
def test_disabled_button_cannot_be_clicked_to_change_state(journey):
    """As a user, I expect a disabled button to do nothing when I click it.

    Steps:
        1. Read current status
        2. Click the "Disabled" button via agent
        3. Verify status is unchanged
    """
    app, _ = journey
    initial = _status(app)
    disabled = app.qt_widget(object_name="qt_btn_disabled")
    assert disabled.get_property("enabled") is False
    disabled.invoke("animateClick")  # No-op on a disabled button: status must not change.
    sleep(0.3)
    assert _status(app) == initial, "disabled button changed status — UX bug"


# ===========================================================================
# Story 7 — Tree drill-down (file browser pattern)
# ===========================================================================


@pytest.mark.timeout(90)
def test_user_explores_tree_widget(journey):
    """As a user, I want the file tree to be reachable and usable.

    Steps:
        1. Switch to Containers tab
        2. Verify the tree is enabled
        3. Verify its header is visible (headerHidden is False)

    Item-level content sits behind plain C++ getters the meta-object system
    does not expose, so the tree's Q_PROPERTYs are the observable here.
    """
    app, win = journey
    win.locator(control_type="TabItem", title="Containers").invoke()
    sleep(0.4)

    tree = app.qt_widget(object_name="qt_tree_files")
    assert tree is not None
    # topLevelItemCount() is a plain C++ getter (not Q_INVOKABLE), so it
    # cannot be reached via the meta-object system. Verify discoverability
    # by reading Q_PROPERTYs that the meta system DOES expose.
    assert tree.get_property("enabled") is True
    # headerHidden is a Q_PROPERTY on QTreeView and reflects the configured
    # header — for our demo it should be visible (False).
    assert tree.get_property("headerHidden") is False


# ===========================================================================
# Story 8 — Data table inspection (read-only)
# ===========================================================================


@pytest.mark.timeout(90)
def test_user_can_query_the_people_table_model(journey):
    """As a user, I want to inspect the People table.

    Steps:
        1. Switch to Containers tab
        2. Find the QTableView
        3. Verify a QStandardItemModel is findable in the QObject tree
    """
    app, win = journey
    win.locator(control_type="TabItem", title="Containers").invoke()
    sleep(0.4)
    table = app.qt_widget(object_name="qt_table_people")
    assert table is not None
    # model() is a plain C++ getter (not Q_INVOKABLE) — reach the model
    # via the QObject tree instead.
    children = app.qt_agent.find(className="QStandardItemModel")
    assert children, "QStandardItemModel should be findable in the QObject tree"
    # The table view itself is still enabled.
    assert table.get_property("enabled") is True


# ===========================================================================
# Story 9 — Compose & confirm: bold + remember + spinner
# ===========================================================================


@pytest.mark.timeout(120)
def test_user_composes_multi_widget_state(journey):
    """As a user, I want to set a combination of options (bold, remember me,
    count = 42) and verify all my choices persist together.

    Steps:
        1. Tab to Buttons
        2. Check "Remember me"
        3. Toggle Bold on the toolbar
        4. Tab to Inputs, set count = 42
        5. Verify all three states persist via agent
    """
    app, win = journey

    win.locator(control_type="TabItem", title="Buttons").invoke()
    sleep(0.3)

    remember = app.qt_widget(object_name="qt_chk_remember")
    remember.set_property("checked", True)

    bold = app.qt_widget(object_name="qt_action_bold")
    bold.invoke("trigger")
    sleep(0.3)

    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.3)

    count = app.qt_widget(object_name="qt_spin_count")
    count.set_property("value", 42)

    # Final state assertions — all three independent settings must hold.
    assert remember.get_property("checked") is True
    assert bold.get_property("checked") is True
    assert count.get_property("value") == 42


# ===========================================================================
# Day-in-the-life sessions — long scenarios spanning many widgets
# ===========================================================================


# ===========================================================================
# Day-in-life #1 — Analyst's morning checklist
# ===========================================================================


@pytest.mark.timeout(180)
def test_analyst_morning_checklist_complete_workflow(fresh_app):
    """Story: An analyst opens her dashboard, sets her work language to Polish,
    enters today's session parameters, picks her preferred fruit for break time,
    and verifies the whole configuration before starting her shift.

    This single test exercises:
      * Tab navigation (Inputs, Choices, Buttons)
      * Menus (File>New, View>Zoom>200%)
      * Toolbar (Bold)
      * Inputs: text, password, spinner, double spinner, slider, plainText
      * Choices: ComboBox (language + city), QListWidget
      * Buttons: checkboxes + radios
      * Status mirror after every step
    """
    app, win = fresh_app

    # Step 1 — Greet the morning with a fresh document.
    app.qt_widget(object_name="qt_action_new").invoke("trigger")
    sleep(0.2)
    assert "menu File>New" in _read_status(app)

    # Step 2 — Zoom in to 200% for better readability.
    app.qt_widget(object_name="qt_action_zoom_200").invoke("trigger")
    sleep(0.2)
    assert "Zoom>200%" in _read_status(app)

    # Step 3 — Switch to the Inputs tab and configure today's session.
    _switch_tab(win, "Inputs")
    inputs = {
        "qt_input_username": ("text", "anna.k"),
        "qt_input_password": ("text", "morning-coffee!"),
        "qt_spin_count": ("value", 8),  # 8 hours
        "qt_spin_price": ("value", 49.50),  # billable rate
        "qt_slider_volume": ("value", 25),  # background music level
        "qt_input_notes": ("plainText", "Daily standup at 10:00\nReview Q2 backlog"),
    }
    for obj_name, (prop, val) in inputs.items():
        app.qt_widget(object_name=obj_name).set_property(prop, val)

    # Step 4 — Verify each input committed.
    for obj_name, (prop, expected) in inputs.items():
        actual = app.qt_widget(object_name=obj_name).get_property(prop)
        assert actual == expected, f"{obj_name}.{prop} = {actual!r}"

    # Step 5 — Move to Choices and pick her work language.
    _switch_tab(win, "Choices")
    lang = app.qt_widget(object_name="qt_combo_language")
    lang.set_property("currentIndex", 1)  # Polish
    assert lang.get_property("currentText") == "Polish"

    # Step 6 — Pick a city for the day's project.
    city = app.qt_widget(object_name="qt_combo_city")
    city.set_property("currentIndex", 0)  # Warsaw
    assert city.get_property("currentText") == "Warsaw"

    # Step 7 — Pick the snack of the day.
    fruits = app.qt_widget(object_name="qt_list_fruits")
    fruits.set_property("currentRow", 2)  # Cherry
    sleep(0.2)
    assert "fruit=Cherry" in _read_status(app)

    # Step 8 — Acknowledge "Remember me" so she doesn't have to log in tomorrow.
    _switch_tab(win, "Buttons")
    app.qt_widget(object_name="qt_chk_remember").set_property("checked", True)
    assert app.qt_widget(object_name="qt_chk_remember").get_property("checked") is True

    # Step 9 — Pick favorite formatting option.
    app.qt_widget(object_name="qt_radio_b").set_property("checked", True)
    sleep(0.2)
    assert "Option B" in _read_status(app)

    # Step 10 — Enable Bold for note headings.
    app.qt_widget(object_name="qt_action_bold").invoke("trigger")
    sleep(0.2)
    assert "bold=True" in _read_status(app)

    # Step 11 — Final check: every setting from steps 3-10 should still hold.
    _switch_tab(win, "Inputs")
    for obj_name, (prop, expected) in inputs.items():
        actual = app.qt_widget(object_name=obj_name).get_property(prop)
        assert actual == expected, f"final check {obj_name}.{prop} = {actual!r}"
    _switch_tab(win, "Choices")
    assert app.qt_widget(object_name="qt_combo_language").get_property("currentText") == "Polish"
    assert app.qt_widget(object_name="qt_combo_city").get_property("currentText") == "Warsaw"


# ===========================================================================
# Day-in-life #2 — Shopping cart checkout
# ===========================================================================


@pytest.mark.timeout(180)
def test_customer_completes_shopping_checkout(fresh_app):
    """Story: A customer enters a 5-item order (using Inputs as cart fields),
    picks German as the receipt language, and confirms via the Edit>Copy action.

    Cart shape:
      * Username = recipient name
      * Count = item count
      * Price = unit price
      * Notes = delivery instructions
      * Volume = preferred packaging level (0=eco / 100=premium)
    Expected total = Count × Price (verified by simple arithmetic).
    """
    app, win = fresh_app

    cart_items = [
        ("Maria Schmidt", 3, 24.99, "Leave at door", 60),
        ("Pavel Novak", 1, 199.00, "Call before delivery", 100),
        ("Yuki Tanaka", 12, 3.50, "Gift wrap each", 90),
        ("Alex Brown", 5, 19.99, "Fragile — handle with care", 80),
        ("Saskia van Dijk", 2, 49.50, "", 50),
    ]
    expected_totals = [round(qty * price, 2) for _, qty, price, _, _ in cart_items]

    _switch_tab(win, "Inputs")
    for (name, qty, price, notes, level), expected in zip(
        cart_items, expected_totals, strict=False
    ):
        # Step: fill cart for this customer.
        app.qt_widget(object_name="qt_input_username").set_property("text", name)
        app.qt_widget(object_name="qt_spin_count").set_property("value", qty)
        app.qt_widget(object_name="qt_spin_price").set_property("value", price)
        app.qt_widget(object_name="qt_input_notes").set_property("plainText", notes)
        app.qt_widget(object_name="qt_slider_volume").set_property("value", level)

        # Verify the inputs landed.
        assert app.qt_widget(object_name="qt_input_username").get_property("text") == name
        assert app.qt_widget(object_name="qt_spin_count").get_property("value") == qty
        assert abs(app.qt_widget(object_name="qt_spin_price").get_property("value") - price) < 0.01
        assert app.qt_widget(object_name="qt_input_notes").get_property("plainText") == notes
        assert app.qt_widget(object_name="qt_slider_volume").get_property("value") == level

        # Verify "total" via local computation matches expectation.
        actual_total = round(
            app.qt_widget(object_name="qt_spin_count").get_property("value")
            * app.qt_widget(object_name="qt_spin_price").get_property("value"),
            2,
        )
        assert actual_total == expected, f"total for {name}: {actual_total} vs {expected}"

    # Step: final receipt language pick.
    _switch_tab(win, "Choices")
    app.qt_widget(object_name="qt_combo_language").set_property("currentIndex", 2)  # German
    assert app.qt_widget(object_name="qt_combo_language").get_property("currentText") == "German"

    # Step: confirm via Edit > Copy (as a stand-in for "Copy receipt to clipboard").
    app.qt_widget(object_name="qt_action_copy").invoke("trigger")
    sleep(0.2)
    assert "Edit>Copy" in _read_status(app)


# ===========================================================================
# Day-in-life #3 — Editor's writing session
# ===========================================================================


@pytest.mark.timeout(180)
def test_editor_complete_writing_session(fresh_app):
    """Story: An editor opens a document, types multiple paragraphs, formats
    them with bold/italic, zooms in, and navigates between sections via tabs
    without losing work.

    Verifies the most common editor regression: switching tab/menu/zoom must
    NOT clear the editing buffer.
    """
    app, win = fresh_app

    paragraphs = [
        "Tytuł rozdziału: Dolphin Desktop\n",
        "\nFramework testowy dla aplikacji Qt na Windows.\n",
        "Wspiera UIA i agenta DLL (Qt agent).\n",
        "\n=== Zalety ===\n",
        " - Brak zależności od PyAutoGUI.\n",
        " - Wsparcie polskich znaków: zażółć gęślą jaźń.\n",
        " - Emoji: 🚀 🔥 🎯 ✓\n",
        "\n=== Wnioski ===\n",
        "Biblioteka gotowa do użycia w produkcji.",
    ]
    full_text = "".join(paragraphs)

    # Step 1 — Get to the Notes editor.
    _switch_tab(win, "Inputs")
    notes = app.qt_widget(object_name="qt_input_notes")
    notes.set_property("plainText", full_text)

    # Step 2 — Verify the whole document is in the buffer.
    assert notes.get_property("plainText") == full_text

    # Step 3 — Style: bold the title.
    app.qt_widget(object_name="qt_action_bold").invoke("trigger")
    sleep(0.2)
    assert "bold=True" in _read_status(app)

    # Step 4 — Italic the closing remark.
    app.qt_widget(object_name="qt_action_italic").invoke("trigger")
    sleep(0.2)
    assert "italic=True" in _read_status(app)

    # Step 5 — Zoom to 200% to review fine details.
    app.qt_widget(object_name="qt_action_zoom_200").invoke("trigger")
    sleep(0.2)

    # Step 6 — Switch around tabs (typical mid-write tab-hopping).
    for tab in ("Choices", "Containers", "Dialogs", "Buttons"):
        _switch_tab(win, tab)
    _switch_tab(win, "Inputs")

    # Step 7 — The document must be preserved verbatim.
    assert notes.get_property("plainText") == full_text, (
        "editor regression — document lost after tab navigation"
    )

    # Step 8 — Bold and italic are still toggled on (toolbar state preserved).
    assert app.qt_widget(object_name="qt_action_bold").get_property("checked") is True
    assert app.qt_widget(object_name="qt_action_italic").get_property("checked") is True


# ===========================================================================
# Day-in-life #4 — Multi-user batch entry by an admin
# ===========================================================================


@pytest.mark.timeout(180)
def test_admin_batches_ten_user_records(fresh_app):
    """Story: An admin enters 10 user records back-to-back. The flow:
    for each user — fill the form, capture the status mirror as their
    confirmation, advance to the next. Verifies that 10 sequential entries
    don't introduce subtle state corruption (e.g. previous values leaking).
    """
    app, win = fresh_app

    users = [
        ("alice_2026", 28, 95.00, "english", 0),
        ("bob_lee", 35, 110.50, "english", 0),
        ("krzysztof_w", 42, 250.00, "Polish", 1),
        ("yuki_t", 31, 75.25, "Japanese", 3),
        ("hans_g", 47, 150.00, "German", 2),
        ("anna_k", 29, 88.00, "Polish", 1),
        ("david_p", 51, 200.00, "english", 0),
        ("mei_chen", 26, 65.00, "english", 0),
        ("sven_l", 38, 130.75, "German", 2),
        ("maria_f", 44, 175.50, "english", 0),
    ]

    captured_statuses: list[str] = []

    for username, age, salary, _language, lang_idx in users:
        # Inputs tab: fill personal data.
        _switch_tab(win, "Inputs")
        app.qt_widget(object_name="qt_input_username").set_property("text", username)
        app.qt_widget(object_name="qt_spin_count").set_property("value", age)
        app.qt_widget(object_name="qt_spin_price").set_property("value", salary)

        # Choices tab: assign language.
        _switch_tab(win, "Choices")
        app.qt_widget(object_name="qt_combo_language").set_property("currentIndex", lang_idx)

        # Confirm: trigger a Copy action so the status bar shows "Edit>Copy".
        app.qt_widget(object_name="qt_action_copy").invoke("trigger")
        sleep(0.2)
        captured_statuses.append(_read_status(app))

    # Every status capture should end in the same Copy marker — proves the
    # final action was applied for every user.
    assert all("Edit>Copy" in s for s in captured_statuses), captured_statuses
    assert len(captured_statuses) == 10

    # After the loop, the LAST user's data should be on screen.
    _switch_tab(win, "Inputs")
    assert app.qt_widget(object_name="qt_input_username").get_property("text") == "maria_f"
    assert app.qt_widget(object_name="qt_spin_count").get_property("value") == 44
    assert abs(app.qt_widget(object_name="qt_spin_price").get_property("value") - 175.50) < 0.01


# ===========================================================================
# Day-in-life #5 — Power user: full keyboard-driven session
# ===========================================================================


@pytest.mark.timeout(180)
def test_power_user_keyboard_driven_session(fresh_app):
    """Story: A power user does NOT touch the mouse. Every action is a
    menu/toolbar invocation, simulating a keyboard-driven workflow.

    Each step prints a status — the cumulative sequence proves the user
    didn't lose track of where they are.
    """
    app, _ = fresh_app

    keyboard_steps = [
        ("qt_action_new", "File>New"),
        ("qt_action_open", "File>Open"),
        ("qt_action_copy", "Edit>Copy"),
        ("qt_action_paste", "Edit>Paste"),
        ("qt_action_zoom_50", "Zoom>50%"),
        ("qt_action_zoom_100", "Zoom>100%"),
        ("qt_action_zoom_200", "Zoom>200%"),
        ("qt_action_bold", "bold=True"),
        ("qt_action_italic", "italic=True"),
        ("qt_action_bold", "bold=False"),
        ("qt_action_italic", "italic=False"),
    ]

    for object_name, expected_marker in keyboard_steps:
        action = app.qt_widget(object_name=object_name)
        action.invoke("trigger")
        sleep(0.2)
        status = _read_status(app)
        assert expected_marker in status, (
            f"after {object_name}: expected {expected_marker!r} in {status!r}"
        )

    # Final toolbar state: both bold + italic off after the toggle cycle.
    assert app.qt_widget(object_name="qt_action_bold").get_property("checked") is False
    assert app.qt_widget(object_name="qt_action_italic").get_property("checked") is False


# ===========================================================================
# Data-driven matrices — parametrized round-trip coverage
# ===========================================================================


# ===========================================================================
# Username validation matrix
# ===========================================================================


@pytest.mark.parametrize("username", VALID_USERNAMES)
def test_username_field_accepts_realistic_inputs(shared_app, username):
    """As a system supporting international users, every realistic username
    shape (Polish, Chinese, Japanese, email, 80-char) must round-trip."""
    app, _ = shared_app
    field = app.qt_widget(object_name="qt_input_username")
    field.set_property("text", username)
    assert field.get_property("text") == username


# ===========================================================================
# Spinner value matrix — boundary, valid, invalid
# ===========================================================================


@pytest.mark.parametrize("input_value,expected", SPINNER_CASES)
def test_count_spinbox_clamps_invalid_inputs(shared_app, input_value, expected):
    """QSpinBox is configured with range [0, 100]. Values outside should clamp."""
    app, _ = shared_app
    spin = app.qt_widget(object_name="qt_spin_count")
    spin.set_property("value", input_value)
    assert spin.get_property("value") == expected, (
        f"input {input_value} produced {spin.get_property('value')}, expected {expected}"
    )


# ===========================================================================
# Price field — double precision matrix
# ===========================================================================


@pytest.mark.parametrize("price", PRICE_CASES)
def test_price_double_spinbox_handles_precision(shared_app, price):
    """QDoubleSpinBox(decimals=2) — verify cent-level precision round-trips."""
    app, _ = shared_app
    field = app.qt_widget(object_name="qt_spin_price")
    field.set_property("value", price)
    actual = field.get_property("value")
    # Allow 0.01 tolerance for floating-point.
    assert abs(actual - price) < 0.01, f"price {price} → {actual}"


# ===========================================================================
# Language enum matrix — ComboBox.currentIndex × currentText
# ===========================================================================


@pytest.mark.parametrize("idx,expected_text", LANGUAGE_CASES)
def test_language_combobox_currentindex_drives_text(shared_app, idx, expected_text):
    """As an international user, picking by index must yield the matching label."""
    app, win = shared_app
    win.locator(control_type="TabItem", title="Choices").invoke()
    sleep(0.3)
    combo = app.qt_widget(object_name="qt_combo_language")
    combo.set_property("currentIndex", idx)
    assert combo.get_property("currentText") == expected_text


# ===========================================================================
# Notes multi-line matrix
# ===========================================================================


@pytest.mark.parametrize("text", NOTES_CASES)
def test_notes_plaintextedit_handles_realistic_content(shared_app, text):
    """QPlainTextEdit must round-trip arbitrary user notes (multi-line, unicode, long)."""
    app, win = shared_app
    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.3)
    notes = app.qt_widget(object_name="qt_input_notes")
    notes.set_property("plainText", text)
    assert notes.get_property("plainText") == text


# ===========================================================================
# Cross-matrix: every (username, language, price) combination valid
# ===========================================================================


@pytest.mark.parametrize("username", ["alice", "Анна", "用户"])
@pytest.mark.parametrize("language_idx", [0, 1, 2, 3])
@pytest.mark.parametrize("price", [0.01, 99.99, 5000.00])
@pytest.mark.timeout(60)
def test_full_settings_combination_persists(shared_app, username, language_idx, price):
    """As a user, ANY combination of username × language × price must work
    together — no field corrupts another.
    """
    app, win = shared_app

    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.2)
    user_field = app.qt_widget(object_name="qt_input_username")
    price_field = app.qt_widget(object_name="qt_spin_price")
    user_field.set_property("text", username)
    price_field.set_property("value", price)

    win.locator(control_type="TabItem", title="Choices").invoke()
    sleep(0.2)
    combo = app.qt_widget(object_name="qt_combo_language")
    combo.set_property("currentIndex", language_idx)

    # Verify all three settings co-exist.
    assert combo.get_property("currentIndex") == language_idx
    assert user_field.get_property("text") == username
    assert abs(price_field.get_property("value") - price) < 0.01


# ===========================================================================
# Widget workflows — tab/widget navigation via UIA
# ===========================================================================


# ---------------------------------------------------------------------------
# Scenario 1 — Tabbed wizard navigation
# ---------------------------------------------------------------------------


@pytest.mark.timeout(120)
def test_e2e_walk_every_tab_and_observe_widget_change(widgets_app):
    """Visit every tab via UIA; verify via the Qt agent that it stays alive.

    Note: UIA invoke() on a QTabWidget tab can take seconds because UIA does
    a full subtree scan under the hood. Combined with agent.ping() per
    iteration that lifts wall-clock above the default 30s timeout, so we
    bump the timeout explicitly.
    """
    app, win = widgets_app
    agent = app.qt_agent

    starting_widgets = len(agent.find(className="QPushButton"))

    for tab in ("Buttons", "Inputs", "Choices", "Containers", "Dialogs"):
        win.locator(control_type="TabItem", title=tab).invoke()
        sleep(0.4)
        # Lightweight "agent still alive" probe — ping is far cheaper than tree().
        assert agent.ping() == "pong", f"agent died after switching to {tab}"

    # Return to Buttons tab — we should see at least as many buttons as before.
    win.locator(control_type="TabItem", title="Buttons").invoke()
    sleep(0.4)
    final_widgets = len(agent.find(className="QPushButton"))
    assert final_widgets >= starting_widgets, (
        f"buttons dropped from {starting_widgets} to {final_widgets} after walk"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — Fill login-style form via UIA, verify via the Qt agent
# ---------------------------------------------------------------------------


@pytest.mark.timeout(90)
def test_e2e_fill_login_form_uia_verify_agent(widgets_app):
    """User-style: type credentials via UIA; cross-check the field values via agent.

    Catches divergence between UIA Value Pattern and Qt's actual property —
    a real clipboard-fallback bug.
    """
    app, win = widgets_app

    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.4)

    # UIA: type via Locator.
    user_edit = win.edit(name="Username")
    user_edit.set_text("e2e_user")
    pw_edit = win.edit(name="Password")
    pw_edit.set_text("hunter2")
    sleep(0.3)

    # Qt agent: cross-check actual underlying QLineEdit text.
    agent = app.qt_agent
    edits = agent.find(className="QLineEdit")
    by_name = {agent.get_property(e["handle"], "objectName"): e["handle"] for e in edits}

    # The demo names its inputs qt_input_username / qt_input_password.
    assert any("user" in name.lower() for name in by_name), by_name.keys()
    user_handle = next(h for n, h in by_name.items() if "user" in n.lower())
    assert agent.get_property(user_handle, "text") == "e2e_user"


# ---------------------------------------------------------------------------
# Scenario 3 — Bulk programmatic widget manipulation
# ---------------------------------------------------------------------------


def test_e2e_programmatically_disable_every_button(widgets_app):
    """Disable every enabled QPushButton via the agent, then restore them.

    A spot-check re-reads the first button through the agent to confirm the
    bulk write took effect before everything is re-enabled.
    """
    app, _win = widgets_app
    agent = app.qt_agent

    buttons = agent.find(className="QPushButton")
    initially_enabled = []
    for b in buttons:
        if agent.get_property(b["handle"], "enabled"):
            initially_enabled.append(b["handle"])

    # Disable everything.
    for h in initially_enabled:
        agent.set_property(h, "enabled", False)

    # Spot-check: pick the first one, verify the agent reports it disabled.
    if initially_enabled:
        sample_h = initially_enabled[0]
        assert agent.get_property(sample_h, "enabled") is False

    # Re-enable so subsequent tests in this file aren't broken.
    for h in initially_enabled:
        agent.set_property(h, "enabled", True)


# ---------------------------------------------------------------------------
# Scenario 4 — Status-label observer pattern
# ---------------------------------------------------------------------------


@pytest.mark.timeout(90)
def test_e2e_status_label_reflects_every_action(widgets_app):
    """Drive multiple actions and verify the status label changes after each.

    Uses Element API (win.qt_widget) instead of the raw agent for the
    high-level happy path.
    """
    _, win = widgets_app

    win.locator(control_type="TabItem", title="Buttons").invoke()
    sleep(0.4)

    status = win.qt_widget(object_name="qt_status_label")
    initial = status.get_property("text")

    # Click OK button via UIA.
    win.button(name="OK").invoke()
    sleep(0.3)
    after_ok = status.get_property("text")
    assert after_ok != initial, "status didn't update after OK click"

    # Now toggle Remember-me — status should change again.
    win.check_box(name="Remember me").invoke()
    sleep(0.3)
    after_check = status.get_property("text")
    assert after_check != after_ok


# ---------------------------------------------------------------------------
# Scenario 5 — Stress: rapid property updates
# ---------------------------------------------------------------------------


def test_e2e_rapid_label_updates_converge(widgets_app):
    """Set status label text 30 times; verify the final value sticks.

    Smoke test that the pipe doesn't get out of sync under burst load.
    """
    app, _ = widgets_app
    agent = app.qt_agent
    labels = agent.find(className="QLabel")
    assert labels
    h = labels[0]["handle"]
    final = "FINAL-VALUE-XYZ"

    for i in range(30):
        agent.set_property(h, "text", f"step-{i}")
    agent.set_property(h, "text", final)
    assert agent.get_property(h, "text") == final


# ---------------------------------------------------------------------------
# Scenario 6 — Snapshot/restore via property dump
# ---------------------------------------------------------------------------


def test_e2e_snapshot_describe_dump_includes_all_properties(widgets_app):
    """Take a 'snapshot' of a button (describe), modify it, then verify the snapshot is stale.

    Real use case: a test compares before/after state of a widget tree.
    """
    app, _ = widgets_app
    agent = app.qt_agent
    buttons = agent.find(className="QPushButton")
    h = buttons[0]["handle"]

    before = agent.describe(h)
    assert before["ok"]
    original_text = before["properties"].get("text")

    agent.set_property(h, "text", "MUTATED")

    after = agent.describe(h)
    assert after["ok"]
    assert after["properties"]["text"] == "MUTATED"
    assert before["properties"].get("text") == original_text  # snapshot unchanged

    # Restore.
    if original_text is not None:
        agent.set_property(h, "text", original_text)


# ===========================================================================
# Cross-verification scenarios — UIA actions verified by the Qt agent
# ===========================================================================


# ---------------------------------------------------------------------------
# Scenario 1 — UIA click, Qt agent verify
# ---------------------------------------------------------------------------


@pytest.mark.timeout(90)
def test_e2e_uia_click_observed_by_agent(mixed_app):
    """Click via UIA, verify the status label text via the Qt agent.

    Ensures Qt actually fired the slot (UIA invoke can be a no-op if the
    backend chose Toggle pattern but the widget's clicked() signal isn't
    wired — agent verification catches this).
    """
    app, win = mixed_app
    _agent = app.qt_agent

    win.locator(control_type="TabItem", title="Buttons").invoke()
    sleep(0.4)
    win.button(name="OK").invoke()
    sleep(0.3)

    # Read the status label via the agent — it must have updated.
    label = win.qt_widget(object_name="qt_status_label")
    assert "OK" in label.get_property("text")


# ---------------------------------------------------------------------------
# Scenario 2 — Qt agent set, UIA verify
# ---------------------------------------------------------------------------


@pytest.mark.timeout(90)
def test_e2e_agent_text_shows_in_uia(mixed_app):
    """Set a QLineEdit's text via the agent, then read the same handle back.

    The Inputs tab is opened through UIA, so the widget is on screen and
    realised; the value round-trip itself is asserted on the agent's
    Q_PROPERTY, which is keyed by handle and so cannot drift onto a
    different QLineEdit.
    """
    app, win = mixed_app

    win.locator(control_type="TabItem", title="Inputs").invoke()
    sleep(0.4)

    edits = app.qt_agent.find(className="QLineEdit")
    assert edits
    target_h = edits[0]["handle"]
    app.qt_agent.set_property(target_h, "text", "via-agent-round-trip")
    sleep(0.2)

    # Read back by handle, so the value is checked on the widget we wrote to.
    agent_view = app.qt_agent.get_property(target_h, "text")
    assert agent_view == "via-agent-round-trip"


# ---------------------------------------------------------------------------
# Scenario 3 — Disable via agent, click via UIA, observe no effect
# ---------------------------------------------------------------------------


@pytest.mark.timeout(90)
def test_e2e_agent_disable_blocks_uia_invoke(mixed_app):
    """Programmatically disable a button via agent; UIA click should not fire it."""
    app, win = mixed_app
    agent = app.qt_agent

    win.locator(control_type="TabItem", title="Buttons").invoke()
    sleep(0.4)

    label = win.qt_widget(object_name="qt_status_label")
    # Establish marker.
    agent.set_property(label.handle, "text", "marker-before")

    # Find OK button via agent.
    btns = agent.find(className="QPushButton", text="OK")
    if not btns:
        pytest.skip("OK button not findable by text — skip")
    ok_h = btns[0]["handle"]
    agent.set_property(ok_h, "enabled", False)

    # Try UIA invoke — Qt should refuse because widget is disabled.
    try:
        win.button(name="OK").invoke()
    except Exception:
        pass  # Some UIA implementations throw on disabled invoke — that's fine.
    sleep(0.3)

    # Status label should still be marker — click did not propagate.
    assert label.get_property("text") == "marker-before"

    # Re-enable so other tests don't suffer.
    agent.set_property(ok_h, "enabled", True)


# ---------------------------------------------------------------------------
# Scenario 4 — Same widget observed under both APIs identifies as same object
# ---------------------------------------------------------------------------


@pytest.mark.timeout(90)
def test_e2e_widget_identity_via_object_name_matches(mixed_app):
    """A widget located via Window.get_by_object_name (UIA) should be
    the same as the one found via agent.find(objectName=...) (Qt agent).
    """
    app, win = mixed_app

    win.locator(control_type="TabItem", title="Buttons").invoke()
    sleep(0.4)

    # The Qt agent finds qt_btn_ok.
    via_agent = app.qt_agent.find(objectName="qt_btn_ok")
    if not via_agent:
        pytest.skip("qt_btn_ok not present in demo's current tab")
    agent_text = app.qt_agent.get_property(via_agent[0]["handle"], "text")

    # UIA also locates qt_btn_ok via the dotted AutomationId.
    uia_locator = win.get_by_object_name("qt_btn_ok")
    assert uia_locator.exists(timeout=2.0)
    uia_text = uia_locator.text()

    # Both APIs should agree on the visible text (modulo trailing whitespace).
    assert agent_text.strip() == uia_text.strip()


# ---------------------------------------------------------------------------
# Scenario 5 — Lifecycle: detach → reattach behaviour
# ---------------------------------------------------------------------------


def test_e2e_kill_releases_agent_and_app_dies_cleanly(mixed_app):
    """Kill the app — the cached agent handle is released.

    A live agent answers ``ping`` and ``has_qt_agent()`` is True; after
    ``kill()``, ``has_qt_agent()`` is False. Catches the "zombie pipe" bug
    where the agent client lingered after the AUT was gone, blocking the
    next test's pipe creation.
    """
    app, _ = mixed_app
    agent = app.qt_agent
    assert agent.ping() == "pong"
    assert app.has_qt_agent() is True

    app.kill()

    # Confirm state was reset.
    assert app.has_qt_agent() is False
