"""Tests for the Playwright-shaped compatibility layer."""


# Fakes

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from dolphin_desktop import playwright_compat as pwc
from dolphin_desktop._cdp import CDPStalePageError


class _FakePage:
    """Stand-in for a Playwright ``Page`` as returned by ``CDPSession.pages()``."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.goto_calls: list[tuple[str, Any]] = []
        self.closed = False

    def goto(self, url: str, timeout: Any = None) -> None:
        self.goto_calls.append((url, timeout))

    def close(self) -> None:
        self.closed = True


class _FakeLocator:
    """Stand-in for ``CDPLocator``.

    Mirrors the one property that matters: ``CDPLocator._resolve()`` reads
    ``session.page`` when the action runs, not when the locator is built. A fake
    that captured the page eagerly would let a page-mixup bug pass unnoticed.
    """

    def __init__(self, session: _FakeSession, selector: str) -> None:
        self._session = session
        self.selector = selector
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.action_pages: list[_FakePage] = []

    def _record(self, name: str, kwargs: dict[str, Any]) -> None:
        self.action_pages.append(self._session.page)
        self.calls.append((name, kwargs))

    # Mouse
    def click(self, **kwargs: Any) -> None:
        self._record("click", kwargs)

    def double_click(self, **kwargs: Any) -> None:
        self._record("double_click", kwargs)

    def hover(self, **kwargs: Any) -> None:
        self._record("hover", kwargs)

    def screenshot(self, **kwargs: Any) -> None:
        self._record("screenshot", kwargs)

    # Keyboard / text
    def type_text(self, text: str, *, clear: bool = True, **kwargs: Any) -> None:
        self._record("type_text", {"text": text, "clear": clear, **kwargs})

    def press_key(self, key: str, **kwargs: Any) -> None:
        self._record("press_key", {"key": key, **kwargs})

    def clear(self, **kwargs: Any) -> None:
        self._record("clear", kwargs)

    # State reads
    def text(self, **kwargs: Any) -> str:
        self._record("text", kwargs)
        return "text"

    def value(self, **kwargs: Any) -> str:
        self._record("value", kwargs)
        return "value"

    def get_attribute(self, name: str, **kwargs: Any) -> str | None:
        self._record("get_attribute", {"name": name, **kwargs})
        return None

    def bounding_box(self, **kwargs: Any) -> dict[str, float]:
        self._record("bounding_box", kwargs)
        return {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}

    def is_visible(self) -> bool:
        self._record("is_visible", {})
        return True

    def is_enabled(self) -> bool:
        self._record("is_enabled", {})
        return True

    def is_checked(self) -> bool:
        self._record("is_checked", {})
        return True

    def wait_for(self, *, state: str = "visible", timeout: float = 10.0) -> None:
        self._record("wait_for", {"state": state, "timeout": timeout})

    # Narrowing
    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self._session, f"{self.selector} {selector}")

    def first(self) -> _FakeLocator:
        return _FakeLocator(self._session, f"{self.selector}>>first")

    def last(self) -> _FakeLocator:
        return _FakeLocator(self._session, f"{self.selector}>>last")

    def nth(self, index: int) -> _FakeLocator:
        return _FakeLocator(self._session, f"{self.selector}>>nth({index})")


class _FakeSession:
    def __init__(self, pages: list[_FakePage]) -> None:
        self._pages = pages
        self.current: _FakePage | None = pages[0] if pages else None
        self.switches: list[int] = []
        self.timeouts: list[tuple[str, float]] = []

    def pages(self) -> list[_FakePage]:
        return list(self._pages)

    def drop_page(self, page: _FakePage) -> None:
        self._pages.remove(page)

    def switch_to_page(self, index: int) -> _FakeSession:
        self.switches.append(index)
        self.current = self._pages[index]
        return self

    @property
    def page(self) -> _FakePage:
        assert self.current is not None
        return self.current

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    def current_url(self) -> str:
        return f"https://example.test/{self.page.name}"

    def wait_for_url(self, url: str, *, timeout: float) -> None:
        self.timeouts.append(("wait_for_url", timeout))

    def wait_for_load_state(self, state: str = "load", *, timeout: float) -> None:
        self.timeouts.append(("wait_for_load_state", timeout))

    def wait_for_selector(self, selector: str, *, state: str, timeout: float) -> _FakeLocator:
        self.timeouts.append(("wait_for_selector", timeout))
        return _FakeLocator(self, selector)

    def reload(self, *, timeout: float) -> None:
        self.timeouts.append(("reload", timeout))


@pytest.fixture
def two_page_context() -> tuple[_FakeSession, Any]:
    session = _FakeSession([_FakePage("first"), _FakePage("second")])
    return session, pwc._PlaywrightContext(session)  # type: ignore[arg-type]


def _lone_locator() -> tuple[_FakeSession, Any]:
    """Return a session with one page and a locator adapter bound to it."""
    session = _FakeSession([_FakePage("p")])
    page = pwc._PlaywrightPage(session, session.pages()[0])  # type: ignore[arg-type]
    return session, page.locator("#x")


# Per-page binding


class TestPageBinding:
    def test_reading_pages_does_not_retarget_the_session(self, two_page_context):
        session, context = two_page_context
        _ = context.pages
        assert session.switches == []

    def test_each_adapter_drives_its_own_page(self, two_page_context):
        session, context = two_page_context
        pages = context.pages
        pages[0].locator("#login").click()
        pages[1].locator("#login").click()
        assert session.pages()[0].name == "first"
        assert pages[0].locator("#a")._inner._session is session

    def test_locator_action_targets_its_own_page_after_a_sibling_acted(self, two_page_context):
        """The whole point: a CDPLocator resolves its page when the action runs."""
        session, context = two_page_context
        pages = context.pages
        first = pages[0].locator("#save")
        pages[1].locator("#other").click()  # retargets the shared session at page 1
        first.click()
        assert first._inner.action_pages == [session.pages()[0]]

    def test_every_locator_read_retargets_too(self, two_page_context):
        session, context = two_page_context
        pages = context.pages
        first = pages[0].locator("#label")
        pages[1].locator("#other").click()
        first.text_content()
        assert first._inner.action_pages == [session.pages()[0]]

    def test_narrowing_resolves_against_the_owning_page(self, two_page_context):
        session, context = two_page_context
        pages = context.pages
        outer = pages[0].locator("#panel")
        pages[1].locator("#other").click()
        outer.locator("button").click()
        assert session.switches[-1] == 0

    def test_wait_for_selector_locator_stays_on_its_page(self, two_page_context):
        session, context = two_page_context
        pages = context.pages
        found = pages[0].wait_for_selector("#ready")
        pages[1].locator("#other").click()
        found.click()
        assert found._inner.action_pages == [session.pages()[0]]

    def test_first_page_still_targeted_after_sibling_used(self, two_page_context):
        _, context = two_page_context
        pages = context.pages
        pages[1].locator("#other").click()
        assert pages[0].url.endswith("/first")

    def test_goto_reaches_the_bound_page(self, two_page_context):
        session, context = two_page_context
        context.pages[1].goto("https://example.test/x")
        assert session.pages()[1].goto_calls == [("https://example.test/x", None)]
        assert session.pages()[0].goto_calls == []

    def test_closed_page_reports_clearly(self, two_page_context):
        session, context = two_page_context
        adapter = context.pages[1]
        session.drop_page(session.pages()[1])
        with pytest.raises(CDPStalePageError, match="closed"):
            adapter.locator("#gone")

    def test_closed_page_error_is_in_the_dolphin_taxonomy(self, two_page_context):
        session, context = two_page_context
        adapter = context.pages[1]
        first = adapter.locator("#gone")
        session.drop_page(session.pages()[1])
        with pytest.raises(CDPStalePageError):
            first.click()

    def test_close_closes_only_its_own_page(self, two_page_context):
        session, context = two_page_context
        pages = context.pages
        pages[1].close()
        assert session.pages()[1].closed is True
        assert session.pages()[0].closed is False


# Timeout units — Playwright milliseconds in, dolphin seconds out


class TestTimeoutUnits:
    def test_goto_converts_milliseconds_to_seconds(self, two_page_context):
        session, context = two_page_context
        context.pages[0].goto("https://example.test/", timeout=60_000)
        assert session.pages()[0].goto_calls == [("https://example.test/", 60.0)]

    @pytest.mark.parametrize(
        "method, kwargs",
        [
            ("wait_for_url", {"url": "**/done"}),
            ("wait_for_load_state", {}),
            ("wait_for_selector", {"selector": "#x"}),
            ("reload", {}),
        ],
    )
    def test_page_waits_convert_ms_to_seconds(self, two_page_context, method, kwargs):
        session, context = two_page_context
        getattr(context.pages[0], method)(**kwargs, timeout=5_000)
        assert session.timeouts[-1] == (method, 5.0)

    def test_locator_wait_for_converts_ms_to_seconds(self):
        _, loc = _lone_locator()
        loc.wait_for(timeout=5_000)
        assert loc._inner.calls == [("wait_for", {"state": "visible", "timeout": 5.0})]

    @pytest.mark.parametrize("method", ["click", "dblclick", "hover", "screenshot"])
    def test_locator_action_timeout_kwarg_converted(self, method):
        _, loc = _lone_locator()
        getattr(loc, method)(timeout=5_000)
        assert loc._inner.calls[-1][1]["timeout"] == 5.0

    def test_action_without_timeout_is_untouched(self):
        _, loc = _lone_locator()
        loc.click(force=True)
        assert loc._inner.calls == [("click", {"force": True})]

    @pytest.mark.parametrize(
        "method, args",
        [
            ("fill", ("hello",)),
            ("type", ("hello",)),
            ("press", ("Enter",)),
            ("clear", ()),
            ("text_content", ()),
            ("inner_text", ()),
            ("input_value", ()),
            ("get_attribute", ("href",)),
            ("bounding_box", ()),
        ],
    )
    def test_timeout_reaches_the_inner_locator_in_seconds(self, method, args):
        """A dropped timeout silently falls back to dolphin's own default."""
        _, loc = _lone_locator()
        getattr(loc, method)(*args, timeout=60_000)
        assert loc._inner.calls[-1][1]["timeout"] == 60.0

    @pytest.mark.parametrize(
        "method, args",
        [
            ("fill", ("hello",)),
            ("type", ("hello",)),
            ("press", ("Enter",)),
            ("clear", ()),
            ("text_content", ()),
            ("get_attribute", ("href",)),
        ],
    )
    def test_omitted_timeout_leaves_the_dolphin_default_in_force(self, method, args):
        _, loc = _lone_locator()
        getattr(loc, method)(*args)
        assert "timeout" not in loc._inner.calls[-1][1]

    @pytest.mark.parametrize("method", ["is_visible", "is_enabled", "is_checked"])
    def test_state_reads_accept_playwrights_timeout_kwarg(self, method):
        """Playwright passes timeout= here; the shim must not raise on it."""
        _, loc = _lone_locator()
        assert getattr(loc, method)(timeout=5_000) is True

    def test_connect_over_cdp_converts_ms_to_seconds(self, monkeypatch):
        seen: dict[str, float] = {}

        def _connect(endpoint: str, *, timeout: float):
            seen["timeout"] = timeout
            return _FakeSession([_FakePage("p")])

        monkeypatch.setattr(pwc.CDPSession, "connect", staticmethod(_connect))
        pwc._PlaywrightChromiumBrowserType().connect_over_cdp("http://x:9222", timeout=15_000)
        assert seen["timeout"] == 15.0


