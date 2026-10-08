"""Qt 5 and Qt 6 authenticated agent integration smoke tests."""

from __future__ import annotations

import threading
import time
from importlib.util import find_spec

import pytest

from dolphin_desktop import Desktop, QtAgentRpcError, sleep
from dolphin_desktop._qt_inject import QtAgentClient
from tests.qt._qt_helpers import (
    QT5_SCRIPT,
    QT5_WINDOW_TITLE,
    QT6_SCRIPT,
    QT6_WINDOW_TITLE,
    launch_demo,
)

pytestmark = pytest.mark.qt_agent


@pytest.mark.parametrize(
    ("binding", "version", "script", "title"),
    [
        ("PyQt5", "5", QT5_SCRIPT, QT5_WINDOW_TITLE),
        ("PySide6", "6", QT6_SCRIPT, QT6_WINDOW_TITLE),
    ],
    ids=["qt5", "qt6"],
)
def test_authenticated_agent_attach_auth_and_stop_restart(binding, version, script, title):
    """Exercise the shipped v2 DLL against a live Qt 5 or Qt 6 process."""
    if find_spec(binding) is None:
        pytest.skip(f"{binding} is not installed")

    app, _window = launch_demo(Desktop(), script, title)
    try:
        sleep(0.5)
        assert app.qt_version() == version

        agent = app.qt_agent
        assert agent.ping() == "pong"
        assert agent.find(className="QPushButton")

        # The server must reject a wrong secret without poisoning the pipe.
        session_secret = agent._auth_token
        assert session_secret
        agent._auth_token = "wrong-session-secret"
        with pytest.raises(QtAgentRpcError, match="unauthorized"):
            agent.ping()
        agent._auth_token = session_secret
        assert agent.ping() == "pong"

        # Two Application/client objects reuse the same authenticated server.
        second = QtAgentClient.attach(app.process_id, version)
        assert second._pipe_name == agent._pipe_name
        assert second.ping() == "pong"

        # Closing one owner must not stop the server used by the other.
        agent.close()
        assert second.ping() == "pong"

        # The last explicit close stops the server; reattach starts it again.
        second.close()
        assert agent.reattach().ping() == "pong"
    finally:
        try:
            if "second" in locals():
                second.close()
        except Exception:
            pass
        try:
            agent.close()
        except Exception:
            pass
        try:
            app.kill()
        except Exception:
            pass


@pytest.mark.parametrize(
    ("binding", "version"),
    [("PyQt5", "5"), ("PySide6", "6")],
    ids=["qt5", "qt6"],
)
def test_native_object_lifetime_overloads_and_bounded_stop(binding, version, tmp_path):
    """Exercise QObject invalidation, overload selection, and stop while GUI is blocked."""
    if find_spec(binding) is None:
        pytest.skip(f"{binding} is not installed")

    if binding == "PyQt5":
        slot = "Slot"
        qt_imports = (
            "from PyQt5.QtCore import QObject, pyqtSlot as Slot\n"
            "from PyQt5.QtWidgets import (\n"
            "    QApplication, QPushButton, QVBoxLayout, QWidget,\n"
            ")"
        )
    else:
        slot = "Slot"
        qt_imports = (
            "from PySide6.QtCore import QObject, Slot\n"
            "from PySide6.QtWidgets import (\n"
            "    QApplication, QPushButton, QVBoxLayout, QWidget,\n"
            ")"
        )

    title = f"Dolphin Agent Lifetime Qt{version}"
    started_file = tmp_path / f"blocked-{version}.txt"
    script = tmp_path / f"agent-lifetime-qt{version}.py"
    script.write_text(
        f"""import sys
import time
from pathlib import Path
{qt_imports}

BLOCK_MARKER = {str(started_file)!r}
app = QApplication(sys.argv)
window = QWidget()
window.setWindowTitle({title!r})
layout = QVBoxLayout(window)

class Controller(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.window = parent
        self.widget = None
        self.replace()

    @{slot}()
    def replace(self):
        if self.widget is not None:
            self.widget.deleteLater()
        self.widget = QPushButton("live", self.window)
        self.widget.setObjectName("replaceable")
        layout.addWidget(self.widget)

    @{slot}(int)
    @{slot}(str)
    def choose(self, value):
        pass

    @{slot}()
    def block_gui(self):
        Path(BLOCK_MARKER).write_text("started", encoding="utf-8")
        time.sleep(5)

controller = Controller(window)
controller.setObjectName("agent_controller")
window.controller = controller
window.show()
app.exec()
""",
        encoding="utf-8",
    )

    app, _window = launch_demo(Desktop(), script, title)
    try:
        agent = app.qt_agent
        controller = agent.find(objectName="agent_controller")[0]["handle"]
        original = agent.find(objectName="replaceable")[0]["handle"]

        assert agent.invoke(controller, "replace")["ok"] is True
        sleep(0.3)
        recreated = agent.find(objectName="replaceable")[0]["handle"]
        assert recreated != original
        assert agent.describe(original)["ok"] is False
        assert agent.describe(recreated)["ok"] is True

        ambiguous = agent.invoke(controller, "choose", 7)
        assert ambiguous["ok"] is False
        assert "ambiguous" in ambiguous["error"]
        assert agent.invoke(controller, "choose(int)", 7)["ok"] is True

        errors = []

        def invoke_blocking_slot():
            try:
                agent.invoke(controller, "block_gui")
            except Exception as exc:
                errors.append(exc)

        rpc_thread = threading.Thread(target=invoke_blocking_slot)
        rpc_thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not started_file.exists():
            sleep(0.05)
        assert started_file.exists(), "GUI slot did not start"

        started = time.monotonic()
        agent.close()
        elapsed = time.monotonic() - started
        assert elapsed < 3.0
        rpc_thread.join(timeout=2)
        assert not rpc_thread.is_alive()
        assert errors
    finally:
        try:
            app.kill()
        except Exception:
            pass
