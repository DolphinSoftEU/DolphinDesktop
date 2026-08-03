"""High-level wrappers around Qt-agent handles.

`QmlElement`, `WidgetElement`, and `GraphicsViewElement` provide
high-level, chainable ergonomics on top of the raw JSON-handle
interface exposed by :class:`QtAgentClient`. Without these wrappers the
user has to manually pass handle strings around; with them the workflow
matches the UIA widget locators.

Usage::

    app = desktop.launch_qt(...)

    # QML
    btn = app.qml("loginButton")
    btn.click()
    btn.set_property("text", "...")
    assert btn.get_property("checked") is True

    # Plain QObject (e.g. arbitrary Qt Widget)
    label = app.qt_widget(class_name="QLabel", object_name="status")
    label.set_property("text", "Hello")

    # QGraphicsView
    view = app.graphics_view(object_name="canvas")
    items = view.items()
    first = view.item_at(60, 40)
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

from ._exceptions import ElementNotFoundError, WaitTimeoutError
from ._qt_inject import QtAgentRpcError

if TYPE_CHECKING:
    from ._qt_inject import QtAgentClient

#: Q_PROPERTYs are qreal/float for opacity, QML ``value``, geometry — an
#: exact ``==`` against a literal fails on the JSON round-trip alone.
_FLOAT_REL_TOL = 1e-6
_FLOAT_ABS_TOL = 1e-9


def _agent_result(res: Any, what: str, element: _AgentElement) -> dict[str, Any]:
    """Return the result payload of *what*, or raise a diagnosable error.

    :meth:`QtAgentClient._send` already strips the reply envelope and raises on
    ``ok=false``, so a reply carrying no ``result`` key arrives here as ``None``
    rather than as a dict to interrogate.
    """
    if not isinstance(res, dict):
        raise QtAgentRpcError(
            f"{what} on {element}: the Qt agent replied ok but sent {res!r} "
            "instead of a result object",
            hint="agent DLL older than dolphin_desktop — reinstall the matching "
            "dolphin_qt5_agent.dll / dolphin_qt6_agent.dll",
        )
    if not res.get("ok", True):
        raise QtAgentRpcError(f"{what} failed on {element}: {res.get('error')}")
    return res


def _property_matches(actual: Any, expected: Any) -> bool:
    # Ints are exact by construction (row/model ids, msecs-since-epoch), and a
    # relative tolerance would call two of them equal once they pass ~1e6.
    if (
        isinstance(actual, (int, float))
        and isinstance(expected, (int, float))
        and not isinstance(actual, bool)
        and not isinstance(expected, bool)
        and (isinstance(actual, float) or isinstance(expected, float))
    ):
        return math.isclose(actual, expected, rel_tol=_FLOAT_REL_TOL, abs_tol=_FLOAT_ABS_TOL)
    return bool(actual == expected)


class _AgentElement:
    """Base for agent-handle-backed elements; not user-facing."""

    __slots__ = ("_agent", "_handle", "_meta")

    def __init__(self, agent: QtAgentClient, handle: str, meta: dict[str, Any]) -> None:
        self._agent = agent
        self._handle = handle
        self._meta = meta

    @property
    def handle(self) -> str:
        return self._handle

    @property
    def class_name(self) -> str:
        return self._meta.get("class") or self._meta.get("type") or ""

    @property
    def object_name(self) -> str:
        return self._meta.get("objectName") or ""

    def get_property(self, name: str) -> Any:
        """Read any Q_PROPERTY by name."""
        return self._agent.get_property(self._handle, name)

    def set_property(self, name: str, value: Any) -> None:
        """Write any Q_PROPERTY by name (type coerced server-side)."""
        _agent_result(
            self._agent.set_property(self._handle, name, value),
            f"set_property({name!r})",
            self,
        )

    def invoke(self, method: str, *args: Any) -> Any:
        """Invoke a meta-method (slot / Q_INVOKABLE) and return its result, if any."""
        res = _agent_result(
            self._agent.invoke(self._handle, method, *args),
            f"invoke({method!r}, {args})",
            self,
        )
        return res.get("result")

    def describe(self) -> dict[str, Any]:
        """Return every Q_PROPERTY + class info."""
        return self._agent.describe(self._handle)

    def members(self) -> dict[str, Any]:
        """List all properties / methods / signals via the meta-object."""
        return self._agent.members(self._handle)

    def wait_for_property(
        self,
        name: str,
        expected: Any,
        *,
        timeout: float = 5.0,
        poll_interval: float = 0.05,
    ) -> Any:
        """Poll ``name`` until it equals ``expected`` or *timeout* elapses.

        Useful for async UI bindings — e.g. QML ``onCheckedChanged`` updates a
        bound status label after a Qt event-loop tick. Returns the final value
        on success; raises :class:`WaitTimeoutError` otherwise.

        The property is read at least once even at ``timeout=0``, and numeric
        properties are compared with a tolerance.
        """
        deadline = time.monotonic() + timeout
        last: Any = None
        while True:
            last = self.get_property(name)
            if _property_matches(last, expected):
                return last
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_interval)
        raise WaitTimeoutError(
            f"{self}.{name} did not become {expected!r} within {timeout}s (last seen: {last!r})"
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.class_name!r}, objectName={self.object_name!r})"


class WidgetElement(_AgentElement):
    """Wrapper around any QWidget handle (also serves as QObject base)."""

    __slots__ = ()


class QmlElement(_AgentElement):
    """Wrapper around a QQuickItem handle.

    Supports synthetic click via Qt event system in addition to property /
    method access.
    """

    __slots__ = ()

    def click(self) -> None:
        """Synthesise a Qt MouseButtonPress + Release on the item's centre.

        Uses ``QCoreApplication::sendEvent`` inside the AUT, so the click is
        delivered through Qt's event system — no real cursor movement, no
        focus-stealing, no race with other foreground windows.
        """
        _agent_result(self._agent.qml_click(self._handle), "qml_click", self)

    def set_text(self, text: str) -> None:
        """Convenience: set the QML 'text' property."""
        self.set_property("text", text)

    def text(self) -> str:
        """Convenience: read the QML 'text' property."""
        return str(self.get_property("text") or "")


class GraphicsViewElement(WidgetElement):
    """Wrapper around a QGraphicsView with helpers to walk its scene."""

    __slots__ = ()

    def items(self) -> list[dict[str, Any]]:
        """Return all top-level QGraphicsItems in the view's scene."""
        return self._agent.graphics_items(self._handle)

    def item_at(self, scene_x: float, scene_y: float) -> dict[str, Any]:
        """Hit-test at scene-space coordinates."""
        res = self._agent.graphics_item_at(self._handle, scene_x, scene_y)
        if isinstance(res, dict) and res.get("ok", True) and "item" in res:
            return res["item"]
        detail = res.get("error") if isinstance(res, dict) else f"agent sent {res!r}"
        raise ElementNotFoundError(f"no item at ({scene_x}, {scene_y}) in {self}: {detail}")
