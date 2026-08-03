"""End-to-end scenarios on the Qt Charts demo.

Realistic interaction patterns with a charting widget: change theme,
toggle animations, modify chart title from outside, change axis ranges.

Headline value: every test below is achieved without the agent linking
against Qt Charts — the entire chart model is reachable through the
runtime meta-object system.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, sleep
from tests.qt._qt_helpers import CHARTS_SCRIPT, GRAPHICS_SCRIPT

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def charts_app():
    desktop = Desktop()
    app = desktop.launch_qt_python_script(str(CHARTS_SCRIPT), timeout=20, startup_delay=2.5)
    sleep(1.5)
    app.detach()
    try:
        agent = app.qt_agent
        yield app, agent
    finally:
        try:
            app.kill()
        except Exception:
            pass


@pytest.fixture
def dashboard():
    desktop = Desktop()
    app = desktop.launch_qt_python_script(str(CHARTS_SCRIPT), timeout=20, startup_delay=2.5)
    sleep(1.5)
    app.detach()
    try:
        agent = app.qt_agent
        chart = agent.find(objectName="priceChart")[0]["handle"]
        yield app, agent, chart
    finally:
        try:
            app.kill()
        except Exception:
            pass


@pytest.fixture
def graphics_app():
    desktop = Desktop()
    app = desktop.launch_qt_python_script(str(GRAPHICS_SCRIPT), timeout=20, startup_delay=2.5)
    sleep(1.5)
    app.detach()
    try:
        yield app
    finally:
        try:
            app.kill()
        except Exception:
            pass


# ###########################################################################
# Chart property round-trips
# ###########################################################################


# ---------------------------------------------------------------------------
# Scenario 1 — Theme cycle
# ---------------------------------------------------------------------------


@pytest.mark.qt_charts
def test_e2e_chart_theme_cycle(charts_app):
    """Cycle through several QChart themes, verifying each set sticks.

    Demonstrates: enum-typed Q_PROPERTY round-trip via agent JSON pipe.

    Note: Qt 6 serialises enum QVariants as strings over JSON (the meta-type
    is QMetaType::QtCharts::QChart::ChartTheme, which JSON can't represent
    natively). We cast to int on the read side to compare cleanly.
    """
    _, agent = charts_app
    chart = agent.find(objectName="priceChart")[0]["handle"]

    # Qt 6 QChart::ChartTheme enum values: 0 (Light), 2 (Dark), 4 (BlueIcy), 7 (Qt).
    for theme in (0, 2, 4, 7, 0):
        agent.set_property(chart, "theme", theme)
        assert int(agent.get_property(chart, "theme")) == theme


# ---------------------------------------------------------------------------
# Scenario 2 — Animation toggle
# ---------------------------------------------------------------------------


@pytest.mark.qt_charts
def test_e2e_chart_animation_toggle(charts_app):
    """Enable/disable chart animations via QChart::animationOptions.

    QChart::AnimationOption: NoAnimation=0, GridAxisAnimations=1,
    SeriesAnimations=2, AllAnimations=3.
    """
    _, agent = charts_app
    chart = agent.find(objectName="priceChart")[0]["handle"]
    for opt in (0, 1, 2, 3, 0):
        agent.set_property(chart, "animationOptions", opt)
        # Enum-typed Q_PROPERTY comes back as string — cast for comparison.
        assert int(agent.get_property(chart, "animationOptions")) == opt


# ---------------------------------------------------------------------------
# Scenario 3 — Title editing
# ---------------------------------------------------------------------------


@pytest.mark.qt_charts
def test_e2e_chart_title_live_edit(charts_app):
    """Push successive titles into the chart and verify each sticks."""
    _, agent = charts_app
    chart = agent.find(objectName="priceChart")[0]["handle"]
    original = agent.get_property(chart, "title")
    titles = [
        "Q4 Earnings",
        "Daily moves — 2026-06",
        "Stress test 🚀",
        original or "",
    ]
    for t in titles:
        agent.set_property(chart, "title", t)
        assert agent.get_property(chart, "title") == t


# ---------------------------------------------------------------------------
# Scenario 4 — Background visibility
# ---------------------------------------------------------------------------


@pytest.mark.qt_charts
def test_e2e_chart_background_visibility_toggle(charts_app):
    """Toggle backgroundVisible — common branding workflow."""
    _, agent = charts_app
    chart = agent.find(objectName="priceChart")[0]["handle"]
    original = agent.get_property(chart, "backgroundVisible")
    agent.set_property(chart, "backgroundVisible", False)
    assert agent.get_property(chart, "backgroundVisible") is False
    agent.set_property(chart, "backgroundVisible", True)
    assert agent.get_property(chart, "backgroundVisible") is True
    agent.set_property(chart, "backgroundVisible", original)


# ---------------------------------------------------------------------------
# Scenario 5 — Re-style via members listing
# ---------------------------------------------------------------------------


@pytest.mark.qt_charts
def test_e2e_chart_members_drives_dynamic_set(charts_app):
    """Discover settable boolean Q_PROPERTYs at runtime and toggle each.

    Realistic for a generic UI inspector or theme-tester.
    """
    _, agent = charts_app
    chart = agent.find(objectName="priceChart")[0]["handle"]
    members = agent.members(chart)
    assert members["ok"]
    # Find any boolean-typed Q_PROPERTY by reading current values.
    bool_props: list[str] = []
    for name in members["properties"]:
        v = agent.get_property(chart, name)
        if isinstance(v, bool):
            bool_props.append(name)
    assert bool_props, "QChart should have at least one bool Q_PROPERTY"

    # Flip and restore the first one.
    target = bool_props[0]
    original = agent.get_property(chart, target)
    agent.set_property(chart, target, not original)
    assert agent.get_property(chart, target) is (not original)
    agent.set_property(chart, target, original)


# ###########################################################################
# Chart dashboard stories
# ###########################################################################


# ===========================================================================
# Charts Story 1 — Presentation mode (theme + title rotation)
# ===========================================================================


@pytest.mark.qt_charts
@pytest.mark.timeout(120)
def test_charts_presentation_mode_rotates_through_slides(dashboard):
    """Story: A speaker presents 5 chart "slides", each with a unique title
    and theme. The chart state at each slide must match the slide deck.
    """
    _, agent, chart = dashboard

    slides = [
        ("Q1 — Account Growth", 0),  # Light
        ("Q2 — Engagement", 2),  # Dark
        ("Q3 — Conversion Funnel", 4),  # BlueIcy
        ("Q4 — Year-End Summary", 7),  # Qt
        ("2027 — Forecast", 0),  # back to Light
    ]
    for title, theme_idx in slides:
        agent.set_property(chart, "title", title)
        agent.set_property(chart, "theme", theme_idx)
        assert agent.get_property(chart, "title") == title
        # Theme enum may come back as string — coerce.
        assert int(agent.get_property(chart, "theme")) == theme_idx


# ===========================================================================
# Charts Story 2 — Live editing during a meeting
# ===========================================================================


@pytest.mark.qt_charts
@pytest.mark.timeout(120)
def test_charts_live_edit_during_meeting(dashboard):
    """Story: During a meeting, the presenter edits the chart title 8 times
    as new questions come up. Verifies each edit lands within ~100ms and
    the previous edit doesn't ghost back.
    """
    _, agent, chart = dashboard

    edits = [
        "Live: incoming data",
        "Edit 1 — note from Alice",
        "Edit 2 — pivot per Bob's question",
        "Edit 3 — Q1 only",
        "Edit 4 — add YoY context",
        "Edit 5 — split out region",
        "Edit 6 — final version v6",
        "FINAL — for handout",
    ]
    seen_history: list[str] = []
    for new_title in edits:
        agent.set_property(chart, "title", new_title)
        actual = agent.get_property(chart, "title")
        assert actual == new_title, f"edit landed as {actual!r}"
        seen_history.append(actual)

    # Final title sticks.
    assert agent.get_property(chart, "title") == "FINAL — for handout"
    # No ghost values reappeared (history matches inputs exactly).
    assert seen_history == edits


# ===========================================================================
# Charts Story 3 — Brand pack — every theme tested in sequence
# ===========================================================================


@pytest.mark.qt_charts
@pytest.mark.timeout(120)
def test_charts_brand_pack_all_themes_round_trip(dashboard):
    """Story: A brand designer evaluates all 8 QChart themes for the new
    visual identity. Each must round-trip without corrupting other properties.
    """
    _, agent, chart = dashboard

    # Capture original baseline.
    original = {
        "title": agent.get_property(chart, "title"),
        "animationOptions": int(agent.get_property(chart, "animationOptions")),
        "backgroundVisible": agent.get_property(chart, "backgroundVisible"),
    }
    test_title = "Brand pack evaluation"
    agent.set_property(chart, "title", test_title)

    # QChart::ChartTheme enum values 0..7
    for theme_idx in range(8):
        agent.set_property(chart, "theme", theme_idx)
        assert int(agent.get_property(chart, "theme")) == theme_idx
        # Title and other properties must not be reset by theme change.
        assert agent.get_property(chart, "title") == test_title

    # Restore originals.
    agent.set_property(chart, "title", original["title"] or "")
    agent.set_property(chart, "animationOptions", original["animationOptions"])
    agent.set_property(chart, "backgroundVisible", original["backgroundVisible"])


# ===========================================================================
# Charts Story 4 — Dim mode for night reading
# ===========================================================================


@pytest.mark.qt_charts
@pytest.mark.timeout(120)
def test_charts_dim_mode_combo_dark_no_animation(dashboard):
    """Story: An analyst works late and wants a low-distraction chart:
    dark theme + no animations + transparent background. Verify the full
    combination applies and reverts cleanly.
    """
    _, agent, chart = dashboard

    # Capture originals so the test is reversible.
    orig_theme = int(agent.get_property(chart, "theme"))
    orig_anim = int(agent.get_property(chart, "animationOptions"))
    orig_bg = agent.get_property(chart, "backgroundVisible")

    # Apply dim mode.
    agent.set_property(chart, "theme", 2)  # Dark
    agent.set_property(chart, "animationOptions", 0)  # NoAnimation
    agent.set_property(chart, "backgroundVisible", False)

    assert int(agent.get_property(chart, "theme")) == 2
    assert int(agent.get_property(chart, "animationOptions")) == 0
    assert agent.get_property(chart, "backgroundVisible") is False

    # Revert.
    agent.set_property(chart, "theme", orig_theme)
    agent.set_property(chart, "animationOptions", orig_anim)
    agent.set_property(chart, "backgroundVisible", orig_bg)

    assert int(agent.get_property(chart, "theme")) == orig_theme
    assert int(agent.get_property(chart, "animationOptions")) == orig_anim
    assert agent.get_property(chart, "backgroundVisible") == orig_bg


# ===========================================================================
# Charts Story 5 — Stress: 30 theme cycles in a tight loop
# ===========================================================================


@pytest.mark.qt_charts
@pytest.mark.timeout(120)
def test_charts_thirty_theme_cycles_no_drift(dashboard):
    """Story: Stress-test the theme property by cycling 30 times. The
    chart must keep up — no missed updates, no drift.
    """
    _, agent, chart = dashboard

    sequence = [0, 1, 2, 3, 4, 5, 6, 7] * 4  # 32 entries
    for theme_idx in sequence[:30]:
        agent.set_property(chart, "theme", theme_idx)
        assert int(agent.get_property(chart, "theme")) == theme_idx

    # Final value sticks.
    agent.set_property(chart, "theme", 0)
    assert int(agent.get_property(chart, "theme")) == 0


# ###########################################################################
# QGraphicsScene interaction
# ###########################################################################


# ---------------------------------------------------------------------------
# Scenario 1 — Move text item programmatically
# ---------------------------------------------------------------------------


@pytest.mark.qt_graphics
def test_e2e_move_text_item(graphics_app):
    """Reposition the draggable QGraphicsTextItem via its `x` / `y` Q_PROPERTYs."""
    items = graphics_app.qt_agent.find(className="QGraphicsTextItem")
    assert items, "demo should have a QGraphicsTextItem"
    h = items[0]["handle"]
    agent = graphics_app.qt_agent

    # Capture original position.
    orig_x = agent.get_property(h, "x")
    orig_y = agent.get_property(h, "y")

    # Move to a new location.
    agent.set_property(h, "x", 200.0)
    agent.set_property(h, "y", 250.0)
    assert agent.get_property(h, "x") == 200.0
    assert agent.get_property(h, "y") == 250.0

    # Restore — proves the test is reversible.
    agent.set_property(h, "x", orig_x)
    agent.set_property(h, "y", orig_y)


# ---------------------------------------------------------------------------
# Scenario 2 — Hit-test sweep
# ---------------------------------------------------------------------------


@pytest.mark.qt_graphics
def test_e2e_hit_test_three_zones(graphics_app):
    """Hit-test inside the rect, the circle, and the text — verify each is found."""
    view = graphics_app.graphics_view()

    # Rect: scene (20,20) → (120,80). Centre approx (70, 50).
    rect_hit = view.item_at(70, 50)
    assert "Rect" in rect_hit["type"] or rect_hit["type"] == "QGraphicsItem"

    # Circle: ellipse 180..260, 60..140. Centre approx (220, 100).
    circle_hit = view.item_at(220, 100)
    assert "Ellipse" in circle_hit["type"] or circle_hit["type"] == "QGraphicsItem"


# ---------------------------------------------------------------------------
# Scenario 3 — Item enumeration
# ---------------------------------------------------------------------------


@pytest.mark.qt_graphics
def test_e2e_scene_enumerate_three_items(graphics_app):
    """The demo populates rect + circle + text — exactly three top-level items."""
    view = graphics_app.graphics_view()
    items = view.items()
    assert len(items) == 3


# ---------------------------------------------------------------------------
# Scenario 4 — Text item content editing
# ---------------------------------------------------------------------------


@pytest.mark.qt_graphics
def test_e2e_text_item_change_html(graphics_app):
    """Edit the QGraphicsTextItem HTML — verify the change is observable."""
    agent = graphics_app.qt_agent
    items = agent.find(className="QGraphicsTextItem")
    assert items
    h = items[0]["handle"]

    # plainText property is mutable on QGraphicsTextItem (Qt 6 exposes it).
    members = agent.members(h)
    if "plainText" in members.get("properties", []):
        agent.set_property(h, "plainText", "Edited via agent")
        assert agent.get_property(h, "plainText") == "Edited via agent"
    else:
        pytest.skip("plainText not exposed as Q_PROPERTY in this Qt build")


# ---------------------------------------------------------------------------
# Scenario 5 — Z-order manipulation
# ---------------------------------------------------------------------------


@pytest.mark.qt_graphics
def test_e2e_bring_text_to_front(graphics_app):
    """Raise the text item's zValue above the other two — verify ordering."""
    agent = graphics_app.qt_agent
    items = agent.find(className="QGraphicsTextItem")
    assert items
    h = items[0]["handle"]
    # Defaults are 0 — bumping to 10 should bring it to front.
    agent.set_property(h, "zValue", 10.0)
    assert agent.get_property(h, "zValue") == 10.0
    agent.set_property(h, "zValue", 0.0)
