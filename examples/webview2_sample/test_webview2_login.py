"""
WebView2 login sample
=====================
Tests a WPF + Edge WebView2 host app containing an HTML login form.

Scenario:
  * input email, password
  * click submit (Log In)
  * assert response message

Prerequisites
-------------
* WebView2 Runtime installed (pre-installed on Windows 11; see
  docs/guides/webview2.md).
* Build with examples/webview2_sample/build.ps1 using the .NET 10 SDK.
* Run: uv run --no-sync pytest examples/webview2_sample/ -v
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

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
    # Poll UIA for the value to propagate — no fixed sleep.
    email_field.wait_for_text("user@example.com", timeout=3)


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
    # No assertion on password read-back (privacy semantics — UIA may
    # mask the value); this test only proves set_text does not raise.


# ---------------------------------------------------------------------------
# Test 4 — submit and assert response message (core acceptance criterion)
# ---------------------------------------------------------------------------


def test_submit_and_response(wv2_window):
    """Fill email + password, click Log In, assert 'Logged in as …' appears."""
    _, win = wv2_window

    # Fill email
    email_field = win.locator(control_type="Edit", title_re=".*Email.*").timeout(8)
    assert email_field.exists()
    email_field.set_text("qa@example.test")

    # Fill password
    password_field = win.locator(control_type="Edit", title_re=".*Password.*").timeout(8)
    assert password_field.exists()
    password_field.set_text("synthetic-password")

    # Click submit
    login_btn = win.locator(control_type="Button", title_re=".*(Log In|Login).*").timeout(8)
    assert login_btn.exists(), "Log In button not found in WebView2 UIA tree"
    login_btn.click()

    # Assert HTML response element (core AC: must contain "Logged in as …")
    response = win.locator(title="Logged in as qa@example.test", found_index=0).timeout(5)
    # Poll for the response text instead of guessing how long the
    # WebView2 JS handler takes to run.
    response.wait_for_text("Logged in as", timeout=5)

    # Also assert native StatusBar updated via JS postMessage
    status_bar = win.get_by_automation_id("StatusBar")
    status_bar.wait_for_text("Logged in as qa@example.test", timeout=5)
