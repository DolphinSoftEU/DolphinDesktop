"""
WebView2 login sample
=====================
Tests a WPF + Edge WebView2 host app containing an HTML login form.

Scenario:
  * input email, password
  * click submit (Log In)
  * assert response message
  * compatible with Dolphin headless mode

Prerequisites
-------------
* WebView2 Runtime installed (pre-installed on Windows 11; see docs/webview2.md).
* .NET 8 SDK for the auto-build step (the conftest builds the app on first run).
* Run: pytest examples/webview2_sample/ -v
"""

from __future__ import annotations

import time

# ---------------------------------------------------------------------------
# Test 1 — is_webview2() detection
# ---------------------------------------------------------------------------


def test_is_webview2(wv2_window):
    """app.is_webview2() must return True for a WebView2-hosted app."""
    app, _ = wv2_window
    assert app.is_webview2(), (
        "is_webview2() returned False — WebView2Loader.dll not found in process modules. "
        "Ensure the sample app was built against the Microsoft.Web.WebView2 NuGet package."
    )


# ---------------------------------------------------------------------------
# Test 2 — email input
# ---------------------------------------------------------------------------


def test_email_input(wv2_window):
    """HTML <input type='email' aria-label='Email'> is accessible as UIA Edit."""
    _, win = wv2_window

    email_field = win.locator(control_type="Edit", title_re=".*Email.*").timeout(8)
    assert email_field.exists(), "Email input not found in WebView2 UIA tree"
    assert email_field.is_enabled(), "Email input is not enabled"

    email_field.click()
    email_field.set_text("user@example.com")
    time.sleep(0.2)

    val = email_field.value() or email_field.text()
    assert "user@example.com" in (val or ""), (
        f"Email value not reflected in UIA after set_text: {val!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — password input
# ---------------------------------------------------------------------------


def test_password_input(wv2_window):
    """HTML <input type='password' aria-label='Password'> is accessible as UIA Edit."""
    _, win = wv2_window

    password_field = win.locator(control_type="Edit", title_re=".*Password.*").timeout(8)
    assert password_field.exists(), "Password input not found in WebView2 UIA tree"

    password_field.click()
    password_field.set_text("secret123")
    time.sleep(0.2)


# ---------------------------------------------------------------------------
# Test 4 — submit and assert response message (core acceptance criterion)
# ---------------------------------------------------------------------------


def test_submit_and_response(wv2_window):
    """Fill email + password, click Log In, assert 'Logged in as …' appears."""
    _, win = wv2_window

    # Fill email
    email_field = win.locator(control_type="Edit", title_re=".*Email.*").timeout(8)
    if email_field.exists():
        email_field.click()
        email_field.set_text("qa@dolphin_desktop.test")

    # Fill password
    password_field = win.locator(control_type="Edit", title_re=".*Password.*").timeout(8)
    if password_field.exists():
        password_field.click()
        password_field.set_text("dolphin123")

    # Click submit
    login_btn = win.locator(control_type="Button", title_re=".*(Log In|Login).*").timeout(8)
    assert login_btn.exists(), "Log In button not found in WebView2 UIA tree"
    login_btn.click()
    time.sleep(0.5)

    # Assert HTML response element (core AC: must contain "Logged in as …")
    response = win.locator(control_type="Text", title_re=".*Response.*").timeout(5)
    assert response.exists(), (
        "Response element not found in WebView2 UIA tree after submit. "
        "Check that aria-label='Response' is present on the #response div in login.html."
    )
    response_text = response.text()
    assert "Logged in as" in response_text, (
        f"Expected 'Logged in as …' in response element, got: {response_text!r}"
    )

    # Also assert native StatusBar updated via JS postMessage
    status_bar = win.locator(control_type="Text", title_re=".*Status.*").timeout(5)
    if status_bar.exists():
        status_text = status_bar.text()
        assert "Logged in as" in status_text, (
            f"StatusBar (JS→native interop) not updated after submit: {status_text!r}"
        )
