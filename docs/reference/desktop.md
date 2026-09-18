# Desktop

`Desktop` is the main entry point. Create one instance per test session.

---

::: dolphin_desktop._desktop.Desktop

---

## Usage examples

### Launch an application

```python
from dolphin_desktop import Desktop

desktop = Desktop(backend="uia", hidden=False)
app = desktop.launch("notepad.exe")
try:
    window = app.window(title_re=".*Notepad.*")
    window.wait_until_ready()
finally:
    # launch() creates an owned process; close only that process.
    app.close()
```

For tests, the context-manager form is equivalent and also cleans up when the
body raises:

```python
with Desktop(backend="uia", hidden=False).launch("my-app.exe") as app:
    window = app.window(title="My application")
    window.wait_until_ready()
```

`Desktop.launch()` owns the process it starts. `Application.close()` /
`Application.kill()` clean up owned processes (including children launched via
that application). `Desktop.connect()` attaches to an existing process and
does not make Dolphin its owner; closing the returned `Application` therefore
must not terminate the attached process. A test must not connect to an
existing Notepad or Calculator window unless that application and its cleanup
are explicitly opted into by the test job.

### Connect to a running application

```python
app = desktop.connect(title_re=".*My Application.*")
app = desktop.connect(class_name="Notepad")
app = desktop.connect(process=12345)
```

`Application.window(...)` waits for a matching visible window. Use
`window.wait_until_ready()` before the first interaction when the application
has additional startup work. Search by process or a unique handle/title when
possible; a broad title or class-name match can select another window.

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

`hidden=False` is the explicit visible-desktop contract needed for physical
input and pixel screenshots. Keep a screenshot path inside the test's
temporary directory and remove it in `finally` after the assertion:

```python
from pathlib import Path

screenshot = Path(tmp_path) / "result.png"
try:
    with Desktop(backend="uia", hidden=False).launch("my-app.exe") as app:
        app.window(title="My application").screenshot(screenshot)
finally:
    screenshot.unlink(missing_ok=True)
```
