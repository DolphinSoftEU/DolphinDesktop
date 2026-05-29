# Electron Apps

Electron applications embed Chromium in a Windows desktop process. Dolphin can automate the parts of the UI that Electron exposes through Windows accessibility APIs.

## Launch With Accessibility Enabled

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch_electron(r"C:\Path\To\MyElectronApp.exe")
win = app.window(title_re=".*My Electron App.*")
```

`launch_electron()` appends `--force-renderer-accessibility` if the command does not already include it.

You can pass the flag yourself when needed:

```python
app = desktop.launch(
    r"C:\Path\To\MyElectronApp.exe --force-renderer-accessibility",
    timeout=20,
)
```

## Detect Electron

```python
if app.is_electron():
    print("Electron runtime detected")
```

Detection checks the Chromium window class and known Electron module names. It returns `False` on errors.

## Selectors

After accessibility is enabled, inspect the tree:

```bash
dolphin spy --window "My Electron App" --depth 4
```

Then use the exposed roles and names:

```python
win.get_by_role("Edit", name="Username").type_text("admin")
win.get_by_role("Button", name="Sign in").click()
win.get_by_text("Welcome").wait_for(state="visible", timeout=10)
```

Not every Electron app exposes the same tree. Custom-rendered controls may require a different selector or an image fallback.

## App-Side Option

For an Electron app you own, enable the accessibility flag in test mode from the main process:

```javascript
app.commandLine.appendSwitch("force-renderer-accessibility")
```

## Fallback

If a control is not visible through UIA, capture a template and use image matching:

```bash
dolphin spy --image-pick --output-dir templates
```

```python
win.image("templates/sign_in.png", confidence=0.9).click()
```
