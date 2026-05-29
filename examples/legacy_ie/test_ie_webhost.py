"""
IE/Trident WPF WebBrowser sample
================================
Tests a WPF app hosting the legacy IE/Trident (MSHTML) rendering engine
via the ``WebBrowser`` WPF control.

Scenario:
  * app.is_legacy_ie() returns True
  * native WPF controls accessible via UIA
  * HTML elements inside WebBrowser accessible via the MSAA to UIA bridge
  * form submit updates DOM (synchronously via MSHTML)
  * compatible with Dolphin headless mode

Prerequisites
-------------
* .NET SDK with net48 support  (build step is automatic on first run).
* Run: pytest examples/legacy_ie/ -v

The conftest.py builds the sample app on first run if not already built.

UIA tree structure
------------------
::

    Window (title="Dolphin IE/Trident Sample")
    ├── Pane / ToolBar
    │   ├── Button   (AutomationProperties.Name="Refresh")   ← native WPF
    │   └── Text     (Label)
    ├── Pane   (AutomationProperties.Name="IEBrowserPanel")  ← WebBrowser host
    │   └── Document  (MSHTML content root — via MSAA bridge)
    │       ├── Edit   (aria-label="Name")                   ← <input type="text">
    │       ├── Edit   (aria-label="City")                   ← <input type="text">
    │       ├── Button (title="Submit")                      ← <button>
    │       └── Text   (#result paragraph)                   ← <p id="result">
    └── Text   (AutomationProperties.AutomationId="StatusBar")  ← native WPF

HTML → UIA mapping via MSAA bridge
-----------------------------------
==============================  ================  =============================
HTML element                    UIA control_type  ValuePattern
==============================  ================  =============================
``<input type="text">``         Edit              SetValue works ✅
``<button>``                    Button            InvokePattern ✅
``<p>`` / ``<span>``            Text              ReadOnly
``<select>``                    ComboBox          SelectionPattern ✅
``<a>``                         Hyperlink
==============================  ================  =============================
"""

from __future__ import annotations

import time

# ---------------------------------------------------------------------------
# Test 1 — is_legacy_ie() detection
# ---------------------------------------------------------------------------


def test_is_legacy_ie(ie_window):
    """app.is_legacy_ie() must return True — mshtml.dll is in the process modules."""
    app, _ = ie_window
    assert app.is_legacy_ie(), (
        "is_legacy_ie() returned False — mshtml.dll not detected in process modules. "
        "Ensure the WPF WebBrowser control has initialised before calling this method."
    )


# ---------------------------------------------------------------------------
# Test 2 — native WPF Button accessible without any special flags
# ---------------------------------------------------------------------------


def test_native_button_accessible(ie_window):
    """WPF native Button 'Refresh' is accessible directly through UIA."""
    _, win = ie_window

    refresh_btn = win.get_by_role("Button", name="Refresh")
    assert refresh_btn.exists(), (
        "Native WPF Refresh button not found via UIA. "
        "Check AutomationProperties.Name in MainWindow.xaml."
    )
    assert refresh_btn.is_enabled(), "Refresh button exists but is disabled"
    assert refresh_btn.is_visible(), "Refresh button exists but is not visible"


# ---------------------------------------------------------------------------
# Test 3 — HTML <input> accessible via MSAA → UIA bridge
# ---------------------------------------------------------------------------


