"""One focused test per method added when we lifted CDPLocator to parity
with :class:`dolphin_desktop.Locator`. Includes:

* Mouse: ``double_click``, ``right_click``, ``hover``, ``drag_to``
* Keyboard: ``focus``, ``press_key`` (element + page-level),
  ``set_text``, ``clear``
* Form semantics: ``check``, ``uncheck``, ``select_option``
* Layout: ``scroll_into_view``, ``bounding_box``
* State readers: ``value``, ``get_attribute``, ``is_enabled``,
  ``is_checked``, ``exists``, ``count``
* Multi-match: ``nth``, ``first``, ``last``, ``all``
* Screenshot: locator + session
* Navigation: session ``reload``, ``wait_for_load_state``
"""

from __future__ import annotations

import pytest

from tests.electron._cdp_env import VSCODE, has_playwright  # type: ignore[import-not-found]

skip_no_vscode = pytest.mark.skipif(VSCODE is None, reason="VS Code not installed")
skip_no_playwright = pytest.mark.skipif(
    not has_playwright(),
    reason="dolphin_desktop[cdp] extra not installed",
)

pytestmark = [skip_no_vscode, skip_no_playwright, pytest.mark.timeout(60)]


def _mount(session, spec: list[dict]) -> None:
    """DOM builder — imperative to satisfy VS Code's Trusted Types policy."""
    session.evaluate(
        """
        (spec) => {
          const wrap = document.createElement('div');
          wrap.id = 'completeness-fixture';
          wrap.style.cssText = 'position:fixed;top:150px;left:150px;'
                             + 'z-index:100000;background:white;padding:8px;'
                             + 'border:1px solid gray;';
          const build = (s) => {
            const el = document.createElement(s.tag);
            if (s.id) el.id = s.id;
            if (s.className) el.className = s.className;
            if (s.attrs) {
              for (const [k, v] of Object.entries(s.attrs)) {
                el.setAttribute(k, v);
              }
            }
            if (s.text !== undefined) el.textContent = s.text;
            if (s.value !== undefined) el.value = s.value;
            if (s.children) {
              for (const child of s.children) el.appendChild(build(child));
            }
            return el;
          };
          for (const item of spec) wrap.appendChild(build(item));
          document.body.appendChild(wrap);
        }
        """,
        spec,
    )


# ---------------------------------------------------------------------------
# Mouse — double_click / right_click / hover / drag_to
# ---------------------------------------------------------------------------


def test_double_click_fires_dblclick(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "dbl-tgt",
                "text": "dbl",
                "attrs": {"style": "width:120px;height:40px;background:#eef;"},
            }
        ],
    )
    vscode_cdp.evaluate(
        """
        () => {
          window.__dbl = 0;
          document.getElementById('dbl-tgt')
            .addEventListener('dblclick', () => { window.__dbl++; });
        }
        """
    )
    vscode_cdp.locator("#dbl-tgt").double_click()
    assert vscode_cdp.evaluate("() => window.__dbl") == 1


def test_right_click_fires_contextmenu(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "rc-tgt",
                "text": "rc",
                "attrs": {"style": "width:120px;height:40px;background:#eef;"},
            }
        ],
    )
    vscode_cdp.evaluate(
        """
        () => {
          window.__rc = 0;
          document.getElementById('rc-tgt')
            .addEventListener('contextmenu', e => {
              e.preventDefault();
              window.__rc++;
            });
        }
        """
    )
    vscode_cdp.locator("#rc-tgt").right_click()
    assert vscode_cdp.evaluate("() => window.__rc") == 1


