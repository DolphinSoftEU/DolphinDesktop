"""Backend plug-in architecture for dolphin_desktop.

Every automation backend (UIA, Win32, Image, and future platform backends) is
a subclass of :class:`Backend`.  Backends register themselves in the
module-level :data:`_REGISTRY` dict, and third-party packages can inject
additional backends via the ``dolphin_desktop.backends`` entry-point group.

Typical flow::

    from dolphin_desktop._backend import resolve, list_backends

    backend = resolve("uia")        # UIABackend instance
    backend = resolve("auto")       # auto-detected best match
    backends = list_backends()      # list of all registered backends
"""

from __future__ import annotations

import abc
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class Backend(abc.ABC):
    """Abstract base class for dolphin automation backends.

    Subclasses must set :attr:`id` (unique string key) and :attr:`platform`
    at class level, and implement all abstract methods.

    Third-party packages register new backends via the
    ``dolphin_desktop.backends`` entry-point group — each entry point must
    point to a :class:`Backend` subclass.
    """

    #: Unique string identifier used in ``Desktop(backend="...")`` and the registry.
    id: str
    #: Target platform: ``"windows"``, ``"macos"``, ``"linux"``, or ``"any"``.
    platform: str

    @abc.abstractmethod
    def find_element(self, parent: Any, criteria: dict[str, Any]) -> Any:
        """Locate a single UI element matching *criteria* under *parent*.

        Parameters
        ----------
        parent:
            Root node to search under (type is backend-specific).
        criteria:
            Backend-specific search criteria (e.g. ``{"control_type": "Button"}``).

        Returns
        -------
        Any
            An opaque element handle understood by the other backend methods.

        Raises
        ------
        dolphin_desktop.ElementNotFoundError
            If no matching element is found within the configured timeout.
        """

    @abc.abstractmethod
    def click(self, element: Any, *, button: str = "left") -> None:
        """Perform a mouse click on *element*.

        Parameters
        ----------
        element:
            Element handle returned by :meth:`find_element`.
        button:
            Mouse button: ``"left"`` (default), ``"right"``, or ``"middle"``.
        """

    @abc.abstractmethod
    def type_text(self, element: Any, text: str) -> None:
        """Type *text* into *element*.

        Parameters
        ----------
        element:
            Element handle returned by :meth:`find_element`.
        text:
            Text to type (sent as keyboard events).
        """

    @abc.abstractmethod
    def get_tree(self, root: Any, *, depth: int | None = None) -> dict[str, Any]:
        """Return the accessibility/element tree rooted at *root* as a dict.

        Parameters
        ----------
        root:
            Root node to start from (type is backend-specific).
        depth:
            Maximum depth to traverse; ``None`` means unlimited.

        Returns
        -------
        dict
            Nested dict with keys ``"name"``, ``"role"``, ``"children"``, etc.
        """

    @abc.abstractmethod
    def screenshot(self, element: Any | None = None) -> Any:
        """Capture a screenshot.

        Parameters
        ----------
        element:
            Element to capture, or ``None`` for the full screen.

        Returns
        -------
        PIL.Image.Image
            Screenshot image.
        """

    def is_available(self) -> bool:
        """Return ``True`` if this backend can run on the current system.

        The default implementation returns ``True``; subclasses that depend on
        optional packages or specific platforms should override this.
        """
        return True

    def description(self) -> str:
        """One-line human-readable description (shown by ``dolphin info backends``)."""
        doc = type(self).__doc__
        return doc.split("\n")[0].strip() if doc else ""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.id!r})"


# ---------------------------------------------------------------------------
# Windows — concrete backends (MVP)
# ---------------------------------------------------------------------------


class UIABackend(Backend):
    """Microsoft UI Automation - default backend for modern Windows apps."""

    id = "uia"
    platform = "windows"

    def find_element(self, parent: Any, criteria: dict[str, Any]) -> Any:
        return parent.child_window(**criteria)

    def click(self, element: Any, *, button: str = "left") -> None:
        if button == "right":
            element.right_click_input()
        elif button == "middle":
            element.click_input(button="middle")
        else:
            element.click_input()

    def type_text(self, element: Any, text: str) -> None:
        element.type_keys(text, with_spaces=True)

    def get_tree(self, root: Any, *, depth: int | None = None) -> dict[str, Any]:
        def _node(el: Any, remaining: int | None) -> dict[str, Any]:
            try:
                info = el.element_info
                node: dict[str, Any] = {
                    "name": info.name,
                    "role": info.control_type,
                    "class": info.class_name,
                    "children": [],
                }
            except Exception:
                return {"name": "", "role": "unknown", "class": "", "children": []}
            if remaining is None or remaining > 0:
                try:
                    next_rem = None if remaining is None else remaining - 1
                    node["children"] = [_node(c, next_rem) for c in el.children()]
                except Exception:
                    pass
            return node

        return _node(root, depth)

    def screenshot(self, element: Any | None = None) -> Any:
        if element is None:
            from PIL import ImageGrab

            return ImageGrab.grab()
        return element.capture_as_image()

    def is_available(self) -> bool:
        try:
            import pywinauto  # noqa: F401

            return sys.platform == "win32"
        except ImportError:
            return False


