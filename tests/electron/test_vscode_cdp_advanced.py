"""Advanced CDPLocator scenarios — selector variants, error paths, edge cases.

Complements :mod:`test_vscode_cdp` (baseline surface) with:

* Selector engine variants (CSS, attribute, pseudo-class, XPath, ``text=``)
* Error paths (`ElementNotFoundError`, `WaitTimeoutError`) — must raise
  the *dolphin* exception type, not Playwright's
* is_visible edge cases (display:none, visibility:hidden, offscreen,
  0×0 size)
* wait_for state machine (attached / detached / visible / hidden)
* Locator resolution against dynamically added / removed elements
* Unicode text, empty text, rendered whitespace collapsing
* Concurrent locators (many resolved in one test without collisions)
"""

from __future__ import annotations

import pytest

from dolphin_desktop import ElementNotFoundError, WaitTimeoutError
from tests.electron._cdp_env import VSCODE, has_playwright  # type: ignore[import-not-found]

skip_no_vscode = pytest.mark.skipif(VSCODE is None, reason="VS Code not installed")
skip_no_playwright = pytest.mark.skipif(
    not has_playwright(),
    reason="dolphin_desktop[cdp] extra not installed",
)

pytestmark = [skip_no_vscode, skip_no_playwright, pytest.mark.timeout(60)]


# ---------------------------------------------------------------------------
# Selector engine variants
# ---------------------------------------------------------------------------


def test_locator_xpath_engine_finds_element(vscode_cdp):
    """Playwright accepts ``xpath=`` engine — CDPLocator must forward it."""
    vscode_cdp.evaluate(
        """
        () => {
          const div = document.createElement('div');
          div.id = 'xpath-target';
          div.className = 'xp-marker';
          div.textContent = 'xpath finds me';
          div.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;';
          document.body.appendChild(div);
        }
        """
    )
    assert vscode_cdp.locator("xpath=//div[@id='xpath-target']").is_visible()
    assert (
        vscode_cdp.locator("xpath=//div[contains(@class,'xp-marker')]").text() == "xpath finds me"
    )


def test_locator_text_engine_matches_visible_text(vscode_cdp):
    """``text=`` selects by rendered text — Playwright's built-in engine."""
    vscode_cdp.evaluate(
        """
        () => {
          const p = document.createElement('p');
          p.textContent = 'find me by text';
          p.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;';
          document.body.appendChild(p);
        }
        """
    )
    assert vscode_cdp.locator("text=find me by text").is_visible()


def test_locator_attribute_selector(vscode_cdp):
    """CSS attribute selectors work through CDPLocator."""
    vscode_cdp.evaluate(
        """
        () => {
          const el = document.createElement('div');
          el.setAttribute('data-testid', 'attr-target');
          el.textContent = 'attr';
          el.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;';
          document.body.appendChild(el);
        }
        """
    )
    assert vscode_cdp.locator('[data-testid="attr-target"]').text() == "attr"


def test_locator_pseudo_class_selectors(vscode_cdp):
    """CSS pseudo-classes (:nth-child, :not) reach through CDP."""
    vscode_cdp.evaluate(
        """
        () => {
          const ul = document.createElement('ul');
          ul.id = 'pseudo-list';
          ul.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;';
          for (const t of ['first','second','third']) {
            const li = document.createElement('li');
            li.textContent = t;
            ul.appendChild(li);
          }
          document.body.appendChild(ul);
        }
        """
    )
    assert vscode_cdp.locator("#pseudo-list li:nth-child(2)").text() == "second"
    assert (
        vscode_cdp.locator("#pseudo-list li:not(:first-child):not(:last-child)").text() == "second"
    )


# ---------------------------------------------------------------------------
# Error paths — MUST raise dolphin exception types
# ---------------------------------------------------------------------------


def test_locator_click_missing_raises_element_not_found(vscode_cdp):
    """Clicking a selector that does not exist must raise ElementNotFoundError."""
    loc = vscode_cdp.locator("#definitely-does-not-exist-abc123")
    with pytest.raises(ElementNotFoundError):
        loc.click(timeout=1.5)


def test_locator_type_text_missing_raises_element_not_found(vscode_cdp):
    """type_text on missing selector → ElementNotFoundError."""
    loc = vscode_cdp.locator("#missing-input-xyz")
    with pytest.raises(ElementNotFoundError):
        loc.type_text("nope", timeout=1.5)


