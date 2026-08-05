"""Qt agent injection + basic QObject introspection (Qt 6).

These tests launch the Qt 6 widget demo and inject ``dolphin_qt6_agent.dll``
to exercise the named-pipe IPC protocol end-to-end.

Imports: ``dolphin_desktop`` public API + ``pytest`` + test-local helpers
only — no stdlib reach-throughs (no ``time``, ``sys``, ``os``).
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, sleep
from tests.qt._qt_helpers import QT6_SCRIPT, QT6_WINDOW_TITLE, launch_demo

pytestmark = pytest.mark.qt_agent


@pytest.fixture
def qt6_app_agent():
    """Launch the Qt 6 demo and attach the agent. Yields (app, win, agent)."""
    desktop = Desktop()
    app, win = launch_demo(desktop, QT6_SCRIPT, QT6_WINDOW_TITLE)
    sleep(0.5)
    try:
        agent = app.qt_agent  # triggers injection + pipe connect
        yield app, win, agent
    finally:
        try:
            app.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Injection lifecycle
# ---------------------------------------------------------------------------


def test_agent_injection_succeeds(qt6_app_agent):
    app, _win, agent = qt6_app_agent
    assert agent is not None
    assert agent.pid == app.process_id


def test_agent_ping(qt6_app_agent):
    _app, _win, agent = qt6_app_agent
    assert agent.ping() == "pong"


def test_agent_idempotent_start(qt6_app_agent):
    """Asking for app.qt_agent twice returns the same cached client."""
    app, _, first = qt6_app_agent
    second = app.qt_agent
    assert first is second


# ---------------------------------------------------------------------------
# QObject tree
# ---------------------------------------------------------------------------


def test_tree_returns_top_level_widgets(qt6_app_agent):
    _, _, agent = qt6_app_agent
    tree = agent.tree()
    assert isinstance(tree, list)
    assert len(tree) >= 1
    classes = {node["class"] for node in tree}
    assert any("MainWindow" in c or "QMainWindow" in c for c in classes)


def test_tree_includes_object_names(qt6_app_agent):
    _, _, agent = qt6_app_agent
    tree = agent.tree()

    def names(node, out):
        if node.get("objectName"):
            out.append(node["objectName"])
        for ch in node.get("children", []):
            names(ch, out)

    flat = []
    for top in tree:
        names(top, flat)
    assert any(n.startswith("qt_") or n == "qt_window" or n for n in flat)


# ---------------------------------------------------------------------------
# Find
# ---------------------------------------------------------------------------


def test_find_by_class_name(qt6_app_agent):
    _, _, agent = qt6_app_agent
    pushbuttons = agent.find(className="QPushButton")
    assert len(pushbuttons) > 0
    for pb in pushbuttons:
        assert "QPushButton" in pb["class"]


def test_find_by_object_name(qt6_app_agent):
    _, _, agent = qt6_app_agent
    hits = agent.find(regex=r"^qt_")
    assert len(hits) > 0


def test_find_returns_handles(qt6_app_agent):
    _, _, agent = qt6_app_agent
    buttons = agent.find(className="QPushButton")
    assert buttons
    h = buttons[0]["handle"]
    assert isinstance(h, str)
    assert "@0x" in h


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_get_property_text_on_button(qt6_app_agent):
    _, _, agent = qt6_app_agent
    buttons = agent.find(className="QPushButton")
    assert buttons
    for b in buttons:
        text = agent.get_property(b["handle"], "text")
        if text:
            assert isinstance(text, str)
            return
    pytest.fail("no QPushButton with text found")


def test_set_property_text_round_trip(qt6_app_agent):
    _, _, agent = qt6_app_agent
    labels = agent.find(className="QLabel")
    assert labels
    h = labels[0]["handle"]
    agent.set_property(h, "text", "agent-modified")
    assert agent.get_property(h, "text") == "agent-modified"


# ---------------------------------------------------------------------------
# Method invocation
# ---------------------------------------------------------------------------


def test_invoke_no_arg_method(qt6_app_agent):
    """Calling a no-arg slot (e.g. clear() on QLineEdit) should succeed."""
    _, _, agent = qt6_app_agent
    edits = agent.find(className="QLineEdit")
    assert edits
    h = edits[0]["handle"]
    agent.set_property(h, "text", "something")
    assert agent.get_property(h, "text") == "something"
    res = agent.invoke(h, "clear")
    assert res.get("ok"), res
    assert agent.get_property(h, "text") == ""


def test_invoke_with_int_arg(qt6_app_agent):
    """Set a QSpinBox via setValue(int) through the agent."""
    _, _, agent = qt6_app_agent
    spinboxes = agent.find(className="QSpinBox")
    if not spinboxes:
        pytest.skip("no QSpinBox in demo")
    h = spinboxes[0]["handle"]
    res = agent.invoke(h, "setValue", 42)
    assert res.get("ok"), res
    assert agent.get_property(h, "value") == 42


def test_members_lists_properties_and_methods(qt6_app_agent):
    _, _, agent = qt6_app_agent
    buttons = agent.find(className="QPushButton")
    assert buttons
    members = agent.members(buttons[0]["handle"])
    assert members.get("ok")
    props = members["properties"]
    assert "text" in props
    assert "enabled" in props
    methods = members["methods"]
    assert any("click" in m for m in methods)


# ---------------------------------------------------------------------------
# Describe
# ---------------------------------------------------------------------------


def test_describe_full_properties(qt6_app_agent):
    _, _, agent = qt6_app_agent
    buttons = agent.find(className="QPushButton")
    assert buttons
    full = agent.describe(buttons[0]["handle"])
    assert full.get("ok")
    assert "QPushButton" in full["class"]
    assert "text" in full["properties"]
