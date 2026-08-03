"""Qt agent — QGraphicsView / QGraphicsScene walker tests."""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, sleep
from tests.qt._qt_helpers import GRAPHICS_SCRIPT

pytestmark = pytest.mark.qt_graphics


@pytest.fixture
def graphics_app_agent():
    desktop = Desktop()
    app = desktop.launch_qt_python_script(str(GRAPHICS_SCRIPT), timeout=20, startup_delay=2.5)
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


def test_graphics_view_discoverable(graphics_app_agent):
    _, agent = graphics_app_agent
    views = agent.find(className="QGraphicsView")
    assert len(views) >= 1


def test_graphics_items_listed(graphics_app_agent):
    _, agent = graphics_app_agent
    views = agent.find(className="QGraphicsView")
    assert views
    items = agent.graphics_items(views[0]["handle"])
    assert isinstance(items, list)
    assert len(items) >= 3
    types = [it["type"] for it in items]
    assert any("Rect" in t or t == "QGraphicsItem" for t in types)


def test_graphics_text_item_movable_property(graphics_app_agent):
    _, agent = graphics_app_agent
    text_items = agent.find(className="QGraphicsTextItem")
    if not text_items:
        pytest.skip("Qt does not expose QGraphicsTextItem as findable QObject child")
    h = text_items[0]["handle"]
    assert h.startswith("QGraphics") or "@0x" in h


def test_graphics_item_at_point(graphics_app_agent):
    _, agent = graphics_app_agent
    views = agent.find(className="QGraphicsView")
    assert views
    view_h = views[0]["handle"]
    res = agent.graphics_item_at(view_h, 60, 40)
    assert res.get("ok"), res
    item = res["item"]
    assert "Rect" in item["type"] or item["type"] == "QGraphicsItem"
