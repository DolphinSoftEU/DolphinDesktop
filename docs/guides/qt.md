# Qt 5 / Qt 6

dolphin supports automating Qt 5 (PyQt5, PySide2) and Qt 6 (PySide6, PyQt6)
desktop applications through Microsoft UI Automation (UIA).

## At a glance

| Capability | Qt 5 | Qt 6 |
|---|---|---|
| Detection (`Application.is_qt`, `qt_version`) | ✓ | ✓ |
| `QPushButton`, `QCheckBox`, `QRadioButton` | ✓ | ✓ |
| `QLineEdit`, `QPlainTextEdit`, `QSpinBox`, `QSlider`, `QDateEdit` | ✓ | ✓ |
| `QComboBox`, `QListWidget`, `QTreeWidget`, `QTableView` | ✓ | ✓ |
| `QTabWidget`, `QToolBar`, top-level `QMenuBar` | ✓ | ✓ |
| `QDialog`, `QMessageBox`, `QInputDialog`, `QDockWidget` | ✓ | ✓ |
| Submenu popups (`File → Open`) | ✓ via desktop-scope fallback | ✓ via desktop-scope fallback |
| Combo / list signal-firing selection | via `select_item_keyboard` | via `select_item_keyboard` |
| Qt Quick / QML scene graphs | ✓ via Qt agent (`app.qt_agent.qml_*`) | ✓ via Qt agent (`app.qt_agent.qml_*`) |
| `QGraphicsView` custom paint | ✓ via Qt agent (`agent.graphics_*`) | ✓ via Qt agent (`agent.graphics_*`) |
| Qt Charts, Qt 3D, Qt Multimedia widgets | ✓ (polymorphic QObject introspection) | ✓ (polymorphic QObject introspection) |
| `Q_PROPERTY` get/set on any `QObject` | ✓ via agent | ✓ via agent |
| `QMetaObject::invokeMethod` with typed args | ✓ via agent | ✓ via agent |

## Launch

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch_qt(r"C:\Program Files\MyApp\my_qt_app.exe")
win = app.window(title="My Qt App")
```

`Desktop.launch_qt()` sets `QT_ACCESSIBILITY=1` in the child process only —
the parent's environment is restored after spawn so it does not leak into
subsequent non-Qt launches.

Optional environment overrides:

```python
app = desktop.launch_qt(
    "trading_app.exe",
    qt_env={"QT_LOGGING_RULES": "qt.accessibility=true"},
)
```

### Qt 5 vs Qt 6 — when is the flag needed?

| Runtime | `QT_ACCESSIBILITY=1` required? |
|---|---|
| Qt 5 (5.9 – 5.15) | **Yes** — UIA tree is empty without it. |
| Qt 6 (≥ 6.0) | No (auto-enabled) — but `launch_qt` sets it anyway for safety. |

## Detection

```python
app = desktop.launch_qt("myapp.exe")

if app.is_qt():
    version = app.qt_version()  # "5" or "6"
    print(f"Running Qt {version}")

if app.uses_qt_quick():
    # QML controls are invisible to UIA — use the Qt agent API instead:
    win.qml("loginButton").click()
```

Detection scans the process module list for `Qt5/6Core.dll`,
`Qt5/6Widgets.dll`, and (for Quick) `Qt5/6Quick.dll` / `Qt5/6Qml.dll`. The
result is cached per `Application` instance.

## Selectors

Qt's UIA bridge exposes accessibility information through two main channels:

- **`accessibleName`** → UIA `Name` property → pywinauto's `title=` criterion
  → dolphin's `name=` parameter on `button()`, `edit()`, etc.
- **`objectName`** → UIA `AutomationId` as a **hierarchical dotted path**
  (`QApplication.win.tabs.tab1.qt_btn_ok`) → matched by `Window.get_by_object_name()`
  which compares against the **leaf segment** only.

```python
# By accessible name (most readable; matches the user's label):
win.button(name="OK").click()
win.edit(name="Username").set_text("alice")
win.check_box(name="Remember me").invoke()

# By objectName leaf (most stable; survives localisation):
win.get_by_object_name("qt_btn_ok").invoke()
win.get_by_object_name("qt_input_username").set_text("alice")

