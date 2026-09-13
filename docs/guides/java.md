# Java Swing / AWT

Dolphin includes Java Access Bridge helpers for Swing and AWT applications on Windows.

## Requirements

- A JRE or JDK with Java Access Bridge tools.
- `JAVA_HOME` must be a trusted absolute JRE/JDK path containing
  `bin\jabswitch.exe` and `bin\windowsaccessbridge-64.dll`, or Java Access
  Bridge must already be enabled. Dolphin does not execute a bare
  `jabswitch.exe` from `PATH`.
- A 64-bit Java runtime is recommended.

## Launch

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch_java("java -jar MySwingApp.jar")
win = app.window(class_name="SunAwtFrame")
```

`launch_java()` calls `JavaAccessBridge.ensure_enabled()` before starting the process and initializes the JAB client session.

## Selectors

For Java windows, `Window.get_by_*` returns a JAB-backed locator.

```python
win.get_by_role("text", name="Username").type_text("admin")
win.get_by_role("password text", name="Password").type_text("secret")
win.get_by_role("push button", name="Sign in").click()
```

Common role strings come from Java Access Bridge `role_en_US`, for example:

| Swing control | JAB role |
| --- | --- |
| `JButton` | `push button` |
| `JTextField` | `text` |
| `JPasswordField` | `password text` |
| `JCheckBox` | `check box` |
| `JRadioButton` | `radio button` |
| `JComboBox` | `combo box` |
| `JList` | `list` |
| `JTable` | `table` |
| `JTree` | `tree` |

## Check Java Access Bridge

```python
from dolphin_desktop import JavaAccessBridge

print(JavaAccessBridge.java_home())
print(JavaAccessBridge.is_enabled())
JavaAccessBridge.ensure_enabled()
```

## Troubleshooting

- If `jabswitch.exe` or `windowsaccessbridge-64.dll` is missing, set
  `JAVA_HOME` to a trusted absolute JDK path that includes both files.
- If the tree is empty immediately after launch, increase the app startup delay or wait for idle.
- If roles differ from the table, inspect the app and use the exact role/name exposed by Java Access Bridge.
