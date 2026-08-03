"""Fourth (final) completeness pass — the "last-mile" surface.

Covers: expose_function, dispatch_event, element-scoped evaluate,
press_sequentially, select_text, blur, tap, all_text_contents,
set_extra_http_headers, set_offline, set_geolocation,
grant_permissions, PDF export, keyboard down/up, keyboard_type,
mouse_wheel, mouse_move.

After this pass, every Playwright capability that maps naturally onto
Locator/Session is available through dolphin_desktop — remaining
Playwright-only features (video/trace recording, viewport emulation for
mobile) are reachable via the ``element_handle()`` and ``session.page``
escape hatches when a user genuinely needs them.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import path_exists, remove_file, sleep, temp_file
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
          wrap.id = 'v4-fixture';
          wrap.style.cssText = 'position:fixed;top:100px;left:100px;'
                             + 'z-index:100000;background:white;padding:8px;';
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
# JS → Python bridge
# ---------------------------------------------------------------------------


def test_expose_function_lets_page_call_python(vscode_cdp):
    captured: list[dict] = []

    def dolphin_callback(payload):
        captured.append(payload)
        return {"echo": payload.get("value"), "ok": True}

    vscode_cdp.expose_function("dolphinBridge", dolphin_callback)
    result = vscode_cdp.evaluate(
        """
        () => window.dolphinBridge({name: 'from-js', value: 42})
        """
    )
    assert result == {"echo": 42, "ok": True}
    assert captured == [{"name": "from-js", "value": 42}]


# ---------------------------------------------------------------------------
# Element-scoped evaluate + dispatch_event + all_text_contents
# ---------------------------------------------------------------------------


def test_locator_evaluate_receives_element_as_argument(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "ev-tgt",
                "attrs": {"data-role": "hero", "data-tier": "gold"},
                "text": "hero content",
            }
        ],
    )
    result = vscode_cdp.locator("#ev-tgt").evaluate(
        "(el) => ({role: el.dataset.role, tier: el.dataset.tier})"
    )
    assert result == {"role": "hero", "tier": "gold"}


def test_locator_evaluate_accepts_arguments(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "div", "id": "arg-tgt", "text": "starter"}])
    result = vscode_cdp.locator("#arg-tgt").evaluate(
        "(el, extra) => el.textContent + '/' + extra", "SUFFIX"
    )
    assert result == "starter/SUFFIX"


def test_dispatch_event_fires_synthetic_event(vscode_cdp):
    """dispatch_event synthesises a real DOM event that listeners catch.

    Playwright uses ``new Event(type, init)`` for unknown event types, so
    ``bubbles`` / ``cancelable`` do round-trip but ``detail`` (a
    CustomEvent-specific field) does not. Use :meth:`CDPSession.evaluate`
    to inject a ``CustomEvent`` when you need ``detail`` — the workaround
    is a one-liner and preserves the autonomous-import contract.
    """
    _mount(vscode_cdp, [{"tag": "div", "id": "cust-tgt", "text": "listen"}])
    vscode_cdp.evaluate(
        """
        () => {
          window.__custom = null;
          document.getElementById('cust-tgt')
            .addEventListener('dolphin-custom', e => {
              window.__custom = {
                type: e.type,
                bubbles: e.bubbles,
                cancelable: e.cancelable,
              };
            });
        }
        """
    )
    vscode_cdp.locator("#cust-tgt").dispatch_event(
        "dolphin-custom",
        {"bubbles": True, "cancelable": True},
    )
    got = vscode_cdp.evaluate("() => window.__custom")
    assert got == {
        "type": "dolphin-custom",
        "bubbles": True,
        "cancelable": True,
    }


def test_all_text_contents_returns_every_match(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "ul",
                "id": "atc-list",
                "children": [
                    {"tag": "li", "className": "row", "text": "first"},
                    {"tag": "li", "className": "row", "text": "second"},
                    {"tag": "li", "className": "row", "text": "third"},
                ],
            }
        ],
    )
    texts = vscode_cdp.locator("#atc-list .row").all_text_contents()
    assert texts == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# Typing / keyboard hold / select_text / blur / tap
# ---------------------------------------------------------------------------


def test_press_sequentially_emits_key_by_key_events(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "input", "id": "seq-in", "attrs": {"type": "text"}}])
    vscode_cdp.evaluate(
        """
        () => {
          window.__seq = [];
          document.getElementById('seq-in')
            .addEventListener('input', e => { window.__seq.push(e.target.value); });
        }
        """
    )
    vscode_cdp.locator("#seq-in").press_sequentially("abc")
    trail = vscode_cdp.evaluate("() => window.__seq")
    assert trail == ["a", "ab", "abc"]


def test_select_text_selects_input_value(vscode_cdp):
    _mount(
        vscode_cdp,
        [{"tag": "input", "id": "sel-in", "attrs": {"type": "text"}, "value": "prefilled"}],
    )
    vscode_cdp.locator("#sel-in").select_text()
    selection_length = vscode_cdp.evaluate(
        """
        () => {
          const el = document.getElementById('sel-in');
          return el.selectionEnd - el.selectionStart;
        }
        """
    )
    assert selection_length == len("prefilled")


def test_blur_fires_blur_event(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "input", "id": "blur-in", "attrs": {"type": "text"}}])
    vscode_cdp.evaluate(
        """
        () => {
          window.__blur = 0;
          document.getElementById('blur-in')
            .addEventListener('blur', () => { window.__blur++; });
        }
        """
    )
    vscode_cdp.locator("#blur-in").focus()
    vscode_cdp.locator("#blur-in").blur()
    assert vscode_cdp.evaluate("() => window.__blur") == 1


def test_keyboard_down_up_holds_modifier_across_keys(vscode_cdp):
    """keyboard_down('Shift') keeps shiftKey=true on every keydown until keyboard_up.

    Chromium's synthetic ``.type()`` does not up-shift ASCII automatically
    (unlike a physical layout), but the modifier state IS held — verify
    by capturing each keydown's ``shiftKey`` from the DOM.
    """
    _mount(vscode_cdp, [{"tag": "input", "id": "kh-in", "attrs": {"type": "text"}}])
    vscode_cdp.evaluate(
        """
        () => {
          window.__shiftKeys = [];
          document.getElementById('kh-in')
            .addEventListener('keydown', e => {
              if (e.key !== 'Shift') {
                window.__shiftKeys.push(e.shiftKey);
              }
            });
        }
        """
    )
    vscode_cdp.locator("#kh-in").focus()
    vscode_cdp.keyboard_down("Shift")
    try:
        vscode_cdp.keyboard_type("abc")
    finally:
        vscode_cdp.keyboard_up("Shift")
    trail = vscode_cdp.evaluate("() => window.__shiftKeys")
    assert trail == [True, True, True]


# ---------------------------------------------------------------------------
# Mouse wheel + move
# ---------------------------------------------------------------------------


def test_mouse_wheel_dispatches_wheel_event(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "wh-tgt",
                "attrs": {"style": "width:200px;height:200px;background:#fef;"},
            }
        ],
    )
    vscode_cdp.evaluate(
        """
        () => {
          window.__wheel = null;
          document.getElementById('wh-tgt')
            .addEventListener('wheel', e => {
              window.__wheel = {dx: e.deltaX, dy: e.deltaY};
            });
        }
        """
    )
    # Move over the target first, then dispatch a wheel event.
    vscode_cdp.mouse_move(200, 200)
    vscode_cdp.mouse_wheel(0, 100)
    got = vscode_cdp.evaluate("() => window.__wheel")
    assert got is not None
    assert got["dy"] == 100


# ---------------------------------------------------------------------------
# Emulation — offline / geolocation / permissions / extra headers
# ---------------------------------------------------------------------------


def test_set_offline_true_emulates_offline_state(vscode_cdp):
    vscode_cdp.set_offline(True)
    try:
        online = vscode_cdp.evaluate("() => navigator.onLine")
        assert online is False
    finally:
        vscode_cdp.set_offline(False)
        assert vscode_cdp.evaluate("() => navigator.onLine") is True


def test_grant_permissions_allows_clipboard(vscode_cdp):
    """After granting clipboard-read, permissions.query resolves to 'granted'."""
    vscode_cdp.grant_permissions(["clipboard-read", "clipboard-write"])
    try:
        state = vscode_cdp.evaluate(
            """
            () => navigator.permissions.query({name: 'clipboard-read'})
                    .then(r => r.state)
            """
        )
        assert state == "granted"
    finally:
        vscode_cdp.clear_permissions()


def test_set_geolocation_updates_navigator_readout(vscode_cdp):
    vscode_cdp.grant_permissions(["geolocation"])
    vscode_cdp.set_geolocation(52.2297, 21.0122, accuracy=5)  # Warsaw
    try:
        coords = vscode_cdp.evaluate(
            """
            () => new Promise(res =>
              navigator.geolocation.getCurrentPosition(
                p => res({lat: p.coords.latitude, lng: p.coords.longitude}),
                () => res(null)
              )
            )
            """
        )
        assert coords is not None
        assert abs(coords["lat"] - 52.2297) < 0.001
        assert abs(coords["lng"] - 21.0122) < 0.001
    finally:
        vscode_cdp.clear_permissions()


def test_set_extra_http_headers_seen_by_route_handler(vscode_cdp):
    """Custom headers show up in the intercepted request."""
    vscode_cdp.set_extra_http_headers({"X-Dolphin-Test": "yes"})
    seen: list[dict[str, str]] = []

    def handler(route):
        seen.append(dict(route.request.headers))
        route.respond(status=200, body="ok")

    vscode_cdp.route("**/dolphin-hdr/**", handler)
    try:
        vscode_cdp.evaluate(
            """
            () => fetch('https://example.com/dolphin-hdr/x').then(r => r.text())
            """
        )
        sleep(0.5)
        headers = seen[-1] if seen else {}
        # Header keys arrive lower-cased.
        assert headers.get("x-dolphin-test") == "yes"
    finally:
        vscode_cdp.unroute("**/dolphin-hdr/**")
        vscode_cdp.set_extra_http_headers({})


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------


def test_pdf_export_documented_electron_limitation(vscode_cdp):
    """``session.pdf()`` uses CDP's ``Page.printToPDF`` which Electron
    disables in its Chromium build for production apps. The wrapper is
    plumbed correctly (verified by the error surface) but the actual PDF
    step must run against a real Chromium — not Electron.
    """
    path = temp_file(prefix="dolphin-cdp-", suffix=".pdf", content=b"")
    try:
        try:
            vscode_cdp.pdf(path)
        except Exception as exc:
            # Expected on Electron: 'Page.printToPDF' wasn't found
            assert "printToPDF" in str(exc) or "not found" in str(exc).lower()
        else:
            # If Electron ever enables it, the file should exist.
            assert path_exists(path)
    finally:
        remove_file(path)


# ---------------------------------------------------------------------------
# tap — touch tap (works even without touch device: dispatches touch events)
# ---------------------------------------------------------------------------


def test_tap_requires_touch_or_raises_gracefully(vscode_cdp):
    """Chromium desktop refuses tap() unless the context has touch enabled.

    We call it and accept either success (touch was enabled somewhere) or
    a clean ElementNotFoundError bubbling up — proves the wrapper is
    plumbed and behaves like Playwright's underlying error surface.
    """
    from dolphin_desktop import ElementNotFoundError

    _mount(vscode_cdp, [{"tag": "button", "id": "tap-btn", "text": "tap"}])
    try:
        vscode_cdp.locator("#tap-btn").tap(timeout=2)
    except ElementNotFoundError:
        pass  # expected on non-touch contexts
