"""dolphin — Playwright-like testing library for Windows desktop applications."""

from . import objects, spy
from ._application import Application
from ._backend import (
    Backend,
    CDPBackend,
    ImageBackend,
    LinuxATSPIBackend,
    MacOSAccessibilityBackend,
    UIABackend,
    Win32Backend,
    list_backends,
)
from ._backend import (
    register as register_backend,
)
from ._backend import (
    resolve as resolve_backend,
)
from ._clipboard import Clipboard
from ._config import config
from ._crash import write_crash_dump
from ._desktop import Desktop
from ._dialogs import FileDialog, MessageBox
from ._element import (
    Button,
    CheckBox,
    ComboBox,
    Edit,
    Element,
    ListBox,
    Menu,
    MenuItem,
    RadioButton,
    Tab,
    Toolbar,
    Tree,
)
from ._exceptions import (
    AliasNotFoundError,
    ApplicationError,
    DolphinError,
    ElementNotFoundError,
    WaitTimeoutError,
    WindowNotFoundError,
)
from ._image import ImageLocator, Screen
from ._java import JavaAccessBridge
from ._keyboard import Keyboard
from ._locator import Locator
from ._logging import get_logger, setup_logging
from ._mouse import Mouse
from ._office import ExcelApp, WordApp
from ._recorder import RecordedAction, Recorder
from ._selfheal import selfheal_stats
from ._telemetry import capture_exception as telemetry_capture_exception
from ._telemetry import init as telemetry_init
from ._telemetry import is_enabled as telemetry_enabled
from ._trace import TraceSession
from ._trace import generate_html as trace_generate_html
from ._video import VideoRecorder
from ._window import Window

__all__ = [
    "AliasNotFoundError",
    "Application",
    "ApplicationError",
    "Backend",
    "Button",
    "CDPBackend",
    "CheckBox",
    "Clipboard",
    "ComboBox",
    "Desktop",
    "DolphinError",
    "Edit",
    "Element",
    "ElementNotFoundError",
    "ExcelApp",
    "FileDialog",
    "ImageBackend",
    "ImageLocator",
    "JavaAccessBridge",
    "Keyboard",
    "LinuxATSPIBackend",
    "ListBox",
    "Locator",
    "MacOSAccessibilityBackend",
    "Menu",
    "MenuItem",
    "MessageBox",
    "Mouse",
    "RadioButton",
    "RecordedAction",
    "Recorder",
    "Screen",
    "Tab",
    "Toolbar",
    "TraceSession",
    "Tree",
    "UIABackend",
    "VideoRecorder",
    "WaitTimeoutError",
    "Win32Backend",
    "Window",
    "WindowNotFoundError",
    "WordApp",
    "config",
    "get_logger",
    "list_backends",
    "objects",
    "register_backend",
    "resolve_backend",
    "selfheal_stats",
    "setup_logging",
    "spy",
    "telemetry_capture_exception",
    "telemetry_enabled",
    "telemetry_init",
    "trace_generate_html",
    "write_crash_dump",
]

__version__ = "0.1.0"
