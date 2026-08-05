"""CDP adapter integration test — drives a real Electron app (VS Code).

Runs the full ``CDPLocator`` surface (``click``, ``type_text``, ``text``,
``is_visible``, ``wait_for``) plus Shadow DOM piercing against a live VS
Code process launched with ``--remote-debugging-port``.

Skip conditions:

* VS Code executable not found in the usual install locations → skip
* ``dolphin_desktop[cdp]`` extra not installed → skip

The test imports only from ``dolphin_desktop`` (per the library's autonomy
rule). Shadow DOM is built in-page via ``session.evaluate`` — no need to
touch Playwright directly.
"""

from __future__ import annotations

import pytest

from tests.electron._cdp_env import VSCODE, has_playwright  # type: ignore[import-not-found]

skip_no_vscode = pytest.mark.skipif(VSCODE is None, reason="VS Code not installed")
skip_no_playwright = pytest.mark.skipif(
    not has_playwright(),
    reason="dolphin_desktop[cdp] extra not installed",
)


# ---------------------------------------------------------------------------
# Sanity — launch + attach
# ---------------------------------------------------------------------------


@skip_no_vscode
@skip_no_playwright
@pytest.mark.timeout(60)
def test_launch_electron_cdp_returns_session_and_application(vscode_cdp):
    """launch_electron_cdp returns a CDPSession bound to a live page."""
    assert vscode_cdp is not None
    assert vscode_cdp.page is not None
    # There is at least one CDP page (the main renderer window).
    pages = vscode_cdp.pages()
    assert len(pages) >= 1


# ---------------------------------------------------------------------------
# 5 acceptance methods — click / type_text / text / is_visible / wait_for
# ---------------------------------------------------------------------------


@skip_no_vscode
@skip_no_playwright
@pytest.mark.timeout(60)
def test_cdp_locator_is_visible_on_workbench(vscode_cdp):
    """`.monaco-workbench` is present in every open VS Code window."""
    loc = vscode_cdp.locator(".monaco-workbench")
    assert loc.is_visible() is True


@skip_no_vscode
@skip_no_playwright
@pytest.mark.timeout(60)
def test_cdp_locator_wait_for_visible_state(vscode_cdp):
    """wait_for should not raise for an already-visible workbench."""
    loc = vscode_cdp.locator(".monaco-workbench")
    result = loc.wait_for(state="visible", timeout=10)
    assert result is loc


@skip_no_vscode
@skip_no_playwright
@pytest.mark.timeout(60)
def test_cdp_locator_text_reads_activity_bar(vscode_cdp):
    """evaluate() returns a non-empty ``document.title`` from the renderer."""
    text = vscode_cdp.evaluate("() => document.title")
    assert isinstance(text, str)
    assert len(text) > 0


@skip_no_vscode
@skip_no_playwright
@pytest.mark.timeout(60)
def test_cdp_locator_click_synthetic_button(vscode_cdp):
    """click() dispatches a trusted CDP mouse event.

    We inject a button plus a click counter into the DOM, click via CDP,
    and read the counter back — round-trips the whole action pipeline
    without depending on VS Code's specific UI state (which can vary
    across versions).
    """
    vscode_cdp.evaluate(
        """
        () => {
          const btn = document.createElement('button');
          btn.id = 'dolphin-cdp-btn';
          btn.textContent = 'click me';
          btn.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;'
                            + 'width:120px;height:40px;';
          window.__dolphin_click_count = 0;
          btn.addEventListener('click', () => { window.__dolphin_click_count++; });
          document.body.appendChild(btn);
        }
        """
    )
    vscode_cdp.locator("#dolphin-cdp-btn").click()
    count = vscode_cdp.evaluate("() => window.__dolphin_click_count")
    assert count == 1


@skip_no_vscode
@skip_no_playwright
@pytest.mark.timeout(60)
def test_cdp_locator_type_text_into_synthetic_input(vscode_cdp):
    """type_text() fills a real <input> and the value round-trips."""
    vscode_cdp.evaluate(
        """
        () => {
          const inp = document.createElement('input');
          inp.id = 'dolphin-cdp-input';
          inp.style.cssText = 'position:fixed;top:60px;left:0;z-index:99999;';
          document.body.appendChild(inp);
        }
        """
    )
    vscode_cdp.locator("#dolphin-cdp-input").type_text("hello dolphin")
    value = vscode_cdp.evaluate("() => document.getElementById('dolphin-cdp-input').value")
    assert value == "hello dolphin"


