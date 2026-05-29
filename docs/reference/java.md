# JavaAccessBridge

Helper for enabling and querying Java Access Bridge - required for Java Swing/AWT automation.

---

::: dolphin_desktop._java.JavaAccessBridge

---

## Usage

```python
from dolphin_desktop import JavaAccessBridge

# Check status
print(f"JAB enabled: {JavaAccessBridge.is_enabled()}")
print(f"Java home:   {JavaAccessBridge.java_home()}")

# Ensure enabled (idempotent - safe to call before every Java test)
JavaAccessBridge.ensure_enabled()
```

## JABLocator

When `Window.get_by_*()` is called on a Java window (`class_name="SunAwtFrame"`), Dolphin
returns a `JABLocator` instead of the standard `Locator`. The high-level API is similar:

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch_java("java -jar SwingApp.jar")
win = app.window(class_name="SunAwtFrame")

# win.get_by_role() returns JABLocator automatically
win.get_by_role("push button", name="OK").click()
win.get_by_role("text", name="Username").type_text("admin")
```

See the [Java guide](../guides/java.md) for the full role mapping table.
