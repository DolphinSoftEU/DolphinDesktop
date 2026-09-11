"""Playwright-shaped API on top of :class:`CDPSession`.

Ease migration for teams with existing Playwright tests. Import
``dolphin_desktop.playwright_compat`` as if it were
``playwright.sync_api`` and drive an Electron / CEF app through the
same call shapes:

    from dolphin_desktop.playwright_compat import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        page.locator("#login").click()
        page.locator("input[name=user]").fill("alice")
        page.locator("button[type=submit]").click()
        assert page.locator(".welcome").is_visible()

The mapping is deliberately thin — every attribute forwards to the
underlying dolphin object. Anything Playwright-specific that dolphin
does not implement (video recording, tracing viewer, remote grid
bridge) raises :class:`NotImplementedError` with a pointer to the
dolphin equivalent.

**Not** a drop-in for `playwright.async_api` — dolphin's CDP surface is
sync-only. If your tests are async, use Playwright directly and switch
to dolphin only for the desktop pieces Playwright cannot reach.

Timeouts
--------
Every ``timeout=`` parameter in this module is in **milliseconds**, exactly
like Playwright — ``page.goto(url, timeout=60000)`` waits 60 seconds and
``locator.wait_for(timeout=5000)`` waits 5 seconds. This differs from the
native dolphin API (:class:`dolphin_desktop.CDPSession`,
:class:`dolphin_desktop.Locator`), where timeouts are **seconds**. Mixing the
two in one test file is the usual source of confusion: the rule is that
anything reached through ``playwright_compat`` speaks milliseconds.

Known divergences
-----------------
The shim covers the call shapes most migrations hit, not all of
Playwright. These differ and will not be silently papered over:

* ``locator.fill(value)`` — Playwright names the parameter ``value``;
  here it is ``text``. Positional calls work either way.
* ``locator.is_visible()`` / ``is_enabled()`` / ``is_checked()`` accept
  ``timeout=`` for call compatibility but ignore it — the underlying
  :class:`CDPLocator` state readers are non-blocking and have no timeout.
* ``locator.count()`` and ``locator.all_text_contents()`` take no
  ``timeout``, exactly as in Playwright.
* ``page.keyboard_press(key)`` — there is no ``page.keyboard`` object,
  so Playwright's ``page.keyboard.press(key)`` has no equivalent shape.
* ``sync_playwright()`` is a context manager only; Playwright's
  ``p = sync_playwright().start()`` form is unsupported.
* ``page.screenshot()`` returns a PIL ``Image``, not ``bytes``, and its
  ``path`` argument is positional.
* Not implemented at all: ``locator.all()``, ``check()``/``uncheck()``,
  ``select_option()``, ``set_input_files()``, ``page.go_back()`` /
  ``go_forward()``, ``context.new_page()``, ``context.storage_state()``,
  ``context.tracing``, ``page.video``, and the ``expect()`` assertion
  library. Use the native :class:`CDPLocator` surface or Dolphin's own
  trace/video facilities for these — it reaches everything the shim does not.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NoReturn

from ._cdp import CDPLocator, CDPSession, CDPStalePageError


def _seconds(timeout_ms: float) -> float:
    """Convert a Playwright millisecond timeout to the seconds dolphin expects."""
    return timeout_ms / 1000.0


def _ms_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return *kwargs* with a millisecond ``timeout`` rewritten to seconds."""
    if kwargs.get("timeout") is None:
        return kwargs
    return {**kwargs, "timeout": _seconds(kwargs["timeout"])}


def _timeout_s(timeout_ms: float | None) -> dict[str, Any]:
    """Return a dolphin ``timeout=`` kwarg (seconds), or nothing when unset.

    Omitting the kwarg entirely — rather than passing ``None`` — keeps each
    dolphin method's own default in force.
    """
    return {} if timeout_ms is None else {"timeout": _seconds(timeout_ms)}