def test_hover_fires_mouseover(vscode_cdp):
    """hover() dispatches a real mousemove; mouseover fires reliably."""
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "hv-tgt",
                "text": "hover me",
                "attrs": {"style": "width:120px;height:40px;background:#efe;"},
            }
        ],
    )
    vscode_cdp.evaluate(
        """
        () => {
          window.__hover = 0;
          document.getElementById('hv-tgt')
            .addEventListener('mouseover', () => { window.__hover++; });
          document.getElementById('hv-tgt')
            .addEventListener('pointerover', () => { window.__hover++; });
        }
        """
    )
    # Move mouse away from the target first so hover actually enters it.
    vscode_cdp.page.mouse.move(0, 0)
    vscode_cdp.locator("#hv-tgt").hover()
    # At least one of mouseover/pointerover fired.
    assert vscode_cdp.evaluate("() => window.__hover") >= 1


def test_drag_to_fires_drop(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "drag-src",
                "text": "src",
                "attrs": {"draggable": "true", "style": "width:80px;height:40px;background:#ffd;"},
            },
            {
                "tag": "div",
                "id": "drag-dst",
                "text": "dst",
                "attrs": {"style": "width:80px;height:40px;background:#dff;margin-top:8px;"},
            },
        ],
    )
    vscode_cdp.evaluate(
        """
        () => {
          window.__dropped = false;
          const dst = document.getElementById('drag-dst');
          dst.addEventListener('dragover', e => e.preventDefault());
          dst.addEventListener('drop', () => { window.__dropped = true; });
        }
        """
    )
    vscode_cdp.locator("#drag-src").drag_to(vscode_cdp.locator("#drag-dst"))
    assert vscode_cdp.evaluate("() => window.__dropped") is True


# ---------------------------------------------------------------------------
# Keyboard — focus / press_key / set_text / clear
# ---------------------------------------------------------------------------


def test_focus_sets_active_element(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "input", "id": "focus-tgt", "attrs": {"type": "text"}}])
    vscode_cdp.locator("#focus-tgt").focus()
    assert vscode_cdp.evaluate("() => document.activeElement.id") == "focus-tgt"


def test_press_key_on_locator_fires_keydown(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "input", "id": "key-tgt", "attrs": {"type": "text"}}])
    vscode_cdp.evaluate(
        """
        () => {
          window.__keys = [];
          document.getElementById('key-tgt')
            .addEventListener('keydown', e => { window.__keys.push(e.key); });
        }
        """
    )
    vscode_cdp.locator("#key-tgt").press_key("Enter")
    vscode_cdp.locator("#key-tgt").press_key("Escape")
    vscode_cdp.locator("#key-tgt").press_key("ArrowDown")
    keys = vscode_cdp.evaluate("() => window.__keys")
    assert keys == ["Enter", "Escape", "ArrowDown"]


def test_session_press_key_fires_document_keydown(vscode_cdp):
    """Session-level press_key hits the page keyboard (no locator required)."""
    vscode_cdp.evaluate(
        """
        () => {
          window.__docKeys = [];
          document.addEventListener('keydown', e => {
            window.__docKeys.push({key: e.key, ctrl: e.ctrlKey, shift: e.shiftKey});
          });
        }
        """
    )
    vscode_cdp.press_key("Control+Shift+X")
    vscode_cdp.press_key("Escape")
    keys = vscode_cdp.evaluate("() => window.__docKeys")
    # Playwright emits the modifier keydowns too, so filter to the final char.
    tail = [k for k in keys if k["key"] in ("X", "Escape")]
    assert len(tail) >= 2
    assert any(k["key"] == "X" and k["ctrl"] and k["shift"] for k in tail)
    assert any(k["key"] == "Escape" for k in tail)


def test_set_text_alias_replaces_value(vscode_cdp):
    _mount(
        vscode_cdp, [{"tag": "input", "id": "st-tgt", "attrs": {"type": "text"}, "value": "old"}]
    )
    vscode_cdp.locator("#st-tgt").set_text("new")
    assert vscode_cdp.locator("#st-tgt").value() == "new"


def test_clear_empties_input(vscode_cdp):
    _mount(
        vscode_cdp,
        [{"tag": "input", "id": "clr-tgt", "attrs": {"type": "text"}, "value": "prefilled"}],
    )
    vscode_cdp.locator("#clr-tgt").clear()
    assert vscode_cdp.locator("#clr-tgt").value() == ""


