"""Form-filling workflows over CDP — the real-world CDPLocator use case.

VS Code's CSP forbids ``innerHTML`` (Trusted Types with an allowlist that
does not include our origin), so every test builds the fixture DOM
imperatively via ``createElement`` + ``appendChild``. Coverage:

* single-field <input>, <input type=email>, <input type=number>
* <textarea> (multi-line)
* contenteditable divs
* <select>
* checkbox / radio group semantics
* keyboard-emitted typing (``type_text(clear=False)``)
* append vs. clear semantics
* focus / blur
* multi-step wizard flow
* long text and special characters
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
    """Build fixture DOM from *spec* — no innerHTML, TT-safe.

    Each spec item is a dict describing one element::

        {
          "tag": "input",
          "id":  "opt-id",
          "text": "textContent for non-inputs",
          "value": "initial value",
          "attrs": {"type": "text", "name": "field"},
          "children": [ ...more specs... ],
        }

    All keys are optional except ``tag``.
    """
    session.evaluate(
        """
        (spec) => {
          const wrap = document.createElement('div');
          wrap.id = 'form-fixture';
          wrap.style.cssText = 'position:fixed;top:200px;left:200px;'
                             + 'z-index:100000;background:white;padding:8px;'
                             + 'border:1px solid gray;';
          const build = (s) => {
            const el = document.createElement(s.tag);
            if (s.id) el.id = s.id;
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


def _input(id_: str, itype: str = "text", value: str | None = None) -> dict:
    """Shortcut for one <input>."""
    d: dict = {"tag": "input", "id": id_, "attrs": {"type": itype}}
    if value is not None:
        d["value"] = value
    return d


# ---------------------------------------------------------------------------
# Single-field inputs
# ---------------------------------------------------------------------------


def test_form_text_input_round_trip(vscode_cdp):
    _mount(vscode_cdp, [_input("in-text")])
    vscode_cdp.locator("#in-text").type_text("hello world")
    assert vscode_cdp.evaluate("() => document.getElementById('in-text').value") == "hello world"


def test_form_email_input_accepts_valid_value(vscode_cdp):
    _mount(vscode_cdp, [_input("in-email", "email")])
    vscode_cdp.locator("#in-email").type_text("user@example.com")
    assert (
        vscode_cdp.evaluate("() => document.getElementById('in-email').value") == "user@example.com"
    )


def test_form_number_input_accepts_numeric_string(vscode_cdp):
    _mount(vscode_cdp, [_input("in-num", "number")])
    vscode_cdp.locator("#in-num").type_text("42")
    assert vscode_cdp.evaluate("() => document.getElementById('in-num').value") == "42"


def test_form_password_input_round_trip(vscode_cdp):
    _mount(vscode_cdp, [_input("in-pwd", "password")])
    vscode_cdp.locator("#in-pwd").type_text("Sup3rS3cret!")
    assert vscode_cdp.evaluate("() => document.getElementById('in-pwd').value") == "Sup3rS3cret!"


# ---------------------------------------------------------------------------
# Textarea + contenteditable
# ---------------------------------------------------------------------------


def test_form_textarea_multiline(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "textarea", "id": "in-ta", "attrs": {"rows": "4", "cols": "30"}}])
    multi = "line one\nline two\nline three"
    vscode_cdp.locator("#in-ta").type_text(multi)
    assert vscode_cdp.evaluate("() => document.getElementById('in-ta').value") == multi


def test_form_contenteditable_div(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "in-ce",
                "attrs": {
                    "contenteditable": "true",
                    "style": "border:1px solid gray;padding:4px;min-width:150px;",
                },
            }
        ],
    )
    vscode_cdp.locator("#in-ce").type_text("editable content")
    assert (
        vscode_cdp.evaluate("() => document.getElementById('in-ce').innerText").strip()
        == "editable content"
    )


# ---------------------------------------------------------------------------
# Type semantics: clear vs. append
# ---------------------------------------------------------------------------


