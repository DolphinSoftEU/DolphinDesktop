"""QML scene-graph introspection via the injected Qt agent."""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, sleep
from tests.qt._qt_helpers import QML_SCRIPT

pytestmark = pytest.mark.qt_qml


@pytest.fixture
def qml_app_agent():
    """Launch QML demo + attach agent. Yields (app, agent)."""
    desktop = Desktop()
    app = desktop.launch_qt_python_script(str(QML_SCRIPT), timeout=20, startup_delay=2.5)
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


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def test_qml_app_is_detected(qml_app_agent):
    app, _ = qml_app_agent
    assert app.is_qt()
    assert app.uses_qt_quick()


def test_qml_root_returns_window(qml_app_agent):
    _, agent = qml_app_agent
    roots = agent.qml_root()
    assert isinstance(roots, list)
    assert len(roots) >= 1
    win = roots[0]
    assert win.get("title", "").startswith("Dolphin QML")


def test_qml_root_contains_named_items(qml_app_agent):
    _, agent = qml_app_agent
    roots = agent.qml_root()
    assert roots
    seen: set[str] = set()

    def walk(node):
        if node.get("objectName"):
            seen.add(node["objectName"])
        for k in ("children", "root"):
            v = node.get(k)
            if isinstance(v, list):
                for c in v:
                    walk(c)
            elif isinstance(v, dict):
                walk(v)

    for r in roots:
        walk(r)
    assert "qmlNameField" in seen
    assert "qmlClickButton" in seen
    assert "qmlStatusLabel" in seen


# ---------------------------------------------------------------------------
# Lookup by objectName
# ---------------------------------------------------------------------------


def test_qml_find_by_object_name(qml_app_agent):
    _, agent = qml_app_agent
    hits = agent.qml_find("qmlClickButton")
    assert len(hits) == 1
    assert hits[0]["objectName"] == "qmlClickButton"


def test_qml_find_missing_returns_empty(qml_app_agent):
    _, agent = qml_app_agent
    assert agent.qml_find("noSuchName_xyz") == []


# ---------------------------------------------------------------------------
# Property access
# ---------------------------------------------------------------------------


def test_qml_set_text_field(qml_app_agent):
    _, agent = qml_app_agent
    hits = agent.qml_find("qmlNameField")
    assert hits
    h = hits[0]["handle"]
    agent.set_property(h, "text", "Alice")
    assert agent.get_property(h, "text") == "Alice"


def test_qml_status_label_reflects_text_change(qml_app_agent):
    _, agent = qml_app_agent
    name = agent.qml_find("qmlNameField")[0]["handle"]
    status = agent.qml_find("qmlStatusLabel")[0]["handle"]
    agent.set_property(name, "text", "Bob")
    assert agent.get_property(status, "text") == "name=Bob"


# ---------------------------------------------------------------------------
# Click simulation
# ---------------------------------------------------------------------------


def test_qml_button_click_fires_handler(qml_app_agent):
    _, agent = qml_app_agent
    btn = agent.qml_find("qmlClickButton")[0]["handle"]
    status = agent.qml_find("qmlStatusLabel")[0]["handle"]
    res = agent.qml_click(btn)
    assert res.get("ok"), res
    sleep(0.2)
    assert agent.get_property(status, "text") == "clicked"
