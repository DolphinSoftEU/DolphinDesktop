"""Shadow DOM scenarios — the headline Electron/CDP use case.

Playwright's CSS engine auto-pierces **open** shadow roots. This suite
proves it end-to-end for depths, chained ``>>`` combinators, custom
elements, slotted content, and confirms the documented failure mode for
**closed** roots.

All fixtures are built via ``createElement`` — VS Code's Trusted Types
policy forbids ``innerHTML``.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import ElementNotFoundError
from tests.electron._cdp_env import VSCODE, has_playwright  # type: ignore[import-not-found]

skip_no_vscode = pytest.mark.skipif(VSCODE is None, reason="VS Code not installed")
skip_no_playwright = pytest.mark.skipif(
    not has_playwright(),
    reason="dolphin_desktop[cdp] extra not installed",
)

pytestmark = [skip_no_vscode, skip_no_playwright, pytest.mark.timeout(60)]


def _mount_host(session, host_id: str, mode: str = "open") -> None:
    """Attach a fresh shadow-root host at *host_id* with mode `open`/`closed`.

    Returns nothing; call :meth:`session.evaluate` again to populate the
    root via handles saved on ``window.__shadowRoots``.
    """
    session.evaluate(
        """
        ({hostId, mode}) => {
          if (!window.__shadowRoots) window.__shadowRoots = {};
          const host = document.createElement('div');
          host.id = hostId;
          host.style.cssText = 'position:fixed;top:250px;left:250px;'
                             + 'z-index:100000;';
          const root = host.attachShadow({mode: mode});
          window.__shadowRoots[hostId] = root;
          document.body.appendChild(host);
        }
        """,
        {"hostId": host_id, "mode": mode},
    )


# ---------------------------------------------------------------------------
# Single-level open shadow root
# ---------------------------------------------------------------------------


def test_shadow_open_root_button_visible_via_arrow_chain(vscode_cdp):
    _mount_host(vscode_cdp, "sh-open-1")
    vscode_cdp.evaluate(
        """
        () => {
          const root = window.__shadowRoots['sh-open-1'];
          const b = document.createElement('button');
          b.id = 'inner-btn';
          b.textContent = 'inner';
          b.style.cssText = 'width:80px;height:30px;';
          window.__innerClicks = 0;
          b.addEventListener('click', () => { window.__innerClicks++; });
          root.appendChild(b);
        }
        """
    )
    assert vscode_cdp.locator("#sh-open-1 >> #inner-btn").is_visible()
    vscode_cdp.locator("#sh-open-1 >> #inner-btn").click()
    assert vscode_cdp.evaluate("() => window.__innerClicks") == 1


def test_shadow_open_root_text_read_via_plain_css(vscode_cdp):
    """Plain CSS descendants auto-pierce open shadow roots."""
    _mount_host(vscode_cdp, "sh-open-2")
    vscode_cdp.evaluate(
        """
        () => {
          const root = window.__shadowRoots['sh-open-2'];
          const s = document.createElement('span');
          s.id = 'inner-label';
          s.textContent = 'inside shadow';
          root.appendChild(s);
        }
        """
    )
    assert vscode_cdp.locator("#sh-open-2 #inner-label").text() == "inside shadow"


# ---------------------------------------------------------------------------
# Nested (3-level) open shadow roots
# ---------------------------------------------------------------------------


def test_shadow_three_level_nested_open_roots(vscode_cdp):
    """Root → root → root, all open, reachable through chained ``>>``."""
    vscode_cdp.evaluate(
        """
        () => {
          const l1 = document.createElement('div');
          l1.id = 'lvl-1';
          l1.style.cssText = 'position:fixed;top:400px;left:250px;z-index:100000;';
          const r1 = l1.attachShadow({mode: 'open'});

          const l2 = document.createElement('div');
          l2.id = 'lvl-2';
          const r2 = l2.attachShadow({mode: 'open'});
          r1.appendChild(l2);

          const l3 = document.createElement('div');
          l3.id = 'lvl-3';
          const r3 = l3.attachShadow({mode: 'open'});
          r2.appendChild(l3);

          const target = document.createElement('button');
          target.id = 'deep-btn';
          target.textContent = 'deep';
          target.style.cssText = 'width:80px;height:30px;';
          window.__deepClicks = 0;
          target.addEventListener('click', () => { window.__deepClicks++; });
          r3.appendChild(target);

          document.body.appendChild(l1);
        }
        """
    )
    # 3-hop >> chain reaches the button.
    loc = vscode_cdp.locator("#lvl-1 >> #lvl-2 >> #lvl-3 >> #deep-btn")
    assert loc.is_visible()
    loc.click()
    assert vscode_cdp.evaluate("() => window.__deepClicks") == 1


def test_shadow_three_level_reached_by_plain_css(vscode_cdp):
    """Even without ``>>``, plain descendant selectors auto-pierce."""
    vscode_cdp.evaluate(
        """
        () => {
          const l1 = document.createElement('div');
          l1.id = 'plain-1';
          l1.style.cssText = 'position:fixed;top:450px;left:250px;z-index:100000;';
          const r1 = l1.attachShadow({mode: 'open'});

          const l2 = document.createElement('div');
          l2.className = 'plain-mid';
          const r2 = l2.attachShadow({mode: 'open'});
          r1.appendChild(l2);

          const inner = document.createElement('span');
          inner.className = 'plain-inner';
          inner.textContent = 'plain hop';
          r2.appendChild(inner);

          document.body.appendChild(l1);
        }
        """
    )
    assert vscode_cdp.locator("#plain-1 .plain-mid .plain-inner").text() == "plain hop"


# ---------------------------------------------------------------------------
# Slotted content
# ---------------------------------------------------------------------------


def test_shadow_slot_projected_light_dom(vscode_cdp):
    """Light-DOM children projected into a slot must remain reachable."""
    vscode_cdp.evaluate(
        """
        () => {
          const host = document.createElement('my-container');
          host.id = 'slot-host';
          host.style.cssText = 'position:fixed;top:500px;left:250px;z-index:100000;';
          const root = host.attachShadow({mode: 'open'});
          const wrap = document.createElement('div');
          wrap.className = 'wrap';
          const slot = document.createElement('slot');
          slot.name = 'body';
          wrap.appendChild(slot);
          root.appendChild(wrap);

          const light = document.createElement('span');
          light.setAttribute('slot', 'body');
          light.id = 'slotted-content';
          light.textContent = 'slotted!';
          host.appendChild(light);

          document.body.appendChild(host);
        }
        """
    )
    # Slotted content stays in the light DOM, so a plain CSS selector
    # (no piercing needed) locates it.
    assert vscode_cdp.locator("#slot-host #slotted-content").text() == "slotted!"


# ---------------------------------------------------------------------------
# Closed shadow root — MUST NOT succeed via CDP
# ---------------------------------------------------------------------------


def test_shadow_closed_root_is_not_reachable(vscode_cdp):
    """Closed shadow roots are opaque — CDP piercing must NOT reach in.

    Documents the current Chromium limitation. If Chromium ever changes and closed
    roots become reachable, this test flips to FAIL and forces a docs
    update.
    """
    vscode_cdp.evaluate(
        """
        () => {
          const host = document.createElement('div');
          host.id = 'closed-host';
          host.style.cssText = 'position:fixed;top:550px;left:250px;z-index:100000;';
          const root = host.attachShadow({mode: 'closed'});
          const b = document.createElement('button');
          b.id = 'unreachable';
          b.textContent = 'no click';
          b.style.cssText = 'width:80px;height:30px;';
          root.appendChild(b);
          document.body.appendChild(host);
        }
        """
    )
    # The host itself is visible (light DOM).
    assert vscode_cdp.locator("#closed-host").is_visible()
    # But the inner button is NOT reachable via either syntax.
    assert vscode_cdp.locator("#closed-host >> #unreachable").is_visible() is False
    with pytest.raises(ElementNotFoundError):
        vscode_cdp.locator("#closed-host >> #unreachable").click(timeout=1.5)


# ---------------------------------------------------------------------------
# Style encapsulation confirmation
# ---------------------------------------------------------------------------


def test_shadow_style_encapsulation_preserved(vscode_cdp):
    """Styles inside a shadow root do not leak to light DOM (sanity for our fixture)."""
    vscode_cdp.evaluate(
        """
        () => {
          const light = document.createElement('p');
          light.id = 'light-para';
          light.textContent = 'light';
          light.style.cssText = 'position:fixed;top:600px;left:250px;z-index:100000;';
          document.body.appendChild(light);

          const host = document.createElement('div');
          host.id = 'style-host';
          host.style.cssText = 'position:fixed;top:640px;left:250px;z-index:100000;';
          const root = host.attachShadow({mode: 'open'});
          const style = document.createElement('style');
          style.textContent = 'p { color: rgb(255, 0, 0); }';
          root.appendChild(style);
          const p = document.createElement('p');
          p.id = 'shadow-para';
          p.textContent = 'shadow';
          root.appendChild(p);
          document.body.appendChild(host);
        }
        """
    )
    # The light-DOM <p> does NOT inherit shadow root's red color.
    light_color = vscode_cdp.evaluate(
        "() => getComputedStyle(document.getElementById('light-para')).color"
    )
    assert light_color != "rgb(255, 0, 0)"
    shadow_color = vscode_cdp.evaluate(
        """
        () => {
          const p = document.getElementById('style-host').shadowRoot
                    .getElementById('shadow-para');
          return getComputedStyle(p).color;
        }
        """
    )
    assert shadow_color == "rgb(255, 0, 0)"


# ---------------------------------------------------------------------------
# Dynamic shadow attach
# ---------------------------------------------------------------------------


def test_shadow_root_attached_after_wait(vscode_cdp):
    """A shadow root created after page load is still reachable via >> chain."""
    vscode_cdp.evaluate(
        """
        () => {
          const host = document.createElement('div');
          host.id = 'late-host';
          host.style.cssText = 'position:fixed;top:700px;left:250px;z-index:100000;';
          document.body.appendChild(host);

          setTimeout(() => {
            const root = host.attachShadow({mode: 'open'});
            const s = document.createElement('span');
            s.id = 'late-target';
            s.textContent = 'late arrival';
            root.appendChild(s);
          }, 300);
        }
        """
    )
    loc = vscode_cdp.locator("#late-host >> #late-target")
    loc.wait_for(state="attached", timeout=5)
    assert loc.text() == "late arrival"


# ---------------------------------------------------------------------------
# Type_text through shadow root
# ---------------------------------------------------------------------------


def test_shadow_input_type_text_via_pierce(vscode_cdp):
    """type_text() must reach an <input> nested inside an open shadow root."""
    vscode_cdp.evaluate(
        """
        () => {
          const host = document.createElement('div');
          host.id = 'shadow-input-host';
          host.style.cssText = 'position:fixed;top:760px;left:250px;z-index:100000;';
          const root = host.attachShadow({mode: 'open'});
          const inp = document.createElement('input');
          inp.id = 'shadow-input';
          inp.type = 'text';
          root.appendChild(inp);
          document.body.appendChild(host);
        }
        """
    )
    vscode_cdp.locator("#shadow-input-host >> #shadow-input").type_text("piercing text")
    value = vscode_cdp.evaluate(
        """
        () => document.getElementById('shadow-input-host').shadowRoot
                .getElementById('shadow-input').value
        """
    )
    assert value == "piercing text"


# ---------------------------------------------------------------------------
# Custom element with shadow root
# ---------------------------------------------------------------------------


def test_shadow_custom_element_registered_and_pierced(vscode_cdp):
    """A user-registered custom element with a shadow root — real-world pattern."""
    vscode_cdp.evaluate(
        """
        () => {
          if (!customElements.get('dolphin-card')) {
            class DolphinCard extends HTMLElement {
              constructor() {
                super();
                const r = this.attachShadow({mode: 'open'});
                const btn = document.createElement('button');
                btn.className = 'card-action';
                btn.textContent = this.getAttribute('label') || 'go';
                btn.style.cssText = 'padding:4px 12px;';
                window.__cardClicks = 0;
                btn.addEventListener('click', () => { window.__cardClicks++; });
                r.appendChild(btn);
              }
            }
            customElements.define('dolphin-card', DolphinCard);
          }
          const card = document.createElement('dolphin-card');
          card.id = 'card-one';
          card.setAttribute('label', 'go');
          card.style.cssText = 'position:fixed;top:100px;left:600px;z-index:100000;';
          document.body.appendChild(card);
        }
        """
    )
    btn = vscode_cdp.locator("#card-one >> .card-action")
    assert btn.text() == "go"
    btn.click()
    assert vscode_cdp.evaluate("() => window.__cardClicks") == 1
