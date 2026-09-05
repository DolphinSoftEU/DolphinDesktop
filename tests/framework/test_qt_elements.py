"""Headless tests for the Qt-agent element wrappers.

The agent client is replaced by a scripted stub: these cover what the
wrappers do with a reply, not how the reply reached them.
"""


from __future__ import annotations

from unittest.mock import Mock

import pytest

from dolphin_desktop._exceptions import DolphinError, ElementNotFoundError, WaitTimeoutError
from dolphin_desktop._qt_elements import GraphicsViewElement, QmlElement, WidgetElement
from dolphin_desktop._qt_inject import QtAgentRpcError


class _FakeAgent:
    """Returns whatever the test scripted for each op.

    ``QtAgentClient._send`` strips the reply envelope and raises on
    ``ok=false``, so what lands here is the ``result`` payload — including
    ``None`` when the agent sent none.
    """

    def __init__(self, **replies) -> None:
        self.replies = replies
        self.calls: list[tuple] = []
        self.properties: list = []

    def _reply(self, op, *args):
        self.calls.append((op, *args))
        value = self.replies.get(op)
        if callable(value):
            return value()
        return value

    def set_property(self, handle, prop, value):
        return self._reply("set_property", handle, prop, value)

    def invoke(self, handle, method, *args):
        return self._reply("invoke", handle, method, args)

    def get_property(self, handle, prop):
        self.calls.append(("get_property", handle, prop))
        if self.properties:
            return self.properties.pop(0)
        return self.replies.get("get_property")

    def qml_click(self, handle):
        return self._reply("qml_click", handle)

    def graphics_item_at(self, handle, x, y):
        return self._reply("graphics_item_at", handle, x, y)


def _widget(agent) -> WidgetElement:
    return WidgetElement(agent, "0xdead", {"class": "QPushButton", "objectName": "ok"})


def _qml(agent) -> QmlElement:
    return QmlElement(agent, "0xbeef", {"class": "QQuickItem", "objectName": "slider"})


def _view(agent) -> GraphicsViewElement:
    return GraphicsViewElement(agent, "0xcafe", {"class": "QGraphicsView", "objectName": "canvas"})


class TestMissingResultPayload:
    """``None`` must produce a diagnosable error, not ``AttributeError`` on
    ``NoneType.get``."""

    def test_set_property_without_a_result(self):
        element = _widget(_FakeAgent(set_property=None))
        with pytest.raises(QtAgentRpcError, match="instead of a result object"):
            element.set_property("text", "hi")

    def test_invoke_without_a_result(self):
        element = _widget(_FakeAgent(invoke=None))
        with pytest.raises(QtAgentRpcError, match="instead of a result object"):
            element.invoke("clear")

    def test_qml_click_without_a_result(self):
        element = _qml(_FakeAgent(qml_click=None))
        with pytest.raises(QtAgentRpcError, match="instead of a result object"):
            element.click()

    def test_graphics_item_at_without_a_result(self):
        element = _view(_FakeAgent(graphics_item_at=None))
        with pytest.raises(ElementNotFoundError):
            element.item_at(1.0, 2.0)

    def test_graphics_item_at_without_an_item_key(self):
        element = _view(_FakeAgent(graphics_item_at={"ok": True}))
        with pytest.raises(ElementNotFoundError):
            element.item_at(1.0, 2.0)

    def test_a_non_dict_result_is_reported_with_its_value(self):
        element = _widget(_FakeAgent(set_property="surprise"))
        with pytest.raises(QtAgentRpcError, match="surprise"):
            element.set_property("text", "hi")


class TestExceptionContract:
    """One ``except DolphinError`` has to cover every backend."""

    @pytest.mark.parametrize("reply", [None, {"ok": False, "error": "no such property"}])
    def test_set_property_failure_is_a_dolphin_error(self, reply):
        element = _widget(_FakeAgent(set_property=reply))
        with pytest.raises(DolphinError):
            element.set_property("text", "hi")

    @pytest.mark.parametrize("reply", [None, {"ok": False, "error": "no such method"}])
    def test_invoke_failure_is_a_dolphin_error(self, reply):
        element = _widget(_FakeAgent(invoke=reply))
        with pytest.raises(DolphinError):
            element.invoke("nope")

    @pytest.mark.parametrize("reply", [None, {"ok": False, "error": "not an item"}])
    def test_qml_click_failure_is_a_dolphin_error(self, reply):
        element = _qml(_FakeAgent(qml_click=reply))
        with pytest.raises(DolphinError):
            element.click()

    def test_item_at_miss_is_a_dolphin_error(self):
        element = _view(_FakeAgent(graphics_item_at={"ok": False, "error": "empty"}))
        with pytest.raises(DolphinError):
            element.item_at(0.0, 0.0)

    def test_agent_error_message_carries_the_agent_text(self):
        element = _widget(_FakeAgent(invoke={"ok": False, "error": "no such method"}))
        with pytest.raises(QtAgentRpcError, match="no such method"):
            element.invoke("nope")


