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
| `image` | `ImageBackend` | Implemented when OpenCV is installed |
| `macos` | `MacOSAccessibilityBackend` | Reserved stub, not implemented |
| `linux` | `LinuxATSPIBackend` | Reserved stub, not implemented |
| `cdp` | `CDPBackend` | Reserved stub, not implemented |

The reserved stubs return `is_available() == False` and raise `NotImplementedError` for backend methods.

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