class _PlaywrightPage:
    """Thin adapter — ``page`` in Playwright ↔ one page of the CDP session.

    Adds the Playwright-flavoured method names (``fill``, ``goto``,
    ``inner_text``) that dolphin renames for consistency across
    backends.
    """

    def __init__(self, session: CDPSession, page: Any) -> None:
        self._session = session
        self._page = page

    def _select(self) -> CDPSession:
        """Retarget the shared session at this adapter's own page.

        A ``CDPSession`` tracks exactly one current page, while Playwright hands
        out independent page objects. Every delegation therefore has to re-select
        this adapter's page first, or whichever page some sibling adapter touched
        last would silently receive the action.
        """
        for index, page in enumerate(self._session.pages()):
            if page is self._page:
                self._session.switch_to_page(index)
                return self._session
        raise CDPStalePageError(
            "page is closed — the underlying CDP target no longer exists",
            hint="list the live pages again with browser.contexts[0].pages",
        )

    @property
    def url(self) -> str:
        return self._select().current_url()

    def goto(self, url: str, *, timeout: float | None = None) -> None:
        native_timeout = None if timeout is None else _seconds(timeout)
        self._select().page.goto(url, timeout=native_timeout)

    def locator(self, selector: str) -> _PlaywrightLocator:
        return _PlaywrightLocator(self, self._select().locator(selector))

    def get_by_role(self, role: str, **kwargs: Any) -> _PlaywrightLocator:
        return _PlaywrightLocator(self, self._select().get_by_role(role, **kwargs))

    def get_by_text(self, text: str, **kwargs: Any) -> _PlaywrightLocator:
        return _PlaywrightLocator(self, self._select().get_by_text(text, **kwargs))

    def get_by_label(self, text: str, **kwargs: Any) -> _PlaywrightLocator:
        return _PlaywrightLocator(self, self._select().get_by_label(text, **kwargs))

    def get_by_placeholder(self, text: str, **kwargs: Any) -> _PlaywrightLocator:
        return _PlaywrightLocator(self, self._select().get_by_placeholder(text, **kwargs))

    def get_by_test_id(self, test_id: str) -> _PlaywrightLocator:
        return _PlaywrightLocator(self, self._select().get_by_test_id(test_id))

    def wait_for_url(self, url: str, *, timeout: float = 30_000) -> None:
        self._select().wait_for_url(url, timeout=_seconds(timeout))

    def wait_for_load_state(self, state: str = "load", *, timeout: float = 30_000) -> None:
        self._select().wait_for_load_state(state, timeout=_seconds(timeout))

    def wait_for_selector(
        self, selector: str, *, state: str = "visible", timeout: float = 10_000
    ) -> _PlaywrightLocator:
        return _PlaywrightLocator(
            self,
            self._select().wait_for_selector(selector, state=state, timeout=_seconds(timeout)),
        )

    def screenshot(self, path: str | None = None, **_: Any) -> Any:
        return self._select().screenshot(path=path)

    def evaluate(self, script: str, *args: Any) -> Any:
        return self._select().evaluate(script, *args)

    def title(self) -> str:
        return self._select().page.title()

    def reload(self, *, timeout: float = 30_000) -> None:
        self._select().reload(timeout=_seconds(timeout))

    def close(self) -> None:
        self._page.close()

    @property
    def video(self) -> NoReturn:
        """Reject Playwright video access in favour of Dolphin video capture."""
        raise NotImplementedError(
            "dolphin.playwright_compat does not expose Playwright video — use "
            "Dolphin's pytest video capture instead."
        )

    def on(self, event: str, handler: Any) -> None:
        self._page.on(event, handler)

    def keyboard_press(self, key: str) -> None:
        self._select().press_key(key)


