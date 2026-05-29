# Application

`Application` wraps a running process. Returned by `Desktop.launch()` and `Desktop.connect()`.

---

::: dolphin_desktop._application.Application

---

## Usage examples

### Get a window

```python
win = app.window(title_re=".*My App.*")
win = app.window(class_name="Notepad")
win = app.window(title="Exact Window Title")
```

### Context manager (automatic teardown)

```python
with desktop.launch("notepad.exe") as app:
    win = app.window(class_name="Notepad")
    win.get_by_role("Document").type_text("hello")
# app.kill() called automatically
```

### Process lifecycle

```python
app.wait_for_idle(timeout=10)  # wait until CPU is idle
pid = app.process_id
app.close()   # request graceful shutdown explicitly
app.kill()    # deterministic teardown used by fixtures/context managers
```

### All windows

```python
for win in app.windows():
    print(win.title())
```
