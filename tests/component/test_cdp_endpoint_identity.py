"""KAN-475: real CDP handshake, isolated profiles and endpoint reuse on Windows."""

from __future__ import annotations

import pytest
from tests.component._cdp_fixture import (
    ChromiumSession,
    chromium_executable,
    local_pages,
    unused_port,
    wait_until,
)

from dolphin_desktop import Desktop, is_cdp_available, is_windows
from dolphin_desktop._netinfo import loopback_listener_pids

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(90)]


def test_parallel_sessions_keep_real_target_and_profile_isolation(tmp_path) -> None:
    # Missing prerequisites fail this required regression rather than hiding it
    # behind a SKIP (the incomplete 2026-10-05 retest in KAN-475).
    assert is_windows(), "The CDP ownership retest requires Windows"
    assert is_cdp_available(), "Install dolphin-desktop[cdp] before the CDP ownership retest"
    executable = chromium_executable()
    first_port, second_port = unused_port(), unused_port()
    while second_port == first_port:
        second_port = unused_port()

    with local_pages() as origin:
        with ChromiumSession(
            executable, tmp_path / "second", second_port, f"{origin}/second"
        ) as second:
            with ChromiumSession(
                executable, tmp_path / "first", first_port, f"{origin}/first"
            ) as first:
                assert first.pid != second.pid
                assert loopback_listener_pids(first_port) == {first.pid}
                assert loopback_listener_pids(second_port) == {second.pid}
                assert first.evaluate("document.title") == "/first"
                assert second.evaluate("document.title") == "/second"

                first.evaluate(
                    "localStorage.setItem('owner', 'first'); "
                    "document.cookie = 'owner=first; path=/'"
                )
                assert second.evaluate("localStorage.getItem('owner')") is None
                assert second.evaluate("document.cookie") == ""
                second.evaluate(
                    "localStorage.setItem('owner', 'second'); "
                    "document.cookie = 'owner=second; path=/'"
                )
                assert first.evaluate("localStorage.getItem('owner')") == "first"
                assert first.evaluate("document.cookie") == "owner=first"

            wait_until(lambda: not loopback_listener_pids(first_port), timeout=5)
            assert second.evaluate("document.title") == "/second"
            assert second.evaluate("localStorage.getItem('owner')") == "second"
            assert second.evaluate("document.cookie") == "owner=second"
            assert loopback_listener_pids(second_port) == {second.pid}

            # A new HTTP 200 server on the closed session's port is foreign.
            # Reusing a port cannot inherit trust from the previous browser.
            with local_pages(first_port):
                owners = loopback_listener_pids(first_port)
                assert owners and first.pid not in owners
                with pytest.raises(RuntimeError, match="already in use"):
                    Desktop(hidden=False).launch_electron_cdp(
                        f'"{executable}" --headless=new --user-data-dir="{tmp_path / "collision"}"',
                        debug_port=first_port,
                        startup_delay=0,
                    )
                assert loopback_listener_pids(first_port) == owners
                assert second.evaluate("document.title") == "/second"

            with ChromiumSession(
                executable, tmp_path / "replacement", first_port, f"{origin}/replacement"
            ) as replacement:
                assert loopback_listener_pids(first_port) == {replacement.pid}
                assert replacement.evaluate("document.title") == "/replacement"
                assert replacement.evaluate("localStorage.getItem('owner')") is None
                assert replacement.evaluate("document.cookie") == ""

    wait_until(lambda: not loopback_listener_pids(first_port), timeout=5)
    wait_until(lambda: not loopback_listener_pids(second_port), timeout=5)
