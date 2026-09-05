"""Headless tests for CDPSession bookkeeping (no Playwright, no Electron).

Covers the parts of the session that are pure Python: console buffering,
selector construction, which page a dead target resolves to, and which
browser context the context-level operations act on.
"""


from __future__ import annotations

import base64
import importlib.metadata
import importlib.util
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest

import dolphin_desktop._cdp as cdp
from dolphin_desktop._cdp import (
    CONSOLE_BUFFER_LIMIT,
    CDPDownload,
    CDPFrameLocator,
    CDPLocator,
    CDPRequest,
    CDPRoute,
    CDPSession,
    CDPStalePageError,
    _LazyValue,
    _substring_text_selector,
    cdp_install_hint,
    is_cdp_available,
)
from dolphin_desktop._exceptions import DolphinError, ElementNotFoundError, WaitTimeoutError


class _FakeConsoleMessage:
    def __init__(self, type_: str, text: str) -> None:
        self.type = type_
        self.text = text


class _FakeDialog:
    def __init__(self) -> None:
        self.outcome: str | None = None

    def accept(self) -> None:
        self.outcome = "accept"

    def dismiss(self) -> None:
        self.outcome = "dismiss"


class _FakeContext:
    """Models Playwright's route registry: one entry per ``route()`` call."""

    def __init__(self, name: str = "ctx") -> None:
        self.name = name
        self.pages: list[_FakePage] = []
        self.routed: list[str] = []
        self.unrouted: list[str] = []
        self.armed: list[tuple[str, object]] = []

    def route(self, pattern, handler):
        self.routed.append(pattern)
        self.armed.append((pattern, handler))

    def unroute(self, pattern, handler=None):
        self.unrouted.append(pattern)
        if handler is None:
            self.armed = [e for e in self.armed if e[0] != pattern]
        else:
            self.armed = [e for e in self.armed if e != (pattern, handler)]


def _raising_route(pattern, handler):
    raise RuntimeError("renderer gone before the route could be armed")


class _FakePopupInfo:
    def __init__(self, page) -> None:
        self.value = page


class _FakePage:
    def __init__(self, url: str = "app://index.html", context=None) -> None:
        self._url = url
        self._closed = False
        self.context = context or _FakeContext()
        self.context.pages.append(self)
        self.events: dict[str, list] = {}
        self.popup: _FakePage | None = None

    def on(self, event, callback):
        self.events.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        listeners = self.events.get(event, [])
        if callback in listeners:
            listeners.remove(callback)

    def emit(self, event, payload):
        for callback in list(self.events.get(event, [])):
            callback(payload)

    def emit_dialog(self, dialog):
        """Playwright auto-dismisses on a page with no ``dialog`` listener."""
        listeners = self.events.get("dialog", [])
        if not listeners:
            dialog.dismiss()
            return
        for callback in list(listeners):
            callback(dialog)

    @contextmanager
    def expect_popup(self, timeout=None):
        yield _FakePopupInfo(self.popup)

    def is_closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True

    @property
    def url(self) -> str:
        if self._closed:
            raise RuntimeError("target closed")
        return self._url