class _PlaywrightLocator:
    """Adapter for Playwright's ``page.locator(...)`` return value.

    Bound to the :class:`_PlaywrightPage` it came from, not just to the shared
    session: a ``CDPLocator`` resolves its selector against ``session.page`` at
    action time, so selecting the page only when the locator was created would
    send the action to whichever page a sibling adapter touched last.
    """

    def __init__(self, page: _PlaywrightPage, inner: CDPLocator) -> None:
        self._page = page
        self._inner = inner

    def _target(self) -> CDPLocator:
        """Retarget the session at the owning page, then return the inner locator."""
        self._page._select()
        return self._inner

    # Mouse
    def click(self, **kwargs: Any) -> None:
        self._target().click(**_ms_kwargs(kwargs))

    def dblclick(self, **kwargs: Any) -> None:
        self._target().double_click(**_ms_kwargs(kwargs))

    def hover(self, **kwargs: Any) -> None:
        self._target().hover(**_ms_kwargs(kwargs))

    # Keyboard / text
    def fill(self, text: str, *, timeout: float | None = None, **_: Any) -> None:
        self._target().type_text(text, clear=True, **_timeout_s(timeout))

    def type(self, text: str, *, timeout: float | None = None, **_: Any) -> None:
        self._target().type_text(text, clear=False, **_timeout_s(timeout))

    def press(self, key: str, *, timeout: float | None = None, **_: Any) -> None:
        self._target().press_key(key, **_timeout_s(timeout))

    def clear(self, *, timeout: float | None = None) -> None:
        self._target().clear(**_timeout_s(timeout))

    # State reads
    def is_visible(self, **_: Any) -> bool:
        return self._target().is_visible()

    def is_enabled(self, **_: Any) -> bool:
        return self._target().is_enabled()

    def is_checked(self, **_: Any) -> bool:
        return self._target().is_checked()

    def text_content(self, *, timeout: float | None = None) -> str:
        return self._target().text(**_timeout_s(timeout))

    def inner_text(self, *, timeout: float | None = None) -> str:
        return self._target().text(**_timeout_s(timeout))

    def input_value(self, *, timeout: float | None = None) -> str:
        return self._target().value(**_timeout_s(timeout))

    def get_attribute(self, name: str, *, timeout: float | None = None) -> str | None:
        return self._target().get_attribute(name, **_timeout_s(timeout))

    def count(self) -> int:
        return self._target().count()

    def all_text_contents(self) -> list[str]:
        return self._target().all_text_contents()

    # Narrowing
    def locator(self, selector: str) -> _PlaywrightLocator:
        return _PlaywrightLocator(self._page, self._target().locator(selector))

    def filter(self, **kwargs: Any) -> _PlaywrightLocator:
        return _PlaywrightLocator(self._page, self._target().filter(**kwargs))

    @property
    def first(self) -> _PlaywrightLocator:
        return _PlaywrightLocator(self._page, self._target().first())

    @property
    def last(self) -> _PlaywrightLocator:
        return _PlaywrightLocator(self._page, self._target().last())

    def nth(self, index: int) -> _PlaywrightLocator:
        return _PlaywrightLocator(self._page, self._target().nth(index))

    # A11y selectors chain
    def get_by_role(self, role: str, **kwargs: Any) -> _PlaywrightLocator:
        return _PlaywrightLocator(self._page, self._target().get_by_role(role, **kwargs))

    def get_by_text(self, text: str, **kwargs: Any) -> _PlaywrightLocator:
        return _PlaywrightLocator(self._page, self._target().get_by_text(text, **kwargs))

    # Wait
    def wait_for(self, *, state: str = "visible", timeout: float = 10_000) -> None:
        self._target().wait_for(state=state, timeout=_seconds(timeout))

    # Debug
    def screenshot(self, **kwargs: Any) -> Any:
        return self._target().screenshot(**_ms_kwargs(kwargs))

    def bounding_box(self, *, timeout: float | None = None) -> dict[str, float] | None:
        try:
            return self._target().bounding_box(**_timeout_s(timeout))
        except Exception:
            return None


