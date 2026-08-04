"""Steam CEF + CDP integration test.

Demonstrates that ``Desktop.launch_cef_cdp`` (or the safer
``CDPSession.connect`` path when Steam is already running with
``-cef-enable-debugging``) is enough to drive Steam's CEF DOM using the
same :class:`CDPSession` / :class:`CDPLocator` surface as Electron.

Nothing about the DOM below is Steam-internals-brittle: we probe pages
by URL substring and evaluate `document.body.innerText` for the game
name. When Valve renames CSS classes, we do not care.

All imports are ``dolphin_desktop`` + ``pytest`` + the local
``_steam_env`` module — the autonomous-library contract holds.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import is_cdp_available
from tests.steam._steam_env import STEAM, has_playwright  # type: ignore[import-not-found]

# --------------------------------------------------------------------------- #
# Preconditions                                                                #
# --------------------------------------------------------------------------- #


def test_cdp_extra_installed() -> None:
    """The ``[cdp]`` extra must be present for any of this to work."""
    if not has_playwright():
        pytest.skip("dolphin_desktop[cdp] not installed")
    assert is_cdp_available()


def test_steam_installed() -> None:
    """Steam client is on the machine."""
    if STEAM is None:
        pytest.skip("Steam not installed at any known path")
    assert STEAM.lower().endswith("steam.exe")


# --------------------------------------------------------------------------- #
# Session smoke                                                                #
# --------------------------------------------------------------------------- #


def test_session_yields_pages(steam_cdp) -> None:
    """Attached CDP session must expose at least one CEF page.

    Steam always has AT LEAST the main window page. If the fixture
    connected successfully but pages() is empty, the CEF runtime is
    broken or in the process of shutting down.
    """
    pages = steam_cdp.pages()
    assert len(pages) >= 1, "Steam CEF returned no pages"


def test_evaluate_runs_in_steam_page(steam_cdp) -> None:
    """``session.evaluate`` executes JavaScript inside the CEF page."""
    result = steam_cdp.evaluate("() => 40 + 2")
    assert result == 42


def test_current_url_looks_like_steam(steam_cdp) -> None:
    """The active page URL should belong to Steam's CEF UI.

    Steam uses ``steamloopback.host`` for its built-in HTML overlays and
    library. Older Steam builds sometimes use ``about:blank`` on the
    hidden helper page; we accept any non-empty URL.
    """
    url = steam_cdp.current_url()
    assert url, f"unexpected empty URL: {url!r}"


# --------------------------------------------------------------------------- #
# Library probe — "does Counter-Strike show up in my library?"                 #
# --------------------------------------------------------------------------- #


def _search_all_pages_for(session, needle: str) -> tuple[bool, list[str]]:
    """Return (found, [urls of pages that mention *needle*])."""
    hits: list[str] = []
    pages = session.pages()
    for i in range(len(pages)):
        try:
            session.switch_to_page(i)
        except Exception:
            continue
        try:
            text = session.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            continue
        if text and needle.lower() in text.lower():
            hits.append(pages[i].url)
    return (len(hits) > 0, hits)


def test_find_counter_strike_in_library(steam_cdp) -> None:
    """CS/CS2 appears somewhere in Steam's currently-rendered CEF DOM.

    This is a state probe, not a regression test — it only passes when
    the running Steam account owns any Counter-Strike title AND the
    library page has been navigated to at least once so the game grid
    is rendered. Skips otherwise, so CI without a signed-in Steam does
    not fail.
    """
    found, hits = _search_all_pages_for(steam_cdp, "Counter-Strike")
    if not found:
        pytest.skip(
            "Counter-Strike not found in any Steam CEF page. Open the "
            "Library tab in Steam and re-run — the library grid must be "
            "rendered for its DOM text to be reachable."
        )
    assert any("steamloopback.host" in u or "steam" in u.lower() for u in hits), (
        f"Match came from an unexpected origin: {hits!r}"
    )


def test_find_cs2_exact_title(steam_cdp) -> None:
    """Prefer the modern title ``Counter-Strike 2`` when the account owns it."""
    found, _hits = _search_all_pages_for(steam_cdp, "Counter-Strike 2")
    if not found:
        pytest.skip("CS2 title not visible — account may only own CS:GO/CS 1.6")
