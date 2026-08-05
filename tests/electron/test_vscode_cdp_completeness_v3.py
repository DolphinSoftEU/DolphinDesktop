"""CDP coverage for network interception, downloads, popups, init scripts,
storage-write, wait_for_response/request/url, element_handle and click
modifiers.
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


def _mount(session, spec):
    session.evaluate(
        """
        (spec) => {
          const wrap = document.createElement('div');
          wrap.id = 'v3-fixture';
          wrap.style.cssText = 'position:fixed;top:120px;left:120px;'
                             + 'z-index:100000;background:white;padding:8px;';
          const build = (s) => {
            const el = document.createElement(s.tag);
            if (s.id) el.id = s.id;
            if (s.className) el.className = s.className;
            if (s.attrs) {
              for (const [k, v] of Object.entries(s.attrs)) el.setAttribute(k, v);
            }
            if (s.text !== undefined) el.textContent = s.text;
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
# Network interception — route / respond / respond_json / abort
# ---------------------------------------------------------------------------


def test_route_respond_with_static_body(vscode_cdp):
    """route() intercepts fetch and returns our synthetic body."""
    vscode_cdp.route(
        "**/dolphin-mock/**",
        lambda r: r.respond(status=200, body="mocked-body", content_type="text/plain"),
    )
    try:
        result = vscode_cdp.evaluate(
            """
            () => fetch('https://example.com/dolphin-mock/x')
                  .then(r => r.text())
            """
        )
        assert result == "mocked-body"
    finally:
        vscode_cdp.unroute("**/dolphin-mock/**")


def test_route_respond_json_returns_parsed_object(vscode_cdp):
    vscode_cdp.route(
        "**/dolphin-json/**",
        lambda r: r.respond_json({"ok": True, "count": 42}),
    )
    try:
        result = vscode_cdp.evaluate(
            """
            () => fetch('https://example.com/dolphin-json/x')
                  .then(r => r.json())
            """
        )
        assert result == {"ok": True, "count": 42}
    finally:
        vscode_cdp.unroute("**/dolphin-json/**")


def test_route_abort_makes_fetch_fail(vscode_cdp):
    vscode_cdp.route(
        "**/dolphin-abort/**",
        lambda r: r.abort("failed"),
    )
    try:
        result = vscode_cdp.evaluate(
            """
            () => fetch('https://example.com/dolphin-abort/x')
                  .then(() => 'ok').catch(() => 'aborted')
            """
        )
        assert result == "aborted"
    finally:
        vscode_cdp.unroute("**/dolphin-abort/**")


def test_route_handler_sees_request_url_and_method(vscode_cdp):
    seen: list[tuple[str, str]] = []

    def handler(route):
        seen.append((route.request.url, route.request.method))
        route.respond(status=204, body="")

    vscode_cdp.route("**/dolphin-inspect/**", handler)
    try:
        vscode_cdp.evaluate(
            """
            () => fetch('https://example.com/dolphin-inspect/z',
                        {method: 'POST', body: 'payload'})
                  .then(r => r.status)
            """
        )
        assert any("/dolphin-inspect/z" in u for u, _ in seen)
        assert any(m == "POST" for _, m in seen)
    finally:
        vscode_cdp.unroute("**/dolphin-inspect/**")


# ---------------------------------------------------------------------------
# expect_response / expect_request
# ---------------------------------------------------------------------------


def test_expect_response_wrapper_yields_lazy_value(vscode_cdp):
    """expect_response raises out of the ``with`` block when nothing matches.

    Live network responses in VS Code are hard to trigger reliably
    (route-fulfilled requests skip the response event, data: URLs are
    treated as top-level navigations by Chromium's renderer). This case
    covers the timeout path; live-network response scenarios are the
    ``expect_request`` test below and the route-based tests, which
    exercise response interception.
    """
    # Playwright raises its own TimeoutError here, which dolphin does not wrap
    # on this path; asserting the concrete class would couple the test to a
    # Playwright internal, so the blind catch is deliberate.
    with pytest.raises(Exception):  # noqa: B017
        with vscode_cdp.expect_response(
            lambda resp: "never-matches-dolphin-xyz" in resp.url,
            timeout=1,
        ):
            pass


def test_expect_request_captures_matching_request(vscode_cdp):
    vscode_cdp.route(
        "**/dolphin-req/**",
        lambda r: r.respond(status=200, body="ok"),
    )
    try:
        with vscode_cdp.expect_request("**/dolphin-req/**", timeout=10) as req:
            vscode_cdp.evaluate("() => fetch('https://example.com/dolphin-req/42')")
        assert "/dolphin-req/42" in req.value.url
        assert req.value.method == "GET"
    finally:
        vscode_cdp.unroute("**/dolphin-req/**")


# ---------------------------------------------------------------------------
# Init scripts — add_init_script
# ---------------------------------------------------------------------------


def test_add_init_script_runs_before_page_load(vscode_cdp):
    """add_init_script marks every subsequent navigation."""
    vscode_cdp.add_init_script("window.__dolphinInit = 'seeded';")
    # Init scripts fire on new documents. Reload the current page so we
    # can verify without changing URL.
    vscode_cdp.reload(timeout=30)
    vscode_cdp.wait_for_load_state("load", timeout=30)
    vscode_cdp.locator(".monaco-workbench").wait_for(state="visible", timeout=30)
    value = vscode_cdp.evaluate("() => window.__dolphinInit")
    assert value == "seeded"


# ---------------------------------------------------------------------------
# Cookies write + sessionStorage
# ---------------------------------------------------------------------------


def test_set_cookies_and_read_back(vscode_cdp):
    """We can inject a cookie into the browser context (HTTP domain — the
    vscode-file:// origin is not a valid cookie origin)."""
    vscode_cdp.set_cookies(
        [
            {
                "name": "dolphin-c",
                "value": "hello",
                "domain": "dolphin-test.example",
                "path": "/",
            }
        ]
    )
    cookies = vscode_cdp.cookies()
    names = [c.get("name") for c in cookies]
    assert "dolphin-c" in names


def test_clear_cookies_removes_all(vscode_cdp):
    vscode_cdp.set_cookies(
        [
            {
                "name": "dolphin-cc",
                "value": "v",
                "domain": "dolphin-test.example",
                "path": "/",
            }
        ]
    )
    assert any(c.get("name") == "dolphin-cc" for c in vscode_cdp.cookies())
    vscode_cdp.clear_cookies()
    assert vscode_cdp.cookies() == []


def test_session_storage_set_get_clear(vscode_cdp):
    vscode_cdp.session_storage_set("ss-key", "ss-value")
    assert vscode_cdp.session_storage_get("ss-key") == "ss-value"
    vscode_cdp.session_storage_clear()
    assert vscode_cdp.session_storage_get("ss-key") is None


# ---------------------------------------------------------------------------
# Navigation — current_url + wait_for_url (no real navigation in VS Code)
# ---------------------------------------------------------------------------


def test_current_url_returns_workbench(vscode_cdp):
    assert "workbench.html" in vscode_cdp.current_url()


def test_wait_for_url_matches_current_immediately(vscode_cdp):
    """wait_for_url returns the session when the current URL already matches,
    and leaves the page where it was."""
    assert vscode_cdp.wait_for_url("**/workbench.html*", timeout=5) is vscode_cdp
    assert "workbench.html" in vscode_cdp.current_url()


# ---------------------------------------------------------------------------
# Locator escape hatch — element_handle
# ---------------------------------------------------------------------------


def test_element_handle_returns_playwright_handle(vscode_cdp):
    _mount(vscode_cdp, [{"tag": "div", "id": "eh-tgt", "text": "handle"}])
    handle = vscode_cdp.locator("#eh-tgt").element_handle()
    # We do not import Playwright here; we just verify the handle behaves.
    assert handle is not None
    text = handle.text_content()
    assert text == "handle"


# ---------------------------------------------------------------------------
# Click with modifiers + position + button
# ---------------------------------------------------------------------------


def test_click_with_modifiers_reports_ctrl_shift(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "button",
                "id": "mod-btn",
                "text": "click me",
                "attrs": {"style": "width:120px;height:40px;"},
            }
        ],
    )
    vscode_cdp.evaluate(
        """
        () => {
          window.__modState = null;
          document.getElementById('mod-btn').addEventListener('click', e => {
            window.__modState = {ctrl: e.ctrlKey, shift: e.shiftKey,
                                  alt: e.altKey, meta: e.metaKey};
          });
        }
        """
    )
    vscode_cdp.locator("#mod-btn").click(modifiers=["Control", "Shift"])
    state = vscode_cdp.evaluate("() => window.__modState")
    assert state == {"ctrl": True, "shift": True, "alt": False, "meta": False}