class _BindFailingPage(_FakePage):
    """A renderer dying mid-bind: every listener call is a protocol round-trip."""

    def __init__(self, *args, fail_bind=(), fail_removal=(), **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fail_bind = set(fail_bind)
        self.fail_removal = set(fail_removal)

    def on(self, event, callback):
        if event in self.fail_bind:
            raise RuntimeError(f"Target closed while subscribing to {event!r}")
        super().on(event, callback)

    def remove_listener(self, event, callback):
        if event in self.fail_removal:
            # What pyee raises for a listener it does not know about.
            raise KeyError(callback)
        super().remove_listener(event, callback)


class _FakeBrowser:
    def __init__(self, contexts) -> None:
        self.contexts = list(contexts)


def _session(page=None, browser=None):
    page = page or _FakePage()
    browser = browser or _FakeBrowser([page.context])
    return CDPSession(None, browser, page.context, page, endpoint="http://127.0.0.1:9222")


class TestConsoleBuffer:
    def test_buffer_is_capped(self):
        session = _session()
        for i in range(CONSOLE_BUFFER_LIMIT + 500):
            session._on_console_message(_FakeConsoleMessage("log", f"line {i}"))
        messages = session.console_messages()
        assert len(messages) == CONSOLE_BUFFER_LIMIT

    def test_oldest_entries_are_dropped_first(self):
        session = _session()
        for i in range(CONSOLE_BUFFER_LIMIT + 3):
            session._on_console_message(_FakeConsoleMessage("log", f"line {i}"))
        messages = session.console_messages()
        assert messages[0]["text"] == "line 3"
        assert messages[-1]["text"] == f"line {CONSOLE_BUFFER_LIMIT + 2}"

    def test_clear_empties_the_buffer(self):
        session = _session()
        session._on_console_message(_FakeConsoleMessage("error", "boom"))
        session.clear_console_messages()
        assert session.console_messages() == []

    def test_console_messages_returns_a_copy(self):
        session = _session()
        session._on_console_message(_FakeConsoleMessage("log", "one"))
        session.console_messages().clear()
        assert len(session.console_messages()) == 1


class TestPageSelection:
    def test_auto_picked_page_heals_when_it_dies(self):
        dead = _FakePage(url="app://old.html")
        replacement = _FakePage(url="app://new.html", context=dead.context)
        session = _session(page=dead, browser=_FakeBrowser([dead.context]))
        dead.close()
        assert session.page is replacement

    def test_explicit_page_death_raises_instead_of_retargeting(self):
        ctx = _FakeContext()
        first = _FakePage(url="app://window0.html", context=ctx)
        second = _FakePage(url="app://window1.html", context=ctx)
        session = _session(page=first, browser=_FakeBrowser([ctx]))
        session.switch_to_page(1)
        assert session.page is second
        second.close()
        with pytest.raises(CDPStalePageError):
            _ = session.page

    def test_popup_selection_is_also_sticky(self):
        ctx = _FakeContext()
        opener = _FakePage(url="app://opener.html", context=ctx)
        popup = _FakePage(url="app://popup.html", context=ctx)
        session = _session(page=opener, browser=_FakeBrowser([ctx]))
        session._select_page(popup)
        popup.close()
        with pytest.raises(CDPStalePageError):
            _ = session.page

    def test_switch_to_page_rejects_out_of_range(self):
        session = _session()
        with pytest.raises(IndexError):
            session.switch_to_page(5)

    def test_live_explicit_selection_is_never_retargeted(self):
        ctx = _FakeContext()
        first = _FakePage(url="app://window0.html", context=ctx)
        second = _FakePage(url="app://window1.html", context=ctx)
        session = _session(page=first, browser=_FakeBrowser([ctx]))
        session.switch_to_page(1)
        assert session.page is second

    def test_reset_page_selection_recovers_a_bricked_session(self):
        ctx = _FakeContext()
        first = _FakePage(url="app://window0.html", context=ctx)
        second = _FakePage(url="app://window1.html", context=ctx)
        session = _session(page=first, browser=_FakeBrowser([ctx]))
        session.switch_to_page(1)
        second.close()
        with pytest.raises(CDPStalePageError):
            _ = session.page
        session.reset_page_selection()
        assert session.page is first

    def test_stale_page_error_names_the_way_out(self):
        ctx = _FakeContext()
        first = _FakePage(url="app://window0.html", context=ctx)
        second = _FakePage(url="app://window1.html", context=ctx)
        session = _session(page=first, browser=_FakeBrowser([ctx]))
        session.switch_to_page(1)
        second.close()
        with pytest.raises(CDPStalePageError, match="reset_page_selection"):
            _ = session.page


class TestSelfClosingPopup:
    """OAuth / "sign in with …" popups close themselves — the session has to
    survive that, not brick every later call."""

    def _session_with_popup(self):
        ctx = _FakeContext()
        opener = _FakePage(url="app://opener.html", context=ctx)
        popup = _FakePage(url="app://popup.html", context=ctx)
        opener.popup = popup
        session = _session(page=opener, browser=_FakeBrowser([ctx]))
        return session, opener, popup

    def test_popup_becomes_the_current_page(self):
        session, _opener, popup = self._session_with_popup()
        with session.expect_popup() as info:
            pass
        assert info.value is popup
        assert session.page is popup

    def test_live_popup_is_not_retargeted_to_the_opener(self):
        session, _opener, popup = self._session_with_popup()
        with session.expect_popup():
            pass
        assert session.page is popup
        assert session._explicit_page is True

    def test_closed_popup_falls_back_to_its_opener(self):
        session, opener, popup = self._session_with_popup()
        with session.expect_popup():
            pass
        popup.close()
        assert session.page is opener
        assert session._explicit_page is False

    def test_console_recording_follows_the_fallback(self):
        session, opener, popup = self._session_with_popup()
        with session.expect_popup():
            pass
        popup.close()
        _ = session.page
        opener.emit("console", _FakeConsoleMessage("error", "back home"))
        assert [m["text"] for m in session.console_messages()] == ["back home"]

    def test_dead_opener_still_refuses_to_guess(self):
        session, opener, popup = self._session_with_popup()
        with session.expect_popup():
            pass
        opener.close()
        popup.close()
        with pytest.raises(CDPStalePageError):
            _ = session.page


class TestPageEventBinding:
    def _two_pages(self):
        ctx = _FakeContext()
        first = _FakePage(url="app://a.html", context=ctx)
        second = _FakePage(url="app://b.html", context=ctx)
        session = _session(page=first, browser=_FakeBrowser([ctx]))
        return session, first, second

    def test_console_recording_follows_a_page_switch(self):
        session, _first, second = self._two_pages()
        session.switch_to_page(1)
        second.emit("console", _FakeConsoleMessage("error", "from the new page"))
        assert [m["text"] for m in session.console_messages()] == ["from the new page"]

    def test_the_page_left_behind_stops_recording(self):
        session, first, _second = self._two_pages()
        session.switch_to_page(1)
        first.emit("console", _FakeConsoleMessage("log", "stale"))
        assert session.console_messages() == []

    def test_accept_policy_follows_a_page_switch(self):
        session, _first, second = self._two_pages()
        session.accept_dialogs()
        session.switch_to_page(1)
        dialog = _FakeDialog()
        second.emit_dialog(dialog)
        assert dialog.outcome == "accept"

    def test_accept_policy_follows_a_popup(self):
        ctx = _FakeContext()
        opener = _FakePage(url="app://opener.html", context=ctx)
        popup = _FakePage(url="app://popup.html", context=ctx)
        opener.popup = popup
        session = _session(page=opener, browser=_FakeBrowser([ctx]))
        session.accept_dialogs()
        with session.expect_popup():
            pass
        dialog = _FakeDialog()
        popup.emit_dialog(dialog)
        assert dialog.outcome == "accept"

    def test_dialogs_follow_an_auto_heal(self):
        ctx = _FakeContext()
        dead = _FakePage(url="app://old.html", context=ctx)
        replacement = _FakePage(url="app://new.html", context=ctx)
        session = _session(page=dead, browser=_FakeBrowser([ctx]))
        session.accept_dialogs()
        dead.close()
        assert session.page is replacement
        dialog = _FakeDialog()
        replacement.emit_dialog(dialog)
        assert dialog.outcome == "accept"


class TestPartialListenerBind:
    """Both subscriptions are channel-backed round-trips, so the page can die
    between them — the case this code exists to survive."""

    def _pages(self, **page_kwargs):
        ctx = _FakeContext()
        first = _FakePage(url="app://a.html", context=ctx)
        half = _BindFailingPage(url="app://b.html", context=ctx, **page_kwargs)
        third = _FakePage(url="app://c.html", context=ctx)
        session = _session(page=first, browser=_FakeBrowser([ctx]))
        return session, first, half, third

    def test_the_half_bound_page_is_the_one_recorded(self):
        session, _first, half, _third = self._pages(fail_bind=("dialog",))
        session.switch_to_page(1)
        assert session._events_page is half

    def test_a_later_switch_cleans_up_what_did_bind(self):
        session, _first, half, _third = self._pages(fail_bind=("dialog",))
        session.switch_to_page(1)
        session.switch_to_page(2)
        half.emit("console", _FakeConsoleMessage("log", "orphan"))
        assert session.console_messages() == []

    def test_a_failed_console_bind_does_not_stop_the_dialog_bind(self):
        session, _first, half, _third = self._pages(fail_bind=("console",))
        session.accept_dialogs()
        session.switch_to_page(1)
        dialog = _FakeDialog()
        half.emit_dialog(dialog)
        assert dialog.outcome == "accept"


class TestListenerRemovalIsIndependent:
    def test_a_failing_console_removal_still_unbinds_the_dialog_listener(self):
        """Otherwise ``_on_dialog`` keeps answering for a page the session left."""
        ctx = _FakeContext()
        first = _BindFailingPage(url="app://a.html", context=ctx, fail_removal=("console",))
        _FakePage(url="app://b.html", context=ctx)
        session = _session(page=first, browser=_FakeBrowser([ctx]))
        session.accept_dialogs()
        session.switch_to_page(1)
        dialog = _FakeDialog()
        first.emit_dialog(dialog)
        assert dialog.outcome == "dismiss"


class TestContextFollowsPage:
    def test_context_switches_with_the_page(self):
        ctx_a = _FakeContext("a")
        ctx_b = _FakeContext("b")
        page_a = _FakePage(url="app://a.html", context=ctx_a)
        _FakePage(url="app://b.html", context=ctx_b)
        session = _session(page=page_a, browser=_FakeBrowser([ctx_a, ctx_b]))
        session.switch_to_page(1)
        assert session._context is ctx_b

    def test_routes_are_re_armed_on_the_new_context(self):
        ctx_a = _FakeContext("a")
        ctx_b = _FakeContext("b")
        page_a = _FakePage(url="app://a.html", context=ctx_a)
        _FakePage(url="app://b.html", context=ctx_b)
        session = _session(page=page_a, browser=_FakeBrowser([ctx_a, ctx_b]))
        session.route("**/api/**", lambda route: None)
        assert ctx_a.routed == ["**/api/**"]
        session.switch_to_page(1)
        assert ctx_b.routed == ["**/api/**"]

    def test_unroute_targets_the_context(self):
        session = _session()
        session.route("**/api/**", lambda route: None)
        session.unroute("**/api/**")
        assert session._context.unrouted == ["**/api/**"]

    def test_routes_are_disarmed_on_the_context_left_behind(self):
        ctx_a = _FakeContext("a")
        ctx_b = _FakeContext("b")
        page_a = _FakePage(url="app://a.html", context=ctx_a)
        _FakePage(url="app://b.html", context=ctx_b)
        session = _session(page=page_a, browser=_FakeBrowser([ctx_a, ctx_b]))
        session.route("**/api/**", lambda route: None)
        session.switch_to_page(1)
        assert ctx_a.armed == []
        assert len(ctx_b.armed) == 1

    def test_bouncing_between_contexts_does_not_stack_handlers(self):
        ctx_a = _FakeContext("a")
        ctx_b = _FakeContext("b")
        page_a = _FakePage(url="app://a.html", context=ctx_a)
        _FakePage(url="app://b.html", context=ctx_b)
        session = _session(page=page_a, browser=_FakeBrowser([ctx_a, ctx_b]))
        session.route("**/api/**", lambda route: None)
        for _ in range(3):
            session.switch_to_page(1)
            session.switch_to_page(0)
        assert len(ctx_a.armed) == 1
        assert ctx_b.armed == []

    def test_unroute_after_a_switch_reaches_the_live_handler(self):
        ctx_a = _FakeContext("a")
        ctx_b = _FakeContext("b")
        page_a = _FakePage(url="app://a.html", context=ctx_a)
        _FakePage(url="app://b.html", context=ctx_b)
        session = _session(page=page_a, browser=_FakeBrowser([ctx_a, ctx_b]))
        session.route("**/api/**", lambda route: None)
        session.switch_to_page(1)
        session.unroute("**/api/**")
        assert ctx_a.armed == []
        assert ctx_b.armed == []


class TestRouteRearmFailure:
    """The route is disarmed on the old context before the new one is tried, so
    a swallowed failure means nothing intercepts while the registry says it does."""

    def _two_contexts(self):
        ctx_a = _FakeContext("a")
        ctx_b = _FakeContext("b")
        page_a = _FakePage(url="app://a.html", context=ctx_a)
        _FakePage(url="app://b.html", context=ctx_b)
        session = _session(page=page_a, browser=_FakeBrowser([ctx_a, ctx_b]))
        return session, ctx_a, ctx_b

    def test_a_route_that_cannot_be_armed_is_reported(self):
        session, _ctx_a, ctx_b = self._two_contexts()
        session.route("**/api/**", lambda route: None)
        ctx_b.route = _raising_route
        with pytest.raises(DolphinError, match=re.escape("**/api/**")):
            session.switch_to_page(1)

    def test_a_route_that_cannot_be_armed_leaves_no_registry_entry(self):
        session, _ctx_a, ctx_b = self._two_contexts()
        session.route("**/api/**", lambda route: None)
        ctx_b.route = _raising_route
        with pytest.raises(DolphinError):
            session.switch_to_page(1)
        assert session._routes == []

    def test_the_routes_that_did_arm_are_kept(self):
        session, _ctx_a, ctx_b = self._two_contexts()
        session.route("**/api/**", lambda route: None)
        session.route("**/static/**", lambda route: None)
        armed = ctx_b.route

        def _selective(pattern, handler):
            if pattern == "**/api/**":
                _raising_route(pattern, handler)
            armed(pattern, handler)

        ctx_b.route = _selective
        with pytest.raises(DolphinError):
            session.switch_to_page(1)
        assert [pattern for pattern, _ in session._routes] == ["**/static/**"]
        assert [pattern for pattern, _ in ctx_b.armed] == ["**/static/**"]


class TestUnrouteRemovesEveryHandler:
    def test_two_handlers_for_one_pattern_are_both_removed(self):
        session = _session()
        session.route("**/api/**", lambda route: None)
        session.route("**/api/**", lambda route: None)
        assert len(session._context.armed) == 2
        session.unroute("**/api/**")
        assert session._context.armed == []

    def test_unrouting_one_pattern_leaves_the_others_armed(self):
        session = _session()
        session.route("**/api/**", lambda route: None)
        session.route("**/static/**", lambda route: None)
        session.unroute("**/api/**")
        assert [pattern for pattern, _ in session._context.armed] == ["**/static/**"]

    def test_unroute_without_a_registration_falls_back_to_the_pattern(self):
        session = _session()
        session.unroute("**/never-registered/**")
        assert session._context.unrouted == ["**/never-registered/**"]


class TestClosedSession:
    def test_page_after_close_raises_instead_of_scanning(self):
        session = _session()
        session.close()
        with pytest.raises(DolphinError, match="closed"):
            _ = session.page

    def test_close_is_idempotent(self):
        session = _session()
        closed: list[int] = []
        session._browser.close = lambda: closed.append(1)
        session.close()
        session.close()
        # The second call returns on the _closed flag alone — the browser is
        # torn down exactly once.
        assert closed == [1]
        assert session._closed is True

    @pytest.mark.parametrize(
        "call",
        [
            lambda s: s.pages(),
            lambda s: s.switch_to_page(0),
            lambda s: s.cookies(),
            lambda s: s.set_cookies([]),
            lambda s: s.clear_cookies(),
            lambda s: s.add_init_script("1"),
            lambda s: s.expose_function("probe", lambda: None),
            lambda s: s.set_extra_http_headers({}),
            lambda s: s.set_offline(True),
            lambda s: s.set_geolocation(1.0, 2.0),
            lambda s: s.grant_permissions(["geolocation"]),
            lambda s: s.clear_permissions(),
            lambda s: s.route("**/api/**", lambda route: None),
            lambda s: s.unroute("**/api/**"),
        ],
        ids=lambda call: call.__code__.co_names[0],
    )
    def test_context_level_calls_after_close_raise_a_dolphin_error(self, call):
        """Playwright answers these with a raw TargetClosedError otherwise."""
        session = _session()
        session.close()
        with pytest.raises(DolphinError, match="closed"):
            call(session)


class TestCloseReleasesSessionState:
    def test_close_unbinds_the_page_listeners(self):
        page = _FakePage()
        session = _session(page=page)
        session.close()
        assert page.events["console"] == []
        assert page.events["dialog"] == []
        assert session._events_page is None

    def test_close_drops_the_route_registry(self):
        session = _session()
        session.route("**/api/**", lambda route: None)
        session.close()
        assert session._routes == []

    def test_close_drops_the_page_references(self):
        session = _session()
        session.close()
        assert session._page is None
        assert session._opener_page is None


class TestScopedLocatorPagePinning:
    def test_scoped_locator_detects_that_its_page_died(self):
        ctx = _FakeContext()
        first = _FakePage(url="app://a.html", context=ctx)
        _FakePage(url="app://b.html", context=ctx)
        session = _session(page=first, browser=_FakeBrowser([ctx]))
        locator = CDPLocator(session, "#submit", _handle=object())
        first.close()
        with pytest.raises(CDPStalePageError, match="bound to a page that has closed"):
            locator._resolve()

    def test_scoped_locator_on_a_live_page_resolves(self):
        session = _session()
        handle = object()
        locator = CDPLocator(session, "#submit", _handle=handle)
        assert locator._resolve() is handle

    def test_selector_locators_follow_the_session(self):
        ctx = _FakeContext()
        dead = _FakePage(url="app://old.html", context=ctx)
        replacement = _FakePage(url="app://new.html", context=ctx)
        replacement.locator = lambda selector: ("locator", selector)
        session = _session(page=dead, browser=_FakeBrowser([ctx]))
        locator = session.locator("#submit")
        dead.close()
        assert locator._resolve() == ("locator", "#submit")


class TestWaitForTextSelector:
    def _selector_for(self, session, text, *, exact):
        captured = {}

        def _fake_wait_for_selector(selector, **kwargs):
            captured["selector"] = selector
            return None

        session.wait_for_selector = _fake_wait_for_selector
        session.wait_for_text(text, exact=exact)
        return captured["selector"]

    def test_exact_text_with_double_quotes_is_escaped(self):
        session = _session()
        assert self._selector_for(session, 'say "hi"', exact=True) == 'text="say \\"hi\\""'

    def test_exact_text_with_backslash_is_escaped(self):
        session = _session()
        assert self._selector_for(session, "C:\\temp", exact=True) == 'text="C:\\\\temp"'

    def test_plain_exact_text_is_quoted(self):
        session = _session()
        assert self._selector_for(session, "Save", exact=True) == 'text="Save"'

    def test_substring_text_uses_the_regex_engine(self):
        session = _session()
        assert self._selector_for(session, "Save", exact=False) == "text=/Save/i"

    def test_empty_text_is_rejected(self):
        session = _session()
        with pytest.raises(ValueError):
            session.wait_for_text("", exact=True)


#: Text that means something to Playwright's selector parser.
_HOSTILE_TEXT = [
    "a >> b",
    "/etc/passwd",
    '"quoted"',
    "'single'",
    "`backtick`",
    "50% (2/4)",
    "a >> css=div >> b",
    "cost: $5.00 [USD]",
    "back\\slash",
    "why?",
]


class TestSubstringTextIsEscaped:
    def _selector(self, text):
        session = _session()
        captured = {}
        session.wait_for_selector = lambda selector, **kw: captured.setdefault("selector", selector)
        session.wait_for_text(text, exact=False)
        return captured["selector"]

    @pytest.mark.parametrize("text", _HOSTILE_TEXT)
    def test_no_parser_significant_character_survives(self, text):
        selector = self._selector(text)
        body = selector[len("text=/") : -len("/i")]
        assert ">>" not in selector
        # The engine prefix is the only unescaped delimiter in the selector.
        assert not any(ch in body for ch in "\"'`") or "\\x" in body

    @pytest.mark.parametrize("text", _HOSTILE_TEXT)
    def test_the_escaped_pattern_still_matches_the_literal_text(self, text):
        selector = self._selector(text)
        body = selector[len("text=/") : -len("/i")]
        assert re.search(body, f"prefix {text} suffix", re.IGNORECASE)

    @pytest.mark.parametrize("text", _HOSTILE_TEXT)
    def test_the_escaped_pattern_does_not_match_unrelated_text(self, text):
        selector = self._selector(text)
        body = selector[len("text=/") : -len("/i")]
        assert not re.search(body, "something else entirely")

    def test_chain_operator_cannot_split_the_selector(self):
        assert ">" not in self._selector("a >> b")

    def test_leading_slash_does_not_reach_the_engine_raw(self):
        assert self._selector("/etc") == "text=/\\/etc/i"


class TestSubstringWhitespaceIsNormalised:
    """The plain ``text=`` engine normalises whitespace on both sides; the
    regex form only on the element's, so the literal has to carry it."""

    def _selector(self, text):
        session = _session()
        captured = {}
        session.wait_for_selector = lambda selector, **kw: captured.setdefault("selector", selector)
        session.wait_for_text(text, exact=False)
        return captured["selector"]

    def _body(self, text):
        return self._selector(text)[len("text=/") : -len("/i")]

    def test_a_run_of_spaces_matches_the_collapsed_rendering(self):
        assert re.search(self._body("Total:  42"), "the Total: 42 row")

    def test_a_newline_matches_the_collapsed_rendering(self):
        assert re.search(self._body("Sign\nin"), "Sign in")

    def test_a_tab_matches_the_collapsed_rendering(self):
        assert re.search(self._body("Sign\tin"), "Sign in")

    def test_the_uncollapsed_source_text_still_matches(self):
        assert re.search(self._body("Total:  42"), "Total:  42")

    def test_surrounding_whitespace_is_stripped(self):
        assert self._selector(" Save ") == "text=/Save/i"

    def test_whitespace_only_text_is_rejected(self):
        session = _session()
        with pytest.raises(ValueError):
            session.wait_for_text("  \n ")


class TestBindFailureDoesNotOrphanListeners:
    """``_bind_context`` raises; the event rebind must already have happened.

    In the ``page`` auto-heal branches ``self._page`` is assigned before
    binding, so an exception thrown between the two leaves the session
    returning the new page from the early-exit at the top of the property —
    the rebinding code is never reached again and the console/dialog
    listeners stay pinned to the closed page forever. Playwright then
    auto-dismisses dialogs and ``console_messages()`` answers ``[]``, so an
    "assert no console errors" check passes vacuously.

    The popup must live in a *different* context from its opener, or
    ``_bind_context`` short-circuits and never tries to re-arm.
    """

    def test_opener_fallback_rebinds_events_even_when_arming_fails(self):
        ctx_a = _FakeContext("a")
        ctx_b = _FakeContext("b")
        opener = _FakePage(url="app://opener.html", context=ctx_a)
        popup = _FakePage(url="app://popup.html", context=ctx_b)
        opener.popup = popup
        session = _session(page=opener, browser=_FakeBrowser([ctx_a, ctx_b]))

        with session.expect_popup():
            pass
        assert session.page is popup

        session.route("**/api/**", lambda route: None)
        popup.close()
        ctx_a.route = _raising_route

        with pytest.raises(DolphinError):
            _ = session.page

        assert session._events_page is opener

    def test_auto_heal_rebinds_events_even_when_arming_fails(self):
        ctx_a = _FakeContext("a")
        ctx_b = _FakeContext("b")
        dying = _FakePage(url="app://dying.html", context=ctx_a)
        survivor = _FakePage(url="app://survivor.html", context=ctx_b)
        session = _session(page=dying, browser=_FakeBrowser([ctx_a, ctx_b]))
        session.route("**/api/**", lambda route: None)
        dying.close()
        ctx_b.route = _raising_route

        try:
            _ = session.page
        except DolphinError:
            pass

        assert session._events_page is survivor


class TestPopupSurfacesArmingFailures:
    """``expect_popup`` must not swallow exceptions from ``_select_page``.

    A route that cannot be re-armed on the popup's context means requests the
    test believes are mocked now reach the real network, while ``_routes``
    reports the mock as registered. That has to surface.
    """

    def test_a_route_that_cannot_be_armed_in_the_popup_is_reported(self):
        ctx_a = _FakeContext("a")
        ctx_b = _FakeContext("b")
        opener = _FakePage(url="app://opener.html", context=ctx_a)
        popup = _FakePage(url="app://popup.html", context=ctx_b)
        opener.popup = popup
        session = _session(page=opener, browser=_FakeBrowser([ctx_a, ctx_b]))
        session.route("**/api/**", lambda route: None)
        ctx_b.route = _raising_route

        with pytest.raises(DolphinError, match=re.escape("**/api/**")):
            with session.expect_popup():
                pass


def _completeness_session():
    page = MagicMock(name="page")
    page.is_closed.return_value = False
    page.url = "app://main.html"
    context = MagicMock(name="context")
    context.pages = [page]
    browser = MagicMock(name="browser")
    browser.contexts = [context]
    playwright = MagicMock(name="playwright")
    session = CDPSession(playwright, browser, context, page, endpoint="http://127.0.0.1:9222")
    return session, page, context, browser, playwright

def _locator():
    session, page, context, browser, playwright = _completeness_session()
    handle = MagicMock(name="handle")
    locator = CDPLocator(session, "#target", _handle=handle)
    return locator, handle, session, page, context, browser, playwright

def _png_bytes() -> bytes:
    # A tiny valid PNG keeps screenshot tests independent of a browser.
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

class TestValueAndOptionalDependencyHelpers:
    def test_request_route_download_and_lazy_value_adapters(self):
        raw_request = MagicMock()
        raw_request.url = "https://example.test/api"
        raw_request.method = "POST"
        raw_request.headers = {"Content-Type": "application/json"}
        raw_request.resource_type = "fetch"
        raw_request.post_data = '{"ok":true}'
        raw_request.post_data_json = {"ok": True}
        request = CDPRequest(raw_request)
        assert request.url.endswith("/api")
        assert request.method == "POST"
        assert request.headers == {"Content-Type": "application/json"}
        assert request.resource_type == "fetch"
        assert request.post_data() == '{"ok":true}'
        assert request.post_data_json() == {"ok": True}

        raw_request.post_data_json = property(lambda _self: None)
        route = CDPRoute(MagicMock(request=raw_request))
        route.respond(status=201, body=b"ok", content_type="text/plain", headers={"X": "1"})
        route.respond_json({"ok": True}, status=202, headers={"X": "2"})
        route.pass_through()
        route.abort()
        route.abort("blockedbyclient")
        route._pw.fulfill.assert_any_call(
            status=201, body=b"ok", content_type="text/plain", headers={"X": "1"}
        )
        route._pw.fulfill.assert_any_call(status=202, json={"ok": True}, headers={"X": "2"})
        route._pw.continue_.assert_called_once_with()
        route._pw.abort.assert_any_call("failed")
        route._pw.abort.assert_any_call("blockedbyclient")

        raw_download = MagicMock(suggested_filename="report.csv", url="https://example.test/file")
        raw_download.path.return_value = 123
        download = CDPDownload(raw_download)
        assert download.suggested_filename == "report.csv"
        assert download.url.endswith("/file")
        assert download.path() == "123"
        download.save_as("copy.csv")
        download.cancel()
        download.delete()
        raw_download.save_as.assert_called_once_with("copy.csv")
        raw_download.cancel.assert_called_once_with()
        raw_download.delete.assert_called_once_with()

        info = SimpleNamespace(value=SimpleNamespace(answer=42))
        wrapper = Mock(return_value="wrapped")
        lazy = _LazyValue(info, wrap=wrapper)
        assert lazy.value == "wrapped"
        assert lazy.value == "wrapped"
        wrapper.assert_called_once_with(info.value)
        raw_lazy = _LazyValue(info)
        assert raw_lazy.value is info.value

    def test_request_post_data_json_failure_is_swallowed(self):
        class BadRequest:
            post_data_json = property(lambda self: (_ for _ in ()).throw(RuntimeError("not JSON")))

        assert CDPRequest(BadRequest()).post_data_json() is None

    @pytest.mark.parametrize("installed", [True, False])
    def test_is_cdp_available_probes_playwright(self, monkeypatch, installed):
        if installed:
            package = ModuleType("playwright")
            package.sync_api = ModuleType("playwright.sync_api")
            monkeypatch.setitem(sys.modules, "playwright", package)
            monkeypatch.setitem(sys.modules, "playwright.sync_api", package.sync_api)
        else:
            monkeypatch.setitem(sys.modules, "playwright", None)
            monkeypatch.delitem(sys.modules, "playwright.sync_api", raising=False)
        assert is_cdp_available() is installed

    def test_install_hint_and_lazy_playwright_import(self, monkeypatch):
        assert "playwright install chromium" in cdp_install_hint()
        sync_api = SimpleNamespace(marker="sync")
        package = ModuleType("playwright")
        package.sync_api = sync_api
        monkeypatch.setitem(sys.modules, "playwright", package)
        assert cdp._require_playwright() is sync_api

        monkeypatch.setitem(sys.modules, "playwright", None)
        with pytest.raises(RuntimeError, match=r"dolphin_desktop\[cdp\]"):
            cdp._require_playwright()

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (" save ", "text=/save/i"),
            ("a\u200bb\u00adc", "text=/abc/i"),
            ("a\u00a0\tb", r"text=/a\s+b/i"),
            (
                r"a\\b^c$d.e|f?g*h+i(j)k[l]m{n}/o",
                r"text=/a\\\\b\^c\$d\.e\|f\?g\*h\+i\(j\)k\[l\]m\{n\}\/o/i",
            ),
            ('a>"\'`b', r"text=/a\x3e\x22\x27\x60b/i"),
        ],
    )
    def test_substring_selector_escapes_parser_and_normalizer_characters(self, text, expected):
        assert _substring_text_selector(text) == expected