def test_locator_text_missing_raises_element_not_found(vscode_cdp):
    """text() on missing selector → ElementNotFoundError."""
    loc = vscode_cdp.locator("#no-such-thing-42")
    with pytest.raises(ElementNotFoundError):
        loc.text(timeout=1.5)


def test_locator_wait_for_missing_raises_wait_timeout(vscode_cdp):
    """wait_for on missing selector → WaitTimeoutError (dolphin type)."""
    loc = vscode_cdp.locator("#never-appears-selector")
    with pytest.raises(WaitTimeoutError):
        loc.wait_for(state="visible", timeout=2)


def test_locator_is_visible_missing_returns_false(vscode_cdp):
    """is_visible() must swallow errors and return False for missing selectors."""
    assert vscode_cdp.locator("#totally-absent-node").is_visible() is False


# ---------------------------------------------------------------------------
# is_visible edge cases
# ---------------------------------------------------------------------------


def test_is_visible_false_for_display_none(vscode_cdp):
    """display:none must report as not visible."""
    vscode_cdp.evaluate(
        """
        () => {
          const d = document.createElement('div');
          d.id = 'invis-display';
          d.style.display = 'none';
          d.textContent = 'hidden';
          document.body.appendChild(d);
        }
        """
    )
    assert vscode_cdp.locator("#invis-display").is_visible() is False


def test_is_visible_false_for_visibility_hidden(vscode_cdp):
    """visibility:hidden must report as not visible."""
    vscode_cdp.evaluate(
        """
        () => {
          const d = document.createElement('div');
          d.id = 'invis-visibility';
          d.style.visibility = 'hidden';
          d.textContent = 'hidden';
          document.body.appendChild(d);
        }
        """
    )
    assert vscode_cdp.locator("#invis-visibility").is_visible() is False


def test_is_visible_true_for_offscreen_but_rendered(vscode_cdp):
    """Off-screen elements ARE visible per Playwright semantics (rendered)."""
    vscode_cdp.evaluate(
        """
        () => {
          const d = document.createElement('div');
          d.id = 'offscreen';
          d.style.cssText = 'position:fixed;top:-99999px;left:-99999px;'
                          + 'width:10px;height:10px;background:red;';
          d.textContent = 'offscreen';
          document.body.appendChild(d);
        }
        """
    )
    assert vscode_cdp.locator("#offscreen").is_visible() is True


def test_is_visible_false_for_zero_size(vscode_cdp):
    """0×0 elements should not report as visible."""
    vscode_cdp.evaluate(
        """
        () => {
          const d = document.createElement('div');
          d.id = 'zero-size';
          d.style.cssText = 'width:0;height:0;overflow:hidden;';
          document.body.appendChild(d);
        }
        """
    )
    assert vscode_cdp.locator("#zero-size").is_visible() is False


# ---------------------------------------------------------------------------
# wait_for state machine
# ---------------------------------------------------------------------------


def test_wait_for_attached_waits_for_dom_insertion(vscode_cdp):
    """wait_for(state='attached') blocks until element enters DOM."""
    vscode_cdp.evaluate(
        """
        () => {
          setTimeout(() => {
            const d = document.createElement('div');
            d.id = 'appears-late';
            d.textContent = 'here now';
            document.body.appendChild(d);
          }, 400);
        }
        """
    )
    vscode_cdp.locator("#appears-late").wait_for(state="attached", timeout=5)
    assert vscode_cdp.locator("#appears-late").text() == "here now"


def test_wait_for_detached_waits_for_dom_removal(vscode_cdp):
    """wait_for(state='detached') blocks until element leaves DOM."""
    vscode_cdp.evaluate(
        """
        () => {
          const d = document.createElement('div');
          d.id = 'goes-away';
          document.body.appendChild(d);
          setTimeout(() => d.remove(), 400);
        }
        """
    )
    vscode_cdp.locator("#goes-away").wait_for(state="detached", timeout=5)
    assert vscode_cdp.locator("#goes-away").is_visible() is False


def test_wait_for_hidden_waits_for_css_hide(vscode_cdp):
    """wait_for(state='hidden') blocks until element becomes non-visible."""
    vscode_cdp.evaluate(
        """
        () => {
          const d = document.createElement('div');
          d.id = 'hides-later';
          d.style.cssText = 'position:fixed;top:0;left:0;width:20px;height:20px;';
          document.body.appendChild(d);
          setTimeout(() => { d.style.display = 'none'; }, 400);
        }
        """
    )
    vscode_cdp.locator("#hides-later").wait_for(state="hidden", timeout=5)
    assert vscode_cdp.locator("#hides-later").is_visible() is False