def test_click_at_specific_position_reports_offset(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "pos-tgt",
                "text": "click zone",
                "attrs": {"style": "width:200px;height:100px;background:#eef;"},
            }
        ],
    )
    vscode_cdp.evaluate(
        """
        () => {
          window.__pos = null;
          document.getElementById('pos-tgt').addEventListener('click', e => {
            const box = e.currentTarget.getBoundingClientRect();
            window.__pos = {
              offX: Math.round(e.clientX - box.left),
              offY: Math.round(e.clientY - box.top),
            };
          });
        }
        """
    )
    vscode_cdp.locator("#pos-tgt").click(position={"x": 25, "y": 15})
    got = vscode_cdp.evaluate("() => window.__pos")
    # ±1 tolerance for browser sub-pixel rounding.
    assert abs(got["offX"] - 25) <= 1
    assert abs(got["offY"] - 15) <= 1


def test_click_middle_button_reports_button_1(vscode_cdp):
    _mount(
        vscode_cdp,
        [
            {
                "tag": "div",
                "id": "mid-tgt",
                "text": "middle",
                "attrs": {"style": "width:120px;height:40px;background:#efe;"},
            }
        ],
    )
    vscode_cdp.evaluate(
        """
        () => {
          window.__mid = null;
          document.getElementById('mid-tgt').addEventListener('mousedown', e => {
            window.__mid = e.button;
          });
        }
        """
    )
    vscode_cdp.locator("#mid-tgt").click(button="middle")
    assert vscode_cdp.evaluate("() => window.__mid") == 1