class TestSessionConstructionAndLifecycle:
    def test_event_binding_is_idempotent_and_each_listener_failure_is_isolated(self):
        session, page, _context, _browser, _playwright = _completeness_session()
        session._bind_page_events(page)
        session._bind_page_events(None)

        replacement = MagicMock()
        replacement.on.side_effect = [
            RuntimeError("console gone"),
            RuntimeError("dialog gone"),
        ]
        session._bind_page_events(replacement)
        assert session._events_page is replacement

        replacement.remove_listener.side_effect = [
            RuntimeError("console gone"),
            RuntimeError("dialog gone"),
        ]
        session._unbind_page_events(replacement)

        session._on_console_message(object())
        session._dialog_policy = "accept"
        dialog = MagicMock()
        session._on_dialog(dialog)
        dialog.accept.assert_called_once_with()
        session._dialog_policy = None
        session._on_dialog(dialog)
        dialog.dismiss.assert_called_once_with()
        session._on_dialog(object())

    def test_backend_class_helpers_delegate_to_backend_registry(self):
        backend_instance = Mock()
        backend_instance.supports.return_value = True
        with patch("dolphin_desktop._backend.resolve", return_value=backend_instance) as resolve:
            assert CDPSession.backend() is backend_instance
            assert CDPSession.backend_supports("click") is True
            CDPSession.require_capability("click")
        assert resolve.call_count == 3
        backend_instance.supports.assert_called_once_with("click")
        backend_instance.require_capability.assert_called_once_with("click")

    def test_connect_success_starts_playwright_and_uses_stable_page(self):
        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "app://stable"
        context = SimpleNamespace(pages=[page])
        browser = MagicMock(contexts=[context])
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp.return_value = browser
        sync_api = SimpleNamespace(
            sync_playwright=lambda: SimpleNamespace(start=lambda: playwright)
        )
        with patch.object(cdp, "_require_playwright", return_value=sync_api):
            session = CDPSession.connect("http://localhost:9222", timeout=2.5)
        assert session._browser is browser
        assert session._context is context
        assert session._page is page
        playwright.chromium.connect_over_cdp.assert_called_once_with(
            "http://localhost:9222", timeout=2500.0
        )

    def test_connect_wraps_connection_failure_and_stops_driver(self):
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp.side_effect = RuntimeError("refused")
        sync_api = SimpleNamespace(
            sync_playwright=lambda: SimpleNamespace(start=lambda: playwright)
        )
        with patch.object(cdp, "_require_playwright", return_value=sync_api):
            with pytest.raises(RuntimeError, match="connect_over_cdp"):
                CDPSession.connect("http://localhost:1", timeout=0.5)
        playwright.stop.assert_called_once_with()

    @pytest.mark.parametrize("has_context", [False, True])
    def test_connect_opens_fallback_page_when_no_stable_page(self, has_context):
        fallback_context = MagicMock()
        fallback_page = MagicMock()
        fallback_context.new_page.return_value = fallback_page
        browser = MagicMock(contexts=[fallback_context] if has_context else [])
        browser.new_context.return_value = fallback_context
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp.return_value = browser
        sync_api = SimpleNamespace(
            sync_playwright=lambda: SimpleNamespace(start=lambda: playwright)
        )
        with patch.object(cdp, "_require_playwright", return_value=sync_api), patch.object(
            CDPSession, "_pick_stable_page", return_value=(None, None)
        ):
            session = CDPSession.connect("http://localhost:9222")
        assert session._page is fallback_page
        if has_context:
            browser.new_context.assert_not_called()
        else:
            browser.new_context.assert_called_once_with()
        fallback_context.new_page.assert_called_once_with()

    def test_connect_cleans_up_when_stable_page_selection_fails(self):
        browser = MagicMock()
        browser.close.side_effect = RuntimeError("already gone")
        playwright = MagicMock()
        playwright.chromium.connect_over_cdp.return_value = browser
        sync_api = SimpleNamespace(
            sync_playwright=lambda: SimpleNamespace(start=lambda: playwright)
        )
        with patch.object(cdp, "_require_playwright", return_value=sync_api), patch.object(
            CDPSession, "_pick_stable_page", side_effect=RuntimeError("target list failed")
        ):
            with pytest.raises(RuntimeError, match="target list failed"):
                CDPSession.connect("http://localhost:9222")
        browser.close.assert_called_once_with()
        playwright.stop.assert_called_once_with()

    def test_page_property_auto_heals_and_explicit_page_can_reset(self):
        session, page, _context, browser, _playwright = _completeness_session()
        replacement = MagicMock()
        replacement.is_closed.return_value = False
        replacement.url = "app://replacement"
        new_context = MagicMock()
        browser.contexts = [new_context]
        new_context.pages = [replacement]
        page.is_closed.return_value = True
        assert session.page is replacement
        assert session._context is new_context

        session._page = page
        session._explicit_page = True
        session._opener_page = None
        with pytest.raises(CDPStalePageError):
            _ = session.page
        assert session.reset_page_selection() is session
        session._page = replacement
        assert session.page is replacement

    def test_page_property_explicit_popup_falls_back_to_live_opener(self):
        session, page, context, _browser, _playwright = _completeness_session()
        opener = MagicMock()
        opener.is_closed.return_value = False
        opener.url = "app://opener"
        opener.context = context
        session._page = page
        page.is_closed.return_value = True
        session._explicit_page = True
        session._opener_page = opener
        assert session.page is opener
        assert session._explicit_page is False
        assert session._opener_page is None

    def test_pick_stable_page_skips_closed_blank_chrome_and_broken_pages(self):
        closed = MagicMock()
        closed.is_closed.return_value = True
        blank = MagicMock()
        blank.is_closed.return_value = False
        blank.url = "about:blank"
        chrome = MagicMock()
        chrome.is_closed.return_value = False
        chrome.url = "chrome://newtab"
        broken = MagicMock()
        broken.is_closed.return_value = False
        type(broken).url = property(lambda self: (_ for _ in ()).throw(RuntimeError("closed")))
        stable = MagicMock()
        stable.is_closed.return_value = False
        stable.url = "app://stable"
        ctx = SimpleNamespace(pages=[closed, blank, chrome, broken, stable])
        browser = SimpleNamespace(contexts=[ctx])
        assert CDPSession._pick_stable_page(browser, timeout=0.1) == (ctx, stable)

    def test_pick_stable_page_sleeps_then_falls_back_to_blank(self):
        blank = MagicMock()
        blank.is_closed.return_value = False
        blank.url = "about:blank"
        ctx = SimpleNamespace(pages=[blank])
        browser = SimpleNamespace(contexts=[ctx])
        with patch("time.monotonic", side_effect=[0.0, 0.0, 1.0]), patch(
            "time.sleep"
        ) as sleep:
            assert CDPSession._pick_stable_page(browser, timeout=0.1) == (ctx, blank)
        sleep.assert_called_once_with(0.25)

    def test_pick_stable_page_returns_none_when_every_fallback_page_is_dead(self):
        closed = MagicMock()
        closed.is_closed.return_value = True
        broken = MagicMock()
        broken.is_closed.return_value = False
        type(broken).url = PropertyMock(side_effect=RuntimeError("target closed"))
        ctx = SimpleNamespace(pages=[closed, broken])
        assert CDPSession._pick_stable_page(SimpleNamespace(contexts=[ctx]), timeout=0) == (
            None,
            None,
        )

    def test_page_and_context_helpers_delegate(self, tmp_path):
        session, page, context, _browser, _playwright = _completeness_session()
        page.keyboard = MagicMock()
        page.mouse = MagicMock()
        page.screenshot.return_value = _png_bytes()
        page.evaluate.return_value = {"ok": True}
        assert session.evaluate("() => 1", 2) == {"ok": True}
        assert session.press_key("Enter") is session
        assert session.set_default_timeout(1.25) is session
        assert session.wait_for_load_state("networkidle", timeout=2) is session
        assert session.reload(timeout=3) is session
        assert session.screenshot() is not None
        output = tmp_path / "page.png"
        session.screenshot(str(output))
        assert output.exists()
        page.keyboard.press.assert_called_once_with("Enter")
        page.keyboard = MagicMock()
        assert session.keyboard_down("Control") is session
        assert session.keyboard_up("Control") is session
        assert session.keyboard_type("abc", delay=0.25) is session
        assert session.mouse_wheel(1, 2) is session
        assert session.mouse_move(3, 4) is session
        page.pdf.assert_not_called()
        assert session.pdf("out.pdf") is session
        page.pdf.assert_called_once_with(path="out.pdf")

        context.cookies.return_value = [{"name": "sid"}]
        assert session.cookies() == [{"name": "sid"}]
        session.local_storage_get("key")
        session.local_storage_set("key", "value")
        session.local_storage_clear()
        session.session_storage_get("key")
        session.session_storage_set("key", "value")
        session.session_storage_clear()
        assert session.frame_locator("iframe#app")._selector == "iframe#app"
        assert session.locator("#x")._selector == "#x"

        session.add_init_script("window.x=1")
        session.set_cookies([{"name": "sid", "value": "1"}])
        session.clear_cookies()
        def callback(value):
            return value

        session.expose_function("callback", callback)
        session.set_extra_http_headers({"X": "1"})
        session.set_offline(True)
        session.set_geolocation(1.0, 2.0, accuracy=3.0)
        session.grant_permissions(["geolocation"])
        session.clear_permissions()
        context.add_init_script.assert_called_once_with(script="window.x=1")
        context.add_cookies.assert_called_once_with([{"name": "sid", "value": "1"}])
        context.clear_cookies.assert_called_once_with()
        context.expose_function.assert_called_once_with("callback", callback)
        context.set_extra_http_headers.assert_called_once_with({"X": "1"})
        context.set_offline.assert_called_once_with(True)
        context.set_geolocation.assert_called_once_with(
            {"latitude": 1.0, "longitude": 2.0, "accuracy": 3.0}
        )
        context.grant_permissions.assert_called_once_with(["geolocation"])
        context.clear_permissions.assert_called_once_with()

    def test_page_and_context_error_wrappers_and_closed_guard(self):
        session, page, _context, _browser, _playwright = _completeness_session()
        page.wait_for_load_state.side_effect = RuntimeError("load timeout")
        with pytest.raises(WaitTimeoutError, match="load state"):
            session.wait_for_load_state(timeout=1)
        page.reload.side_effect = RuntimeError("reload timeout")
        with pytest.raises(RuntimeError, match="page reload failed"):
            session.reload(timeout=1)

        session.clear_console_messages()
        assert session.console_messages() == []
        session.close()
        with pytest.raises(DolphinError, match="closed"):
            session.pages()

    def test_context_route_migration_covers_success_and_failures(self):
        session, _page, old_context, _browser, _playwright = _completeness_session()
        session.route("**/api/**", lambda route: None)
        new_context = MagicMock()
        session._bind_context(new_context)
        old_context.unroute.assert_called_once()
        new_context.route.assert_called_once()

        session2, _page2, old_context2, _browser2, _playwright2 = _completeness_session()
        session2.route("**/api/**", lambda route: None)
        old_context2.unroute.side_effect = RuntimeError("old context closed")
        target_context = MagicMock()
        session2._bind_context(target_context)
        assert session2._context is target_context

        session3, _page3, _old3, _browser3, _playwright3 = _completeness_session()
        session3.route("**/api/**", lambda route: None)
        target_context3 = MagicMock()
        target_context3.route.side_effect = RuntimeError("new context closed")
        with pytest.raises(DolphinError, match="could not re-arm"):
            session3._bind_context(target_context3)

    def test_select_page_handles_page_without_context_and_alive_handles_bad_page(self):
        session, _page, _context, _browser, _playwright = _completeness_session()

        class PageWithoutContext:
            @property
            def context(self):
                raise RuntimeError("target closed")

            def on(self, event, callback):
                pass

            def is_closed(self):
                return False

            @property
            def url(self):
                return "app://page"

        session._select_page(PageWithoutContext())
        assert session._explicit_page is True
        assert CDPSession._is_page_alive(None) is False

    def test_pages_and_switch_to_page_cover_live_closed_and_invalid_entries(self):
        session, page, context, browser, _playwright = _completeness_session()
        closed = MagicMock()
        closed.is_closed.return_value = True
        broken = MagicMock()
        broken.is_closed.side_effect = RuntimeError("target gone")
        context.pages = [page, closed, broken]
        assert session.pages() == [page]
        assert session.switch_to_page(0) is session
        with pytest.raises(IndexError, match="out of range"):
            session.switch_to_page(3)
        assert browser.contexts == [context]

    def test_session_shortcut_selectors_have_expected_handles_and_selectors(self):
        session, page, _context, _browser, _playwright = _completeness_session()
        methods = [
            ("get_by_role", ("button",), {"name": "Save"}, "role=button"),
            ("get_by_text", ("Save",), {"exact": True}, "text='Save'"),
            ("get_by_label", ("Name",), {}, "label='Name'"),
            ("get_by_placeholder", ("Search",), {}, "placeholder='Search'"),
            ("get_by_title", ("Help",), {}, "title='Help'"),
            ("get_by_alt_text", ("Logo",), {}, "alt='Logo'"),
            ("get_by_test_id", ("save",), {}, "test_id='save'"),
        ]
        for method, args, kwargs, selector in methods:
            handle = MagicMock(name=method)
            getattr(page, method).return_value = handle
            result = getattr(session, method)(*args, **kwargs)
            assert isinstance(result, CDPLocator)
            assert result._selector == selector
            assert result._handle is handle
            getattr(page, method).assert_called_once_with(*args, **kwargs)

    def test_session_route_handler_and_expectation_contexts(self):
        session, page, context, _browser, _playwright = _completeness_session()
        seen = []

        def handler(route):
            seen.append(route)

        assert session.route("**/api/**", handler) is session
        registered_handler = context.route.call_args.args[1]
        raw_route = SimpleNamespace(request=SimpleNamespace(url="https://example.test"))
        registered_handler(raw_route)
        assert isinstance(seen[0], CDPRoute)

        def setup_expectation(name, value):
            info = SimpleNamespace(value=value)
            manager = MagicMock()
            manager.__enter__.return_value = info
            manager.__exit__.return_value = False
            getattr(page, name).return_value = manager
            return info

        setup_expectation("expect_response", "response")
        with session.expect_response("**/api/**", timeout=1) as response:
            assert response.value == "response"
        page.expect_response.assert_called_once_with("**/api/**", timeout=1000)
        request = SimpleNamespace(
            url="https://example.test",
            method="GET",
            headers={},
            resource_type="xhr",
            post_data=None,
        )
        setup_expectation("expect_request", request)
        with session.expect_request("**/api/**", timeout=2) as request_value:
            assert isinstance(request_value.value, CDPRequest)
        setup_expectation("expect_download", MagicMock(suggested_filename="x", url="app://x"))
        with session.expect_download(timeout=3) as download:
            assert isinstance(download.value, CDPDownload)
        session.unroute("**/api/**")
        assert session._routes == []
        session.unroute("**/never-registered/**")
        context.unroute.assert_called_with("**/never-registered/**")

    def test_popup_expectation_selects_popup_and_handles_missing_value(self):
        session, page, _context, _browser, _playwright = _completeness_session()
        popup = MagicMock()
        popup.context = page.context
        popup.is_closed.return_value = False
        popup.url = "app://popup"
        info = SimpleNamespace(value=popup)
        manager = MagicMock()
        manager.__enter__.return_value = info
        manager.__exit__.return_value = False
        page.expect_popup.return_value = manager
        with session.expect_popup(timeout=2) as result:
            assert result.value is popup
        assert session._page is popup
        assert session._explicit_page is True
        page.expect_popup.assert_called_once_with(timeout=2000)

        missing = MagicMock()
        missing.__enter__.return_value = SimpleNamespace(
            value=property(lambda _self: (_ for _ in ()).throw(RuntimeError("vanished")))
        )
        # A property cannot be evaluated through SimpleNamespace, so use an
        # object whose value property raises to reach the defensive return.
        class Missing:
            @property
            def value(self):
                raise RuntimeError("vanished")

        missing.__enter__.return_value = Missing()
        page.expect_popup.return_value = missing
        session._page = page
        with session.expect_popup():
            pass

    def test_navigation_waits_and_wait_for_selector_translate_failures(self):
        session, page, _context, _browser, _playwright = _completeness_session()
        assert session.wait_for_url("**/done", timeout=1.5) is session
        assert session.current_url() == page.url
        page.wait_for_url.side_effect = RuntimeError("still loading")
        with pytest.raises(WaitTimeoutError, match="URL did not match"):
            session.wait_for_url("**/done", timeout=1)
        page.url = property(lambda _self: (_ for _ in ()).throw(RuntimeError("gone")))

        locator = Mock()
        session.locator = Mock(return_value=locator)
        assert session.wait_for_selector("#ready", state="attached", timeout=2) is locator
        locator.wait_for.assert_called_once_with(state="attached", timeout=2)
        locator.wait_for.side_effect = WaitTimeoutError("waited")
        with pytest.raises(WaitTimeoutError, match="waited"):
            session.wait_for_selector("#ready")
        locator.wait_for.side_effect = RuntimeError("bad selector")
        with pytest.raises(WaitTimeoutError, match="did not become"):
            session.wait_for_selector("#ready")

    def test_wait_for_url_handles_unavailable_current_url_and_wait_for_text_builds_selectors(self):
        session, page, _context, _browser, _playwright = _completeness_session()
        page.wait_for_url.side_effect = RuntimeError("navigation failed")
        with patch.object(CDPSession, "page", new_callable=PropertyMock, return_value=page):
            type(page).url = PropertyMock(side_effect=RuntimeError("target closed"))
            with pytest.raises(WaitTimeoutError, match="URL did not match"):
                session.wait_for_url("**/done", timeout=1)

        session.wait_for_selector = Mock(side_effect=lambda selector, **kwargs: selector)
        assert session.wait_for_text("Hello", exact=True, timeout=2) == 'text="Hello"'
        assert session.wait_for_text("Hello world", exact=False) == "text=/Hello\\s+world/i"
        with pytest.raises(ValueError, match="matches every element"):
            session.wait_for_text("  \n ")

    def test_wait_and_context_manager_and_close_are_idempotent(self):
        session, _page, _context, browser, playwright = _completeness_session()
        assert session.dismiss_dialogs() is session
        assert session.accept_dialogs() is session
        assert session.console_messages() == []
        with session as entered:
            assert entered is session
        assert session._closed is True
        session.close()
        browser.close.assert_called_once_with()
        playwright.stop.assert_called_once_with()

        session2, _page2, _context2, browser2, playwright2 = _completeness_session()
        browser2.close.side_effect = RuntimeError("browser gone")
        playwright2.stop.side_effect = RuntimeError("driver gone")
        session2.close()
        assert session2._closed is True

