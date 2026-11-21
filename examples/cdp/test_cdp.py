"""Use Dolphin CDP locators against a local page in an isolated Chromium profile."""

import socket
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def cdp_page(desktop, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        executable = Path(driver.chromium.executable_path)
    if not executable.is_file():
        pytest.skip("Install Chromium: uv run --no-sync python -m playwright install chromium")
    page = tmp_path / "greeting.html"
    page.write_text(
        '<label for="name">Name</label><input id="name">'
        "<button onclick=\"document.querySelector('#result').textContent = "
        "'Hello, ' + document.querySelector('#name').value + '!'\">Greet</button>"
        '<p id="result" data-testid="result"></p>',
        encoding="utf-8",
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = subprocess.list2cmdline(
        [
            str(executable),
            "--headless=new",
            "--no-first-run",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={tmp_path / 'profile'}",
            page.as_uri(),
        ]
    )
    app, session = desktop.launch_electron_cdp(command, debug_port=port, timeout=20)
    with app:
        try:
            session.wait_for_url(page.as_uri(), timeout=10)
            yield session
        finally:
            session.close()


def test_fill_and_submit(cdp_page):
    cdp_page.get_by_label("Name").set_text("Ada")
    cdp_page.get_by_role("button", name="Greet").click()
    assert cdp_page.get_by_test_id("result").text() == "Hello, Ada!"