def test_html_input_accessible(ie_window):
    """HTML <input type='text' aria-label='Name'> is accessible as UIA Edit.

    The MSHTML/Trident engine exposes HTML elements via the MSAA bridge,
    which UIA then wraps as standard UIA controls.  No special flags required.
    """
    _, win = ie_window

    # MSHTML exposes the WebBrowser content under a Document node
    doc = win.locator(control_type="Document", found_index=0).timeout(8)
    if not doc.exists():
        # Fallback: search from window root for older pywinauto versions
        doc = win

    name_input = doc.locator(control_type="Edit", title_re=r".*[Nn]ame.*").timeout(8)
    if not name_input.exists():
        name_input = doc.locator(control_type="Edit", found_index=0).timeout(5)

    assert name_input.exists(), (
        "HTML Name input not found via UIA. "
        "MSHTML MSAA bridge may not have initialised yet — increase startup delay."
    )
    assert name_input.is_enabled(), "Name input is not enabled"

    name_input.click()
    name_input.type_text("DolphinTest")
    time.sleep(0.2)

    # ValuePattern is available via MSAA bridge — value() should reflect typed text
    val = name_input.value() or name_input.text()
    assert "DolphinTest" in (val or ""), (
        f"Typed text not reflected in UIA value after type_text(): {val!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — HTML <button> click via MSAA InvokePattern
# ---------------------------------------------------------------------------


def test_html_button_click(ie_window):
    """HTML <button>Submit</button> is invokable via UIA InvokePattern.

    MSHTML exposes button click through MSAA IAccessible::accDoDefaultAction,
    which UIA wraps as InvokePattern.  Dolphin's click() uses this automatically.
    """
    _, win = ie_window

    doc = win.locator(control_type="Document", found_index=0).timeout(8)
    if not doc.exists():
        doc = win

    # Fill name so the result message is meaningful
    name_input = doc.locator(control_type="Edit", found_index=0).timeout(5)
    if name_input.exists():
        name_input.click()
        name_input.type_text("Dolphin")

    submit_btn = doc.locator(control_type="Button", title_re=r".*[Ss]ubmit.*").timeout(5)
    if not submit_btn.exists():
        submit_btn = doc.locator(control_type="Button", found_index=0).timeout(5)

    assert submit_btn.exists(), (
        "Submit button not found inside MSHTML document. "
        "Verify the HTML form loaded correctly (check StatusBar text)."
    )
    submit_btn.click()
    time.sleep(0.3)


# ---------------------------------------------------------------------------
# Test 5 — DOM update after Submit is synchronously visible via UIA
# ---------------------------------------------------------------------------


def test_result_text_after_submit(ie_window):
    """After Submit, JS updates #result — MSHTML propagates the DOM change to UIA.

    Unlike WebView2 and Electron, MSHTML updates UIA properties synchronously
    on DOM mutations — no wait_for() is needed after JS execution.
    """
    _, win = ie_window

    doc = win.locator(control_type="Document", found_index=0).timeout(8)
    if not doc.exists():
        doc = win

    # Fill and submit
    name_input = doc.locator(control_type="Edit", found_index=0).timeout(5)
    if name_input.exists():
        name_input.click()
        name_input.type_text("Alice")

    city_input = doc.locator(control_type="Edit", found_index=1).timeout(5)
    if city_input.exists():
        city_input.click()
        city_input.type_text("Warsaw")

    submit_btn = doc.locator(control_type="Button", found_index=0).timeout(5)
    if submit_btn.exists():
        submit_btn.click()
        time.sleep(0.3)

    # MSHTML updates UIA synchronously after JS DOM mutation
    result_elem = doc.locator(control_type="Text", title_re=r".*Submitted.*").timeout(3)
    if result_elem.exists():
        result_text = result_elem.text()
        assert "Submitted" in (result_text or "") and "Alice" in (result_text or ""), (
            f"Result paragraph not updated after Submit: {result_text!r}"
        )
    else:
        # Fallback: check the whole document text via window_text
        doc_text = doc.text() or ""
        assert "Alice" in doc_text or "Submitted" in doc_text, (
            "DOM not updated after Submit — JS may not have executed in MSHTML."
        )


# ---------------------------------------------------------------------------
# Test 6 — native WPF StatusBar updated by page load event
# ---------------------------------------------------------------------------


def test_status_bar_reflects_load(ie_window):
    """Native WPF StatusBar shows 'Status: loaded' after the HTML page loads."""
    _, win = ie_window

    # The StatusBar is a native WPF TextBlock, not inside the WebBrowser
    status = win.get_by_automation_id("StatusBar").timeout(5)
    if not status.exists():
        status = win.locator(control_type="Text", title_re=r".*Status.*").timeout(5)

    assert status.exists(), (
        "StatusBar TextBlock not found. "
        "Check AutomationProperties.AutomationId='StatusBar' in MainWindow.xaml."
    )

    status_text = status.text() or ""
    assert "loaded" in status_text or "ready" in status_text or "refreshed" in status_text, (
        f"StatusBar text unexpected after page load: {status_text!r}"
    )