class TestLocatorOperations:
    def test_locator_resolution_and_mouse_keyboard_form_operations(self):
        locator, handle, _session_obj, _page, _context, _browser, _playwright = _locator()
        assert locator._resolve() is handle
        assert locator.click(
            timeout=1.5,
            modifiers=["Control"],
            position={"x": 1, "y": 2},
            button="right",
            force=True,
        ) is locator
        handle.click.assert_called_once_with(
            timeout=1500.0,
            modifiers=["Control"],
            position={"x": 1, "y": 2},
            button="right",
            force=True,
        )
        assert locator.double_click(timeout=1) is locator
        assert locator.right_click(timeout=1) is locator
        assert locator.hover(timeout=1) is locator
        other = CDPLocator(locator._session, "#other", _handle=MagicMock())
        assert locator.drag_to(other, timeout=1) is locator
        assert locator.focus(timeout=1) is locator
        assert locator.press_key("Enter", timeout=1) is locator
        assert locator.type_text("filled", timeout=1) is locator
        assert locator.type_text("typed", timeout=2, clear=False) is locator
        assert locator.set_text("set", timeout=3) is locator
        assert locator.clear(timeout=4) is locator
        assert locator.check(timeout=5) is locator
        assert locator.uncheck(timeout=6) is locator
        assert locator.scroll_into_view(timeout=7) is locator
        handle.dblclick.assert_called_once_with(timeout=1000.0)
        handle.click.assert_any_call(button="right", timeout=1000.0)
        handle.hover.assert_called_once_with(timeout=1000.0)
        handle.focus.assert_called_once_with(timeout=1000.0)
        handle.press.assert_called_once_with("Enter", timeout=1000.0)
        handle.fill.assert_any_call("filled", timeout=1000.0)
        handle.type.assert_called_once_with("typed", timeout=2000.0)
        handle.fill.assert_any_call("set", timeout=3000.0)
        handle.fill.assert_any_call("", timeout=4000.0)
        handle.check.assert_called_once_with(timeout=5000.0)
        handle.uncheck.assert_called_once_with(timeout=6000.0)
        handle.scroll_into_view_if_needed.assert_called_once_with(timeout=7000.0)

    def test_locator_readers_waiting_selection_and_screenshot(self, tmp_path):
        locator, handle, _session_obj, _page, _context, _browser, _playwright = _locator()
        handle.select_option.return_value = ["one"]
        assert locator.select_option(value="one", label="One", index=0, timeout=1) == ["one"]
        assert locator.select_option(timeout=2) == ["one"]
        handle.inner_text.return_value = "Hello"
        handle.input_value.return_value = "hello"
        handle.get_attribute.return_value = "button"
        handle.bounding_box.return_value = {"x": 1, "y": 2, "width": 3, "height": 4}
        assert locator.text(timeout=1) == "Hello"
        assert locator.value(timeout=2) == "hello"
        assert locator.get_attribute("role", timeout=3) == "button"
        assert locator.bounding_box(timeout=4)["width"] == 3
        handle.is_visible.return_value = 1
        handle.is_enabled.return_value = 0
        handle.is_checked.return_value = True
        assert locator.is_visible() is True
        assert locator.is_enabled() is False
        assert locator.is_checked() is True
        handle.count.return_value = 2
        assert locator.exists() is True
        assert locator.exists(timeout=1) is True
        assert locator.count() == 2
        handle.wait_for.assert_called_once_with(state="attached", timeout=1000.0)
        assert locator.wait_for(state="hidden", timeout=1) is locator

        handle.screenshot.return_value = _png_bytes()
        assert locator.screenshot(timeout=1) is not None
        target = tmp_path / "element.png"
        assert locator.screenshot(str(target), timeout=2) is not None
        assert target.exists()
        handle.screenshot.assert_any_call(timeout=1000.0)
        handle.screenshot.assert_any_call(timeout=2000.0)

    def test_locator_multi_match_narrowing_and_a11y_helpers(self):
        locator, handle, _session_obj, _page, _context, _browser, _playwright = _locator()
        handle.count.return_value = 2
        nth_results = [
            MagicMock(name="direct_nth"),
            MagicMock(name="first"),
            MagicMock(name="all_first"),
            MagicMock(name="all_second"),
        ]
        handle.nth.side_effect = nth_results
        assert locator.nth(0)._handle is nth_results[0]
        first = locator.first()
        last = locator.last()
        all_locators = locator.all()
        assert isinstance(first, CDPLocator)
        assert isinstance(last, CDPLocator)
        assert len(all_locators) == 2
        assert first._handle is nth_results[1]
        assert all_locators[0]._handle is nth_results[2]
        assert all_locators[1]._handle is nth_results[3]
        handle.locator.return_value = MagicMock(name="nested")
        assert locator.locator(".child")._selector == "#target"

        has = CDPLocator(locator._session, ".has", _handle=MagicMock(name="has"))
        has_not = CDPLocator(locator._session, ".has-not", _handle=MagicMock(name="has_not"))
        handle.filter.return_value = MagicMock(name="filtered")
        filtered = locator.filter(
            has_text="yes", has_not_text="no", has=has, has_not=has_not
        )
        assert isinstance(filtered, CDPLocator)
        handle.filter.assert_called_once_with(
            has_text="yes", has_not_text="no", has=has._handle, has_not=has_not._handle
        )

        for method, args, kwargs in [
            ("get_by_role", ("button",), {"name": "Save"}),
            ("get_by_text", ("Save",), {}),
            ("get_by_label", ("Name",), {}),
            ("get_by_placeholder", ("Search",), {}),
            ("get_by_title", ("Help",), {}),
            ("get_by_alt_text", ("Logo",), {}),
            ("get_by_test_id", ("save",), {}),
        ]:
            getattr(handle, method).return_value = MagicMock(name=method)
            result = getattr(locator, method)(*args, **kwargs)
            assert isinstance(result, CDPLocator)
            getattr(handle, method).assert_called_once_with(*args, **kwargs)

    def test_locator_raw_html_events_evaluate_and_file_helpers(self):
        locator, handle, _session_obj, _page, _context, _browser, _playwright = _locator()
        handle.inner_html.return_value = "<button>Save</button>"
        assert locator.inner_html(timeout=1).startswith("<button")
        assert locator.dispatch_event("click", {"bubbles": True}, timeout=2) is locator
        assert locator.dispatch_event("blur", timeout=3) is locator
        handle.evaluate.return_value = {"id": 1}
        assert locator.evaluate("el => el.id", 1) == {"id": 1}
        assert locator.press_sequentially("abc", delay=0.1, timeout=2) is locator
        assert locator.select_text(timeout=3) is locator
        assert locator.blur(timeout=4) is locator
        assert locator.tap(timeout=5) is locator
        handle.all_text_contents.return_value = ["a", "b"]
        assert locator.all_text_contents() == ["a", "b"]
        handle.element_handle.return_value = "element-handle"
        assert locator.element_handle(timeout=6) == "element-handle"
        assert locator.set_input_files(["a.txt"], timeout=7) is locator
        handle.dispatch_event.assert_any_call("click", {"bubbles": True}, timeout=2000.0)
        handle.dispatch_event.assert_any_call("blur", {}, timeout=3000.0)
        handle.press_sequentially.assert_called_once_with(
            "abc", delay=100.0, timeout=2000.0
        )
        handle.select_text.assert_called_once_with(timeout=3000.0)
        handle.blur.assert_called_once_with(timeout=4000.0)
        handle.tap.assert_called_once_with(timeout=5000.0)
        handle.element_handle.assert_called_once_with(timeout=6000.0)
        handle.set_input_files.assert_called_once_with(["a.txt"], timeout=7000.0)

    @pytest.mark.parametrize(
        ("method", "args", "message"),
        [
            ("click", (), "click failed"),
            ("double_click", (), "double_click failed"),
            ("right_click", (), "right_click failed"),
            ("hover", (), "hover failed"),
            ("drag_to", ("other",), "drag_to failed"),
            ("focus", (), "focus failed"),
            ("press_key", ("Enter",), "press_key"),
            ("type_text", ("x",), "type_text failed"),
            ("clear", (), "clear failed"),
            ("check", (), "check failed"),
            ("uncheck", (), "uncheck failed"),
            ("select_option", (), "select_option failed"),
            ("scroll_into_view", (), "scroll_into_view failed"),
            ("text", (), r"text\(\) failed"),
            ("value", (), r"value\(\) failed"),
            ("get_attribute", ("role",), "get_attribute"),
            ("bounding_box", (), "bounding_box failed"),
            ("wait_for", (), "did not reach state"),
            ("screenshot", (), "screenshot failed"),
            ("inner_html", (), r"inner_html\(\) failed"),
            ("dispatch_event", ("click",), "dispatch_event"),
            ("evaluate", ("el => el",), "locator.evaluate"),
            ("press_sequentially", ("x",), "press_sequentially"),
            ("select_text", (), "select_text failed"),
            ("blur", (), "blur failed"),
            ("tap", (), "tap failed"),
            ("all_text_contents", (), "all_text_contents"),
            ("element_handle", (), "element_handle"),
            ("set_input_files", ("x.txt",), "set_input_files"),
        ],
    )
    def test_locator_wraps_playwright_errors(self, method, args, message):
        locator, handle, _session_obj, _page, _context, _browser, _playwright = _locator()
        other = CDPLocator(locator._session, "#other", _handle=MagicMock())
        handle_method = {
            "click": "click", "double_click": "dblclick", "right_click": "click",
            "hover": "hover", "drag_to": "drag_to", "focus": "focus", "press_key": "press",
            "type_text": "fill", "clear": "fill", "check": "check", "uncheck": "uncheck",
            "select_option": "select_option", "scroll_into_view": "scroll_into_view_if_needed",
            "text": "inner_text", "value": "input_value", "get_attribute": "get_attribute",
            "bounding_box": "bounding_box", "wait_for": "wait_for", "screenshot": "screenshot",
            "inner_html": "inner_html", "dispatch_event": "dispatch_event", "evaluate": "evaluate",
            "press_sequentially": "press_sequentially",
            "select_text": "select_text", "blur": "blur",
            "tap": "tap", "all_text_contents": "all_text_contents",
            "element_handle": "element_handle",
            "set_input_files": "set_input_files",
        }[method]
        getattr(handle, handle_method).side_effect = RuntimeError("boom")
        call_args = (other,) if method == "drag_to" else args
        expected = ElementNotFoundError if method not in {"wait_for"} else WaitTimeoutError
        with pytest.raises(expected, match=message):
            getattr(locator, method)(*call_args)

    def test_locator_boolean_and_count_failures_are_safe(self):
        locator, handle, _session_obj, _page, _context, _browser, _playwright = _locator()
        handle.is_visible.side_effect = RuntimeError()
        handle.is_enabled.side_effect = RuntimeError()
        handle.is_checked.side_effect = RuntimeError()
        handle.count.side_effect = RuntimeError()
        handle.wait_for.side_effect = RuntimeError()
        assert locator.is_visible() is False
        assert locator.is_enabled() is False
        assert locator.is_checked() is False
        assert locator.count() == 0
        assert locator.exists() is False
        assert locator.exists(timeout=1) is False

    def test_locator_scoped_handle_detects_dead_page(self):
        locator, _handle, session, page, _context, _browser, _playwright = _locator()
        page.is_closed.return_value = True
        with pytest.raises(CDPStalePageError, match="bound to a page"):
            locator._resolve()
        unscoped = CDPLocator(session, "#target")
        page.is_closed.return_value = False
        page.locator.return_value = "fresh-handle"
        assert unscoped._resolve() == "fresh-handle"