# By Qt class name (combine with title for fast resolution):
win.locator(class_name="QPushButton", title="OK").click()
```

### Qt class → UIA control type mapping

| Qt widget | UIA `control_type` | Notes |
|---|---|---|
| `QPushButton` (regular) | `Button` | `button(name=...)` |
| `QPushButton` (`setCheckable(True)`) | `CheckBox` | `check_box(name=...)` |
| `QToolButton` (regular) | `Button` | inside a `QToolBar` |
| `QToolButton` (checkable action) | `CheckBox` | use `invoke()` |
| `QCheckBox` | `CheckBox` | |
| `QRadioButton` | `RadioButton` | |
| `QLineEdit` | `Edit` | `edit(name=...)` |
| `QPlainTextEdit` / `QTextEdit` | `Edit` | multi-line |
| `QSpinBox` / `QDoubleSpinBox` | `Spinner` | `locator(control_type="Spinner")` |
| `QSlider` | `Slider` | `locator(control_type="Slider")` |
| `QDateEdit` / `QTimeEdit` | `Edit` | |
| `QComboBox` | `ComboBox` | `combo_box(name=...)` |
| `QListWidget` | `List` | `list_box(name=...)` |
| `QTreeWidget` / `QTreeView` | `Tree` | `tree(name=...)` |
| `QTableWidget` / `QTableView` | `Table` | `locator(control_type="Table")` |
| `QTabWidget` | `Group` (outer) + `Tab` (tab bar) | `tab().select_tab(name)` |
| `QMenuBar` | `MenuBar` | `menu(name=...)` |
| `QMenu` (popup) | top-level window | reached via desktop-scope fallback |
| `QToolBar` | `ToolBar` | `toolbar(name=...)` |
| `QStatusBar` | `StatusBar` | |
| `QDockWidget` | `Pane` | |
| `QGroupBox` | `Group` | `locator(title=...)` |
| `QDialog` / `QMessageBox` | `Window` (top-level) | `app.window(title=...)` |

## Recommended interaction patterns

### Clicking — prefer `invoke()` over `click()`

`Locator.click()` uses physical mouse (`SetCursorPos`) which fails with
`ERROR_FILE_NOT_FOUND` when the target window is obscured. `Locator.invoke()`
uses the UIA Invoke / Toggle / SelectionItem pattern — no mouse, no focus
race.

```python
win.button(name="OK").invoke()       # works even if window isn't foreground
win.check_box(name="Remember me").invoke()
win.radio_button(name="Option B").invoke()
```

### Selecting from `QComboBox` / `QListWidget` — use keyboard

Qt's UIA Select pattern only flips `IsSelected` on the target item without
driving the model — your `currentTextChanged` slot never fires.
`select_item_keyboard()` opens the dropdown and uses Qt's typeahead:

```python
win.combo_box(name="Language").select_item_keyboard("Polish")
win.list_box(name="Fruits").select_item_keyboard("Banana")
# Use 0-based index when the first letters are ambiguous:
win.combo_box(name="City").select_item_keyboard(2)
```

For non-Qt apps (WinForms, WPF), the standard `select_item()` is faster
and supports more matching strategies.

### Reading text — works directly

`Locator.text()` returns the current content for Qt edit widgets via
`IValueProvider.Value`:

```python
assert win.edit(name="Username").text() == "alice"
```

`Locator.text()` on Qt no longer falls through to a clipboard read, so it
never hangs on empty values (a previous bug fixed during Qt 6 support work).

## Menus & submenus

Top-level menubar items are found like any other element:

```python
win.menu("File").click()   # opens the File popup
```

When the popup opens, Qt creates a **top-level `QMenu` window** detached
from the main window. Dolphin's menu resolver handles this by searching:

1. The parent element's children (in-tree menus, Win32).
2. The main window scope.
3. **Every visible top-level window of the same process** (Qt popups).

```python
win.menu("File").item("Open...").click()
win.menu("View").item("Zoom").item("100%").click()    # nested submenu
```

If the submenu still isn't reachable (some Qt apps render menus on a
separate-process out-of-tree), fall back to keyboard accelerators:

```python
win.focus()
Keyboard.press("%fo")   # Alt+F, O for File → Open
```

## UIA limitations — when to use the Qt agent

The following are opaque to UIA. They are handled by the Qt agent DLL,
which ships with the library and is injected automatically on first use
(see `docs/architecture/qt-agent.md`):

- **Qt Quick / QML scene graphs** are exposed as a single `Pane` in UIA with
  no descendants — use `win.qml(...)` / `app.qt_agent` instead.
- **`QGraphicsView` with custom paint** is similarly opaque — common in
  trading apps and graph editors. Use `win.graphics_view(...)`.
- **Custom `QStyleItemDelegate`-rendered cells** in `QTableView`/`QListView`
  are addressable as the parent cell under UIA; use the agent's meta-object
  introspection for their internal sub-elements.

Use `Application.uses_qt_quick()` to detect QML targets at launch time and
route those screens through the agent API instead of UIA selectors:

```python
app = desktop.launch_qt("trading_app.exe")
if app.uses_qt_quick():
    app.window(title="Trading").qml("loginButton").click()