# Playwright API shape


class TestPlaywrightShape:
    @pytest.mark.parametrize("name", ["first", "last"])
    def test_first_and_last_are_properties(self, name):
        assert isinstance(getattr(pwc._PlaywrightLocator, name), property)

    @pytest.mark.parametrize("name", ["first", "last"])
    def test_first_and_last_return_adapters(self, name):
        _, loc = _lone_locator()
        assert isinstance(getattr(loc, name), pwc._PlaywrightLocator)

    def test_nth_stays_a_method(self):
        _, loc = _lone_locator()
        assert isinstance(loc.nth(2), pwc._PlaywrightLocator)

    def test_context_pages_is_a_property(self):
        assert isinstance(pwc._PlaywrightContext.pages, property)

    def test_page_url_is_a_property(self):
        assert isinstance(pwc._PlaywrightPage.url, property)

    def test_fill_clears_and_type_appends(self):
        _, loc = _lone_locator()
        loc.fill("a")
        loc.type("b")
        assert [c[1]["clear"] for c in loc._inner.calls] == [True, False]


def test_playwright_page_reselects_session_before_delegation() -> None:
    from dolphin_desktop.playwright_compat import _PlaywrightPage

    session = Mock()
    page = SimpleNamespace()
    session.pages.return_value = [page]
    adapter = _PlaywrightPage(session, page)
    assert adapter._select() is session
    session.switch_to_page.assert_called_once_with(0)


