"""Chrome DevTools Protocol adapter for Electron / embedded-web apps.

Windows UIA is blind to two common cases:

* Packaged Electron apps launched without ``--force-renderer-accessibility``
  (Spotify, Steam, VS Code out-of-the-box).
* Any element rendered inside a closed Shadow DOM root.

CDP (Chrome DevTools Protocol) is the fallback. Electron ships a Chromium
runtime that exposes CDP on ``--remote-debugging-port``; Playwright talks
CDP natively via :meth:`~playwright.sync_api.BrowserType.connect_over_cdp`,
and its CSS engine pierces open Shadow DOM roots by default. This module
wraps Playwright behind an API shaped like :class:`Locator` so that a
dolphin_desktop user never has to touch Playwright directly.

Install::

    pip install "dolphin-desktop[cdp]"
    playwright install chromium

Basic usage::

    from dolphin_desktop import Desktop

    app, cdp = Desktop().launch_electron_cdp(
        r'"C:\\Program Files\\App\\App.exe" --no-sandbox',
        debug_port=9222,
    )
    cdp.locator("#login-button").click()
    cdp.locator("app-shell >> #username").type_text("alice")
    assert cdp.locator("#status").text() == "OK"
    cdp.close()
    app.kill()
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

from ._exceptions import DolphinError, ElementNotFoundError, WaitTimeoutError

if TYPE_CHECKING:  # pragma: no cover — types only, never imported at runtime
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright


_INSTALL_HINT = (
    "dolphin_desktop[cdp] is not installed — run:\n"
    '    pip install "dolphin-desktop[cdp]"\n'
    "    playwright install chromium"
)

#: Console entries retained per session; older entries are discarded.
CONSOLE_BUFFER_LIMIT = 1000

#: Characters Playwright's text normaliser deletes outright before matching.
#: A literal that carries one can only match if the selector drops it too.
_SELECTOR_ZERO_WIDTH = "\u200b\u00ad"

#: Whitespace Playwright's text engine collapses before matching — JavaScript's
#: ``\s`` class, the one the normaliser's ``replace(/\s+/g, " ")`` uses, which is
#: wider than the ASCII set and than Python's own view of whitespace.
_SELECTOR_WHITESPACE = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)

#: Neither class survives normalisation, so neither can anchor a match.
_SELECTOR_TRIMMABLE = _SELECTOR_WHITESPACE + _SELECTOR_ZERO_WIDTH


class CDPStalePageError(DolphinError):
    """Raised when the explicitly selected page has closed.

    Only an explicitly selected page (:meth:`CDPSession.switch_to_page`,
    :meth:`CDPSession.expect_popup`) raises this. Substituting another window
    would run the action against a different renderer, so the session refuses
    to guess.
    """


class CDPRequest:
    """Read-only view of a network request seen by CDP.

    Exposed to :meth:`CDPSession.route` handlers so tests can decide how
    to respond without touching Playwright directly.
    """

    def __init__(self, pw_request: Any) -> None:
        self._pw = pw_request

    @property
    def url(self) -> str:
        return self._pw.url

    @property
    def method(self) -> str:
        return self._pw.method

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._pw.headers)

    @property
    def resource_type(self) -> str:
        """One of ``document``, ``xhr``, ``fetch``, ``script``, …"""
        return self._pw.resource_type

    def post_data(self) -> str | None:
        return self._pw.post_data

    def post_data_json(self) -> Any:
        try:
            return self._pw.post_data_json
        except Exception:
            return None


class CDPRoute:
    """Handle for a routed request — decide how to complete it.

    Yielded to callbacks registered with :meth:`CDPSession.route`. The
    handler must call exactly one of :meth:`respond`, :meth:`pass_through`
    or :meth:`abort` per request or the network layer will hang.
    """

    def __init__(self, pw_route: Any) -> None:
        self._pw = pw_route
        self.request = CDPRequest(pw_route.request)

    def respond(
        self,
        *,
        status: int = 200,
        body: str | bytes = "",
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Fulfil the request locally with the supplied response."""
        kwargs: dict[str, Any] = {"status": status, "body": body}
        if content_type is not None:
            kwargs["content_type"] = content_type
        if headers is not None:
            kwargs["headers"] = headers
        self._pw.fulfill(**kwargs)

    def respond_json(
        self,
        payload: Any,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Fulfil with a JSON payload (Playwright's ``fulfill(json=…)``)."""
        kwargs: dict[str, Any] = {"status": status, "json": payload}
        if headers is not None:
            kwargs["headers"] = headers
        self._pw.fulfill(**kwargs)

    def pass_through(self) -> None:
        """Let the request continue to the real server unchanged."""
        self._pw.continue_()

    def abort(self, reason: str = "failed") -> None:
        """Fail the request as if the network layer errored out."""
        self._pw.abort(reason)


class CDPDownload:
    """A file the page attempted to download."""

    def __init__(self, pw_download: Any) -> None:
        self._pw = pw_download

    @property
    def suggested_filename(self) -> str:
        return self._pw.suggested_filename

    @property
    def url(self) -> str:
        return self._pw.url

    def save_as(self, path: str) -> None:
        """Save the download to *path* (overwrites existing files)."""
        self._pw.save_as(path)

    def path(self) -> str:
        """Return Playwright's temporary path holding the download bytes."""
        return str(self._pw.path())

    def cancel(self) -> None:
        self._pw.cancel()

    def delete(self) -> None:
        self._pw.delete()


class _LazyValue:
    """Thin ``.value`` accessor so ``expect_*`` callers use Playwright semantics."""

    def __init__(self, info: Any, wrap: Callable[[Any], Any] | None = None) -> None:
        self._info = info
        self._wrap = wrap
        self._cached: Any = None
        self._resolved = False

    @property
    def value(self) -> Any:
        if not self._resolved:
            raw = self._info.value
            self._cached = self._wrap(raw) if self._wrap is not None else raw
            self._resolved = True
        return self._cached


def is_cdp_available() -> bool:
    """Return True when the ``[cdp]`` extra is installed.

    Public probe so tests can ``pytest.skip`` without importing Playwright
    themselves — matches the autonomous-library contract. Note this only
    checks the Python package: the Chromium browser Playwright uses for
    its protocol implementation is a separate install
    (:func:`cdp_install_hint` returns the exact command).
    """
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def cdp_install_hint() -> str:
    """Return a human-readable install command for the CDP extra.

    Public helper for skip messages and error surfaces so callers do not
    have to hardcode the command in every test file.
    """
    return _INSTALL_HINT


def _substring_text_selector(text: str) -> str:
    """Return a ``text=`` selector matching *text* as a literal substring.

    Playwright splits a selector on ``>>`` and tracks quote state before the
    text engine sees the body, so raw user text turns ``>>`` into a selector
    chain and a leading ``/`` or ``"`` into a different engine. Only the regex
    form of the text engine can carry arbitrary text: every character the
    selector parser reacts to is emitted as an ``\\xNN`` escape, the rest as a
    regex-literal escape. ``i`` reproduces the case-insensitive substring
    semantics of the plain ``text=`` engine.

    The plain engine normalises whitespace on both sides; ``text=/…/`` only on
    the element's. Runs of whitespace in *text* are therefore emitted as
    ``\\s+`` and the ends trimmed, so a literal copied out of the markup
    ("Total:  42", "Sign\\nin", " Save ") keeps matching the rendered element.
    Characters the normaliser deletes rather than collapses (zero-width space,
    soft hyphen) are dropped for the same reason: a ``&nbsp;``/``&shy;`` pasted
    out of DevTools would otherwise be a literal no element text can contain.
    """
    out: list[str] = []
    pending_space = False
    for ch in text.strip(_SELECTOR_TRIMMABLE):
        if ch in _SELECTOR_ZERO_WIDTH:
            continue
        if ch in _SELECTOR_WHITESPACE:
            pending_space = True
            continue
        if pending_space:
            out.append("\\s+")
            pending_space = False
        if ch in "\\^$.|?*+()[]{}/":
            out.append("\\" + ch)
        elif ch in ">\"'`":
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return "text=/" + "".join(out) + "/i"


def _require_playwright() -> Any:
    """Import Playwright lazily and re-raise ImportError as RuntimeError.

    Absent extra must produce a *readable* ``RuntimeError`` with install
    steps, never a bare ``ImportError`` that a user would have to translate.
    """
    try:
        from playwright import sync_api
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc
    return sync_api


class CDPSession:
    """Live CDP connection to an Electron process.

    Wraps Playwright's ``connect_over_cdp`` result: browser, context, page.
    Use :meth:`connect` (existing debug port) or the higher-level
    :meth:`Desktop.launch_electron_cdp` (spawn + connect in one step).

    The session picks Playwright's first context and its first page as the
    "current" page — this matches Electron's single-window BrowserWindow
    layout. Use :meth:`pages` for multi-window apps.

    An auto-picked page is replaced transparently when the renderer dies. A
    page chosen explicitly with :meth:`switch_to_page` / :meth:`expect_popup`
    is not: once it closes, every page-level call raises
    :class:`CDPStalePageError` rather than acting on a different window.
    """

    #: Registered :class:`Backend` id this facade fronts.
    backend_id: str = "cdp"

    @classmethod
    def backend(cls):
        from ._backend import resolve as _resolve

        return _resolve(cls.backend_id)

    @classmethod
    def backend_supports(cls, capability) -> bool:
        return cls.backend().supports(capability)

    @classmethod
    def require_capability(cls, capability) -> None:
        cls.backend().require_capability(capability)

    def __init__(
        self,
        playwright: Playwright,
        browser: Browser,
        context: BrowserContext,
        page: Page,
        *,
        endpoint: str,
    ) -> None:
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._page = page
        self._explicit_page = False
        self._opener_page: Page | None = None
        self._endpoint = endpoint
        self._closed = False
        self._console_messages: deque[dict[str, Any]] = deque(maxlen=CONSOLE_BUFFER_LIMIT)
        self._dialog_policy: str | None = None
        self._routes: list[tuple[str, Callable[[Any], None]]] = []
        self._events_page: Page | None = None
        self._bind_page_events(page)

    def _bind_page_events(self, page: Page) -> None:
        """Move the console + dialog listeners onto *page*.

        Playwright auto-dismisses dialogs raised by a page that has no
        ``dialog`` listener, so leaving them on a page the session has left
        silently defeats :meth:`accept_dialogs` and stops the console buffer.

        Every ``on`` / ``remove_listener`` here is a protocol round-trip that a
        renderer which just died makes fail, so each is attempted on its own:
        one failure must not leave a listener bound to a page the session has
        left, nor stop the other listener from moving. ``_events_page`` follows
        the attempt either way — leaving it on the page just unbound would make
        the next switch clean up nothing.
        """
        if page is None or page is self._events_page:
            return
        previous = self._events_page
        if previous is not None:
            self._unbind_page_events(previous)
        self._events_page = page
        try:
            page.on("console", self._on_console_message)
        except Exception:
            pass
        try:
            page.on("dialog", self._on_dialog)
        except Exception:
            pass

    def _unbind_page_events(self, page: Page) -> None:
        try:
            page.remove_listener("console", self._on_console_message)
        except Exception:
            pass
        try:
            page.remove_listener("dialog", self._on_dialog)
        except Exception:
            pass

    def _on_console_message(self, msg: Any) -> None:
        try:
            self._console_messages.append({"type": msg.type, "text": msg.text})
        except Exception:
            pass

    def _on_dialog(self, dialog: Any) -> None:
        try:
            policy = self._dialog_policy or "dismiss"
            if policy == "accept":
                dialog.accept()
            else:
                dialog.dismiss()
        except Exception:
            pass

    # Construction

    @classmethod
    def connect(cls, endpoint: str, *, timeout: float = 15.0) -> CDPSession:
        """Attach to an already-running Electron on *endpoint*.

        Args:
            endpoint: ``http://host:port`` where the target exposes the
                CDP JSON endpoint. Playwright accepts either the JSON URL
                or the raw ``ws://`` URL from ``/json/version``.
            timeout: seconds to wait for the browser + a durable page.

        Raises:
            RuntimeError: if the [cdp] extra is not installed, or if the
                endpoint is unreachable within *timeout* seconds.

        Notes:
            Electron apps (VS Code, Slack, Teams, Spotify) transiently
            open blank / helper pages during startup. This method waits
            for a page whose URL is non-blank (i.e. the actual UI
            renderer) so ``session.page`` points at a stable target,
            not a short-lived helper that closes moments later.
        """
        sync_api = _require_playwright()
        pw = sync_api.sync_playwright().start()
        try:
            browser = pw.chromium.connect_over_cdp(endpoint, timeout=timeout * 1000)
        except Exception as exc:
            pw.stop()
            raise RuntimeError(f"CDP connect_over_cdp({endpoint!r}) failed: {exc}") from exc

        # Everything past ``connect_over_cdp`` may still raise — a stale
        # target list, a slow renderer, or ``new_context()`` timing out.
        # Without this guard we'd leak both the Playwright driver
        # subprocess and the connected browser handle until interpreter
        # exit, which on long-running test runs shows up as zombie
        # ``node.exe`` processes.
        try:
            context, page = cls._pick_stable_page(browser, timeout=timeout)
            if page is None:
                # Last-resort fallback: open a fresh page so the caller at least
                # gets a live target. In Electron this is unusual — the app's
                # own renderer should have surfaced by now.
                if not browser.contexts:
                    context = browser.new_context()
                else:
                    context = browser.contexts[0]
                page = context.new_page()
        except Exception:
            try:
                browser.close()
            except Exception:
                pass
            pw.stop()
            raise

        return cls(pw, browser, context, page, endpoint=endpoint)

    @staticmethod
    def _pick_stable_page(browser: Browser, *, timeout: float) -> tuple[Any, Any]:
        """Return the first live ``(context, page)`` whose URL is not about:blank.

        Retries until *timeout* seconds elapse, then falls back to any live
        page — even ``about:blank`` — because an app that never navigates is
        still automatable. ``(None, None)`` means no live page at all.
        Closed pages are skipped on the same
        terms as :meth:`pages`: Playwright keeps serving ``url`` from the last
        known frame after a target closes, so without the check this scanner
        would hand back a page ``_is_page_alive`` rejects on the next access —
        costing a full re-scan per call, forever.
        """
        import time as _t

        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            for ctx in browser.contexts:
                for p in ctx.pages:
                    try:
                        if p.is_closed():
                            continue
                        url = p.url
                    except Exception:
                        continue
                    if url and url != "about:blank" and not url.startswith("chrome://"):
                        return ctx, p
            _t.sleep(0.25)
        # Fallback: any live page at all, even blank.
        for ctx in browser.contexts:
            for p in ctx.pages:
                try:
                    if p.is_closed():
                        continue
                    _ = p.url
                except Exception:
                    continue
                return ctx, p
        return None, None

    # Page / navigation helpers

    @property
    def page(self) -> Page:
        """Currently active Playwright ``Page``, auto-refreshing if it died.

        Electron renderers can vanish (window closed, hot-reload) between
        actions. While the session is still on the auto-picked page, rather
        than surface a stale ``Page`` that raises ``TargetClosedError`` on
        every method call, re-scan the browser for the current durable page
        and fall back to the cached page if the scan finds nothing better.

        Raises:
            CDPStalePageError: if a page selected explicitly through
                :meth:`switch_to_page` has closed and there is no opener to
                return to. Silently retargeting a different window would run
                the caller's next action against the wrong renderer;
                :meth:`reset_page_selection` is the way back.
            DolphinError: if the session has been closed.
        """
        self._require_open()
        if self._is_page_alive(self._page):
            return self._page
        if self._explicit_page:
            opener = self._opener_page
            if opener is not None and self._is_page_alive(opener):
                # A popup that closes itself (OAuth, "sign in with …") is the
                # normal flow, not a lost selection: the opener is the one
                # window that is unambiguously the right target, so fall back
                # to it and drop the pin instead of bricking the session.
                self._explicit_page = False
                self._opener_page = None
                self._page = opener
                # Events before the context, as in _select_page: _bind_context
                # raises when a route cannot be re-armed, and self._page is
                # already the live page, so a raise here would skip the event
                # rebind permanently — every later access returns early and the
                # listeners stay pinned to the closed page.
                self._bind_page_events(opener)
                self._bind_context(getattr(opener, "context", None))
                return self._page
            raise CDPStalePageError(
                "the page selected with switch_to_page()/expect_popup() has "
                "closed — dolphin will not fall back to another window",
                hint="pick a live window with pages() + switch_to_page(i), or "
                "call reset_page_selection() to go back to auto-picking the "
                "current durable page",
            )
        new_context, new_page = self._pick_stable_page(self._browser, timeout=2.0)
        if new_page is not None:
            self._page = new_page
            self._bind_page_events(new_page)
            self._bind_context(new_context)
        return self._page

    def _require_open(self) -> None:
        """Guard every entry point that reaches the browser / context / page.

        Playwright answers a call made after ``close()`` with a raw
        ``TargetClosedError``, which is neither a :class:`DolphinError` nor a
        description of what the caller did wrong.
        """
        if self._closed:
            raise DolphinError(
                "this CDPSession is closed — close() tore down the browser connection",
                hint="reconnect with CDPSession.connect(endpoint) or "
                "Desktop.launch_electron_cdp(...)",
            )

    def reset_page_selection(self) -> CDPSession:
        """Forget an explicit page choice and resume auto-picking.

        The escape hatch from :class:`CDPStalePageError`: once the explicitly
        selected page has closed, nothing else clears the selection. The next
        :attr:`page` access re-scans the browser for the current durable page.
        """
        self._explicit_page = False
        self._opener_page = None
        return self

    def _bind_context(self, context: Any) -> None:
        """Point context-level operations at *context* and move its routes.

        ``pages()`` deliberately spans contexts, so the active page can live in
        a context other than the one this session started on. Routing is moved
        — disarmed on the old context, armed on the new — so a request is
        intercepted exactly once no matter how often the session bounces.

        Nothing else migrates: init scripts, exposed functions, extra headers
        and granted permissions stay on the context they were installed on
        (re-applying them is not idempotent — ``expose_function`` rejects a
        duplicate name). Re-apply the ones the new context needs.

        Raises:
            DolphinError: if a route could not be armed on the new context. It
                is disarmed on the old one by then, so swallowing this would
                let requests a test believes are mocked reach the real network.
        """
        if context is None or context is self._context:
            return
        old = self._context
        for pattern, handler in self._routes:
            try:
                old.unroute(pattern, handler)
            except Exception:
                pass
        self._context = context
        armed: list[tuple[str, Callable[[Any], None]]] = []
        failed: list[str] = []
        for pattern, handler in self._routes:
            try:
                context.route(pattern, handler)
            except Exception:
                failed.append(pattern)
            else:
                armed.append((pattern, handler))
        # A route that never armed intercepts nothing, so keeping it would make
        # unroute() report a removal it did not perform.
        self._routes = armed
        if failed:
            raise DolphinError(
                "the session followed its page into another browser context but "
                f"could not re-arm {len(failed)} route(s) there "
                f"({', '.join(sorted(set(failed)))}) — requests matching them "
                "now reach the real network",
                hint="re-register them with route(pattern, handler) once the new context is live",
            )

    def _select_page(self, page: Page, *, opener: Page | None = None) -> None:
        """Make *page* the explicitly selected page (its death becomes an error).

        *opener* is the page to fall back to if the selection closes — set for
        popups, which routinely close themselves.
        """
        self._page = page
        self._explicit_page = True
        self._opener_page = opener
        try:
            context = page.context
        except Exception:
            context = None
        self._bind_page_events(page)
        self._bind_context(context)

    @staticmethod
    def _is_page_alive(page: Page) -> bool:
        try:
            if page.is_closed():
                return False
            _ = page.url
            return True
        except Exception:
            return False

    def pages(self) -> list[Page]:
        """Return every non-closed page across every browser context."""
        self._require_open()
        result: list[Page] = []
        for ctx in self._browser.contexts:
            for p in ctx.pages:
                try:
                    if not p.is_closed():
                        result.append(p)
                except Exception:
                    continue
        return result

    def switch_to_page(self, index: int) -> CDPSession:
        """Switch the "current" page to the *index*-th across all contexts.

        The choice is sticky: from here on, a closed page raises
        :class:`CDPStalePageError` instead of silently falling back to another
        window — call :meth:`reset_page_selection` to undo that. Routing
        follows the selected page's browser context; see :meth:`_bind_context`
        for the context state that does not.
        """
        self._require_open()
        all_pages = self.pages()
        if index < 0 or index >= len(all_pages):
            raise IndexError(f"page index {index} out of range (have {len(all_pages)} pages)")
        self._select_page(all_pages[index])
        return self

    # Scripting

    def evaluate(self, script: str, *args: Any) -> Any:
        """Run JavaScript in the current page and return its result.

        Thin wrapper over Playwright's ``Page.evaluate`` so tests can
        inject DOM (e.g. build a Shadow DOM fixture) without importing
        Playwright directly — the ``dolphin_desktop``-only rule stays
        intact for consumers.

        Targets the same page as :meth:`locator` — see :attr:`page` for what
        happens when that page dies.
        """
        return self.page.evaluate(script, *args)

    # Page-level keyboard, screenshot, navigation

    def press_key(self, key: str) -> CDPSession:
        """Dispatch *key* to the page (page keyboard, no locator needed).

        Use for global shortcuts (``"Control+Shift+P"``, ``"Escape"``) that
        do not require a specific focused element. For key-with-focus,
        prefer :meth:`CDPLocator.press_key`.

        Playwright key syntax:

        * single characters — ``"a"``, ``"5"``
        * named keys — ``"Enter"``, ``"Tab"``, ``"Escape"``, ``"ArrowDown"``
        * chords — ``"Control+S"``, ``"Control+Shift+P"``, ``"Alt+F4"``
        """
        self.page.keyboard.press(key)
        return self

    def screenshot(self, path: str | None = None) -> Any:
        """Capture the current page as a :class:`PIL.Image`.

        The result is a PIL image (dolphin already depends on Pillow).
        Passing *path* saves the image before returning it.
        """
        from io import BytesIO

        from PIL import Image as _PILImage

        data = self.page.screenshot()
        img = _PILImage.open(BytesIO(data))
        img.load()
        if path:
            img.save(path)
        return img

    def wait_for_load_state(self, state: str = "load", *, timeout: float = 30.0) -> CDPSession:
        """Block until the page reaches *state* ('load', 'domcontentloaded', 'networkidle')."""
        try:
            self.page.wait_for_load_state(cast(Any, state), timeout=timeout * 1000)
        except Exception as exc:
            raise WaitTimeoutError(
                f"page did not reach load state {state!r} within {timeout}s: {exc}",
                hint=(
                    "valid states: 'load', 'domcontentloaded', 'networkidle'. increase timeout= or "
                    "use wait_for_selector() for a specific element"
                ),
            ) from exc
        return self

    def reload(self, *, timeout: float = 30.0) -> CDPSession:
        """Reload the current page and wait for load."""
        try:
            self.page.reload(timeout=timeout * 1000)
        except Exception as exc:
            raise RuntimeError(f"page reload failed: {exc}") from exc
        return self

    def set_default_timeout(self, timeout: float) -> CDPSession:
        """Set the default action timeout (seconds) for the underlying page."""
        self.page.set_default_timeout(timeout * 1000)
        return self

    # Console + dialogs (things Electron apps do that need to be intercepted)

    def console_messages(self) -> list[dict[str, Any]]:
        """Return the console entries emitted since attach, oldest first.

        Each entry is ``{"type": "log"|"warn"|"error"|..., "text": str}``.
        Useful to assert that no ``error`` message escaped during a test.

        The buffer is a ring of the most recent
        :data:`CONSOLE_BUFFER_LIMIT` (1000) entries — a chatty Electron app
        would otherwise grow it without bound over a long session. Call
        :meth:`clear_console_messages` between phases to keep the window
        meaningful.
        """
        return list(self._console_messages)

    def clear_console_messages(self) -> CDPSession:
        """Drop the buffered console history — useful between test phases."""
        self._console_messages.clear()
        return self

    def accept_dialogs(self) -> CDPSession:
        """Auto-accept every subsequent ``alert``/``confirm``/``prompt``."""
        self._dialog_policy = "accept"
        return self

    def dismiss_dialogs(self) -> CDPSession:
        """Auto-dismiss every subsequent ``alert``/``confirm``/``prompt`` (default)."""
        self._dialog_policy = "dismiss"
        return self

    # Frames — iframes / webviews / embedded panels

    def frame_locator(self, selector: str) -> CDPFrameLocator:
        """Return a locator targeting the ``<iframe>`` matched by *selector*.

        Chain :meth:`CDPFrameLocator.locator` to reach into the frame's
        DOM — e.g. VS Code webviews, Slack's message panels, Teams'
        embedded content are all iframes.
        """
        return CDPFrameLocator(self, selector)

    # Cookies + storage

    def cookies(self) -> list[dict[str, Any]]:
        """Return the browser context's cookies (Playwright format)."""
        self._require_open()
        return cast(list[dict[str, Any]], list(self._context.cookies()))

    def local_storage_get(self, key: str) -> Any:
        """Read ``window.localStorage.getItem(key)`` from the current page."""
        return self.evaluate("(k) => window.localStorage.getItem(k)", key)

    def local_storage_set(self, key: str, value: str) -> CDPSession:
        """Set ``window.localStorage.setItem(key, value)`` in the current page."""
        self.evaluate("([k, v]) => window.localStorage.setItem(k, v)", [key, value])
        return self

    def local_storage_clear(self) -> CDPSession:
        """Empty ``window.localStorage`` in the current page."""
        self.evaluate("() => window.localStorage.clear()")
        return self

    # Playwright a11y-first selectors (session shortcuts)

    def get_by_role(self, role: str, **kwargs: Any) -> CDPLocator:
        """Find an element by ARIA role (Playwright's ``get_by_role``)."""
        handle = self.page.get_by_role(cast(Any, role), **kwargs)
        return CDPLocator(self, f"role={role}", _handle=handle)

    def get_by_text(self, text: str, **kwargs: Any) -> CDPLocator:
        """Find an element by rendered text (Playwright's ``get_by_text``)."""
        handle = self.page.get_by_text(text, **kwargs)
        return CDPLocator(self, f"text={text!r}", _handle=handle)

    def get_by_label(self, text: str, **kwargs: Any) -> CDPLocator:
        """Find a form control by its associated ``<label>`` text."""
        handle = self.page.get_by_label(text, **kwargs)
        return CDPLocator(self, f"label={text!r}", _handle=handle)

    def get_by_placeholder(self, text: str, **kwargs: Any) -> CDPLocator:
        """Find an input by its ``placeholder`` attribute."""
        handle = self.page.get_by_placeholder(text, **kwargs)
        return CDPLocator(self, f"placeholder={text!r}", _handle=handle)

    def get_by_title(self, text: str, **kwargs: Any) -> CDPLocator:
        """Find an element by its ``title`` attribute."""
        handle = self.page.get_by_title(text, **kwargs)
        return CDPLocator(self, f"title={text!r}", _handle=handle)

    def get_by_alt_text(self, text: str, **kwargs: Any) -> CDPLocator:
        """Find an image by its ``alt`` attribute."""
        handle = self.page.get_by_alt_text(text, **kwargs)
        return CDPLocator(self, f"alt={text!r}", _handle=handle)

    def get_by_test_id(self, test_id: str) -> CDPLocator:
        """Find an element by ``data-testid`` (default) or configured test-id attr."""
        handle = self.page.get_by_test_id(test_id)
        return CDPLocator(self, f"test_id={test_id!r}", _handle=handle)

    # Network interception

    def route(
        self,
        url_pattern: str,
        handler: Callable[[CDPRoute], None],
    ) -> CDPSession:
        """Intercept every request matching *url_pattern* and dispatch to *handler*.

        The handler receives a :class:`CDPRoute` and must call
        :meth:`CDPRoute.respond`, :meth:`CDPRoute.pass_through` or
        :meth:`CDPRoute.abort` on it. Pattern accepts glob (``**/api/**``)
        or regex-in-string form per Playwright's routing API.

        Interception is registered on the browser context, so it covers every
        page in it and is re-armed on the new context when the session follows
        a page into another one.
        """
        self._require_open()

        def _pw_handler(route: Any) -> None:
            handler(CDPRoute(route))

        # Registered only once it is actually intercepting: an entry for a
        # route that never armed would be re-armed on the next context and
        # would make unroute() report a removal it did not perform.
        self._context.route(url_pattern, _pw_handler)
        self._routes.append((url_pattern, _pw_handler))
        return self

    def unroute(self, url_pattern: str) -> CDPSession:
        """Remove every handler registered for *url_pattern*.

        Playwright keeps one interception wrapper per :meth:`route` call, so
        the registry has to as well: dropping only the last-registered handler
        would leave the earlier ones intercepting with no way to reach them.
        """
        self._require_open()
        removed = [h for pattern, h in self._routes if pattern == url_pattern]
        self._routes = [entry for entry in self._routes if entry[0] != url_pattern]
        for handler in removed:
            self._context.unroute(url_pattern, handler)
        if not removed:
            self._context.unroute(url_pattern)
        return self

    @contextmanager
    def expect_response(
        self,
        url_pattern: str | Callable[[Any], bool],
        *,
        timeout: float = 30.0,
    ) -> Iterator[Any]:
        """Yield an object whose ``.value`` becomes the matching response.

        Playwright semantics: enter the block, perform an action that
        triggers the network call, exit the block — ``value`` is then
        the response.
        """
        with self.page.expect_response(url_pattern, timeout=timeout * 1000) as info:
            yield _LazyValue(info)

    @contextmanager
    def expect_request(
        self,
        url_pattern: str | Callable[[Any], bool],
        *,
        timeout: float = 30.0,
    ) -> Iterator[Any]:
        """Same as :meth:`expect_response` but resolves to the outgoing request."""
        with self.page.expect_request(url_pattern, timeout=timeout * 1000) as info:
            yield _LazyValue(info, wrap=CDPRequest)

    # Downloads + popups (context managers around Playwright's expect_*)

    @contextmanager
    def expect_download(self, *, timeout: float = 30.0) -> Iterator[Any]:
        """Wait for the next file download; ``value`` is a :class:`CDPDownload`."""
        with self.page.expect_download(timeout=timeout * 1000) as info:
            yield _LazyValue(info, wrap=CDPDownload)

    @contextmanager
    def expect_popup(self, *, timeout: float = 10.0) -> Iterator[Any]:
        """Wait for the next popup window; auto-switch the session to it on exit.

        Use ``session.switch_to_page(0)`` afterwards to hop back to the
        opener. The yielded object exposes ``.value`` too if you want the
        raw Playwright Page.

        A popup that closes itself — the usual end of an OAuth / "sign in
        with …" flow — hands the session back to the opener instead of
        raising :class:`CDPStalePageError`.
        """
        opener = self.page
        with opener.expect_popup(timeout=timeout * 1000) as info:
            yield _LazyValue(info)
        # After the popup opens, the current page becomes the popup so
        # subsequent locator() calls target the new window. The selection is
        # sticky while the popup lives — see switch_to_page().
        #
        # A DolphinError from _bind_context means a registered route could not
        # be re-armed on the popup's context, i.e. requests the test believes
        # are mocked now reach the real network. That must not be swallowed.
        # Anything else here is a popup that vanished before we could select
        # it, which the opener fallback already handles.
        try:
            page = info.value
        except Exception:
            return
        self._select_page(page, opener=opener)

    # Navigation

    def wait_for_url(
        self,
        pattern: str,
        *,
        timeout: float = 30.0,
    ) -> CDPSession:
        """Block until the page URL matches *pattern* (glob or regex string)."""
        page = self.page
        try:
            page.wait_for_url(pattern, timeout=timeout * 1000)
        except Exception as exc:
            # A dead renderer is a plausible cause of the timeout, so neither
            # self.page (CDPStalePageError) nor page.url (TargetClosedError)
            # can be reached from here without replacing the error the caller
            # is waiting to catch.
            try:
                current = repr(page.url)
            except Exception:
                current = "<unavailable — the page closed>"
            raise WaitTimeoutError(
                f"URL did not match {pattern!r} within {timeout}s: {exc}",
                hint=(
                    f"current URL: {current}. glob pattern? try "
                    f"'https://**/login' — regex? pass a compiled re.Pattern"
                ),
            ) from exc
        return self

    def current_url(self) -> str:
        return self.page.url

    # Convenience: combine wait + locate

    def wait_for_selector(
        self,
        selector: str,
        *,
        state: str = "visible",
        timeout: float = 10.0,
    ) -> CDPLocator:
        """Block until *selector* enters *state*, then return the locator.

        Common one-liner for "wait for element to appear then click it"
        style tests::

            cdp.wait_for_selector("#logged-in-username").text()
            cdp.wait_for_selector(".spinner", state="hidden")
            cdp.wait_for_selector("dialog[open]").locator("button.ok").click()

        Args:
            selector: Playwright-compatible CSS / text / role selector.
            state: ``"visible"`` (default), ``"hidden"``, ``"attached"``,
                or ``"detached"``.
            timeout: Seconds to wait before raising WaitTimeoutError.
        """
        loc = self.locator(selector)
        try:
            loc.wait_for(state=state, timeout=timeout)
        except WaitTimeoutError:
            raise
        except Exception as exc:
            raise WaitTimeoutError(
                f"selector {selector!r} did not become {state!r} within {timeout}s: {exc}",
                hint=(
                    "verify with locator(...).count() > 0, or use session.evaluate('() => "
                    "document.querySelectorAll(...)') to inspect the DOM"
                ),
            ) from exc
        return loc

    def wait_for_text(
        self,
        text: str,
        *,
        exact: bool = False,
        timeout: float = 10.0,
    ) -> CDPLocator:
        """Block until an element containing *text* is visible.

        *text* is matched literally — it is escaped into the selector, never
        parsed as one. Whitespace is normalised the way Playwright's ``text=``
        engine does it, so a literal copied out of the markup matches however
        the browser collapsed it. Set ``exact=True`` to require the element's
        full accessible name to match, not just contain, *text*.

        Empty or whitespace-only ``text`` is rejected — Playwright's ``text=`` selector
        matches every element when the substring is empty, which would
        make the wait succeed on the first non-visible node in the
        tree. Guarded on the same principle as the other
        ``wait_for_text`` methods across dolphin's locator families.
        """
        if not text.strip(_SELECTOR_TRIMMABLE):
            raise ValueError(
                "wait_for_text(text='') matches every element in the tree — "
                "pass a real substring; use wait_for_selector(selector, "
                "state='visible') for structural waits"
            )
        if exact:
            # Playwright's quoted text engine takes \" and \\ escapes; an
            # unescaped quote in *text* would close the selector early.
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            selector = f'text="{escaped}"'
        else:
            selector = _substring_text_selector(text)
        return self.wait_for_selector(selector, state="visible", timeout=timeout)

    # Init scripts + storage write

    def add_init_script(self, script: str) -> CDPSession:
        """Run *script* on every page BEFORE the page's own scripts.

        Use for mocking ``Date.now``, ``navigator.geolocation``, feature
        flags, or any prerequisite the app expects to see at load time.
        """
        self._require_open()
        self._context.add_init_script(script=script)
        return self

    def set_cookies(self, cookies: list[dict[str, Any]]) -> CDPSession:
        """Write cookies into the current browser context.

        Each dict is Playwright's cookie shape: ``{name, value, url?,
        domain?, path?, expires?, httpOnly?, secure?, sameSite?}``.
        """
        self._require_open()
        self._context.add_cookies(cast(Any, cookies))
        return self

    def clear_cookies(self) -> CDPSession:
        """Empty the browser context's cookies."""
        self._require_open()
        self._context.clear_cookies()
        return self

    def session_storage_get(self, key: str) -> Any:
        """Read ``window.sessionStorage.getItem(key)``."""
        return self.evaluate("(k) => window.sessionStorage.getItem(k)", key)

    def session_storage_set(self, key: str, value: str) -> CDPSession:
        """Set ``window.sessionStorage.setItem(key, value)``."""
        self.evaluate("([k, v]) => window.sessionStorage.setItem(k, v)", [key, value])
        return self

    def session_storage_clear(self) -> CDPSession:
        """Empty ``window.sessionStorage``."""
        self.evaluate("() => window.sessionStorage.clear()")
        return self

    # JS ↔ Python bridge + emulation

    def expose_function(self, name: str, callback: Callable[..., Any]) -> CDPSession:
        """Install *callback* on ``window`` so page JS can call ``window[name](args)``.

        Playwright bridges the call across processes — arguments arrive as
        JSON-friendly Python values, the return value is JSON-encoded back.
        Use to observe events from inside the app (analytics hooks, event
        bus listeners) without polling.
        """
        self._require_open()
        self._context.expose_function(name, callback)
        return self

    def set_extra_http_headers(self, headers: dict[str, str]) -> CDPSession:
        """Attach *headers* to every subsequent request from this context."""
        self._require_open()
        self._context.set_extra_http_headers(headers)
        return self

    def set_offline(self, offline: bool) -> CDPSession:
        """Emulate network offline / restore online."""
        self._require_open()
        self._context.set_offline(offline)
        return self

    def set_geolocation(
        self, latitude: float, longitude: float, *, accuracy: float = 1.0
    ) -> CDPSession:
        """Override the browser's geolocation."""
        self._require_open()
        self._context.set_geolocation(
            {"latitude": latitude, "longitude": longitude, "accuracy": accuracy}
        )
        return self

    def grant_permissions(self, permissions: list[str]) -> CDPSession:
        """Grant permissions (``"geolocation"``, ``"microphone"``, ``"camera"``,
        ``"clipboard-read"``, ``"clipboard-write"``, ``"notifications"``, …)
        for the current origin without a user prompt.
        """
        self._require_open()
        self._context.grant_permissions(permissions)
        return self

    def clear_permissions(self) -> CDPSession:
        """Reset granted permissions."""
        self._require_open()
        self._context.clear_permissions()
        return self

    def pdf(self, path: str) -> CDPSession:
        """Print the current page to *path* as a PDF (Chromium only)."""
        self.page.pdf(path=path)
        return self

    def keyboard_down(self, key: str) -> CDPSession:
        """Press *key* down and hold it (release with :meth:`keyboard_up`)."""
        self.page.keyboard.down(key)
        return self

    def keyboard_up(self, key: str) -> CDPSession:
        """Release a *key* previously held by :meth:`keyboard_down`."""
        self.page.keyboard.up(key)
        return self

    def keyboard_type(self, text: str, *, delay: float = 0.0) -> CDPSession:
        """Type *text* character-by-character with an optional per-key *delay* (seconds)."""
        self.page.keyboard.type(text, delay=delay * 1000)
        return self

    def mouse_wheel(self, delta_x: float, delta_y: float) -> CDPSession:
        """Dispatch a mouse-wheel event with the given deltas (pixels)."""
        self.page.mouse.wheel(delta_x, delta_y)
        return self

    def mouse_move(self, x: float, y: float) -> CDPSession:
        """Move the virtual mouse to page coordinates (*x*, *y*)."""
        self.page.mouse.move(x, y)
        return self

    # Locator entry point

    def locator(self, selector: str) -> CDPLocator:
        """Return a :class:`CDPLocator` bound to this session.

        ``selector`` follows Playwright's engine chaining: plain CSS by
        default, ``a >> b`` chains sub-locators (used to pierce Shadow DOM
        roots). Examples:

        * ``"#submit"`` — plain CSS id
        * ``"nav .item"`` — CSS descendant
        * ``"my-app >> #shadow-child"`` — CSS-engine chain, pierces the
          open shadow root under ``my-app``
        * ``"xpath=//button[@aria-label='Close']"`` — XPath engine
        """
        return CDPLocator(self, selector)

    # Lifecycle

    def close(self) -> None:
        """Detach from Electron. Idempotent.

        Unbinds the page listeners and drops the route registry as well: a
        session kept in a fixture would otherwise hold the whole dead
        Playwright object graph — pages, contexts, handlers — alive for the
        rest of the run.
        """
        if self._closed:
            return
        self._closed = True
        if self._events_page is not None:
            self._unbind_page_events(self._events_page)
            self._events_page = None
        self._routes.clear()
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._playwright.stop()
        except Exception:
            pass
        self._page = None  # type: ignore[assignment]
        self._opener_page = None

    def __enter__(self) -> CDPSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class CDPLocator:
    """CDP-backed locator mirroring the UIA :class:`Locator` API.

    Delegates to Playwright's own ``Locator`` and wraps exceptions to
    raise dolphin's :class:`WaitTimeoutError` / :class:`ElementNotFoundError`
    so user code catches the same types regardless of backend.

    Shadow DOM piercing: Playwright's CSS engine pierces **open** shadow
    roots automatically (``document.querySelector('#a #b')`` sees ``#b``
    even when ``#b`` lives inside an open shadow tree under ``#a``). For
    closed roots, no CDP-based tool can reach in — a Chromium-level
    restriction documented in ``docs/guides/embedded-web.md``.

    A CDPLocator may be scoped to a specific match (via :meth:`nth`,
    :meth:`first`, :meth:`all`) — in that case the raw Playwright Locator
    is stashed in ``_handle`` and re-used instead of re-resolving the
    selector against the page.
    """

    def __init__(
        self,
        session: CDPSession,
        selector: str,
        *,
        _handle: Any | None = None,
    ) -> None:
        self._session = session
        self._selector = selector
        self._handle = _handle
        # A raw Playwright Locator belongs to the page it was built from and
        # cannot follow the session onto another one.
        self._handle_page: Any = session._page if _handle is not None else None

    # Resolution

    def _resolve(self) -> Any:
        """Return the underlying Playwright ``Locator``."""
        if self._handle is not None:
            if self._handle_page is not None and not self._session._is_page_alive(
                self._handle_page
            ):
                raise CDPStalePageError(
                    f"this locator ({self._selector!r}) is bound to a page that "
                    "has closed — scoped locators (nth/first/all/filter/frame, "
                    "get_by_*) do not follow the session to another page",
                    hint="re-create it from the session after the switch: "
                    "session.locator(...) / session.get_by_role(...)",
                )
            return self._handle
        return self._session.page.locator(self._selector)

    def _scoped(self, handle: Any) -> CDPLocator:
        """Return a new CDPLocator wrapping *handle* (scoped result)."""
        scoped = CDPLocator(self._session, self._selector, _handle=handle)
        scoped._handle_page = self._handle_page or scoped._handle_page
        return scoped

    # Mouse actions

    def click(
        self,
        *,
        timeout: float = 10.0,
        modifiers: list[str] | None = None,
        position: dict[str, float] | None = None,
        button: str = "left",
        force: bool = False,
    ) -> CDPLocator:
        """Click the element (Playwright dispatches a trusted mouse event).

        Optional:

        * ``modifiers``: list of ``"Control"``/``"Alt"``/``"Shift"``/``"Meta"``
        * ``position``: ``{"x": px, "y": px}`` — click relative to top-left
        * ``button``: ``"left"`` (default), ``"middle"``, ``"right"``
        * ``force``: skip Playwright's actionability checks
        """
        kwargs: dict[str, Any] = {"timeout": timeout * 1000}
        if modifiers:
            kwargs["modifiers"] = modifiers
        if position is not None:
            kwargs["position"] = position
        if button != "left":
            kwargs["button"] = button
        if force:
            kwargs["force"] = True
        try:
            self._resolve().click(**kwargs)
        except CDPStalePageError:
            raise
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP click failed for selector {self._selector!r}: {exc}"
            ) from exc
        return self

    def double_click(self, *, timeout: float = 10.0) -> CDPLocator:
        try:
            self._resolve().dblclick(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP double_click failed for selector {self._selector!r}: {exc}"
            ) from exc
        return self

    def right_click(self, *, timeout: float = 10.0) -> CDPLocator:
        """Right-click (context menu) the element."""
        try:
            self._resolve().click(button="right", timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP right_click failed for selector {self._selector!r}: {exc}"
            ) from exc
        return self

    def hover(self, *, timeout: float = 10.0) -> CDPLocator:
        """Move the mouse over the element — reveals tooltips / hover menus."""
        try:
            self._resolve().hover(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP hover failed for selector {self._selector!r}: {exc}"
            ) from exc
        return self

    def drag_to(self, other: CDPLocator, *, timeout: float = 10.0) -> CDPLocator:
        try:
            self._resolve().drag_to(other._resolve(), timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP drag_to failed for {self._selector!r} → {other._selector!r}: {exc}"
            ) from exc
        return self

    # Keyboard actions

    def focus(self, *, timeout: float = 5.0) -> CDPLocator:
        """Programmatically focus the element (fires focus event)."""
        try:
            self._resolve().focus(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP focus failed for selector {self._selector!r}: {exc}"
            ) from exc
        return self

    def press_key(self, key: str, *, timeout: float = 10.0) -> CDPLocator:
        """Focus the element then press *key* (Playwright key syntax).

        Supports single keys ("Enter"), named keys ("Escape", "ArrowUp"),
        and chords ("Control+A", "Shift+Tab"). For a page-level chord
        that does not target an element use :meth:`CDPSession.press_key`.
        """
        try:
            self._resolve().press(key, timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP press_key({key!r}) failed for {self._selector!r}: {exc}"
            ) from exc
        return self

    def type_text(self, text: str, *, timeout: float = 10.0, clear: bool = True) -> CDPLocator:
        """Fill the element with *text*.

        By default clears the field first (``fill`` semantics). Pass
        ``clear=False`` to append instead (``type`` semantics — key-by-key).
        """
        try:
            if clear:
                self._resolve().fill(text, timeout=timeout * 1000)
            else:
                self._resolve().type(text, timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP type_text failed for selector {self._selector!r}: {exc}"
            ) from exc
        return self

    def set_text(self, text: str, *, timeout: float = 10.0) -> CDPLocator:
        """Semantic alias for :meth:`type_text` (clear + fill)."""
        return self.type_text(text, timeout=timeout, clear=True)

    def clear(self, *, timeout: float = 10.0) -> CDPLocator:
        """Empty an editable field."""
        try:
            self._resolve().fill("", timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP clear failed for selector {self._selector!r}: {exc}"
            ) from exc
        return self

    # Form semantics

    def check(self, *, timeout: float = 10.0) -> CDPLocator:
        """Ensure a checkbox / radio is checked (idempotent)."""
        try:
            self._resolve().check(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP check failed for selector {self._selector!r}: {exc}"
            ) from exc
        return self

    def uncheck(self, *, timeout: float = 10.0) -> CDPLocator:
        """Ensure a checkbox is unchecked (idempotent)."""
        try:
            self._resolve().uncheck(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP uncheck failed for selector {self._selector!r}: {exc}"
            ) from exc
        return self

    def select_option(
        self,
        value: str | list[str] | None = None,
        *,
        label: str | list[str] | None = None,
        index: int | list[int] | None = None,
        timeout: float = 10.0,
    ) -> list[str]:
        """Select an ``<option>`` in a native ``<select>`` element.

        Pass at least one of ``value``, ``label`` or ``index``. Returns the
        list of actually-selected values (Playwright semantics).
        """
        try:
            kwargs: dict[str, Any] = {"timeout": timeout * 1000}
            if value is not None:
                kwargs["value"] = value
            if label is not None:
                kwargs["label"] = label
            if index is not None:
                kwargs["index"] = index
            return list(self._resolve().select_option(**kwargs))
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP select_option failed for selector {self._selector!r}: {exc}"
            ) from exc

    # Scroll

    def scroll_into_view(self, *, timeout: float = 10.0) -> CDPLocator:
        """Scroll the element into view (Playwright picks the best axis)."""
        try:
            self._resolve().scroll_into_view_if_needed(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP scroll_into_view failed for {self._selector!r}: {exc}"
            ) from exc
        return self

    # State readers

    def text(self, *, timeout: float = 5.0) -> str:
        """Return the element's rendered text."""
        try:
            return self._resolve().inner_text(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP text() failed for selector {self._selector!r}: {exc}"
            ) from exc

    def value(self, *, timeout: float = 5.0) -> str:
        """Return the ``value`` of an <input>/<textarea>/<select>."""
        try:
            return self._resolve().input_value(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP value() failed for selector {self._selector!r}: {exc}"
            ) from exc

    def get_attribute(self, name: str, *, timeout: float = 5.0) -> str | None:
        """Return the value of the named attribute, or ``None`` when unset."""
        try:
            return self._resolve().get_attribute(name, timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP get_attribute({name!r}) failed for {self._selector!r}: {exc}"
            ) from exc

    def bounding_box(self, *, timeout: float = 5.0) -> dict[str, float] | None:
        """Return ``{x, y, width, height}`` in CSS pixels, or ``None`` if unmounted."""
        try:
            return self._resolve().bounding_box(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP bounding_box failed for {self._selector!r}: {exc}"
            ) from exc

    def is_visible(self) -> bool:
        """Return ``True`` when the element is present AND rendered."""
        try:
            return bool(self._resolve().is_visible())
        except Exception:
            return False

    def is_enabled(self) -> bool:
        try:
            return bool(self._resolve().is_enabled())
        except Exception:
            return False

    def is_checked(self) -> bool:
        """Return ``True`` for a checked checkbox / radio."""
        try:
            return bool(self._resolve().is_checked())
        except Exception:
            return False

    def exists(self, *, timeout: float = 0.0) -> bool:
        """Return ``True`` if at least one element matches (non-blocking by default)."""
        try:
            if timeout > 0:
                self._resolve().wait_for(state="attached", timeout=timeout * 1000)
                return True
            return self._resolve().count() > 0
        except Exception:
            return False

    # Waiting

    def wait_for(self, *, state: str = "visible", timeout: float = 10.0) -> CDPLocator:
        """Block until the element reaches *state*.

        Supported states: ``"visible"``, ``"hidden"``, ``"attached"``,
        ``"detached"`` — Playwright's own state vocabulary.
        """
        try:
            self._resolve().wait_for(state=state, timeout=timeout * 1000)
        except Exception as exc:
            raise WaitTimeoutError(
                f"CDP selector {self._selector!r} did not reach state "
                f"{state!r} within {timeout}s: {exc}"
            ) from exc
        return self

    # Screenshot

    def screenshot(self, path: str | None = None, *, timeout: float = 10.0) -> Any:
        """Capture the element as a :class:`PIL.Image`; optionally save it."""
        from io import BytesIO

        from PIL import Image as _PILImage

        try:
            data = self._resolve().screenshot(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP screenshot failed for {self._selector!r}: {exc}"
            ) from exc
        img = _PILImage.open(BytesIO(data))
        img.load()
        if path:
            img.save(path)
        return img

    # Multi-match handling

    def count(self) -> int:
        """Return the number of DOM matches."""
        try:
            return int(self._resolve().count())
        except Exception:
            return 0

    def nth(self, index: int) -> CDPLocator:
        """Return a locator scoped to the *index*-th match (0-based)."""
        return self._scoped(self._resolve().nth(index))

    def first(self) -> CDPLocator:
        """Shortcut for ``nth(0)``."""
        return self.nth(0)

    def last(self) -> CDPLocator:
        return self._scoped(self._resolve().last)

    def all(self) -> list[CDPLocator]:
        """Return one CDPLocator per match, in document order."""
        return [self.nth(i) for i in range(self.count())]

    # Locator narrowing — chaining + filter + a11y selectors

    def locator(self, selector: str) -> CDPLocator:
        """Narrow this locator by chaining a sub-selector.

        Equivalent to Playwright's ``locator.locator(sub)`` — resolves the
        outer selector, then applies the sub-selector inside each match.
        """
        return self._scoped(self._resolve().locator(selector))

    def filter(
        self,
        *,
        has_text: str | None = None,
        has_not_text: str | None = None,
        has: CDPLocator | None = None,
        has_not: CDPLocator | None = None,
    ) -> CDPLocator:
        """Narrow the current locator by predicate.

        Only elements matching ALL supplied predicates survive. Mirrors
        Playwright's ``Locator.filter``.
        """
        kwargs: dict[str, Any] = {}
        if has_text is not None:
            kwargs["has_text"] = has_text
        if has_not_text is not None:
            kwargs["has_not_text"] = has_not_text
        if has is not None:
            kwargs["has"] = has._resolve()
        if has_not is not None:
            kwargs["has_not"] = has_not._resolve()
        return self._scoped(self._resolve().filter(**kwargs))

    def get_by_role(self, role: str, **kwargs: Any) -> CDPLocator:
        """Nested ``get_by_role`` — scoped under the current locator."""
        return self._scoped(self._resolve().get_by_role(role, **kwargs))

    def get_by_text(self, text: str, **kwargs: Any) -> CDPLocator:
        """Nested ``get_by_text`` — scoped under the current locator."""
        return self._scoped(self._resolve().get_by_text(text, **kwargs))

    def get_by_label(self, text: str, **kwargs: Any) -> CDPLocator:
        """Nested ``get_by_label`` — scoped under the current locator."""
        return self._scoped(self._resolve().get_by_label(text, **kwargs))

    def get_by_placeholder(self, text: str, **kwargs: Any) -> CDPLocator:
        """Nested ``get_by_placeholder`` — scoped under the current locator."""
        return self._scoped(self._resolve().get_by_placeholder(text, **kwargs))

    def get_by_title(self, text: str, **kwargs: Any) -> CDPLocator:
        """Nested ``get_by_title`` — scoped under the current locator."""
        return self._scoped(self._resolve().get_by_title(text, **kwargs))

    def get_by_alt_text(self, text: str, **kwargs: Any) -> CDPLocator:
        """Nested ``get_by_alt_text`` — scoped under the current locator."""
        return self._scoped(self._resolve().get_by_alt_text(text, **kwargs))

    def get_by_test_id(self, test_id: str) -> CDPLocator:
        """Nested ``get_by_test_id`` — scoped under the current locator."""
        return self._scoped(self._resolve().get_by_test_id(test_id))

    # Raw HTML + file inputs

    def inner_html(self, *, timeout: float = 5.0) -> str:
        """Return raw ``innerHTML`` of the element."""
        try:
            return self._resolve().inner_html(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP inner_html() failed for {self._selector!r}: {exc}"
            ) from exc

    def dispatch_event(
        self,
        event_type: str,
        event_init: dict[str, Any] | None = None,
        *,
        timeout: float = 10.0,
    ) -> CDPLocator:
        """Dispatch a synthetic DOM event on the element.

        ``event_type`` is the event name (``"click"``, ``"pointerdown"``,
        ``"drop"``, custom events). ``event_init`` populates the event
        object's fields — same shape as ``new Event(name, init)``.
        """
        try:
            self._resolve().dispatch_event(event_type, event_init or {}, timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP dispatch_event({event_type!r}) failed for {self._selector!r}: {exc}"
            ) from exc
        return self

    def evaluate(self, script: str, *args: Any) -> Any:
        """Run *script* in the page with the element passed as ``arguments[0]``.

        Element-scoped counterpart to :meth:`CDPSession.evaluate`. Useful
        for inspecting element internals (``el.dataset``, computed styles)
        without duplicating a selector lookup in JavaScript.
        """
        try:
            return self._resolve().evaluate(script, *args)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP locator.evaluate failed for {self._selector!r}: {exc}"
            ) from exc

    def press_sequentially(
        self,
        text: str,
        *,
        delay: float = 0.0,
        timeout: float = 10.0,
    ) -> CDPLocator:
        """Type *text* character-by-character with an optional *delay* per key.

        Simulates human typing — some form validators only fire per-key
        events, so ``fill()`` (which sets the value atomically) misses them.
        """
        try:
            self._resolve().press_sequentially(text, delay=delay * 1000, timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP press_sequentially failed for {self._selector!r}: {exc}"
            ) from exc
        return self

    def select_text(self, *, timeout: float = 10.0) -> CDPLocator:
        """Select the element's text content (equivalent to Ctrl+A on inputs)."""
        try:
            self._resolve().select_text(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP select_text failed for {self._selector!r}: {exc}"
            ) from exc
        return self

    def blur(self, *, timeout: float = 10.0) -> CDPLocator:
        """Programmatically blur (unfocus) the element."""
        try:
            self._resolve().blur(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(f"CDP blur failed for {self._selector!r}: {exc}") from exc
        return self

    def tap(self, *, timeout: float = 10.0) -> CDPLocator:
        """Dispatch a touch tap on the element (touch-enabled apps)."""
        try:
            self._resolve().tap(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(f"CDP tap failed for {self._selector!r}: {exc}") from exc
        return self

    def all_text_contents(self) -> list[str]:
        """Return the ``textContent`` of every element that matches."""
        try:
            return list(self._resolve().all_text_contents())
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP all_text_contents failed for {self._selector!r}: {exc}"
            ) from exc

    def element_handle(self, *, timeout: float = 10.0) -> Any:
        """Return Playwright's underlying ``ElementHandle`` — advanced escape hatch.

        Prefer the wrapped methods above; reach for this only when you
        need something Playwright exposes that dolphin has not (yet)
        wrapped. Callers accept the coupling to Playwright's API.
        """
        try:
            return self._resolve().element_handle(timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP element_handle failed for {self._selector!r}: {exc}"
            ) from exc

    def set_input_files(
        self,
        files: str | list[str],
        *,
        timeout: float = 10.0,
    ) -> CDPLocator:
        """Upload files through an ``<input type=file>``.

        *files* is a path (or list of paths) on the local filesystem. To
        clear the file input, pass an empty list.
        """
        try:
            self._resolve().set_input_files(files, timeout=timeout * 1000)
        except Exception as exc:
            raise ElementNotFoundError(
                f"CDP set_input_files failed for {self._selector!r}: {exc}"
            ) from exc
        return self


class CDPFrameLocator:
    """Locator scoped to an ``<iframe>``.

    Chain :meth:`locator`, :meth:`get_by_role` etc. on the result to
    reach into the frame's DOM — this is what you use for VS Code
    webviews, Slack message panels, Teams embedded content, and every
    other iframe-hosted UI in Electron apps.
    """

    def __init__(self, session: CDPSession, selector: str) -> None:
        self._session = session
        self._selector = selector

    def _resolve(self) -> Any:
        return self._session.page.frame_locator(self._selector)

    def locator(self, selector: str) -> CDPLocator:
        """Return a :class:`CDPLocator` for *selector* inside this frame."""
        return CDPLocator(
            self._session,
            f"{self._selector} >> {selector}",
            _handle=self._resolve().locator(selector),
        )

    def get_by_role(self, role: str, **kwargs: Any) -> CDPLocator:
        return CDPLocator(
            self._session,
            f"frame/{self._selector} >> role={role}",
            _handle=self._resolve().get_by_role(role, **kwargs),
        )

    def get_by_text(self, text: str, **kwargs: Any) -> CDPLocator:
        return CDPLocator(
            self._session,
            f"frame/{self._selector} >> text={text!r}",
            _handle=self._resolve().get_by_text(text, **kwargs),
        )

    def get_by_label(self, text: str, **kwargs: Any) -> CDPLocator:
        return CDPLocator(
            self._session,
            f"frame/{self._selector} >> label={text!r}",
            _handle=self._resolve().get_by_label(text, **kwargs),
        )
