# Backend Architecture

This page is for contributors and extension authors. Most tests should use `Desktop(backend="uia")` or `Desktop(backend="win32")` and do not need the backend registry directly.

## Runtime Path Used By Tests

The primary API path is:

```text
test code
  -> Desktop
  -> Application
  -> Window
  -> Locator
  -> pywinauto UIA or Win32 wrappers
```

## Backend capability contract

Every `Backend` publishes a `capabilities()` frozenset naming
the `Capability` members it fully supports. Runtime operations
gated on a capability check raise `UnsupportedCapabilityError`
(with a hint pointing at working alternatives) when the current
backend does not declare support.

Introspection surfaces:

* `Desktop.backend_supports(cap)` — for the currently-configured backend
* `Desktop.require_capability(cap)` — raises if missing
* `SapGui.backend_supports(cap)` / `CDPSession.backend_supports(cap)` /
  `DelphiApp.backend_supports(cap)` / `MainframeTerminal.backend_supports(cap)` /
  `OracleFormsApp.backend_supports(cap)` — per-facade
* `supported_backends(cap)` — every registered backend id that
  supports the capability
* `list_backends()` — full matrix, capabilities included

Third-party plugin backends declare `capabilities()` in their
`Backend` subclass and register through the `dolphin_desktop.backends`
entry-point group; capability queries flow through the registry
automatically.

Full guidance: [backend-capabilities.md](backend-capabilities.md).

## Two Action Paths — Input Simulation vs Programmatic

Every `Locator` (and every `JABLocator`) exposes actions along two
parallel families:

| Family                 | Dispatches                                     | Requires an input desktop | Fires hover/keystroke handlers | Fails when unsupported                       |
| ---------------------- | ---------------------------------------------- | ------------------------- | ------------------------------ | -------------------------------------------- |
| **Input simulation**   | `click()`, `type_text()`, `hover()`, `drag_to()` | Yes                       | Yes                            | Underlying `SetCursorPos`/`SendInput` error  |
| **Programmatic**       | `invoke()`, `toggle()`, `expand()`, `collapse()`, `select()`, `set_value()` | No                | No                             | `UnsupportedPatternError` — no silent fallback |

The two families exist so headless mode is usable for smoke tests
without lying to the caller about how the click was delivered. Selection of the right primitive
is a **test-author decision**, not a library fallback — the
programmatic family raises `UnsupportedPatternError` cleanly rather
than reaching for the mouse in the background.

Full guidance in [tutorials/headless-mode.md](tutorials/headless-mode.md#headless-safe-api-invoke-and-friends).

## Backend selection

`Desktop` passes its `backend` value to pywinauto. The documented values for application automation are:

| Value | Meaning |
| --- | --- |
| `uia` | Microsoft UI Automation. This is the default. |
| `win32` | Win32 HWND backend for older controls and legacy applications. |

Image matching is not selected with `Desktop(backend="image")`. Use `ImageLocator`, `Screen`, `window.image(...)`, or a locator `image_fallback` for that path.

## Registry Module

`dolphin_desktop._backend` defines an abstract `Backend` interface and registry helpers:

| Object | Purpose |
| --- | --- |
| `Backend` | Abstract interface for backend experiments and plugins |
| `UIABackend` | Wrapper implementation for UIA operations |
| `Win32Backend` | Wrapper implementation for Win32 operations |
| `ImageBackend` | Template-matching backend object used by registry experiments |
| `register_backend` | Register a backend class in-process |
| `resolve_backend` | Instantiate a backend by ID |
| `list_backends` | Return metadata shown by `dolphin info backends` |

The registry is public API, but the current `Desktop` implementation does not route normal locator actions through `resolve_backend`.

## Built-In Backend IDs

| ID | Class | Status |
| --- | --- | --- |
| `uia` | `UIABackend` | Implemented on Windows |
| `win32` | `Win32Backend` | Implemented on Windows |
| `qt` | `QtBackend` | Implemented on Windows — UIA with a Qt-aware locator wrapper |
| `image` | `ImageBackend` | Implemented when OpenCV is installed |
| `cdp` | `CDPBackend` | Marker — real surface is `CDPSession` via `Desktop.launch_electron_cdp()` and friends |
| `sap` | `SapBackend` | Marker — real surface is `SapGui` via `Desktop.sap()` |
| `delphi` | `DelphiBackend` | Marker — real surface is `DelphiApp` via `Desktop.launch_delphi()` |
| `java` | `JavaBackend` | Marker — real surface is `JavaAccessBridge` / `OracleFormsApp` via `Desktop.launch_java()` / `Desktop.launch_oracle_forms()` |
| `mainframe` | `MainframeBackend` | Marker — real surface is `MainframeTerminal` via `Desktop.mainframe()` |
| `macos` | `MacOSAccessibilityBackend` | Reserved stub, not implemented |
| `linux` | `LinuxATSPIBackend` | Reserved stub, not implemented |

Marker backends appear in `list_backends()` and publish their stack's capability set, but direct calls on them raise `UnsupportedCapabilityError` with a hint pointing at the matching facade. The reserved stubs (`macos`, `linux`) return `is_available() == False` and also raise `UnsupportedCapabilityError` for backend methods.

## Listing Backends

```bash
dolphin info backends
```

The command prints backend ID, target platform, availability on the current machine, source, and description.

## Registering A Backend In Process

```python
from dolphin_desktop import Backend, register_backend


@register_backend
class MyBackend(Backend):
    id = "my_backend"
    platform = "windows"

    def find_element(self, parent, criteria):
        ...

    def click(self, element, *, button="left"):
        ...

    def type_text(self, element, text):
        ...

    def get_tree(self, root, *, depth=None):
        ...

    def screenshot(self, element=None):
        ...
```

## Registering Through Package Metadata

Third-party packages can expose backends through the `dolphin_desktop.backends` entry-point group:

```toml
[project.entry-points."dolphin_desktop.backends"]
my_backend = "my_backend_pkg.my_backend:MyBackend"
```

Installed entry points are discovered by `list_backends()` and `resolve_backend()`.
