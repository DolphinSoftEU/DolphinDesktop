"""Fake ``playwright.sync_api`` shared by the CDP session and compat-layer tests."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

SECOND_START_ERROR = "It looks like you are using Playwright Sync API inside the asyncio loop."


class CountingSyncApi:
    """Mimics Playwright's one-sync-instance-per-thread rule.

    ``start()`` raises Playwright's real message while an earlier driver
    started on the same thread is still running, and every
    ``connect_over_cdp`` returns a distinct browser, like separate CDP
    connections do.
    """

    def __init__(self) -> None:
        self.drivers: list[MagicMock] = []
        self.browsers: list[MagicMock] = []
        self.running: set[int] = set()

    def _start(self) -> MagicMock:
        ident = threading.get_ident()
        if ident in self.running:
            raise RuntimeError(SECOND_START_ERROR)
        self.running.add(ident)
        driver = MagicMock(name=f"driver{len(self.drivers)}")
        driver.stop.side_effect = lambda: self.running.discard(ident)
        driver.chromium.connect_over_cdp.side_effect = self._connect
        self.drivers.append(driver)
        return driver

    def _connect(self, endpoint: str, timeout: float) -> MagicMock:
        page = MagicMock(name=f"page{len(self.browsers)}")
        page.is_closed.return_value = False
        page.url = "app://main.html"
        browser = MagicMock(name=f"browser{len(self.browsers)}")
        browser.contexts = [SimpleNamespace(pages=[page])]
        self.browsers.append(browser)
        return browser

    def sync_playwright(self) -> SimpleNamespace:
        return SimpleNamespace(start=self._start)