class TestFrameLocatorAndImport:
    def test_frame_locator_resolves_and_builds_nested_locator_selectors(self):
        session, page, _context, _browser, _playwright = _completeness_session()
        frame = MagicMock(name="frame")
        page.frame_locator.return_value = frame
        frame.locator.return_value = MagicMock(name="inside")
        frame.get_by_role.return_value = MagicMock(name="role")
        frame.get_by_text.return_value = MagicMock(name="text")
        frame.get_by_label.return_value = MagicMock(name="label")
        frames = CDPFrameLocator(session, "iframe#app")
        assert frames._resolve() is frame
        assert frames.locator("#button")._selector == "iframe#app >> #button"
        assert frames.get_by_role("button", name="Save")._selector == (
            "frame/iframe#app >> role=button"
        )
        assert frames.get_by_text("Save")._selector == "frame/iframe#app >> text='Save'"
        assert frames.get_by_label("Name")._selector == "frame/iframe#app >> label='Name'"
        page.frame_locator.assert_called()

    def test_module_can_be_imported_in_a_fresh_namespace(self):
        """Cover import-time declarations when pytest preloads the package."""
        module_name = "dolphin_desktop._cdp_completeness_probe"
        spec = importlib.util.spec_from_file_location(module_name, cdp.__file__)
        assert spec is not None and spec.loader is not None
        probe = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = probe
        try:
            spec.loader.exec_module(probe)
            assert probe.CONSOLE_BUFFER_LIMIT == 1000
            assert probe.CDPSession.backend_id == "cdp"
        finally:
            sys.modules.pop(module_name, None)