class _PlaywrightContext:
    """Playwright ``BrowserContext`` view — carries the pages list."""

    def __init__(self, session: CDPSession) -> None:
        self._session = session

    @property
    def pages(self) -> list[_PlaywrightPage]:
        # Reading this property must not retarget the session — each adapter
        # binds to its own page and selects it lazily, when used.
        return [_PlaywrightPage(self._session, page) for page in self._session.pages()]

    def new_page(self, **_: Any) -> NoReturn:
        """Reject creating a page through a new Playwright context."""
        raise NotImplementedError(
            "dolphin's CDP compatibility layer does not create new pages — "
            "open a page in the Electron application instead."
        )

    def storage_state(self, **_: Any) -> NoReturn:
        """Reject Playwright persistent storage-state snapshots."""
        raise NotImplementedError(
            "dolphin.playwright_compat does not support Playwright storage state — "
            "use Dolphin CDP storage helpers instead."
        )

    @property
    def tracing(self) -> NoReturn:
        """Reject Playwright tracing in favour of Dolphin trace capture."""
        raise NotImplementedError(
            "dolphin.playwright_compat does not expose Playwright tracing — use "
            "Dolphin's TraceSession instead."
        )


class _PlaywrightBrowser:
    """Playwright ``Browser`` view. Only ``contexts`` and ``close`` are wired."""

    def __init__(self, session: CDPSession) -> None:
        self._session = session

    @property
    def contexts(self) -> list[_PlaywrightContext]:
        return [_PlaywrightContext(self._session)]

    def close(self) -> None:
        self._session.close()

    def new_context(self, **_: Any) -> _PlaywrightContext:
        raise NotImplementedError(
            "dolphin's CDP session does not create new contexts — Electron "
            "apps host their pages under the default context. Use "
            "browser.contexts[0] instead."
        )


class _PlaywrightChromiumBrowserType:
    """Playwright's ``playwright.chromium`` bridge."""

    def connect_over_cdp(self, endpoint: str, *, timeout: float = 15_000) -> _PlaywrightBrowser:
        session = CDPSession.connect(endpoint, timeout=_seconds(timeout))
        return _PlaywrightBrowser(session)

    def launch(self, **_: Any) -> _PlaywrightBrowser:
        raise NotImplementedError(
            "dolphin.playwright_compat.chromium.launch() is not supported — "
            "dolphin never launches a standalone Chromium; use "
            "Desktop().launch_electron_cdp(...) for Electron apps or "
            ".connect_over_cdp(url) for an already-running host."
        )


class _Playwright:
    """The object yielded by :func:`sync_playwright`."""

    def __init__(self) -> None:
        self.chromium = _PlaywrightChromiumBrowserType()

    @property
    def firefox(self) -> Any:
        raise NotImplementedError(
            "dolphin.playwright_compat covers Chromium (Electron / CEF) only. "
            "For Firefox use Playwright directly."
        )

    @property
    def webkit(self) -> Any:
        raise NotImplementedError(
            "dolphin.playwright_compat covers Chromium (Electron / CEF) only. "
            "For WebKit use Playwright directly."
        )


@contextmanager
def sync_playwright() -> Iterator[_Playwright]:
    """Yield a Playwright-shaped facade backed by dolphin's CDP session.

    Usage matches Playwright's own ``sync_playwright`` context manager::

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            page = browser.contexts[0].pages[0]
            page.locator("#login").click()
            browser.close()
    """
    p = _Playwright()
    try:
        yield p
    finally:
        # Nothing to clean up at the module scope — individual browsers
        # / sessions manage their own lifecycles.
        pass


def async_playwright() -> NoReturn:
    """Reject Playwright's asynchronous API explicitly.

    Dolphin's compatibility layer is synchronous.  Raising here gives users
    a deterministic migration hint instead of an import-time ``ImportError``.
    """
    raise NotImplementedError(
        "dolphin.playwright_compat is sync-only; use sync_playwright() or "
        "use Playwright's async API directly without this compatibility layer."
    )


__all__ = ["async_playwright", "sync_playwright"]
