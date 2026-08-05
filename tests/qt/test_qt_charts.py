"""Qt agent — Qt Charts smoke test (polymorphic introspection)."""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, sleep
from tests.qt._qt_helpers import CHARTS_SCRIPT

pytestmark = pytest.mark.qt_charts


@pytest.fixture
def charts_app_agent():
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


def test_chart_view_present(charts_app_agent):
    _, agent = charts_app_agent
    views = agent.find(className="QChartView")
    assert len(views) >= 1


def test_chart_q_properties_introspectable(charts_app_agent):
    _, agent = charts_app_agent
    charts = agent.find(objectName="priceChart")
    assert charts, "priceChart not findable"
    chart_h = charts[0]["handle"]
    members = agent.members(chart_h)
    assert members.get("ok")
    props = members["properties"]
    for required in ("title", "theme", "animationOptions", "backgroundVisible"):
        assert required in props, f"missing Q_PROPERTY {required!r}: {props}"


def test_chart_properties_readable(charts_app_agent):
    _, agent = charts_app_agent
    chart = agent.find(objectName="priceChart")
    assert chart
    title = agent.get_property(chart[0]["handle"], "title")
    assert title == "Sample price action"
