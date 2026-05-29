# Desktop

`Desktop` is the main entry point. Create one instance per test session.

---

::: dolphin_desktop._desktop.Desktop

---

## Usage examples

### Launch an application

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch("notepad.exe")
```

### Connect to a running application

```python
app = desktop.connect(title_re=".*My Application.*")
app = desktop.connect(class_name="Notepad")
app = desktop.connect(process=12345)
```

### Technology-specific launchers

```python
# Qt app - enables QT_ACCESSIBILITY=1
app = desktop.launch_qt("myqtapp.exe")

# Java Swing - enables Java Access Bridge
app = desktop.launch_java("java -jar myapp.jar")

# Electron - adds --force-renderer-accessibility
app = desktop.launch_electron("myelectronapp.exe")

# Edge WebView2 hybrid
app = desktop.launch_webview2("myhybridapp.exe")

# Legacy apps (Delphi, MFC, VB6)
desktop = Desktop.for_legacy_apps()   # backend="win32"
```

### Find a running process

```python
app = desktop.find_process(name="notepad.exe")
app = desktop.find_process(title_re=".*Notepad.*")
app = desktop.find_process(pid=1234)
```

### Headless mode

```python
desktop = Desktop(hidden=True)   # always hidden
desktop = Desktop(hidden=False)  # always visible
desktop = Desktop()              # auto-detect (default)
```