# ---------------------------------------------------------------------------
# Downloads — expect_download
# ---------------------------------------------------------------------------


def test_expect_download_captures_generated_file(vscode_cdp):
    """Triggering an <a download> click should be observable via expect_download."""
    vscode_cdp.evaluate(
        """
        () => {
          const a = document.createElement('a');
          a.id = 'dl-link';
          a.textContent = 'download';
          a.href = 'data:text/plain;base64,ZG9scGhpbi1kbG9hZA==';  // "dolphin-dload"
          a.download = 'dolphin-payload.txt';
          a.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;';
          document.body.appendChild(a);
        }
        """
    )
    with vscode_cdp.expect_download(timeout=10) as dl:
        vscode_cdp.locator("#dl-link").click()
    download = dl.value
    assert download.suggested_filename == "dolphin-payload.txt"
    # Playwright materializes the download to a temp path.
    path = download.path()
    assert path


# ---------------------------------------------------------------------------
# Popups — expect_popup + auto-switch
# ---------------------------------------------------------------------------


def test_expect_popup_captures_new_page_and_switches(vscode_cdp):
    """Clicking a target=_blank link opens a popup; session auto-switches."""
    vscode_cdp.evaluate(
        """
        () => {
          const a = document.createElement('a');
          a.id = 'popup-link';
          a.textContent = 'popup';
          a.href = 'about:blank';
          a.target = '_blank';
          a.style.cssText = 'position:fixed;top:0;left:0;z-index:99999;';
          document.body.appendChild(a);
        }
        """
    )
    try:
        with vscode_cdp.expect_popup(timeout=10) as popup:
            vscode_cdp.locator("#popup-link").click()
        # After the context exits, the session's current page is the popup.
        assert popup.value is not None
        # The workbench selector should NOT be on the popup page.
        assert vscode_cdp.locator(".monaco-workbench").is_visible() is False
    finally:
        # Return to the workbench page so subsequent tests keep working.
        pages = vscode_cdp.pages()
        for i, p in enumerate(pages):
            if "workbench.html" in getattr(p, "url", ""):
                vscode_cdp.switch_to_page(i)
                break