def test_cdp_download_delegates_and_text_selector_normalizes_whitespace() -> None:
    from dolphin_desktop._cdp import CDPDownload, _substring_text_selector

    download = Mock(suggested_filename="report.csv", url="https://example.test/report")
    download.path.return_value = Path("temporary.csv")
    wrapped = CDPDownload(download)
    assert wrapped.suggested_filename == "report.csv"
    assert wrapped.path() == "temporary.csv"
    wrapped.save_as("report.csv")
    wrapped.cancel()
    wrapped.delete()
    assert "\\s+" in _substring_text_selector("  Save   now ")


def test_cdp_request_route_and_lazy_value_wrap_playwright_objects() -> None:
    from dolphin_desktop._cdp import CDPRequest, CDPRoute, _LazyValue, _substring_text_selector

    request = SimpleNamespace(
        url="https://example.test",
        method="POST",
        headers={"A": "B"},
        resource_type="xhr",
        post_data="{}",
        post_data_json={"ok": True},
    )
    route = Mock(request=request)
    wrapped = CDPRoute(route)
    assert CDPRequest(request).headers == {"A": "B"}
    assert wrapped.request.post_data_json() == {"ok": True}
    wrapped.respond_json({"ok": True}, status=201)
    route.fulfill.assert_called_once_with(status=201, json={"ok": True})
    value = _LazyValue(SimpleNamespace(value=3), lambda n: n * 2)
    assert (value.value, value.value) == (6, 6)
    assert _substring_text_selector(' Save >> "now" ').startswith("text=/")


