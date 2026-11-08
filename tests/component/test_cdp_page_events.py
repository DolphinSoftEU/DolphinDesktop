"""KAN-762: CDPSession.pages() sees a window opened between plain-sleep polls.

Real Playwright and a real headless Chromium: the bug is Playwright's sync
API delivering protocol events only while one of its calls runs, which a
fake can only imitate.
"""

from __future__ import annotations

import subprocess
import time

import pytest
from tests.component._cdp_fixture import chromium_executable, local_pages, unused_port

from dolphin_desktop import Desktop, is_cdp_available, is_windows
from dolphin_desktop import playwright_compat as pwc

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(120)]

_OPEN_BUTTON = """
const button = document.createElement("button");
button.id = "open-second";
button.textContent = "Open second";
button.addEventListener("click", () => window.open("/second", "_blank", "noopener"));
document.body.appendChild(button);
"""


def _poll_with_plain_sleep(read, predicate, *, timeout: float = 15.0):
    """Poll like DESKTOP-152 does: no Playwright call between reads."""
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if predicate(value) or time.monotonic() >= deadline:
            return value
        time.sleep(0.1)


@pytest.mark.parametrize("noopener", [True, False], ids=["noopener", "opener"])
def test_pages_lists_a_window_opened_between_plain_sleeps(tmp_path, noopener) -> None:
    assert is_windows(), "The KAN-762 page-event regression requires Windows"
    assert is_cdp_available(), "Install dolphin-desktop[cdp] before the KAN-762 regression"
    port = unused_port()
    with local_pages() as origin:
        first_url, second_url = f"{origin}/first", f"{origin}/second"
        command = subprocess.list2cmdline(
            [
                chromium_executable(),
                "--headless=new",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={tmp_path / 'profile'}",
                first_url,
            ]
        )
        app, session = Desktop(hidden=False).launch_electron_cdp(
            command, debug_port=port, timeout=30, startup_delay=0
        )
        try:
            session.wait_for_url(first_url, timeout=20)
            script = _OPEN_BUTTON if noopener else _OPEN_BUTTON.replace(', "noopener"', "")
            session.evaluate(script)
            session.locator("#open-second").click(timeout=10)

            urls = _poll_with_plain_sleep(
                lambda: [page.url for page in session.pages()],
                lambda found: second_url in found,
            )
            assert second_url in urls
            # Listing pages never retargets the session.
            assert session.evaluate("location.href") == first_url

            compat_context = pwc._PlaywrightBrowser(session).contexts[0]
            assert second_url in [page.url for page in compat_context.pages]
        finally:
            session.close()
            app.kill()
