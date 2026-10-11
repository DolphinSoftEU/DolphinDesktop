"""Regression tests for CDP fixture ownership and cleanup after failed launch."""

from __future__ import annotations

import pytest

from dolphin_desktop import monotonic
from tests.component import _cdp_fixture as fixture
from tests.component._cdp_probe import LaunchProbe


@pytest.mark.parametrize(
    ("owners", "accepted"),
    [({100}, True), ({200}, True), ({100, 200}, True), (set(), False), ({200, 300}, False)],
)
def test_port_owner_accepts_only_a_nonempty_verified_tree(monkeypatch, owners, accepted):
    monkeypatch.setattr(fixture, "loopback_listener_pids", lambda port: owners)
    monkeypatch.setattr(fixture, "_enumerate_descendant_pids", lambda pid: {200})
    if accepted:
        fixture.assert_port_owned_by_tree(9222, 100)
    else:
        with pytest.raises(AssertionError, match="owners"):
            fixture.assert_port_owned_by_tree(9222, 100)


def test_launch_timeout_returns_before_worker_finishes_and_cleans_late_session(
    monkeypatch, tmp_path
):
    probe = LaunchProbe()
    monkeypatch.setattr(fixture.Desktop, "launch_electron_cdp", probe.launch)
    started = monotonic()
    try:
        with pytest.raises(TimeoutError):
            fixture.ChromiumSession(
                "chrome.exe", tmp_path, 9222, "http://local", startup_timeout=0.1
            )
        assert monotonic() - started < 2
        assert probe.started.wait(1)
        assert not probe.cleaned.is_set()
    finally:
        probe.release.set()
        assert probe.cleaned.wait(2)
    assert probe.cleanup_calls == [("close", probe.launch_thread), ("kill", probe.launch_thread)]


def test_failure_after_session_acquisition_cleans_on_the_owner_thread(monkeypatch, tmp_path):
    class InvalidProcess(LaunchProbe):
        @property
        def process_id(self):
            raise RuntimeError("process identity unavailable")

    probe = InvalidProcess()
    probe.release.set()
    monkeypatch.setattr(fixture.Desktop, "launch_electron_cdp", probe.launch)
    with pytest.raises(RuntimeError, match="process identity unavailable"):
        fixture.ChromiumSession("chrome.exe", tmp_path, 9222, "http://local")
    assert probe.cleaned.wait(2)
    assert probe.cleanup_calls == [("close", probe.launch_thread), ("kill", probe.launch_thread)]


def test_launch_error_without_resources_is_propagated(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        raise RuntimeError("handshake failed")

    monkeypatch.setattr(fixture.Desktop, "launch_electron_cdp", fail)
    with pytest.raises(RuntimeError, match="handshake failed"):
        fixture.ChromiumSession("chrome.exe", tmp_path, 9222, "http://local")
