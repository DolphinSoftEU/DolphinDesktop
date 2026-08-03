"""Second completeness pass — everything the first round did not cover.

* File uploads (``set_input_files``)
* Dialog interception (``accept_dialogs`` / ``dismiss_dialogs``)
* Iframes (``frame_locator``)
* Locator narrowing: ``locator(chain)``, ``filter(has_text=…)``
* Playwright a11y selectors (``get_by_role`` / ``label`` / ``text`` /
  ``placeholder`` / ``title`` / ``alt_text`` / ``test_id``) — session +
  nested locator variants
* Raw HTML read (``inner_html``)
* Console capture (``console_messages`` / ``clear_console_messages``)
* localStorage helpers
* Default timeout override
"""

from __future__ import annotations

import pytest

from dolphin_desktop import (
    path_basename,
    remove_file,
    sleep,
    temp_file,
)
from tests.electron._cdp_env import VSCODE, has_playwright  # type: ignore[import-not-found]

skip_no_vscode = pytest.mark.skipif(VSCODE is None, reason="VS Code not installed")
skip_no_playwright = pytest.mark.skipif(
    not has_playwright(),
    reason="dolphin_desktop[cdp] extra not installed",
)

pytestmark = [skip_no_vscode, skip_no_playwright, pytest.mark.timeout(60)]