def test_type_text_default_clears_previous_value(vscode_cdp):
    _mount(vscode_cdp, [_input("in-clear", value="old-value")])
    vscode_cdp.locator("#in-clear").type_text("new-value")
    assert vscode_cdp.evaluate("() => document.getElementById('in-clear').value") == "new-value"


def test_type_text_clear_false_appends_via_keypress(vscode_cdp):
    _mount(vscode_cdp, [_input("in-append", value="AAA")])
    vscode_cdp.evaluate(
        """
        () => {
          const el = document.getElementById('in-append');
          el.focus();
          // Move caret to end.
          el.setSelectionRange(el.value.length, el.value.length);
        }
        """
    )
    vscode_cdp.locator("#in-append").type_text("BBB", clear=False)
    assert vscode_cdp.evaluate("() => document.getElementById('in-append').value") == "AAABBB"


# ---------------------------------------------------------------------------
# Selects, checkboxes, radios
# ---------------------------------------------------------------------------


def test_form_select_change_via_evaluate_and_read_back(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "select",
                "id": "in-sel",
                "children": [
                    {"tag": "option", "attrs": {"value": "a"}, "text": "Alpha"},
                    {"tag": "option", "attrs": {"value": "b"}, "text": "Bravo"},
                    {"tag": "option", "attrs": {"value": "c"}, "text": "Charlie"},
                ],
            }
        ],
    )
    vscode_cdp.evaluate(
        """
        () => {
          const s = document.getElementById('in-sel');
          s.value = 'b';
          s.dispatchEvent(new Event('change'));
        }
        """
    )
    assert vscode_cdp.evaluate("() => document.getElementById('in-sel').value") == "b"


def test_form_checkbox_click_toggles(vscode_cdp):
    _mount(vscode_cdp, [_input("in-chk", "checkbox")])
    assert vscode_cdp.evaluate("() => document.getElementById('in-chk').checked") is False
    vscode_cdp.locator("#in-chk").click()
    assert vscode_cdp.evaluate("() => document.getElementById('in-chk').checked") is True
    vscode_cdp.locator("#in-chk").click()
    assert vscode_cdp.evaluate("() => document.getElementById('in-chk').checked") is False


def test_form_radio_group_exclusive_selection(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {"tag": "input", "id": "r1", "attrs": {"type": "radio", "name": "grp", "value": "v1"}},
            {"tag": "input", "id": "r2", "attrs": {"type": "radio", "name": "grp", "value": "v2"}},
            {"tag": "input", "id": "r3", "attrs": {"type": "radio", "name": "grp", "value": "v3"}},
        ],
    )
    vscode_cdp.locator("#r2").click()
    checked = vscode_cdp.evaluate(
        """
        () => Array.from(document.querySelectorAll('input[name=grp]'))
          .filter(r => r.checked).map(r => r.id)
        """
    )
    assert checked == ["r2"]
    vscode_cdp.locator("#r3").click()
    checked = vscode_cdp.evaluate(
        """
        () => Array.from(document.querySelectorAll('input[name=grp]'))
          .filter(r => r.checked).map(r => r.id)
        """
    )
    assert checked == ["r3"]


# ---------------------------------------------------------------------------
# Focus / blur
# ---------------------------------------------------------------------------


def test_form_type_text_focuses_field(vscode_cdp):
    _mount(vscode_cdp, [_input("in-a"), _input("in-b")])
    vscode_cdp.locator("#in-b").type_text("focused")
    active_id = vscode_cdp.evaluate("() => document.activeElement.id")
    assert active_id == "in-b"


# ---------------------------------------------------------------------------
# Long text + special content
# ---------------------------------------------------------------------------


def test_form_type_text_long_string(vscode_cdp):
    """A long string round-trips without truncation."""
    payload = "x" * 5000
    _mount(vscode_cdp, [{"tag": "textarea", "id": "in-long"}])
    vscode_cdp.locator("#in-long").type_text(payload)
    length = vscode_cdp.evaluate("() => document.getElementById('in-long').value.length")
    assert length == 5000


