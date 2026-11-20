"""Automate an existing-style WPF MSHTML host through UIA.

Build examples/legacy_ie/build.ps1 first. This is a compatibility sample for
embedded WebBrowser controls; use WebView2 for new browser-host applications.
"""

import pytest

pytestmark = pytest.mark.integration


def _document(window):
    document = window.locator(control_type="Document", found_index=0).timeout(5)
    return document if document.exists() else window


def test_is_legacy_ie(ie_window):
    app, _ = ie_window
    assert app.is_legacy_ie()


def test_native_button_accessible(ie_window):
    _, window = ie_window
    refresh = window.get_by_role("Button", name="Refresh")
    assert refresh.is_enabled()
    assert refresh.is_visible()


def test_html_input_accessible(ie_window):
    _, window = ie_window
    name = _document(window).locator(control_type="Edit", found_index=0)
    name.type_text("DolphinTest")
    name.wait_for_text("DolphinTest", timeout=5)


def test_html_button_click(ie_window):
    _, window = ie_window
    document = _document(window)
    document.locator(control_type="Edit", found_index=0).type_text("Dolphin")
    document.locator(control_type="Button", title="Submit").click()
    document.locator(control_type="Text", title_re=".*Submitted.*").wait_for_text("Dolphin")


def test_result_text_after_submit(ie_window):
    _, window = ie_window
    document = _document(window)
    document.locator(control_type="Edit", found_index=0).type_text("Alice")
    document.locator(control_type="Edit", found_index=1).type_text("Warsaw")
    document.locator(control_type="Button", title="Submit").click()
    document.locator(control_type="Text", title_re=".*Submitted.*").wait_for_text(
        "Submitted: Alice from Warsaw", timeout=5
    )


def test_status_bar_reflects_load(ie_window):
    _, window = ie_window
    window.get_by_automation_id("StatusBar").wait_for_text("loaded", timeout=5)
