"""Headless tests for CDPSession bookkeeping (no Playwright, no Electron).

Covers the parts of the session that are pure Python: console buffering,
selector construction, which page a dead target resolves to, and which
browser context the context-level operations act on.
"""

from __future__ import annotations

import re
from contextlib import contextmanager

import pytest

from dolphin_desktop._cdp import (
    CONSOLE_BUFFER_LIMIT,
    CDPLocator,
    CDPSession,
    CDPStalePageError,
)
from dolphin_desktop._exceptions import DolphinError


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