class Win32Backend(Backend):
    """Win32 HWND backend - best for legacy apps (MFC, VB6, Delphi/VCL)."""

    id = "win32"
    platform = "windows"

    def find_element(self, parent: Any, criteria: dict[str, Any]) -> Any:
        return parent.child_window(**criteria)

    def click(self, element: Any, *, button: str = "left") -> None:
        if button == "right":
            element.right_click_input()
        elif button == "middle":
            element.click_input(button="middle")
        else:
            element.click_input()

    def type_text(self, element: Any, text: str) -> None:
        element.type_keys(text, with_spaces=True)

    def get_tree(self, root: Any, *, depth: int | None = None) -> dict[str, Any]:
        def _node(el: Any, remaining: int | None) -> dict[str, Any]:
            try:
                node: dict[str, Any] = {
                    "name": el.window_text(),
                    "role": el.friendly_class_name(),
                    "class": el.class_name(),
                    "children": [],
                }
            except Exception:
                return {"name": "", "role": "unknown", "class": "", "children": []}
            if remaining is None or remaining > 0:
                try:
                    next_rem = None if remaining is None else remaining - 1
                    node["children"] = [_node(c, next_rem) for c in el.children()]
                except Exception:
                    pass
            return node

        return _node(root, depth)

    def screenshot(self, element: Any | None = None) -> Any:
        if element is None:
            from PIL import ImageGrab

            return ImageGrab.grab()
        return element.capture_as_image()

    def is_available(self) -> bool:
        try:
            import pywinauto  # noqa: F401

            return sys.platform == "win32"
        except ImportError:
            return False


class ImageBackend(Backend):
    """Image / template-matching backend - pixel-level fallback (requires [vision])."""

    id = "image"
    platform = "any"

    def find_element(self, parent: Any, criteria: dict[str, Any]) -> Any:
        from ._image import ImageLocator

        template = criteria.get("template")
        if not template:
            raise ValueError("ImageBackend.find_element requires criteria['template']")
        return ImageLocator(template, threshold=criteria.get("threshold", 0.8))

    def click(self, element: Any, *, button: str = "left") -> None:
        element.click()

    def type_text(self, element: Any, text: str) -> None:
        element.click()
        from ._keyboard import Keyboard

        Keyboard.type(text)

    def get_tree(self, root: Any, *, depth: int | None = None) -> dict[str, Any]:
        return {"name": "screen", "role": "image", "class": "", "children": []}

    def screenshot(self, element: Any | None = None) -> Any:
        from ._image import Screen

        return Screen.screenshot()

    def is_available(self) -> bool:
        try:
            import cv2  # noqa: F401

            return True
        except ImportError:
            return False


# ---------------------------------------------------------------------------
# Stub backends — reserved for future platforms
# ---------------------------------------------------------------------------

_STUB_MSG = (
    "{name} is a reserved stub for a future dolphin platform backend.  "
    "It will be implemented in a later version.  "
    "See https://github.com/dolphinsoft/dolphin for the roadmap."
)


class MacOSAccessibilityBackend(Backend):
    """macOS Accessibility API backend - reserved, not yet implemented."""

    id = "macos"
    platform = "macos"

    def _raise(self) -> None:
        raise NotImplementedError(_STUB_MSG.format(name="MacOSAccessibilityBackend"))

    def find_element(self, parent: Any, criteria: dict[str, Any]) -> Any:
        self._raise()

    def click(self, element: Any, *, button: str = "left") -> None:
        self._raise()

    def type_text(self, element: Any, text: str) -> None:
        self._raise()

    def get_tree(self, root: Any, *, depth: int | None = None) -> dict[str, Any]:
        self._raise()
        return {}  # unreachable — satisfies type checker

    def screenshot(self, element: Any | None = None) -> Any:
        self._raise()

    def is_available(self) -> bool:
        return False


