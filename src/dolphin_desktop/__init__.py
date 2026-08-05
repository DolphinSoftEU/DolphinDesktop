"""dolphin — test-automation library for Windows desktop applications.

This package is Windows-only in practice — every automation backend
(UIA, pywinauto Win32, Java Access Bridge, ws3270 subprocess, HLLAPI
ctypes, SAP GUI COM) requires a Windows host. Importing the package on
macOS or Linux emits a warning but does not raise: a subset of helpers
(mainframe TN5250, EBCDIC translation, `path_*` wrappers, etc.) work
cross-platform, so we let the caller decide when the failure surfaces.
"""

import platform as _platform
import warnings as _warnings

if _platform.system() != "Windows":
    _warnings.warn(
        "dolphin_desktop is designed for Windows. Most backends (UIA, "
        "Win32, JAB, ws3270, HLLAPI, SAP GUI) will not work on "
        f"{_platform.system()!r}. See docs/getting-started.md for the "
        "supported-platform table.",
        RuntimeWarning,
        stacklevel=2,
    )

from . import objects, spy
from ._application import Application
from ._backend import (
    Backend,
    CDPBackend,
    DelphiBackend,
    ImageBackend,
    JavaBackend,
    LinuxATSPIBackend,
    MacOSAccessibilityBackend,
    MainframeBackend,
    QtBackend,
    SapBackend,
    UIABackend,
    Win32Backend,
    list_backends,
    supported_backends,
)
from ._backend import (
    register as register_backend,
)
from ._backend import (
    resolve as resolve_backend,
)
from ._capabilities import (
    ALL_CAPABILITIES,
    IMAGE_ONLY,
    STANDARD_ACCESSIBILITY,
    Capability,
)
from ._cdp import (
    CDPDownload,
    CDPFrameLocator,
    CDPLocator,
    CDPRequest,
    CDPRoute,
    CDPSession,
    CDPStalePageError,
    cdp_install_hint,
    is_cdp_available,
)
from ._clipboard import Clipboard
from ._config import config
from ._crash import write_crash_dump
from ._delphi import DelphiApp, DelphiComponent, DelphiError, DelphiForm
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
    AmbiguousMatchError,
    ApplicationError,
    DolphinError,
    ElementNotFoundError,
    UnsupportedCapabilityError,
    UnsupportedPatternError,
    WaitTimeoutError,
    WindowNotFoundError,
)
from ._helpers import (
    add_import_path,
    b64decode,
    b64encode,
    counter,
    dirname,
    env_var,
    find_pid_by_image_name,
    http_ok,
    is_windows,
    monotonic,
    path_basename,
    path_exists,
    path_join,
    python_executable,
    remove_file,
    running_processes,
    sleep,
    start_thread,
    tcp_reachable,
    temp_file,
    tempdir,
    which,
)
from ._image import ImageLocator, Screen
from ._java import JavaAccessBridge
from ._keyboard import Keyboard
from ._locator import Locator
from ._logging import get_logger, setup_logging
from ._mainframe import (
    AID,
    FieldInfo,
    MainframeError,
    MainframeTerminal,
    TerminalField,
    TerminalScreen,
)
from ._mouse import Mouse
from ._office import ExcelApp, WordApp
from ._oracle_forms import (
    OracleFormsApp,
    OracleFormsBlock,
    OracleFormsError,
    OracleFormsItem,
    OracleFormsKey,
    OracleFormsLov,
    OracleFormsMenu,
    OracleFormsWindow,
)
from ._qt_elements import (
    GraphicsViewElement,
    QmlElement,
    WidgetElement,
)
from ._qt_inject import (
    QtAgentClient,
    QtAgentInjectError,
    QtAgentRpcError,
    QtAgentTimeoutError,
)
from ._recorder import RecordedAction, Recorder
from ._sap import SapConnection, SapGui, SapLocator, SapSession, Stopwatch
from ._selfheal import selfheal_stats
from ._telemetry import capture_exception as telemetry_capture_exception
from ._telemetry import init as telemetry_init
from ._telemetry import is_enabled as telemetry_enabled
from ._trace import TraceSession
from ._trace import generate_html as trace_generate_html
from ._video import VideoRecorder
from ._window import Window

__all__ = [
    "AID",
    "ALL_CAPABILITIES",
    "IMAGE_ONLY",
    "STANDARD_ACCESSIBILITY",
    "AliasNotFoundError",
    "AmbiguousMatchError",
    "Application",
    "ApplicationError",
    "Backend",
    "Button",
    "CDPBackend",
    "CDPDownload",
    "CDPFrameLocator",
    "CDPLocator",
    "CDPRequest",
    "CDPRoute",
    "CDPSession",
    "CDPStalePageError",
    "Capability",
    "CheckBox",
    "Clipboard",
    "ComboBox",
    "DelphiApp",
    "DelphiBackend",
    "DelphiComponent",
    "DelphiError",
    "DelphiForm",
    "Desktop",
    "DolphinError",
    "Edit",
    "Element",
    "ElementNotFoundError",
    "ExcelApp",
    "FieldInfo",
    "FileDialog",
    "GraphicsViewElement",
    "ImageBackend",
    "ImageLocator",
    "JavaAccessBridge",
    "JavaBackend",
    "Keyboard",
    "LinuxATSPIBackend",
    "ListBox",
    "Locator",
    "MacOSAccessibilityBackend",
    "MainframeBackend",
    "MainframeError",
    "MainframeTerminal",
    "Menu",
    "MenuItem",
    "MessageBox",
    "Mouse",
    "OracleFormsApp",
    "OracleFormsBlock",
    "OracleFormsError",
    "OracleFormsItem",
    "OracleFormsKey",
    "OracleFormsLov",
    "OracleFormsMenu",
    "OracleFormsWindow",
    "QmlElement",
    "QtAgentClient",
    "QtAgentInjectError",
    "QtAgentRpcError",
    "QtAgentTimeoutError",
    "QtBackend",
    "RadioButton",
    "RecordedAction",
    "Recorder",
    "SapBackend",
    "SapConnection",
    "SapGui",
    "SapLocator",
    "SapSession",
    "Screen",
    "Stopwatch",
    "Tab",
    "TerminalField",
    "TerminalScreen",
    "Toolbar",
    "TraceSession",
    "Tree",
    "UIABackend",
    "UnsupportedCapabilityError",
    "UnsupportedPatternError",
    "VideoRecorder",
    "WaitTimeoutError",
    "WidgetElement",
    "Win32Backend",
    "Window",
    "WindowNotFoundError",
    "WordApp",
    "add_import_path",
    "b64decode",
    "b64encode",
    "cdp_install_hint",
    "config",
    "counter",
    "dirname",
    "env_var",
    "find_pid_by_image_name",
    "get_logger",
    "http_ok",
    "is_cdp_available",
    "is_windows",
    "list_backends",
    "monotonic",
    "objects",
    "path_basename",
    "path_exists",
    "path_join",
    "python_executable",
    "register_backend",
    "remove_file",
    "resolve_backend",
    "running_processes",
    "selfheal_stats",
    "setup_logging",
    "sleep",
    "spy",
    "start_thread",
    "supported_backends",
    "tcp_reachable",
    "telemetry_capture_exception",
    "telemetry_enabled",
    "telemetry_init",
    "temp_file",
    "tempdir",
    "trace_generate_html",
    "which",
    "write_crash_dump",
]

__version__ = "0.2.0"