def test_playwright_compat_converts_timeout_to_milliseconds() -> None:
    from dolphin_desktop.playwright_compat import _ms_kwargs, _seconds, _timeout_s

    assert _seconds(1500) == 1.5
    assert _timeout_s(2.5) == {"timeout": 0.0025}
    assert _ms_kwargs({"timeout": 1.25, "x": 1}) == {"timeout": 0.00125, "x": 1}


# Unsupported surfaces must fail explicitly rather than leaking ImportError or AttributeError.


def test_unsupported_playwright_surfaces_raise_not_implemented() -> None:
    from dolphin_desktop.playwright_compat import async_playwright, sync_playwright

    with pytest.raises(NotImplementedError, match="sync-only"):
        async_playwright()

    with sync_playwright() as playwright:
        with pytest.raises(NotImplementedError, match="Chromium"):
            _ = playwright.firefox
        with pytest.raises(NotImplementedError, match="Chromium"):
            _ = playwright.webkit
        with pytest.raises(NotImplementedError, match=r"chromium\.launch"):
            playwright.chromium.launch()


def test_unsupported_browser_context_and_page_surfaces_raise_not_implemented() -> None:
    browser = pwc._PlaywrightBrowser(Mock())
    context = pwc._PlaywrightContext(Mock())

    with pytest.raises(NotImplementedError, match="new contexts"):
        browser.new_context()
    with pytest.raises(NotImplementedError, match="new pages"):
        context.new_page()
    with pytest.raises(NotImplementedError, match="storage state"):
        context.storage_state()
    with pytest.raises(NotImplementedError, match="tracing"):
        _ = context.tracing
