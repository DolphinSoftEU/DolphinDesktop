# WebView2 Apps

WebView2 hosts Microsoft Edge content inside a native Windows application. Dolphin drives the accessibility tree exposed to UIA.

## Launch

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch_webview2("MyHybridApp.exe")
win = app.window(title_re=".*My Hybrid App.*")
```

`launch_webview2()` is a semantic wrapper around `Desktop.launch()`. It does not add command-line flags.

## Detect WebView2

```python
if app.is_webview2():
    print("WebView2 detected")
```

Detection checks for WebView2 loader modules in the process and returns `False` on errors.

## Work With Native And Web Controls

Use `dolphin spy` to see where the WebView document appears:

```bash
dolphin spy --window "My Hybrid App" --depth 5
```

Example:

```python
# Native host control
win.get_by_automation_id("btnRefresh").click()

# Web content exposed through UIA
doc = win.get_by_role("Document")
doc.get_by_role("Edit", name="Username").type_text("admin")
doc.get_by_role("Button", name="Sign in").click()
```

If the page loads slowly, use a locator timeout:

```python
doc.get_by_role("Button", name="Submit").timeout(20).click()
```

## Notes

- Native controls and embedded web controls may be siblings in the UIA tree.
- Iframes can appear as nested `Document` nodes.
- The flow on this page drives the accessibility tree through UIA — no DOM selectors or JavaScript injection. If you need DOM-level selectors, network interception, or JavaScript evaluation, WebView2 also supports the Chrome DevTools Protocol path (`[cdp]` extra): enable remote debugging on the WebView2 host (for example via `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222`) and attach a `CDPSession` — see the Electron/CDP guide for the API.
