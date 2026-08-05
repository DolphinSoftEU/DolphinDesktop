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
| `JavaAccessBridge`, `ExcelApp`, `WordApp`, `SapGui` | [Java](java.md), [Office](office.md), [SAP](sap.md) |
| **CDP** (Electron / CEF): `CDPSession`, `CDPLocator`, `CDPFrameLocator`, `CDPRequest`, `CDPRoute`, `CDPDownload` | [CDP](cdp.md) |
| **Mainframe**: `MainframeTerminal`, `TerminalScreen`, `TerminalField`, `FieldInfo`, `AID` | [Mainframe](mainframe.md) |
| **Oracle Forms**: `OracleFormsApp`, `OracleFormsKey`, `OracleFormsBlock`, `OracleFormsItem`, `OracleFormsMenu`, `OracleFormsLov` | [Oracle Forms](oracle-forms.md) |
| **Delphi / VCL**: `DelphiApp`, `DelphiForm`, `DelphiComponent`, `DelphiError` | [Delphi](delphi.md) |
| Playwright-shaped facade | [Playwright compat](playwright-compat.md) |
| pytest plugin and CLI | [pytest Plugin](pytest-plugin.md), [CLI Reference](cli.md) |

## Public Package API

The package exports these names through `dolphin_desktop.__all__`.

::: dolphin_desktop
    options:
      members:
        - AID
        - ALL_CAPABILITIES
        - AliasNotFoundError
        - Application
        - ApplicationError
        - Backend
        - Button
        - CDPBackend
        - CDPDownload
        - CDPFrameLocator
        - CDPLocator
        - CDPRequest
        - CDPRoute
        - CDPSession
        - CDPStalePageError
        - Capability
        - CheckBox
        - Clipboard
        - ComboBox
        - DelphiApp
        - DelphiBackend
        - DelphiComponent
        - DelphiError
        - DelphiForm
        - Desktop
        - DolphinError
        - Edit
        - Element
        - ElementNotFoundError
        - ExcelApp
        - FieldInfo
        - FileDialog
        - GraphicsViewElement
        - IMAGE_ONLY
        - ImageBackend
        - ImageLocator
        - JavaAccessBridge
        - JavaBackend
        - Keyboard
        - LinuxATSPIBackend
        - ListBox
        - Locator
        - MacOSAccessibilityBackend
        - MainframeBackend
        - MainframeError
        - MainframeTerminal
        - Menu
        - MenuItem
        - MessageBox
        - Mouse
        - OracleFormsApp
        - OracleFormsBlock
        - OracleFormsError
        - OracleFormsItem
        - OracleFormsKey
        - OracleFormsLov
        - OracleFormsMenu
        - OracleFormsWindow
        - QmlElement
        - QtAgentClient
        - QtAgentInjectError
        - QtAgentRpcError
        - QtAgentTimeoutError
        - QtBackend
        - RadioButton
        - RecordedAction
        - Recorder
        - STANDARD_ACCESSIBILITY
        - SapBackend
        - SapConnection
        - SapGui
        - SapLocator
        - SapSession
        - Screen
        - Stopwatch
        - Tab
        - TerminalField
        - TerminalScreen
        - Toolbar
        - TraceSession
        - Tree
        - UIABackend
        - UnsupportedCapabilityError
        - UnsupportedPatternError
        - VideoRecorder
        - WaitTimeoutError
        - WidgetElement
        - Win32Backend
        - Window
        - WindowNotFoundError
        - WordApp
        - add_import_path
        - b64decode
        - b64encode
        - cdp_install_hint
        - config
        - counter
        - dirname
        - env_var
        - find_pid_by_image_name
        - get_logger
        - http_ok
        - is_cdp_available
        - is_windows
        - list_backends
        - monotonic
        - path_basename
        - path_exists
        - path_join
        - python_executable
        - register_backend
        - remove_file
        - resolve_backend
        - running_processes
        - selfheal_stats
        - setup_logging
        - sleep
        - start_thread
        - supported_backends
        - tcp_reachable
        - telemetry_capture_exception
        - telemetry_enabled
        - telemetry_init
        - temp_file
        - tempdir
        - trace_generate_html
        - which
        - write_crash_dump

## `dolphin_desktop.spy` — introspection

Read-only inspection of a live UI, used by the `dolphin spy` CLI and by
external tooling that needs a machine-readable snapshot. `inspect()` and
`sap_inspect()` return JSON-serializable dictionaries carrying
`schema_version`, so a consumer can detect format changes.

::: dolphin_desktop.spy
    options:
      members:
        - SCHEMA_VERSION
        - inspect
        - sap_inspect
        - pick
        - sap_pick
        - format_tree
        - format_sap_tree

## `dolphin_desktop.objects` — object repository

Selector aliases loaded from YAML, so page objects can refer to an
alias instead of repeating criteria — window aliases resolve via
`app.window("alias")` and their nested `children` via
`win.element("child_alias")`. See
[Object Repository](../tutorials/object-repository.md) for the workflow.

::: dolphin_desktop.objects
    options:
      members:
        - ObjectRepository
        - ObjectEntry
        - load
        - discover
        - available
        - clear
        - enable_watch
        - disable_watch