def test_form_type_text_special_characters(vscode_cdp):
    """<, >, &, quotes must survive round trip (fill() escapes internally)."""
    payload = "<script>alert('xss')</script> & \"quoted\" 'apostrophe'"
    _mount(vscode_cdp, [_input("in-html")])
    vscode_cdp.locator("#in-html").type_text(payload)
    assert vscode_cdp.evaluate("() => document.getElementById('in-html').value") == payload


# ---------------------------------------------------------------------------
# Multi-step wizard
# ---------------------------------------------------------------------------


def test_form_wizard_three_step_workflow(vscode_cdp):
    """Full multi-step form: 3 steps, click Next twice, then Submit."""
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "wiz",
                "children": [
                    {
                        "tag": "div",
                        "id": "wiz-1",
                        "children": [
                            _input("wiz-name"),
                            {"tag": "button", "id": "wiz-next-1", "text": "Next"},
                        ],
                    },
                    {
                        "tag": "div",
                        "id": "wiz-2",
                        "attrs": {"style": "display:none;"},
                        "children": [
                            _input("wiz-age", "number"),
                            {"tag": "button", "id": "wiz-next-2", "text": "Next"},
                        ],
                    },
                    {
                        "tag": "div",
                        "id": "wiz-3",
                        "attrs": {"style": "display:none;"},
                        "children": [
                            _input("wiz-agree", "checkbox"),
                            {"tag": "button", "id": "wiz-submit", "text": "Submit"},
                        ],
                    },
                    {"tag": "output", "id": "wiz-out"},
                ],
            }
        ],
    )
    # Wire handlers via addEventListener (no inline <script>, TT-safe).
    vscode_cdp.evaluate(
        """
        () => {
          window.__wizardState = null;
          document.getElementById('wiz-next-1').addEventListener('click', () => {
            document.getElementById('wiz-1').style.display = 'none';
            document.getElementById('wiz-2').style.display = '';
          });
          document.getElementById('wiz-next-2').addEventListener('click', () => {
            document.getElementById('wiz-2').style.display = 'none';
            document.getElementById('wiz-3').style.display = '';
          });
          document.getElementById('wiz-submit').addEventListener('click', () => {
            window.__wizardState = {
              name: document.getElementById('wiz-name').value,
              age: document.getElementById('wiz-age').value,
              agree: document.getElementById('wiz-agree').checked,
            };
            document.getElementById('wiz-out').textContent = JSON.stringify(
              window.__wizardState
            );
          });
        }
        """
    )
    vscode_cdp.locator("#wiz-name").type_text("Ada")
    vscode_cdp.locator("#wiz-next-1").click()
    vscode_cdp.locator("#wiz-age").type_text("35")
    vscode_cdp.locator("#wiz-next-2").click()
    vscode_cdp.locator("#wiz-agree").click()
    vscode_cdp.locator("#wiz-submit").click()

    state = vscode_cdp.evaluate("() => window.__wizardState")
    assert state == {"name": "Ada", "age": "35", "agree": True}
    assert '"name":"Ada"' in vscode_cdp.locator("#wiz-out").text()


# ---------------------------------------------------------------------------
# Interaction with dynamic form state
# ---------------------------------------------------------------------------


def test_form_field_reappears_after_toggle(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            _input("toggler"),
            {"tag": "button", "id": "show-toggler", "text": "show"},
        ],
    )
    vscode_cdp.evaluate(
        """
        () => {
          document.getElementById('toggler').style.display = 'none';
          document.getElementById('show-toggler').addEventListener('click', () => {
            document.getElementById('toggler').style.display = '';
          });
        }
        """
    )
    loc = vscode_cdp.locator("#toggler")
    assert loc.is_visible() is False
    vscode_cdp.locator("#show-toggler").click()
    loc.wait_for(state="visible", timeout=3)
    loc.type_text("resolved after reveal")
    assert (
        vscode_cdp.evaluate("() => document.getElementById('toggler').value")
        == "resolved after reveal"
    )
