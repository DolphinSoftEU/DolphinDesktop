# API Reference

This section uses `mkdocstrings` so signatures and docstrings are read from the current source code.

Common entry points:

| Object | Page |
| --- | --- |
| `Desktop` | [Desktop](desktop.md) |
| `Application` | [Application](application.md) |
| `Window` | [Window](window.md) |
| `Locator` | [Locator](locator.md) |
| `Keyboard` and `Mouse` | [Keyboard](keyboard.md), [Mouse](mouse.md) |
| `Clipboard`, `FileDialog`, `MessageBox` | [Clipboard](clipboard.md), [Dialogs](dialogs.md) |
| `ImageLocator` and `Screen` | [ImageLocator / Screen](image.md) |
| `JavaAccessBridge`, `ExcelApp`, `WordApp` | [Java](java.md), [Office](office.md) |
| pytest plugin and CLI | [pytest Plugin](pytest-plugin.md), [CLI Reference](cli.md) |

## Public Package API

The package exports these names through `dolphin_desktop.__all__`.

::: dolphin_desktop
    options:
      members:
        - AliasNotFoundError
        - Application
        - ApplicationError
        - Backend
        - Button
        - CDPBackend
        - CheckBox
        - Clipboard
        - ComboBox
        - Desktop
        - DolphinError
        - Edit
        - Element
        - ElementNotFoundError
        - ExcelApp
        - FileDialog
        - ImageBackend
        - ImageLocator
        - JavaAccessBridge
        - Keyboard
        - LinuxATSPIBackend
        - ListBox
        - Locator
        - MacOSAccessibilityBackend
        - Menu
        - MenuItem
        - MessageBox
        - Mouse
        - RadioButton
        - RecordedAction
        - Recorder
        - Screen
        - Tab
        - Toolbar
        - TraceSession
        - Tree
        - UIABackend
        - VideoRecorder
        - WaitTimeoutError
        - Win32Backend
        - Window
        - WindowNotFoundError
        - WordApp
        - config
        - get_logger
        - list_backends
        - register_backend
        - resolve_backend
        - selfheal_stats
        - setup_logging
        - telemetry_capture_exception
        - telemetry_enabled
        - telemetry_init
        - trace_generate_html
        - write_crash_dump