# ---------------------------------------------------------------------------
# Form semantics — check / uncheck / select_option
# ---------------------------------------------------------------------------


def test_check_uncheck_are_idempotent(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "input", "id": "chk-tgt", "attrs": {"type": "checkbox"}}])
    loc = vscode_cdp.locator("#chk-tgt")
    assert loc.is_checked() is False
    loc.check()
    assert loc.is_checked() is True
    loc.check()  # idempotent
    assert loc.is_checked() is True
    loc.uncheck()
    assert loc.is_checked() is False
    loc.uncheck()  # idempotent
    assert loc.is_checked() is False


def test_select_option_by_value(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "select",
                "id": "sel-tgt",
                "children": [
                    {"tag": "option", "attrs": {"value": "a"}, "text": "Alpha"},
                    {"tag": "option", "attrs": {"value": "b"}, "text": "Bravo"},
                    {"tag": "option", "attrs": {"value": "c"}, "text": "Charlie"},
                ],
            }
        ],
    )
    picked = vscode_cdp.locator("#sel-tgt").select_option("c")
    assert picked == ["c"]
    assert vscode_cdp.locator("#sel-tgt").value() == "c"


def test_select_option_by_label(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "select",
                "id": "sel-lbl",
                "children": [
                    {"tag": "option", "attrs": {"value": "1"}, "text": "One"},
                    {"tag": "option", "attrs": {"value": "2"}, "text": "Two"},
                ],
            }
        ],
    )
    picked = vscode_cdp.locator("#sel-lbl").select_option(label="Two")
    assert picked == ["2"]


# ---------------------------------------------------------------------------
# Layout — scroll_into_view / bounding_box
# ---------------------------------------------------------------------------


def test_scroll_into_view_brings_element_into_viewport(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "scroll-parent",
                "attrs": {"style": "height:120px;overflow:auto;border:1px solid #333;width:200px;"},
                "children": [
                    {
                        "tag": "div",
                        "id": "scroll-spacer",
                        "attrs": {"style": "height:1200px;background:#fee;"},
                    },
                    {
                        "tag": "div",
                        "id": "scroll-target",
                        "text": "target",
                        "attrs": {"style": "background:#efe;padding:6px;"},
                    },
                ],
            }
        ],
    )
    before = vscode_cdp.evaluate("() => document.getElementById('scroll-parent').scrollTop")
    assert before == 0
    vscode_cdp.locator("#scroll-target").scroll_into_view()
    after = vscode_cdp.evaluate("() => document.getElementById('scroll-parent').scrollTop")
    assert after > 0


def test_bounding_box_returns_xywh(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "bb-tgt",
                "text": "b",
                "attrs": {"style": "width:100px;height:40px;"},
            }
        ],
    )
    box = vscode_cdp.locator("#bb-tgt").bounding_box()
    assert box is not None
    assert set(box.keys()) == {"x", "y", "width", "height"}
    assert box["width"] == 100
    assert box["height"] == 40


# ---------------------------------------------------------------------------
# State readers — value / get_attribute / is_enabled / is_checked / exists
# ---------------------------------------------------------------------------


def test_value_reads_input_value(vscode_cdp):
    _mount(
        vscode_cdp, [{"tag": "input", "id": "v-tgt", "attrs": {"type": "text"}, "value": "current"}]
    )
    assert vscode_cdp.locator("#v-tgt").value() == "current"


def test_get_attribute_reads_and_returns_none_for_missing(vscode_cdp):
    _mount(
        vscode_cdp,
        [{"tag": "div", "id": "attr-tgt", "attrs": {"data-role": "button", "aria-label": "hi"}}],
    )
    loc = vscode_cdp.locator("#attr-tgt")
    assert loc.get_attribute("data-role") == "button"
    assert loc.get_attribute("aria-label") == "hi"
    assert loc.get_attribute("data-missing") is None


