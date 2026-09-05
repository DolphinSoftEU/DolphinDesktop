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

# --------------------------------------------------------------------------- #
# Preconditions                                                                #
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