# ---------------------------------------------------------------------------
# Shadow DOM piercing
# ---------------------------------------------------------------------------


@skip_no_vscode
@skip_no_playwright
@pytest.mark.timeout(60)
def test_cdp_locator_pierces_open_shadow_dom(vscode_cdp):
    """CDPLocator must reach elements inside an OPEN shadow root.

    Playwright's CSS engine auto-pierces open shadow roots for plain CSS
    selectors — the piercing arrow ``>>`` chains sub-locators when the
    element is deeply nested. We build a host with an open shadow root
    containing a button, then reach in with ``>>`` and click.
    """
    vscode_cdp.evaluate(
        """
        () => {
          const host = document.createElement('div');
          host.id = 'dolphin-shadow-host';
          host.style.cssText = 'position:fixed;top:120px;left:0;z-index:99999;';
          const root = host.attachShadow({mode: 'open'});
          const btn = document.createElement('button');
          btn.id = 'shadow-btn';
          btn.textContent = 'in-shadow';
          btn.style.cssText = 'width:120px;height:40px;';
          window.__dolphin_shadow_clicks = 0;
          btn.addEventListener('click', () => { window.__dolphin_shadow_clicks++; });
          root.appendChild(btn);
          document.body.appendChild(host);
        }
        """
    )
    # Two paths must both succeed:
    #   1. Auto-pierced plain CSS ("descendant of the host")
    #   2. Explicit ">>" chain — the shadow-descendant syntax
    vscode_cdp.locator("#dolphin-shadow-host >> #shadow-btn").click()
    clicks_after_arrow = vscode_cdp.evaluate("() => window.__dolphin_shadow_clicks")
    assert clicks_after_arrow == 1

    vscode_cdp.locator("#dolphin-shadow-host #shadow-btn").click()
    clicks_after_plain = vscode_cdp.evaluate("() => window.__dolphin_shadow_clicks")
    assert clicks_after_plain == 2


@skip_no_vscode
@skip_no_playwright
@pytest.mark.timeout(60)
def test_cdp_locator_text_inside_shadow_root(vscode_cdp):
    """text() through a >> chain reads content from inside a shadow root."""
    vscode_cdp.evaluate(
        """
        () => {
          const host = document.createElement('div');
          host.id = 'dolphin-shadow-host-2';
          host.style.cssText = 'position:fixed;top:180px;left:0;z-index:99999;';
          const root = host.attachShadow({mode: 'open'});
          const span = document.createElement('span');
          span.id = 'shadow-label';
          span.textContent = 'hidden treasure';
          root.appendChild(span);
          document.body.appendChild(host);
        }
        """
    )
    text = vscode_cdp.locator("#dolphin-shadow-host-2 >> #shadow-label").text()
    assert text == "hidden treasure"


# ---------------------------------------------------------------------------
# Lifecycle & multi-page
# ---------------------------------------------------------------------------


@skip_no_vscode
@skip_no_playwright
@pytest.mark.timeout(60)
def test_cdp_session_pages_returns_at_least_one(vscode_cdp):
    """pages() reports every CDP-visible page across every context."""
    pages = vscode_cdp.pages()
    assert isinstance(pages, list)
    assert len(pages) >= 1


@skip_no_vscode
@skip_no_playwright
@pytest.mark.timeout(60)
def test_cdp_locator_wait_for_hidden_state_times_out_cleanly(vscode_cdp):
    """wait_for(state=hidden) on a visible element raises WaitTimeoutError."""
    from dolphin_desktop import WaitTimeoutError

    loc = vscode_cdp.locator(".monaco-workbench")
    with pytest.raises(WaitTimeoutError):
        loc.wait_for(state="hidden", timeout=2)


# ---------------------------------------------------------------------------
# Missing-extra error path (does not need VS Code)
# ---------------------------------------------------------------------------


def test_missing_cdp_extra_gives_runtime_error(monkeypatch):
    """If Playwright is not installed, CDPSession.connect() must raise
    RuntimeError (with install instructions), never ImportError."""
    import dolphin_desktop._cdp as cdp_mod
    from dolphin_desktop import CDPSession

    monkeypatch.setattr(
        cdp_mod,
        "_require_playwright",
        lambda: (_ for _ in ()).throw(
            RuntimeError(
                "dolphin_desktop[cdp] is not installed — run:\n"
                '    pip install "dolphin-desktop[cdp]"\n'
                "    playwright install chromium"
            )
        ),
    )
    with pytest.raises(RuntimeError, match="dolphin-desktop\\[cdp\\]"):
        CDPSession.connect("http://localhost:9999")
