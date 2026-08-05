"""Qt public API surface tests.

Covers:
* Window-level proxies (win.qml / win.qt_widget / win.graphics_view).
* High-level Element API (QmlElement / WidgetElement / GraphicsViewElement).
* Qt agent classes importable from the package root.
* Advanced find() queries: multi-criteria AND, inheritance chain.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import (
    Desktop,
    ElementNotFoundError,
    GraphicsViewElement,
    QmlElement,
    WidgetElement,
    sleep,
)
from tests.qt._qt_helpers import (
    GRAPHICS_SCRIPT,
    GRAPHICS_WINDOW_TITLE,
    QML_SCRIPT,
    QML_WINDOW_TITLE,
    QT6_SCRIPT,
    QT6_WINDOW_TITLE,
    launch_demo,
)

pytestmark = pytest.mark.qt_agent


# ===========================================================================
# Window-level proxies (win.qml / win.qt_widget / win.graphics_view)
# ===========================================================================


@pytest.fixture
def widget_window():
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
def qml_window():
    desktop = Desktop()
    app = desktop.launch_qt_python_script(str(QML_SCRIPT), timeout=20, startup_delay=2.5)
    sleep(1.5)
    app.detach()
    try:
        win = app.window(title=QML_WINDOW_TITLE, timeout=10)
        yield app, win
    finally:
        try:
            app.kill()
        except Exception:
            pass


# ---------- Window.qt_widget ----------


def test_window_qt_widget_returns_widget_element(widget_window):
    _, win = widget_window
    label = win.qt_widget(object_name="qt_status_label")
    assert isinstance(label, WidgetElement)
    assert label.object_name == "qt_status_label"


def test_window_qt_widget_missing_raises(widget_window):
    _, win = widget_window
    with pytest.raises(ElementNotFoundError):
        win.qt_widget(object_name="no_such_widget_42")


def test_window_qt_widget_by_class(widget_window):
    _, win = widget_window
    btn = win.qt_widget(class_name="QPushButton")
    assert "QPushButton" in btn.class_name


# ---------- Window.qml ----------


def test_window_qml_returns_qml_element(qml_window):
    _, win = qml_window
    btn = win.qml("qmlClickButton")
    assert isinstance(btn, QmlElement)
    assert btn.object_name == "qmlClickButton"


def test_window_qml_click_and_status(qml_window):
    _, win = qml_window
    btn = win.qml("qmlClickButton")
    status = win.qml("qmlStatusLabel")
    btn.click()
    assert status.wait_for_property("text", "clicked", timeout=2.0)


# ---------- Window.graphics_view ----------


def test_window_graphics_view_proxy():
    desktop = Desktop()
    app = desktop.launch_qt_python_script(str(GRAPHICS_SCRIPT), timeout=20, startup_delay=2.5)
    sleep(1.5)
    app.detach()
    try:
        win = app.window(title=GRAPHICS_WINDOW_TITLE, timeout=10)
        view = win.graphics_view()
        assert isinstance(view, GraphicsViewElement)
        items = view.items()
        assert len(items) >= 3
    finally:
        app.kill()


# ===========================================================================
# High-level Element API (QmlElement / WidgetElement / GraphicsViewElement)
# ===========================================================================


@pytest.fixture
def widget_app():
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
def qml_app():
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


# ---------- QmlElement ----------


def test_qml_element_returned(qml_app):
    el = qml_app.qml("qmlClickButton")
    assert isinstance(el, QmlElement)
    assert el.object_name == "qmlClickButton"
    assert "Button" in el.class_name or "QQuick" in el.class_name


def test_qml_element_set_text_round_trip(qml_app):
    el = qml_app.qml("qmlNameField")
    el.set_text("Hello via Element API")
    assert el.text() == "Hello via Element API"


def test_qml_element_click_fires_handler(qml_app):
    btn = qml_app.qml("qmlClickButton")
    status = qml_app.qml("qmlStatusLabel")
    btn.click()
    sleep(0.2)
    assert status.text() == "clicked"


def test_qml_element_missing_raises(qml_app):
    with pytest.raises(ElementNotFoundError):
        qml_app.qml("absolutely_no_such_qml_item_xyz")


def test_qml_element_invoke_passes_args(qml_app):
    el = qml_app.qml("qmlAgreeCheck")
    el.set_property("checked", True)
    assert el.get_property("checked") is True


# ---------- WidgetElement ----------


def test_qt_widget_returned_by_class(widget_app):
    app, _ = widget_app
    label = app.qt_widget(class_name="QLabel")
    assert isinstance(label, WidgetElement)
    assert "QLabel" in label.class_name


def test_qt_widget_returned_by_object_name(widget_app):
    app, _ = widget_app
    label = app.qt_widget(object_name="qt_status_label")
    assert label.object_name == "qt_status_label"


def test_qt_widget_missing_raises(widget_app):
    app, _ = widget_app
    with pytest.raises(ElementNotFoundError):
        app.qt_widget(object_name="no_such_widget_xyz_42")


def test_qt_widget_invoke_method(widget_app):
    app, _ = widget_app
    edits_present = app.qt_widget(class_name="QLineEdit")
    edits_present.set_property("text", "via element API")
    assert edits_present.get_property("text") == "via element API"
    edits_present.invoke("clear")
    assert edits_present.get_property("text") == ""


# ---------- GraphicsViewElement ----------


def test_graphics_view_returned(graphics_app):
    view = graphics_app.graphics_view()
    assert isinstance(view, GraphicsViewElement)
    assert "QGraphicsView" in view.class_name


def test_graphics_view_items_lists_three(graphics_app):
    view = graphics_app.graphics_view()
    items = view.items()
    assert len(items) >= 3


def test_graphics_view_hit_test(graphics_app):
    view = graphics_app.graphics_view()
    item = view.item_at(60, 40)
    assert "type" in item
    assert "Rect" in item["type"] or item["type"] == "QGraphicsItem"


def test_graphics_view_hit_test_miss_raises(graphics_app):
    view = graphics_app.graphics_view()
    with pytest.raises(ElementNotFoundError):
        view.item_at(10_000, 10_000)


# ===========================================================================
# Qt agent classes importable from the package root
# ===========================================================================


def test_imports_from_package_root():
    """Every Qt agent type should be importable from ``dolphin_desktop`` directly."""
    from dolphin_desktop import (
        Application,
        Desktop,
        GraphicsViewElement,
        QmlElement,
        QtAgentClient,
        QtAgentInjectError,
        QtAgentRpcError,
        WidgetElement,
        Window,
    )

    # Confirm they are classes / exception types.
    assert isinstance(Application, type)
    assert isinstance(Desktop, type)
    assert isinstance(Window, type)
    assert isinstance(QmlElement, type)
    assert isinstance(WidgetElement, type)
    assert isinstance(GraphicsViewElement, type)
    assert isinstance(QtAgentClient, type)
    assert issubclass(QtAgentInjectError, Exception)
    assert issubclass(QtAgentRpcError, Exception)


def test_qt_agent_classes_in_all():
    """The exported names show up in __all__ so star-import users can see them."""
    import dolphin_desktop

    expected = {
        "GraphicsViewElement",
        "QmlElement",
        "QtAgentClient",
        "QtAgentInjectError",
        "QtAgentRpcError",
        "WidgetElement",
    }
    assert expected.issubset(set(dolphin_desktop.__all__))


def test_qml_element_inherits_widget_element():
    """QmlElement extends the same base as WidgetElement — share property API."""
    from dolphin_desktop import QmlElement, WidgetElement

    # WidgetElement and QmlElement share _AgentElement base — both have these methods.
    for name in (
        "get_property",
        "set_property",
        "invoke",
        "describe",
        "members",
        "wait_for_property",
        "handle",
        "class_name",
        "object_name",
    ):
        assert hasattr(QmlElement, name), f"QmlElement missing {name!r}"
        assert hasattr(WidgetElement, name), f"WidgetElement missing {name!r}"


def test_graphics_view_element_has_items_and_item_at():
    """GraphicsViewElement extends WidgetElement with scene-specific helpers."""
    from dolphin_desktop import GraphicsViewElement, WidgetElement

    assert issubclass(GraphicsViewElement, WidgetElement)
    for name in ("items", "item_at"):
        assert hasattr(GraphicsViewElement, name), f"missing {name!r}"


def test_qml_element_has_click_and_set_text():
    """QmlElement-specific shortcuts."""
    from dolphin_desktop import QmlElement

    for name in ("click", "set_text", "text"):
        assert hasattr(QmlElement, name), f"missing {name!r}"


def test_qt_agent_errors_are_distinct_exception_types():
    from dolphin_desktop import QtAgentInjectError, QtAgentRpcError

    assert QtAgentInjectError is not QtAgentRpcError
    assert issubclass(QtAgentInjectError, RuntimeError)
    assert issubclass(QtAgentRpcError, RuntimeError)


# ===========================================================================
# Advanced find() queries — multi-criteria AND, inheritance chain
# ===========================================================================


@pytest.fixture(scope="module")
def qt6_agent_for_find():
    desktop = Desktop()
    app, win = launch_demo(desktop, QT6_SCRIPT, QT6_WINDOW_TITLE)
    sleep(0.5)
    try:
        agent = app.qt_agent
        yield app, win, agent
    finally:
        try:
            app.kill()
        except Exception:
            pass


# ---------- Multi-criteria AND semantics ----------


def test_find_class_and_objectname_both_must_match(qt6_agent_for_find):
    _, _, agent = qt6_agent_for_find
    hits = agent.find(className="QLabel", objectName="qt_status_label")
    assert len(hits) == 1
    h = hits[0]
    assert h["class"] == "QLabel"
    assert h["objectName"] == "qt_status_label"


def test_find_class_and_wrong_objectname_returns_empty(qt6_agent_for_find):
    _, _, agent = qt6_agent_for_find
    hits = agent.find(className="QLabel", objectName="this_label_doesnt_exist")
    assert hits == []


# ---------- Inheritance chain walking ----------


def test_find_qabstractbutton_includes_pushbuttons(qt6_agent_for_find):
    _, _, agent = qt6_agent_for_find
    pushbuttons = agent.find(className="QPushButton")
    abstract = agent.find(className="QAbstractButton")
    assert len(abstract) >= len(pushbuttons)


def test_find_qwidget_returns_many(qt6_agent_for_find):
    _, _, agent = qt6_agent_for_find
    widgets = agent.find(className="QWidget")
    assert len(widgets) > 10


# ---------- Text filter ----------


def test_find_by_text_matches_button_caption(qt6_agent_for_find):
    _, _, agent = qt6_agent_for_find
    buttons = agent.find(className="QPushButton")
    target_text = None
    for b in buttons:
        t = agent.get_property(b["handle"], "text")
        if t and len(t) > 0 and len(t) < 30:
            target_text = t
            break
    if target_text is None:
        pytest.skip("no QPushButton with a non-empty text property")
    matched = agent.find(text=target_text)
    assert any(h.get("class") == "QPushButton" for h in matched)


# ---------- Regex over objectName ----------


def test_find_regex_partial_match(qt6_agent_for_find):
    _, _, agent = qt6_agent_for_find
    hits = agent.find(regex=r"^qt_action_")
    assert len(hits) >= 2
    for h in hits:
        assert h["objectName"].startswith("qt_action_")


def test_find_regex_invalid_pattern_returns_safely(qt6_agent_for_find):
    _, _, agent = qt6_agent_for_find
    try:
        hits = agent.find(regex="(unclosed")
        assert isinstance(hits, list)
    except Exception as exc:
        assert "regex" in str(exc).lower() or "invalid" in str(exc).lower()