class LinuxATSPIBackend(Backend):
    """Linux AT-SPI2 accessibility backend - reserved, not yet implemented."""

    id = "linux"
    platform = "linux"

    def _raise(self) -> None:
        raise NotImplementedError(_STUB_MSG.format(name="LinuxATSPIBackend"))

    def find_element(self, parent: Any, criteria: dict[str, Any]) -> Any:
        self._raise()

    def click(self, element: Any, *, button: str = "left") -> None:
        self._raise()

    def type_text(self, element: Any, text: str) -> None:
        self._raise()

    def get_tree(self, root: Any, *, depth: int | None = None) -> dict[str, Any]:
        self._raise()
        return {}

    def screenshot(self, element: Any | None = None) -> Any:
        self._raise()

    def is_available(self) -> bool:
        return False


class CDPBackend(Backend):
    """Chrome DevTools Protocol backend - reserved, not yet implemented."""

    id = "cdp"
    platform = "any"

    def _raise(self) -> None:
        raise NotImplementedError(_STUB_MSG.format(name="CDPBackend"))

    def find_element(self, parent: Any, criteria: dict[str, Any]) -> Any:
        self._raise()

    def click(self, element: Any, *, button: str = "left") -> None:
        self._raise()

    def type_text(self, element: Any, text: str) -> None:
        self._raise()

    def get_tree(self, root: Any, *, depth: int | None = None) -> dict[str, Any]:
        self._raise()
        return {}

    def screenshot(self, element: Any | None = None) -> Any:
        self._raise()

    def is_available(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: Built-in backend classes (registered at module load time).
_BUILT_IN: list[type[Backend]] = [
    UIABackend,
    Win32Backend,
    ImageBackend,
    MacOSAccessibilityBackend,
    LinuxATSPIBackend,
    CDPBackend,
]

_REGISTRY: dict[str, type[Backend]] = {cls.id: cls for cls in _BUILT_IN}
_plugins_loaded = False


def _load_plugins() -> None:
    """Discover and register backends from the ``dolphin_desktop.backends`` entry-point group."""
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True

    try:
        from importlib.metadata import entry_points

        eps = entry_points(group="dolphin_desktop.backends")
    except Exception:
        return

    for ep in eps:
        # Skip built-ins re-registered via pyproject.toml entry points.
        if ep.name in _REGISTRY:
            continue
        try:
            cls = ep.load()
        except Exception:
            continue
        if isinstance(cls, type) and issubclass(cls, Backend):
            _REGISTRY[ep.name] = cls


def register(cls: type[Backend]) -> type[Backend]:
    """Register a :class:`Backend` subclass in the global registry.

    This is the programmatic alternative to the ``dolphin_desktop.backends``
    entry-point mechanism — useful for in-process backend registration in tests
    or extension packages that don't install an entry point.

    Returns *cls* unchanged so it can be used as a decorator::

        @dolphin_desktop._backend.register
        class MyBackend(Backend):
            id = "my_backend"
            ...
    """
    _REGISTRY[cls.id] = cls
    return cls


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve(backend_id: str) -> Backend:
    """Return an instantiated :class:`Backend` for *backend_id*.

    Parameters
    ----------
    backend_id:
        One of the registered backend IDs (``"uia"``, ``"win32"``,
        ``"image"``, or any plugin-registered ID), or ``"auto"`` to let
        dolphin pick the best available backend for the current platform.

    Raises
    ------
    ValueError
        If *backend_id* is not found in the registry.
    """
    _load_plugins()

    if backend_id == "auto":
        return _auto_detect()

    if backend_id not in _REGISTRY:
        available = sorted(_REGISTRY)
        raise ValueError(
            f"Unknown backend {backend_id!r}.  "
            f"Available backends: {available}.  "
            "Third-party backends can be added via the "
            "'dolphin_desktop.backends' entry-point group."
        )

    return _REGISTRY[backend_id]()


def _auto_detect() -> Backend:
    """Return the best available backend for the current platform."""
    if sys.platform == "win32":
        return UIABackend()
    if sys.platform == "darwin":
        return MacOSAccessibilityBackend()
    if sys.platform.startswith("linux"):
        return LinuxATSPIBackend()
    # Fallback
    return ImageBackend()


def list_backends() -> list[dict[str, Any]]:
    """Return info dicts for all registered backends.

    Each dict has keys: ``id``, ``platform``, ``class``, ``available``,
    ``description``, ``source`` (``"built-in"`` or entry-point value).

    Plugin backends from ``dolphin_desktop.backends`` entry points are
    included automatically.
    """
    _load_plugins()

    built_in_ids = {cls.id for cls in _BUILT_IN}

    result = []
    for bid, cls in sorted(_REGISTRY.items()):
        instance = cls()
        result.append(
            {
                "id": bid,
                "platform": cls.platform,
                "class": f"{cls.__module__}.{cls.__qualname__}",
                "available": instance.is_available(),
                "description": instance.description(),
                "source": "built-in" if bid in built_in_ids else "plugin",
            }
        )
    return result
