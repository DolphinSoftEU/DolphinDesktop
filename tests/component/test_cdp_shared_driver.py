"""KAN-746: a launched CDP session and playwright_compat share one real driver.

Real Playwright and a real headless Chromium, because the bug lives in
Playwright's own one-sync-instance-per-thread rule and greenlet thread
binding — fakes can only imitate both.
"""

from __future__ import annotations

import os
import subprocess
import threading

import pytest
from tests.component._cdp_fixture import (
    chromium_executable,
    local_pages,
    unused_port,
    wait_until,
)

from dolphin_desktop import CDPSession, Desktop, DolphinError, is_cdp_available, is_windows
from dolphin_desktop._application import _enumerate_descendant_pids
from dolphin_desktop.playwright_compat import sync_playwright

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(120)]


def _launch(tmp_path, port: int, url: str):
    command = subprocess.list2cmdline(
        [
            chromium_executable(),
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={tmp_path / 'profile'}",
            url,
        ]
    )
    app, session = Desktop(hidden=False).launch_electron_cdp(
        command, debug_port=port, timeout=30, startup_delay=0
    )
    try:
        session.wait_for_url(url, timeout=20)
    except BaseException:
        session.close()
        app.kill()
        raise
    return app, session


def _assert_no_leftover_children(before: set[int]) -> None:
    """Every driver (``node``) and browser process started here has exited."""
    me = os.getpid()
    wait_until(lambda: _enumerate_descendant_pids(me) <= before, timeout=15)


@pytest.fixture
def prerequisites():
    assert is_windows(), "The KAN-746 real-driver regression requires Windows"
    assert is_cdp_available(), "Install dolphin-desktop[cdp] before the KAN-746 regression"
    return _enumerate_descendant_pids(os.getpid())


def test_compat_facade_attaches_next_to_a_launched_session(tmp_path, prerequisites) -> None:
    port = unused_port()
    with local_pages() as origin:
        url = f"{origin}/kan746"
        app, session = _launch(tmp_path, port, url)
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=20_000)
                page = browser.contexts[0].pages[0]
                assert page.title() == "/kan746"
                assert page.locator("h1").inner_text() == "/kan746"
                browser.close()
            # Closing the facade disconnected only the facade.
            assert session.evaluate("document.title") == "/kan746"

            second = CDPSession.connect(f"http://127.0.0.1:{port}", timeout=20)
            session.close()
            assert second.evaluate("document.title") == "/kan746"
            second.close()
        finally:
            session.close()
            app.kill()
    _assert_no_leftover_children(prerequisites)


def test_close_from_a_foreign_thread_is_refused_and_the_owner_still_closes(
    tmp_path, prerequisites
) -> None:
    port = unused_port()
    with local_pages() as origin:
        url = f"{origin}/owner"
        app, session = _launch(tmp_path, port, url)
        try:
            errors: list[BaseException] = []

            def _close_elsewhere() -> None:
                try:
                    session.close()
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(target=_close_elsewhere, name="foreign-closer")
            worker.start()
            worker.join(timeout=30)

            assert len(errors) == 1 and isinstance(errors[0], DolphinError), errors
            assert "opened on thread" in str(errors[0])
            # Refused without side effects: the session still works here.
            assert session.evaluate("document.title") == "/owner"
            session.close()
        finally:
            session.close()
            app.kill()
    _assert_no_leftover_children(prerequisites)