def test_is_enabled_reports_disabled_attribute(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {"tag": "button", "id": "en-on", "text": "on"},
            {"tag": "button", "id": "en-off", "text": "off", "attrs": {"disabled": ""}},
        ],
    )
    assert vscode_cdp.locator("#en-on").is_enabled() is True
    assert vscode_cdp.locator("#en-off").is_enabled() is False


def test_exists_returns_true_for_present_false_for_missing(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "div", "id": "ex-yes", "text": "here"}])
    assert vscode_cdp.locator("#ex-yes").exists() is True
    assert vscode_cdp.locator("#ex-no-such-thing-xyz").exists() is False


def test_exists_waits_when_timeout_positive(vscode_cdp):
    """exists(timeout>0) blocks until the element appears."""
    vscode_cdp.evaluate(
        """
        () => {
          setTimeout(() => {
            const d = document.createElement('div');
            d.id = 'ex-delayed';
            d.textContent = 'here now';
            document.body.appendChild(d);
          }, 300);
        }
        """
    )
    assert vscode_cdp.locator("#ex-delayed").exists(timeout=3) is True


# ---------------------------------------------------------------------------
# Multi-match — count / nth / first / last / all
# ---------------------------------------------------------------------------


def test_count_returns_number_of_matches(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "ul",
                "id": "list",
                "children": [
                    {"tag": "li", "className": "row", "text": "one"},
                    {"tag": "li", "className": "row", "text": "two"},
                    {"tag": "li", "className": "row", "text": "three"},
                ],
            }
        ],
    )
    assert vscode_cdp.locator("#list .row").count() == 3


def test_nth_and_first_and_last_scope_correctly(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "ol",
                "id": "olist",
                "children": [
                    {"tag": "li", "className": "item", "text": "alpha"},
                    {"tag": "li", "className": "item", "text": "bravo"},
                    {"tag": "li", "className": "item", "text": "charlie"},
                ],
            }
        ],
    )
    loc = vscode_cdp.locator("#olist .item")
    assert loc.first().text() == "alpha"
    assert loc.nth(1).text() == "bravo"
    assert loc.last().text() == "charlie"


def test_all_returns_one_locator_per_match(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "all-parent",
                "children": [
                    {"tag": "span", "className": "chip", "text": "aaa"},
                    {"tag": "span", "className": "chip", "text": "bbb"},
                    {"tag": "span", "className": "chip", "text": "ccc"},
                ],
            }
        ],
    )
    locs = vscode_cdp.locator("#all-parent .chip").all()
    assert len(locs) == 3
    assert [loc.text() for loc in locs] == ["aaa", "bbb", "ccc"]


# ---------------------------------------------------------------------------
# Screenshot — locator + session
# ---------------------------------------------------------------------------


def test_locator_screenshot_returns_pil_image(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "shot-tgt",
                "text": "S",
                "attrs": {"style": "width:60px;height:60px;background:red;"},
            }
        ],
    )
    img = vscode_cdp.locator("#shot-tgt").screenshot()
    # PIL image with non-zero dimensions.
    assert img.width > 0
    assert img.height > 0


def test_session_screenshot_returns_full_page_image(vscode_cdp):
    img = vscode_cdp.screenshot()
    assert img.width > 200
    assert img.height > 200


# ---------------------------------------------------------------------------
# Navigation — reload / wait_for_load_state
# ---------------------------------------------------------------------------


def test_session_reload_reruns_the_page(vscode_cdp):
    """VS Code renderer survives a reload — verify the page comes back alive."""
    before_url = vscode_cdp.page.url
    vscode_cdp.reload(timeout=30)
    vscode_cdp.wait_for_load_state("load", timeout=30)
    # Wait for workbench to reappear (VS Code fully re-inits its DOM).
    vscode_cdp.locator(".monaco-workbench").wait_for(state="visible", timeout=30)
    assert vscode_cdp.page.url == before_url


def test_wait_for_load_state_returns_self(vscode_cdp):
    """wait_for_load_state on an already-loaded page returns immediately."""
    result = vscode_cdp.wait_for_load_state("load", timeout=5)
    assert result is vscode_cdp