def test_cdp_wrappers_forward_request_route_download_and_missing_dependency(monkeypatch) -> None:
    import dolphin_desktop._cdp as cdp

    class Request:
        def __init__(self):
            self.url = "https://example.test/api"
            self.method = "GET"
            self.headers = {"Accept": "application/json"}
            self.resource_type = "fetch"
            self.post_data = None

        @property
        def post_data_json(self):
            raise RuntimeError("not json")

    request = cdp.CDPRequest(Request())
    assert request.url.endswith("/api")
    assert request.method == "GET"
    assert request.headers == {"Accept": "application/json"}
    assert request.resource_type == "fetch"
    assert request.post_data() is None
    assert request.post_data_json() is None

    raw_route = Mock(request=Request())
    route = cdp.CDPRoute(raw_route)
    route.respond(status=201, body="ok", content_type="text/plain", headers={"X": "1"})
    route.respond_json({"ok": True}, status=202, headers={"X": "2"})
    route.pass_through()
    route.abort("blockedbyclient")
    raw_route.fulfill.assert_any_call(
        status=201,
        body="ok",
        content_type="text/plain",
        headers={"X": "1"},
    )
    raw_route.fulfill.assert_any_call(status=202, json={"ok": True}, headers={"X": "2"})
    raw_route.continue_.assert_called_once_with()
    raw_route.abort.assert_called_once_with("blockedbyclient")

    raw_download = Mock(suggested_filename="a.txt", url="https://example.test/a")
    raw_download.path.return_value = 123
    download = cdp.CDPDownload(raw_download)
    assert download.suggested_filename == "a.txt"
    assert download.url.endswith("/a")
    assert download.path() == "123"
    download.save_as("copy.txt")
    download.cancel()
    download.delete()
    raw_download.save_as.assert_called_once_with("copy.txt")

    monkeypatch.setitem(sys.modules, "playwright", None)
    with pytest.raises(RuntimeError, match=r"dolphin_desktop\[cdp\]"):
        cdp._require_playwright()
    assert "playwright install chromium" in cdp.cdp_install_hint()


