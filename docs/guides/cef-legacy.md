# CEF And Legacy IE

This guide covers applications that embed Chromium Embedded Framework (CEF) or the legacy MSHTML/Trident browser engine.

## CEF

Start with UIA:

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch_cef("MyCefApp.exe")
win = app.window(title_re=".*My CEF App.*")
```

`launch_cef()` appends `--force-renderer-accessibility` if the command does not already include it. Some applications ignore unknown command-line flags, so verify with:

```bash
dolphin spy --window "My CEF App" --depth 4
```

Detect CEF:

```python
if app.is_cef():
    print("CEF detected")
```

## CEF Fallback

If the embedded content is custom-rendered or not exposed through UIA, the primary escape hatch for CEF's Chromium runtime is the CDP path — enable a remote debugging port and drive the DOM directly (see [Embedded web](embedded-web.md)). Use image matching only when a debug port cannot be enabled:

```python
from dolphin_desktop import ImageLocator

submit = ImageLocator("templates/submit.png", threshold=0.9)
submit.wait_for(timeout=10)
submit.click()
```

Capture templates with:

```bash
dolphin spy --image-pick --output-dir templates
```

## Legacy IE / Trident

Older applications may host MSHTML through a WebBrowser control.

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch("LegacyPortal.exe")
win = app.window(title_re=".*Portal.*")

doc = win.get_by_role("Document")
doc.get_by_role("Edit", name="Username").type_text("admin")
doc.get_by_role("Button", name="Login").click()
```

Detect legacy IE:

```python
if app.is_legacy_ie():
    print("MSHTML detected")
```

If UIA does not expose useful children, try the Win32 backend and class-name selectors:

```python
desktop = Desktop(backend="win32")
app = desktop.launch("LegacyPortal.exe")
win = app.window(title_re=".*Portal.*")
win.get_by_class("Internet Explorer_Server").click()
```

## Notes

- Prefer UIA or Win32 selectors when available.
- Use image matching only for controls that accessibility APIs cannot see.
- Test embedded browser apps in isolation; multiple similar windows can make broad selectors ambiguous.