def test_wait_for_visible_times_out_cleanly(vscode_cdp):
    """wait_for(state='visible') for a permanently hidden element raises."""
    vscode_cdp.evaluate(
        """
        () => {
          const d = document.createElement('div');
          d.id = 'never-visible';
          d.style.display = 'none';
          document.body.appendChild(d);
        }
        """
    )
    with pytest.raises(WaitTimeoutError):
        vscode_cdp.locator("#never-visible").wait_for(state="visible", timeout=2)


# ---------------------------------------------------------------------------
# Text encoding & special content
# ---------------------------------------------------------------------------


def test_text_reads_unicode_content(vscode_cdp):
    """Unicode text round-trips: emoji, CJK, RTL, Polish diacritics."""
    unicode = "Zażółć gęślą jaźń — 日本語 عربى 🐬"
    vscode_cdp.evaluate(
        """
        (payload) => {
          const p = document.createElement('p');
          p.id = 'unicode-target';
          p.textContent = payload;
          p.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;';
          document.body.appendChild(p);
        }
        """,
        unicode,
    )
    assert vscode_cdp.locator("#unicode-target").text() == unicode


def test_text_reads_empty_string(vscode_cdp):
    """Empty text element returns empty string, not None."""
    vscode_cdp.evaluate(
        """
        () => {
          const p = document.createElement('p');
          p.id = 'empty-text';
          p.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;';
          document.body.appendChild(p);
        }
        """
    )
    assert vscode_cdp.locator("#empty-text").text() == ""


def test_text_trims_surrounding_whitespace(vscode_cdp):
    """inner_text collapses collapsible whitespace per browser rendering."""
    vscode_cdp.evaluate(
        """
        () => {
          const p = document.createElement('p');
          p.id = 'ws-target';
          p.textContent = '   inner   ';
          p.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;';
          document.body.appendChild(p);
        }
        """
    )
    text = vscode_cdp.locator("#ws-target").text()
    # Playwright's inner_text returns rendered text, which collapses spaces.
    assert "inner" in text


# ---------------------------------------------------------------------------
# Locator reuse & concurrency
# ---------------------------------------------------------------------------


def test_multiple_locators_can_coexist(vscode_cdp):
    """Building N locators does not exhaust or lock the CDP session."""
    vscode_cdp.evaluate(
        """
        () => {
          for (let i = 0; i < 25; i++) {
            const d = document.createElement('div');
            d.id = `slot-${i}`;
            d.textContent = `n${i}`;
            d.style.cssText = 'position:fixed;top:0;left:0;';
            document.body.appendChild(d);
          }
        }
        """
    )
    for i in range(25):
        loc = vscode_cdp.locator(f"#slot-{i}")
        assert loc.text() == f"n{i}"


def test_same_locator_reused_after_dom_mutation(vscode_cdp):
    """A CDPLocator is a selector, not an element — re-resolves each call."""
    loc = vscode_cdp.locator("#mutating-target")
    vscode_cdp.evaluate(
        """
        () => {
          const d = document.createElement('div');
          d.id = 'mutating-target';
          d.textContent = 'v1';
          d.style.cssText = 'position:fixed;top:0;left:0;';
          document.body.appendChild(d);
        }
        """
    )
    assert loc.text() == "v1"
    vscode_cdp.evaluate("() => { document.getElementById('mutating-target').textContent = 'v2'; }")
    assert loc.text() == "v2"


def test_locator_click_returns_self_for_chaining(vscode_cdp):
    """All action methods return self so tests can chain calls."""
    vscode_cdp.evaluate(
        """
        () => {
          const b = document.createElement('button');
          b.id = 'chain-btn';
          b.textContent = 'x';
          b.style.cssText = 'position:fixed;top:300px;left:300px;'
                          + 'width:80px;height:30px;z-index:100000;';
          window.__chain_clicks = 0;
          b.addEventListener('click', () => { window.__chain_clicks++; });
          document.body.appendChild(b);
        }
        """
    )
    loc = vscode_cdp.locator("#chain-btn")
    result = loc.click().click().click()
    assert result is loc
    assert vscode_cdp.evaluate("() => window.__chain_clicks") == 3