```

## Real-world Qt apps validated

The library is exercised against these production Qt apps in
`tests/qt/test_qt_real_apps.py`:

- **AMD Radeon Software** (Qt 6) — GPU control panel.
- **AMD Ryzen Master** (Qt 6) — CPU monitoring.
- **Logitech G HUB** (Qt 5) — gaming peripheral software.

Tests are skipped automatically when the target app isn't running.

## Examples

Working demos under `examples/qt_demo/`:

- `all_widgets.py` — PySide6 (Qt 6) widget gallery with every supported control.
- `all_widgets_qt5.py` — PyQt5 (Qt 5) twin of the above.
- `qml_widgets.py` + `qml_widgets.qml` — QML controls; covered by the Qt agent.
- `charts_demo.py` — Qt Charts (`QChart`, `QLineSeries`); polymorphic agent introspection.
- `graphics_demo.py` — `QGraphicsView` with named items; Qt agent graphics walker.
- `contact_manager.py` — a small CRUD app (table, dialogs, status bar) used by the end-to-end scenario suite.

Run with `python examples/qt_demo/all_widgets.py` and inspect the layout
with `dolphin spy --pid <pid>` to see the UIA tree.

## Qt agent — injection for QML / QGraphicsView / custom widgets

For UI elements that UIA cannot see (Qt Quick, custom paint, Qt Charts,
Qt 3D, anything that rolls its own QPainter scene), dolphin ships a
small native DLL that gets injected into the AUT and exposes the live
`QObject` meta-object tree over a named-pipe JSON protocol. The agent
DLLs are bundled with the library — no separate install or build step.

### Recommended: high-level Element API

```python
from dolphin_desktop import Desktop

app = desktop.launch_qt(r'"python" "trading_app.py"')
win = app.window(title="Trading")

# QML — found by objectName, returned as QmlElement
btn = win.qml("loginButton")
btn.click()                      # synthetic Qt MouseEvent (no real cursor)
btn.set_property("enabled", False)

# QML reactive — wait until binding propagates
field = win.qml("usernameField")
field.set_text("Alice")
field.wait_for_property("text", "Alice", timeout=2.0)

# QGraphicsView — walk its scene
view = win.graphics_view(object_name="priceChart")
for item in view.items():
    print(item["type"], item["x"], item["y"])
top_hit = view.item_at(120, 80)

# Plain QObject / QWidget — via meta-object (more granular than UIA)
label = win.qt_widget(object_name="connectionStatus")
assert label.get_property("text") == "Connected"
```

### Raw agent API (advanced)

The `Application.qt_agent` property returns a `QtAgentClient` for full
JSON-RPC access — needed when you want to bypass the Element wrappers:

```python
agent = app.qt_agent  # lazy injection on first access

# Find by objectName in the QML scene graph
btn = agent.qml_find("qmlClickButton")[0]
agent.qml_click(btn["handle"])

# Read/write any Q_PROPERTY
field = agent.qml_find("qmlNameField")[0]["handle"]
agent.set_property(field, "text", "Alice")

# Invoke any Q_INVOKABLE / slot with typed args
spin = agent.find(className="QSpinBox")[0]["handle"]
agent.invoke(spin, "setValue", 42)
```

See [`docs/architecture/qt-agent.md`](../architecture/qt-agent.md) for
the full agent API, IPC protocol, and security model.

### Agent limitations

- **Anti-cheat / DRM apps** detect `CreateRemoteThread` and may close.
  There is no workaround.
- **First call latency** — agent injection takes ~50-100 ms for the
  first access. Subsequent calls on the same `Application` are cached.
- **Sandboxed apps** (UWP / packaged) cannot be injected without
  elevated AppContainer-aware tooling.
- **ABI lock** — `dolphin_qt5_agent.dll` only loads into Qt 5.x AUTs;
  `dolphin_qt6_agent.dll` only into Qt 6.x. Detection is automatic via
  `app.qt_version()`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Widgets invisible to `window.child_window(...)` | `QT_ACCESSIBILITY=1` env var not set on the child process | Use `Desktop.launch_qt("your_app.exe")` — it sets the flag automatically. If attaching to a running Qt app, relaunch it with `QT_ACCESSIBILITY=1` |
| QML items not found | Qt agent not injected | Call `app.qml("objectName")` to trigger injection, or `app.has_qt_agent()` to verify it attached without injecting (accessing the `app.qt_agent` property itself triggers injection) |
| `QtAgentInjectError: sandboxed process` | UWP / packaged app | Not currently supported. Use `ImageLocator` fallback for the affected screens |
| Buttons return `role="pane"` instead of `Button` | `QPushButton` marked `checkable=True` — Qt maps to `CheckBox` role | Use `window.check_box(name=...)` instead of `.button(...)`, or `Locator(control_type="CheckBox")` |
| Menu items disappear before click | Qt opens submenus as top-level windows; menu closes on focus loss | Chain `menu.open() → item.click()` in the same call, don't `menu.text()` in between |
| `SetCursorPos: No error message is available` on click | RDP session or background process | Use `Locator.invoke()` (UIA Invoke pattern; raises `UnsupportedPatternError` if the control doesn't support it) instead of `.click()` |
| `objectName` matches wrong widget | Qt inheritance: parent widget shares `objectName` with a child | Use the dotted-path form: `win.get_by_automation_id("MainForm.SubmitButton")` — matches the full hierarchical AutomationId |
| `graphics.item_at(x, y)` / `items()` returns nothing | `QGraphicsView` scene items don't inherit `QObject` | Only items derived from `QGraphicsObject` (not `QGraphicsItem`) publish an `objectName`. Set `.setObjectName("...")` in the item constructor |
| Qt 5 app but agent tries to load Qt 6 DLL | `Application.qt_version()` autodetect confused by mixed installs | Check what was detected with `app.qt_version()`, then bypass the property and attach explicitly: `QtAgentClient.attach(pid, "5")` |