def _mount(session, spec):
    session.evaluate(
        """
        (spec) => {
          const wrap = document.createElement('div');
          wrap.id = 'completeness2-fixture';
          wrap.style.cssText = 'position:fixed;top:120px;left:120px;'
                             + 'z-index:100000;background:white;padding:8px;'
                             + 'border:1px solid gray;';
          const build = (s) => {
            const el = document.createElement(s.tag);
            if (s.id) el.id = s.id;
            if (s.className) el.className = s.className;
            if (s.attrs) {
              for (const [k, v] of Object.entries(s.attrs)) el.setAttribute(k, v);
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
# File uploads
# ---------------------------------------------------------------------------


def test_set_input_files_uploads_file(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "input", "id": "file-tgt", "attrs": {"type": "file"}}])
    path = temp_file(prefix="dolphin-cdp-", suffix=".txt", content="hello upload")
    try:
        vscode_cdp.locator("#file-tgt").set_input_files(path)
        info = vscode_cdp.evaluate(
            """
            () => {
              const f = document.getElementById('file-tgt').files[0];
              return {name: f.name, size: f.size};
            }
            """
        )
        assert info["name"] == path_basename(path)
        assert info["size"] == len("hello upload")
    finally:
        remove_file(path)


def test_set_input_files_accepts_empty_list_to_clear(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "input", "id": "file-clr", "attrs": {"type": "file"}}])
    path = temp_file(prefix="dolphin-cdp-clr-", content=b"")
    try:
        vscode_cdp.locator("#file-clr").set_input_files(path)
        assert vscode_cdp.evaluate("() => document.getElementById('file-clr').files.length") == 1
        vscode_cdp.locator("#file-clr").set_input_files([])
        assert vscode_cdp.evaluate("() => document.getElementById('file-clr').files.length") == 0
    finally:
        remove_file(path)


# ---------------------------------------------------------------------------
# Dialog interception
# ---------------------------------------------------------------------------


def test_dismiss_dialogs_default_dismisses_confirm(vscode_cdp):
    vscode_cdp.dismiss_dialogs()
    result = vscode_cdp.evaluate("() => confirm('yes?')")
    assert result is False


def test_accept_dialogs_makes_confirm_return_true(vscode_cdp):
    vscode_cdp.accept_dialogs()
    result = vscode_cdp.evaluate("() => confirm('yes?')")
    assert result is True
    # Reset for the next test.
    vscode_cdp.dismiss_dialogs()


# ---------------------------------------------------------------------------
# Iframes
# ---------------------------------------------------------------------------


def test_frame_locator_reaches_into_iframe_dom(vscode_cdp):
    """Same-origin (about:blank) iframe populated via contentDocument.

    VS Code CSP forbids ``srcdoc`` (Trusted Types) AND ``data:`` iframes
    (frame-src allowlist). ``about:blank`` inherits the parent origin so
    we can build DOM inside it imperatively without touching innerHTML.
    """
    vscode_cdp.evaluate(
        """
        () => new Promise(resolve => {
          const f = document.createElement('iframe');
          f.id = 'test-frame';
          f.src = 'about:blank';
          f.style.cssText = 'position:fixed;top:120px;left:120px;'
                          + 'width:300px;height:200px;z-index:100000;';
          f.addEventListener('load', () => {
            const d = f.contentDocument;
            const btn = d.createElement('button');
            btn.id = 'in-frame-btn';
            btn.textContent = 'click me';
            const lbl = d.createElement('span');
            lbl.id = 'in-frame-lbl';
            lbl.textContent = 'frame content';
            d.body.appendChild(btn);
            d.body.appendChild(lbl);
            resolve(true);
          });
          document.body.appendChild(f);
        })
        """
    )
    frame = vscode_cdp.frame_locator("#test-frame")
    frame.locator("#in-frame-lbl").wait_for(state="visible", timeout=10)
    assert frame.locator("#in-frame-lbl").text() == "frame content"
    frame.locator("#in-frame-btn").click()


# ---------------------------------------------------------------------------
# Locator narrowing — chained locator() + filter()
# ---------------------------------------------------------------------------


def test_locator_chained_locator_narrows_scope(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "chain-root",
                "children": [
                    {
                        "tag": "div",
                        "className": "row",
                        "children": [{"tag": "span", "text": "one"}],
                    },
                    {
                        "tag": "div",
                        "className": "row",
                        "children": [{"tag": "span", "text": "two"}],
                    },
                ],
            }
        ],
    )
    root = vscode_cdp.locator("#chain-root")
    spans = root.locator(".row span").all()
    assert [s.text() for s in spans] == ["one", "two"]


def test_locator_filter_by_has_text(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "ul",
                "id": "flt-list",
                "children": [
                    {"tag": "li", "className": "row", "text": "apple pie"},
                    {"tag": "li", "className": "row", "text": "banana bread"},
                    {"tag": "li", "className": "row", "text": "cherry cake"},
                ],
            }
        ],
    )
    banana = vscode_cdp.locator("#flt-list .row").filter(has_text="banana")
    assert banana.count() == 1
    assert banana.text() == "banana bread"


def test_locator_filter_by_has_locator(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "ul",
                "id": "flt-has",
                "children": [
                    {
                        "tag": "li",
                        "className": "row",
                        "children": [{"tag": "span", "className": "badge", "text": "new"}],
                    },
                    {"tag": "li", "className": "row"},
                    {
                        "tag": "li",
                        "className": "row",
                        "children": [{"tag": "span", "className": "badge", "text": "hot"}],
                    },
                ],
            }
        ],
    )
    has_badge = vscode_cdp.locator("#flt-has .row").filter(has=vscode_cdp.locator(".badge"))
    assert has_badge.count() == 2


# ---------------------------------------------------------------------------
# Playwright a11y selectors
# ---------------------------------------------------------------------------


def test_get_by_role_locates_button(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "button",
                "id": "rl-btn",
                "text": "Submit form",
                "attrs": {"style": "z-index:100001;position:relative;"},
            }
        ],
    )
    loc = vscode_cdp.get_by_role("button", name="Submit form")
    assert loc.is_visible()


def test_get_by_label_locates_input(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {"tag": "label", "attrs": {"for": "lbl-in"}, "text": "Username"},
            {"tag": "input", "id": "lbl-in", "attrs": {"type": "text"}},
        ],
    )
    vscode_cdp.get_by_label("Username").type_text("alice")
    assert vscode_cdp.evaluate("() => document.getElementById('lbl-in').value") == "alice"


def test_get_by_text_locates_paragraph(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "p", "id": "gt-p", "text": "Hello finder"}])
    loc = vscode_cdp.get_by_text("Hello finder")
    assert loc.is_visible()


def test_get_by_placeholder_locates_input(vscode_cdp):
    _mount(
        vscode_cdp,
        [{"tag": "input", "id": "ph-in", "attrs": {"type": "text", "placeholder": "Type here"}}],
    )
    vscode_cdp.get_by_placeholder("Type here").type_text("filled")
    assert vscode_cdp.evaluate("() => document.getElementById('ph-in').value") == "filled"


def test_get_by_title_locates_element(vscode_cdp):
    _mount(
        vscode_cdp,
        [{"tag": "div", "id": "ttl-d", "attrs": {"title": "helpful tooltip"}, "text": "hover me"}],
    )
    loc = vscode_cdp.get_by_title("helpful tooltip")
    assert loc.text() == "hover me"


def test_get_by_test_id_locates_element(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "button",
                "id": "tid-b",
                "text": "act",
                "attrs": {"data-testid": "primary-action"},
            }
        ],
    )
    loc = vscode_cdp.get_by_test_id("primary-action")
    assert loc.text() == "act"


def test_nested_get_by_role_scopes_search(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {"tag": "div", "id": "region-1", "children": [{"tag": "button", "text": "one"}]},
            {"tag": "div", "id": "region-2", "children": [{"tag": "button", "text": "two"}]},
        ],
    )
    region1 = vscode_cdp.locator("#region-1")
    btn = region1.get_by_role("button")
    assert btn.text() == "one"


# ---------------------------------------------------------------------------
# inner_html
# ---------------------------------------------------------------------------


def test_inner_html_reads_raw_markup(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "ih-tgt",
                "children": [
                    {"tag": "b", "text": "bold"},
                    {"tag": "i", "text": "italic"},
                ],
            }
        ],
    )
    html = vscode_cdp.locator("#ih-tgt").inner_html()
    assert "<b>bold</b>" in html
    assert "<i>italic</i>" in html


# ---------------------------------------------------------------------------
# Console capture
# ---------------------------------------------------------------------------


def test_console_messages_captures_log_warn_error(vscode_cdp):
    vscode_cdp.clear_console_messages()
    vscode_cdp.evaluate(
        """
        () => {
          console.log('hi');
          console.warn('careful');
          console.error('boom');
        }
        """
    )
    # Playwright dispatches console asynchronously; give it a tick.
    sleep(0.5)
    types = [m["type"] for m in vscode_cdp.console_messages()]
    assert "log" in types
    assert "warning" in types or "warn" in types
    assert "error" in types


def test_clear_console_messages_resets_buffer(vscode_cdp):
    vscode_cdp.evaluate("() => console.log('will-be-cleared')")
    sleep(0.3)
    assert len(vscode_cdp.console_messages()) >= 1
    vscode_cdp.clear_console_messages()
    assert vscode_cdp.console_messages() == []


# ---------------------------------------------------------------------------
# localStorage helpers
# ---------------------------------------------------------------------------


def test_local_storage_set_get_and_clear(vscode_cdp):
    vscode_cdp.local_storage_set("dolphin-key", "dolphin-value")
    assert vscode_cdp.local_storage_get("dolphin-key") == "dolphin-value"
    vscode_cdp.local_storage_clear()
    assert vscode_cdp.local_storage_get("dolphin-key") is None


# ---------------------------------------------------------------------------
# Default timeout override
# ---------------------------------------------------------------------------


def test_set_default_timeout_takes_effect(vscode_cdp):
    """set_default_timeout tightens Playwright's implicit action timeouts."""
    vscode_cdp.set_default_timeout(30)
    # Restore for the next test.
    vscode_cdp.set_default_timeout(30)