def test_cdp_stable_page_picker_skips_closed_pages_and_uses_fallback() -> None:
    from dolphin_desktop._cdp import CDPSession

    class Page:
        def __init__(self, url, closed=False):
            self.url = url
            self.closed = closed

        def is_closed(self):
            return self.closed

    stable = Page("https://example.test")
    blank = Page("about:blank")
    closed = Page("https://closed.test", closed=True)
    browser = SimpleNamespace(
        contexts=[SimpleNamespace(pages=[closed, blank]), SimpleNamespace(pages=[stable])]
    )
    context, page = CDPSession._pick_stable_page(browser, timeout=0.1)
    assert page is stable
    assert context is browser.contexts[1]

    fallback_browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[closed, blank])])
    context, page = CDPSession._pick_stable_page(fallback_browser, timeout=0)
    assert context is fallback_browser.contexts[0]
    assert page is blank
    assert CDPSession._pick_stable_page(SimpleNamespace(contexts=[]), timeout=0) == (None, None)


def test_cdp_session_binds_events_and_applies_dialog_policy() -> None:
    from dolphin_desktop._cdp import CDPSession

    page = Mock()
    session = CDPSession(Mock(), Mock(), Mock(), page, endpoint="http://localhost")
    session._on_console_message(SimpleNamespace(type="warning", text="careful"))
    assert session.console_messages() == [{"type": "warning", "text": "careful"}]

    dialog = Mock()
    session._dialog_policy = "accept"
    session._on_dialog(dialog)
    dialog.accept.assert_called_once_with()
    session._dialog_policy = "dismiss"
    session._on_dialog(dialog)
    dialog.dismiss.assert_called_once_with()
    assert page.on.call_count == 2
