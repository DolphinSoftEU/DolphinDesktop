"""End-to-end stress / lifecycle scenarios.

These tests do work the typical user wouldn't do in one test (rapid
launch/kill cycles, hundreds of pipe ops, parallel app instances) to
flush out latent bugs that only surface at scale.

Marked ``slow`` so they can be excluded from quick local runs.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, monotonic, sleep
from tests.qt._qt_helpers import QML_SCRIPT, QT6_SCRIPT, QT6_WINDOW_TITLE, launch_demo

pytestmark = [
    pytest.mark.qt_agent,
    pytest.mark.slow,
    pytest.mark.timeout(180),  # stress scenarios spawn 2-3 Qt processes per test
]


# ---------------------------------------------------------------------------
# Scenario 1 — Launch/kill cycle
# ---------------------------------------------------------------------------


def test_e2e_launch_kill_three_times_in_a_row():
    """Launch → use agent → kill, repeated 3×. Tests pipe + DLL cleanup.

    Earlier bug: the named pipe wasn't being released after kill, so the
    next launch's pipe-create failed with ERROR_PIPE_BUSY.
    """
    desktop = Desktop()
    for _ in range(3):
        app, _win = launch_demo(desktop, QT6_SCRIPT, QT6_WINDOW_TITLE)
        try:
            sleep(0.5)
            agent = app.qt_agent
            assert agent.ping() == "pong"
            assert len(agent.tree()) >= 1
        finally:
            app.kill()


# ---------------------------------------------------------------------------
# Scenario 2 — Two QML apps in parallel
# ---------------------------------------------------------------------------


def test_e2e_two_qml_apps_each_with_own_agent():
    """Two QML demo instances running side-by-side; each agent independent.

    Catches: pipe-name collision (both used \\\\.\\pipe\\dolphin_qt_<pid>;
    pid should differ → no clash) and PID-tracking confusion.
    """
    desktop = Desktop()
    app1 = desktop.launch_qt_python_script(str(QML_SCRIPT), timeout=20, startup_delay=2.5)
    sleep(1.5)
    app1.detach()

    app2 = desktop.launch_qt_python_script(str(QML_SCRIPT), timeout=20, startup_delay=2.5)
    sleep(1.5)
    app2.detach()

    try:
        a1 = app1.qt_agent
        a2 = app2.qt_agent
        assert a1.ping() == "pong"
        assert a2.ping() == "pong"

        # Each agent sees its own QML root.
        roots1 = a1.qml_root()
        roots2 = a2.qml_root()
        assert roots1 and roots2

        # Modify app1's title; app2 must NOT see the change.
        win1_h = roots1[0]["handle"]
        a1.set_property(win1_h, "title", "App ONE — modified")
        win2_titles = [r.get("title") for r in a2.qml_root()]
        assert "App ONE — modified" not in win2_titles
    finally:
        for app in (app1, app2):
            try:
                app.kill()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Scenario 3 — Burst RPC
# ---------------------------------------------------------------------------


def test_e2e_200_pipe_calls_under_8_seconds():
    """200 round-trip get_property calls must finish in under 8 s — 40 ms each.

    Realistic ceiling for a selector-heavy Qt agent test suite where each
    locator translates to ~3-5 agent calls.
    """
    desktop = Desktop()
    app, _ = launch_demo(desktop, QT6_SCRIPT, QT6_WINDOW_TITLE)
    try:
        sleep(0.5)
        agent = app.qt_agent
        buttons = agent.find(className="QPushButton")
        h = buttons[0]["handle"]

        start = monotonic()
        for _ in range(200):
            agent.get_property(h, "text")
        elapsed = monotonic() - start
        avg_ms = (elapsed / 200) * 1000
        assert elapsed < 8.0, f"200 calls took {elapsed:.2f}s (avg {avg_ms:.1f}ms)"
    finally:
        app.kill()


# ---------------------------------------------------------------------------
# Scenario 4 — Find-then-modify many widgets
# ---------------------------------------------------------------------------


def test_e2e_modify_every_label_in_demo():
    """Find every QLabel; rename each via agent; verify final state."""
    desktop = Desktop()
    app, _ = launch_demo(desktop, QT6_SCRIPT, QT6_WINDOW_TITLE)
    try:
        sleep(0.5)
        agent = app.qt_agent
        labels = agent.find(className="QLabel")
        assert labels

        # Tag each label with its index.
        for i, lbl in enumerate(labels):
            agent.set_property(lbl["handle"], "text", f"label-{i}")

        # Verify all stuck.
        for i, lbl in enumerate(labels):
            assert agent.get_property(lbl["handle"], "text") == f"label-{i}"
    finally:
        app.kill()


# ---------------------------------------------------------------------------
# Scenario 5 — Agent survives many tab switches
# ---------------------------------------------------------------------------


def test_e2e_agent_survives_repeated_tab_switching():
    """Switch between every demo tab 25 times; pipe should stay healthy.

    Uses the agent's QTabWidget::setCurrentIndex slot rather than UIA
    invoke — agent path is ~50× faster than UIA's full subtree scan, so
    we can fit 125 switches inside the 180-second timeout.
    """
    desktop = Desktop()
    app, _ = launch_demo(desktop, QT6_SCRIPT, QT6_WINDOW_TITLE)
    try:
        sleep(0.5)
        agent = app.qt_agent
        tabs = agent.find(className="QTabWidget")
        if not tabs:
            pytest.skip("no QTabWidget in demo")
        tab_h = tabs[0]["handle"]
        tab_count = int(agent.get_property(tab_h, "count") or 5)

        for _ in range(25):
            for idx in range(tab_count):
                agent.invoke(tab_h, "setCurrentIndex", idx)

        # Pipe must still respond after 125 switches.
        assert agent.ping() == "pong"
        assert len(agent.tree()) >= 1
    finally:
        app.kill()
