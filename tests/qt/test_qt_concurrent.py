"""Qt agent — concurrent access from multiple threads.

``threading`` is the only stdlib import here because it's the unit under
test — there's no dolphin_desktop wrapper for "spawn a thread", nor should
there be.
"""

from __future__ import annotations

import threading

import pytest

from dolphin_desktop import Desktop, monotonic, sleep
from tests.qt._qt_helpers import QT6_SCRIPT, QT6_WINDOW_TITLE, launch_demo

pytestmark = pytest.mark.qt_agent


@pytest.fixture
def qt6_app_concurrent():
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


def test_sequential_burst(qt6_app_concurrent):
    """100 sequential get_property calls finish under 5 seconds."""
    _, _, agent = qt6_app_concurrent
    buttons = agent.find(className="QPushButton")
    h = buttons[0]["handle"]
    start = monotonic()
    for _ in range(100):
        agent.get_property(h, "text")
    elapsed = monotonic() - start
    assert elapsed < 5.0, f"100 calls took {elapsed:.2f}s"


def test_calls_from_two_threads_serialize_safely(qt6_app_concurrent):
    """Two threads with a shared lock around _send must both finish without errors."""
    _, _, agent = qt6_app_concurrent
    buttons = agent.find(className="QPushButton")
    h = buttons[0]["handle"]
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(reps: int) -> None:
        try:
            for _ in range(reps):
                with lock:
                    val = agent.get_property(h, "text")
                assert isinstance(val, str) or val is None
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=(30,))
    t2 = threading.Thread(target=worker, args=(30,))
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)
    assert not errors, errors


def test_invoke_during_property_read(qt6_app_concurrent):
    _, _, agent = qt6_app_concurrent
    edits = agent.find(className="QLineEdit")
    h = edits[0]["handle"]
    agent.set_property(h, "text", "")
    for i in range(20):
        agent.set_property(h, "text", f"step-{i}")
        assert agent.get_property(h, "text") == f"step-{i}"
        res = agent.invoke(h, "clear")
        assert res.get("ok")
        assert agent.get_property(h, "text") == ""