class TestSuccessPath:
    def test_invoke_returns_the_inner_result(self):
        agent = _FakeAgent(invoke={"ok": True, "result": 42})
        assert _widget(agent).invoke("value") == 42

    def test_set_property_forwards_the_value(self):
        agent = _FakeAgent(set_property={"ok": True})
        _widget(agent).set_property("text", "hi")
        assert agent.calls == [("set_property", "0xdead", "text", "hi")]

    def test_item_at_returns_the_item(self):
        agent = _FakeAgent(graphics_item_at={"ok": True, "item": {"type": "Rect"}})
        assert _view(agent).item_at(60.0, 40.0) == {"type": "Rect"}

    def test_a_result_without_an_ok_key_is_not_a_failure(self):
        agent = _FakeAgent(invoke={"result": "done"})
        assert _widget(agent).invoke("go") == "done"


class TestWaitForProperty:
    def test_zero_timeout_still_reads_the_property_once(self):
        agent = _FakeAgent()
        agent.properties = ["ready"]
        assert _widget(agent).wait_for_property("state", "ready", timeout=0.0) == "ready"

    def test_zero_timeout_reports_what_it_saw(self):
        agent = _FakeAgent()
        agent.properties = ["busy"]
        with pytest.raises(WaitTimeoutError, match="last seen: 'busy'"):
            _widget(agent).wait_for_property("state", "ready", timeout=0.0)

    def test_a_later_poll_can_still_succeed(self):
        agent = _FakeAgent()
        agent.properties = ["busy", "busy", "ready"]
        got = _widget(agent).wait_for_property("state", "ready", timeout=2.0, poll_interval=0.0)
        assert got == "ready"

    def test_floats_are_compared_with_a_tolerance(self):
        agent = _FakeAgent()
        # What a float32 qreal round-trips to through JSON.
        agent.properties = [0.30000001192092896]
        assert _widget(agent).wait_for_property("opacity", 0.3, timeout=0.0) == pytest.approx(0.3)

    def test_an_int_reply_matches_a_float_expectation(self):
        agent = _FakeAgent()
        agent.properties = [1]
        assert _widget(agent).wait_for_property("value", 1.0, timeout=0.0) == 1

    def test_a_genuinely_different_float_still_times_out(self):
        agent = _FakeAgent()
        agent.properties = [0.5]
        with pytest.raises(WaitTimeoutError):
            _widget(agent).wait_for_property("opacity", 0.3, timeout=0.0)

    def test_large_ints_are_not_compared_with_a_tolerance(self):
        """A relative tolerance makes distinct ids equal past ~1e6 — model row
        ids and msecs-since-epoch live well above that."""
        agent = _FakeAgent()
        agent.properties = [10_000_005]
        with pytest.raises(WaitTimeoutError):
            _widget(agent).wait_for_property("rowId", 10_000_000, timeout=0.0)

    def test_an_exactly_equal_large_int_still_matches(self):
        agent = _FakeAgent()
        agent.properties = [10_000_000]
        got = _widget(agent).wait_for_property("rowId", 10_000_000, timeout=0.0)
        assert got == 10_000_000

    def test_booleans_are_not_compared_with_a_tolerance(self):
        agent = _FakeAgent()
        agent.properties = [True]
        with pytest.raises(WaitTimeoutError):
            _widget(agent).wait_for_property("checked", 1.0000001, timeout=0.0)


class TestSlots:
    @pytest.mark.parametrize("factory", [_widget, _qml, _view])
    def test_elements_carry_no_instance_dict(self, factory):
        element = factory(_FakeAgent())
        assert not hasattr(element, "__dict__")

    @pytest.mark.parametrize("factory", [_widget, _qml, _view])
    def test_typos_do_not_silently_become_attributes(self, factory):
        element = factory(_FakeAgent())
        with pytest.raises(AttributeError):
            element.hadnle = "typo"


def test_qt_agent_element_delegates_properties_and_reports_invalid_replies() -> None:
    from dolphin_desktop._qt_elements import WidgetElement, _agent_result
    from dolphin_desktop._qt_inject import QtAgentRpcError

    agent = Mock()
    agent.get_property.return_value = "Save"
    element = WidgetElement(agent, "h1", {"class": "QPushButton", "objectName": "save"})
    assert element.get_property("text") == "Save"
    assert repr(element) == "WidgetElement('QPushButton', objectName='save')"
    with pytest.raises(QtAgentRpcError, match="instead of a result object"):
        _agent_result(None, "describe", element)


def test_qt_element_property_matching_is_type_aware() -> None:
    from dolphin_desktop._qt_elements import _property_matches

    assert _property_matches("Save", "Save") is True
    assert _property_matches("Save", "save") is False
