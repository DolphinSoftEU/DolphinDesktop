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
import threading
from typing import Any

from ._capabilities import (
    IMAGE_ONLY,
    STANDARD_ACCESSIBILITY,
    Capability,
)
from ._exceptions import UnsupportedCapabilityError
from ._helpers import _escape_keys


def _check_capability_arg(value: Any, caller: str) -> None:
    """Raise TypeError when *value* is not a :class:`Capability` member.

    Guards public API entry points that take a Capability argument so
    callers get a clear "wrong type" message instead of a silent
    ``False`` (for :meth:`Backend.supports`) or an ``AttributeError``
    from ``.name`` access (for :meth:`Backend.require_capability`).
    """
    if isinstance(value, Capability):
        return
    if isinstance(value, str):
        # Common typos worth hinting explicitly:
        #   * value string  ("invoke")  -> Capability("invoke")     -> INVOKE
        #   * member name   ("INVOKE")  -> Capability[value]        -> INVOKE
        # Try value-lookup first (the canonical form the enum casts
        # from), then name-lookup (what the caller probably typed
        # when they wrote ``"INVOKE"`` from muscle memory).
        matched: Capability | None = None
        try:
            matched = Capability(value)
        except ValueError:
            try:
                matched = Capability[value]
            except KeyError:
                matched = None
        if matched is not None:
            raise TypeError(
                f"{caller} expected a Capability member, got string {value!r}. "
                f"Pass Capability.{matched.name} (import Capability from dolphin_desktop)."
            )
    raise TypeError(
        f"{caller} expected a Capability member, got {type(value).__name__}: "
        f"{value!r}. Import Capability from dolphin_desktop and pass a member "
        f"like Capability.INVOKE."
    )


# Abstract base


class Backend(abc.ABC):
    """Abstract base class for dolphin automation backends.

    Subclasses must set :attr:`id` (unique string key) and :attr:`platform`
    at class level, and implement all abstract methods.

    Third-party packages register new backends via the
    ``dolphin_desktop.backends`` entry-point group — each entry point must
    point to a :class:`Backend` subclass.

    Every backend must also publish a :meth:`capabilities` set declaring
    which :class:`~dolphin_desktop.Capability` members it supports.
    Operations gated on a capability check will raise
    :class:`~dolphin_desktop.UnsupportedCapabilityError` (with a hint
    pointing at an alternative backend) instead of silently failing on
    an unsupported call.
    """

    #: Unique string identifier used in ``Desktop(backend="...")`` and the registry.
    id: str
    #: Target platform: ``"windows"``, ``"macos"``, ``"linux"``, or ``"any"``.
    platform: str

    def __init__(self) -> None:
        # Concrete subclasses must set both class-level attributes so
        # every call site that reads self.id / self.platform (registry
        # keying, require_capability error message, list_backends
        # output) has a real value to work with. Detect the omission
        # here rather than let it surface as a stray AttributeError
        # deep in the call stack. Require STR specifically, not just
        # truthy — an accidental ``id = 5`` (int) would key the
        # registry on an unhashable-lookalike that would confuse every
        # subsequent registry operation and the JSON output of
        # list_backends().
        cls = type(self)
        bid = getattr(cls, "id", None)
        if not isinstance(bid, str) or not bid:
            raise TypeError(
                f"{cls.__name__} must set a class attribute "
                f"``id: str`` (e.g. id = 'my_backend'). "
                f"Got {type(bid).__name__}: {bid!r}. Used as the "
                f"registry key and in error messages."
            )
        plat = getattr(cls, "platform", None)
        if not isinstance(plat, str) or not plat:
            raise TypeError(
                f"{cls.__name__} must set a class attribute "
                f"``platform: str`` ('windows', 'macos', 'linux', or 'any'). "
                f"Got {type(plat).__name__}: {plat!r}."
            )

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

    # Capability contract

    def capabilities(self) -> frozenset[Capability]:
        """Return the set of :class:`Capability` members this backend fully supports.

        Default: empty set — a backend that overrides nothing gets
        clean ``UnsupportedCapabilityError`` on every operation gated
        by :meth:`require_capability`. Concrete backends MUST override
        to publish their real capability set.

        Third-party plugin backends declare here what they can do. The
        declared set is the source of truth for :meth:`supports` /
        :meth:`require_capability` and for
        :meth:`~dolphin_desktop.Desktop.backend_supports`.
        """
        return frozenset()

    def supports(self, capability: Capability) -> bool:
        """Return True iff *capability* is in :meth:`capabilities`.

        Rejects non-Capability arguments with a :class:`TypeError` —
        silently returning ``False`` for a string or ``None`` would
        hide typos and keyword mixups from the caller.
        """
        _check_capability_arg(capability, "supports")
        caps = self.capabilities()
        if caps is None:
            # Plugin author bug — surface with a helpful message rather
            # than the raw "argument of type 'NoneType' is not iterable"
            # from the `in` operator.
            raise TypeError(
                f"{type(self).__name__}.capabilities() returned None; "
                f"return a frozenset[Capability] (empty frozenset() means "
                f"'no operations supported')."
            )
        return capability in caps

    def require_capability(self, capability: Capability) -> None:
        """Raise :class:`UnsupportedCapabilityError` when *capability* is missing.

        The error message names the backend and the missing capability,
        and its ``hint=`` argument lists any built-in backend whose
        declared capability set includes the requested operation — so
        the caller sees the working alternative right next to the
        failure.

        Rejects non-Capability arguments with a :class:`TypeError` —
        see :meth:`supports`.
        """
        _check_capability_arg(capability, "require_capability")
        if self.supports(capability):
            return
        alternatives = _find_supporting_backends(capability, exclude_id=self.id)
        if alternatives:
            hint = (
                "other backends that support this: "
                + ", ".join(sorted(alternatives))
                + " — pass one via Desktop(backend='…')"
            )
        else:
            hint = (
                "no other registered backend supports this operation; "
                "either implement it in a plugin backend or use a different "
                "stack facade (e.g. Desktop.sap() / .mainframe() / .cdp())"
            )
        raise UnsupportedCapabilityError(
            f"backend {self.id!r} does not support Capability.{capability.name}",
            hint=hint,
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.id!r})"


# Windows — concrete backends


class UIABackend(Backend):
    """Microsoft UI Automation - default backend for modern Windows apps."""

    id = "uia"
    platform = "windows"

    def capabilities(self) -> frozenset[Capability]:
        # UIA delivers the full accessibility stack: tree walking,
        # every input-simulation verb, every programmatic pattern,
        # and pixel capture. Screenshot added on top of the standard
        # accessibility set.
        return STANDARD_ACCESSIBILITY | frozenset(
            {
                Capability.SCREENSHOT,
                Capability.DRAG,
                Capability.SCROLL,
            }
        )

    def find_element(self, parent: Any, criteria: dict[str, Any]) -> Any:
        self.require_capability(Capability.LOCATE)
        return parent.child_window(**criteria)

    def click(self, element: Any, *, button: str = "left") -> None:
        # Route the capability guard by the button variant — a backend
        # that publishes CLICK but not RIGHT_CLICK must NOT silently
        # dispatch a right-click. Middle-click has no dedicated
        # capability (there is no MIDDLE_CLICK in the vocabulary); it
        # rides on CLICK per the "modifier of the standard click verb"
        # principle documented in _capabilities.py.
        if button == "right":
            self.require_capability(Capability.RIGHT_CLICK)
            element.right_click_input()
        elif button == "middle":
            self.require_capability(Capability.CLICK)
            element.click_input(button="middle")
        else:
            self.require_capability(Capability.CLICK)
            element.click_input()

    def type_text(self, element: Any, text: str) -> None:
        self.require_capability(Capability.TYPE_TEXT)
        element.type_keys(_escape_keys(text), with_spaces=True, with_tabs=True, with_newlines=True)

    def get_tree(self, root: Any, *, depth: int | None = None) -> dict[str, Any]:
        self.require_capability(Capability.GET_TREE)

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
        self.require_capability(Capability.SCREENSHOT)
        if element is None:
            from PIL import ImageGrab

            # ``all_screens=True`` is mandatory on a multi-monitor setup:
            # without it Pillow captures the primary monitor only, so a
            # full-desktop screenshot silently omits every other display.
            return ImageGrab.grab(all_screens=True)
        return element.capture_as_image()

    def is_available(self) -> bool:
        try:
            import pywinauto  # noqa: F401

            return sys.platform == "win32"
        except ImportError:
            return False


class QtBackend(UIABackend):
    """Qt-aware UIA backend - for Qt 5 / Qt 6 desktop apps (QWidget + QML).

    UIA behaviour:

    * Inherits all UIA element resolution and actions from :class:`UIABackend`.
    * Documents the Qt mapping for users:

      ===================  ==================  =======================================
      Qt class             UIA ControlType     dolphin lookup
      ===================  ==================  =======================================
      QPushButton          Button              ``window.button(name=...)``
      QLineEdit            Edit                ``window.edit(name=...)``
      QCheckBox            CheckBox            ``window.check_box(name=...)``
      QRadioButton         RadioButton         ``window.radio_button(name=...)``
      QComboBox            ComboBox            ``window.combo_box(name=...)``
      QListWidget          List                ``window.list_box(name=...)``
      QTreeWidget          Tree                ``window.tree(name=...)``
      QTabWidget           Tab                 ``window.tab(name=...)``
      QToolBar             ToolBar             ``window.toolbar(name=...)``
      QMenuBar             MenuBar             ``window.menu(name=...)``
      QSpinBox             Spinner             ``window.locator(control_type="Spinner")``
      QSlider              Slider              ``window.locator(control_type="Slider")``
      QDateEdit            Edit                ``window.edit(name=...)``
      QTableView           Table               ``window.locator(control_type="Table")``
      ===================  ==================  =======================================

    * Qt's ``objectName`` is exposed as UIA ``AutomationId``; ``accessibleName``
      becomes UIA ``Name``. Tests should set both for stable selectors.

    For widgets that are opaque under UIA — Qt Quick / QML scenes and
    ``QGraphicsView`` custom-paint widgets — the bundled injected Qt agent
    enumerates the QObject tree directly; see ``Application.qt_agent()``
    and ``docs/guides/qt.md``.
    """

    id = "qt"
    platform = "windows"

    def description(self) -> str:
        return "Qt 5/6 backend — UIA-based, requires QT_ACCESSIBILITY=1 in target process."


class Win32Backend(Backend):
    """Win32 HWND backend - best for legacy apps (MFC, VB6, Delphi/VCL)."""

    id = "win32"
    platform = "windows"

    def capabilities(self) -> frozenset[Capability]:
        # Win32 messaging can locate windows, walk the HWND tree, read
        # window text, and dispatch mouse / keyboard events — but the
        # UIA programmatic patterns (Invoke, Toggle, ExpandCollapse,
        # SelectionItem, Value) require the UIA layer and are NOT
        # exposed here. Callers who need those must switch to the UIA
        # backend or accept UnsupportedCapabilityError.
        return frozenset(
            {
                Capability.LOCATE,
                Capability.GET_TREE,
                Capability.READ_TEXT,
                Capability.READ_STATE,
                Capability.CLICK,
                Capability.DOUBLE_CLICK,
                Capability.RIGHT_CLICK,
                Capability.HOVER,
                Capability.DRAG,
                Capability.TYPE_TEXT,
                Capability.PRESS_KEY,
                Capability.SCROLL,
                Capability.SCREENSHOT,
            }
        )

    def find_element(self, parent: Any, criteria: dict[str, Any]) -> Any:
        self.require_capability(Capability.LOCATE)
        return parent.child_window(**criteria)

    def click(self, element: Any, *, button: str = "left") -> None:
        # Route the capability guard by the button variant — a backend
        # that publishes CLICK but not RIGHT_CLICK must NOT silently
        # dispatch a right-click. Middle-click has no dedicated
        # capability (there is no MIDDLE_CLICK in the vocabulary); it
        # rides on CLICK per the "modifier of the standard click verb"
        # principle documented in _capabilities.py.
        if button == "right":
            self.require_capability(Capability.RIGHT_CLICK)
            element.right_click_input()
        elif button == "middle":
            self.require_capability(Capability.CLICK)
            element.click_input(button="middle")
        else:
            self.require_capability(Capability.CLICK)
            element.click_input()

    def type_text(self, element: Any, text: str) -> None:
        self.require_capability(Capability.TYPE_TEXT)
        element.type_keys(_escape_keys(text), with_spaces=True, with_tabs=True, with_newlines=True)

    def get_tree(self, root: Any, *, depth: int | None = None) -> dict[str, Any]:
        self.require_capability(Capability.GET_TREE)

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
        self.require_capability(Capability.SCREENSHOT)
        if element is None:
            from PIL import ImageGrab

            # See UIABackend.screenshot — the primary-monitor default would
            # drop every secondary display from a full-desktop capture.
            return ImageGrab.grab(all_screens=True)
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

    def capabilities(self) -> frozenset[Capability]:
        # Pixel matching + OS-level mouse / keyboard. No tree, no
        # accessibility state, no programmatic patterns — that's the
        # trade-off image backends make. See _capabilities.IMAGE_ONLY.
        return IMAGE_ONLY

    def find_element(self, parent: Any, criteria: dict[str, Any]) -> Any:
        self.require_capability(Capability.LOCATE)
        from ._image import ImageLocator

        template = criteria.get("template")
        if not template:
            raise ValueError("ImageBackend.find_element requires criteria['template']")
        return ImageLocator(template, threshold=criteria.get("threshold", 0.8))

    def click(self, element: Any, *, button: str = "left") -> None:
        # Route to the ImageLocator's own per-button methods
        # (right_click_input / click_input(button='middle')) and gate
        # the capability by variant, matching the UIABackend /
        # Win32Backend pattern.
        if button == "right":
            self.require_capability(Capability.RIGHT_CLICK)
            element.right_click_input()
        elif button == "middle":
            self.require_capability(Capability.CLICK)
            # ImageLocator has no middle-click helper — fall back to
            # click_input (best-effort; middle-click via image locate
            # is not part of the documented image surface).
            element.click_input()
        else:
            self.require_capability(Capability.CLICK)
            element.click()

    def type_text(self, element: Any, text: str) -> None:
        self.require_capability(Capability.TYPE_TEXT)
        element.click()
        from ._keyboard import Keyboard

        Keyboard.type(text)

    def get_tree(self, root: Any, *, depth: int | None = None) -> dict[str, Any]:
        # ImageBackend does not publish GET_TREE — raise clean.
        self.require_capability(Capability.GET_TREE)
        return {"name": "screen", "role": "image", "class": "", "children": []}

    def screenshot(self, element: Any | None = None) -> Any:
        self.require_capability(Capability.SCREENSHOT)
        from ._image import Screen

        return Screen.screenshot()

    def is_available(self) -> bool:
        try:
            import cv2  # noqa: F401

            return True
        except ImportError:
            return False


# Stub / marker backends

_STUB_MSG = (
    "{name} is a reserved stub for a future dolphin platform backend.  "
    "It will be implemented in a later version.  "
    "See https://github.com/DolphinSoftEU/DolphinDesktop for the roadmap."
)


class _MarkerBackend(Backend):
    """Base for backends that raise on every operation call.

    Two flavours use this:

    * **Stubs** — reserved slots for future platform backends (macOS,
      Linux, CDP-as-backend). ``is_available()`` returns False.
    * **Markers** — backends that appear in ``list_backends()`` but whose
      real automation lives elsewhere (Delphi → :class:`DelphiApp`,
      Mainframe → :class:`MainframeTerminal`). ``is_available()`` returns
      True on the platforms where the real surface works.

    Subclasses set :attr:`id`, :attr:`platform`, :attr:`_stub_msg`, and
    override :meth:`is_available` if needed. The five find/click/type/
    tree/screenshot methods all reroute through :meth:`_raise` — one
    place to change the failure format instead of five per subclass.

    Marker backends may override :meth:`capabilities` to advertise what
    their downstream Facade (DelphiApp, MainframeTerminal, CDPSession, …)
    supports. That way callers can ask
    ``Desktop.backend_supports(Capability.INVOKE)`` and get an accurate
    answer for the stack even though the direct call on this marker
    class raises. The direct-call raise is
    :class:`~dolphin_desktop.UnsupportedCapabilityError` (not
    ``NotImplementedError``) so it fits the same exception category
    every other capability-gated operation uses.
    """

    _stub_msg: str = _STUB_MSG

    def capabilities(self) -> frozenset[Capability]:
        # Markers by default do nothing on their own — subclasses that
        # front a real Facade override to publish the stack's caps.
        return frozenset()

    def _raise(self) -> Any:
        raise UnsupportedCapabilityError(
            self._stub_msg.format(name=type(self).__name__),
            hint=(
                "this backend is a registry marker — the real automation "
                "surface lives on the matching facade "
                "(Desktop.sap() / .mainframe() / .launch_delphi() / …)"
            ),
        )

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


class MacOSAccessibilityBackend(_MarkerBackend):
    """macOS Accessibility API backend - reserved, not yet implemented."""

    id = "macos"
    platform = "macos"


class LinuxATSPIBackend(_MarkerBackend):
    """Linux AT-SPI2 accessibility backend - reserved, not yet implemented."""

    id = "linux"
    platform = "linux"


class DelphiBackend(_MarkerBackend):
    """Delphi / VCL (and Lazarus / LCL) marker backend.

    Real automation runs through UIA — dolphin's Delphi surface
    (:class:`~dolphin_desktop.DelphiApp`, ``.launch_delphi``) sits on
    top of the UIA backend, adding a Delphi-aware
    ``TComponent.Name → AutomationId`` locator. Registered as a marker
    so ``list_backends()`` surfaces it.

    Capability set matches UIA + Delphi component surface: locate /
    read / input / programmatic — the full accessibility contract.
    """

    id = "delphi"
    platform = "windows"
    _stub_msg = (
        "DelphiBackend is a marker — use Desktop.launch_delphi() / "
        "attach_delphi() which build a DelphiApp on top of UIA."
    )

    def capabilities(self) -> frozenset[Capability]:
        return STANDARD_ACCESSIBILITY | frozenset(
            {
                Capability.SCREENSHOT,
                Capability.DRAG,
                Capability.SCROLL,
            }
        )

    def is_available(self) -> bool:
        return sys.platform == "win32"


class MainframeBackend(_MarkerBackend):
    """3270/5250 mainframe backend — see ``dolphin_desktop.MainframeTerminal``.

    Registered as a marker so ``list_backends()`` surfaces it. The
    actual automation surface lives on :class:`MainframeTerminal`, which
    is instantiated via :meth:`Desktop.mainframe` — mainframe screens do
    not fit the element/click/type model this ``Backend`` interface
    assumes.

    Capability set reflects what a 3270 / 5250 terminal actually
    exposes: text screen buffer, cursor / field addressing, AID key
    dispatch. No hover, no drag, no programmatic patterns (terminals
    have no such concept), no screenshot (the "image" of a terminal is
    the text — use :meth:`~dolphin_desktop.MainframeTerminal.text`).
    """

    id = "mainframe"
    platform = "any"
    _stub_msg = (
        "MainframeBackend is a marker — use Desktop.mainframe(...) to "
        "obtain a MainframeTerminal for terminal automation."
    )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.LOCATE,
                Capability.READ_TEXT,
                Capability.READ_STATE,
                Capability.TYPE_TEXT,
                Capability.PRESS_KEY,
            }
        )

    def is_available(self) -> bool:
        # Marker only — always report available so it shows in list_backends().
        return True


class CDPBackend(_MarkerBackend):
    """Chrome DevTools Protocol backend for Electron / CEF / WebView2.

    Registered as a marker so ``list_backends()`` surfaces it. The
    actual automation surface lives on :class:`CDPSession`,
    instantiated via :meth:`Desktop.launch_electron_cdp` and friends.

    Capability set mirrors what a full Chromium can do: everything.
    """

    id = "cdp"
    platform = "any"
    _stub_msg = (
        "CDPBackend is a marker — use Desktop.launch_electron_cdp() / "
        "Desktop.attach_electron_cdp() which return a CDPSession facade."
    )

    def capabilities(self) -> frozenset[Capability]:
        return STANDARD_ACCESSIBILITY | frozenset(
            {
                Capability.SCREENSHOT,
                Capability.DRAG,
                Capability.SCROLL,
            }
        )

    def is_available(self) -> bool:
        # CDPBackend is a marker for a real stack (CDPSession /
        # Playwright) that works on every platform Chromium runs on,
        # so list_backends() must report available=True — otherwise
        # callers see 'cdp: unavailable' and skip a working code path.
        return True


class SapBackend(_MarkerBackend):
    """SAP GUI Scripting backend — see :class:`dolphin_desktop.SapGui`.

    Marker for the SAP COM Scripting stack. Real automation surface
    lives on :class:`SapGui` / :class:`SapSession` / :class:`SapLocator`.
    SAP GUI exposes locate / read / input / invoke via component IDs;
    ExpandCollapse / Toggle / Select map to component-specific
    ``Selected``/``Expanded`` properties handled by the facade.
    """

    id = "sap"
    platform = "windows"
    _stub_msg = (
        "SapBackend is a marker — use Desktop.sap() which returns a "
        "SapGui / SapSession facade for SAP GUI Scripting automation."
    )

    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.LOCATE,
                Capability.GET_TREE,
                Capability.READ_TEXT,
                Capability.READ_STATE,
                Capability.CLICK,
                Capability.DOUBLE_CLICK,
                # SAP GUI's ShowContextMenu / RightClick methods expose
                # right-click on grids, trees, and context-menu-bearing
                # components. Backing SapLocator.right_click() with the
                # RIGHT_CLICK cap so a caller checking the stack up-front
                # sees the right answer.
                Capability.RIGHT_CLICK,
                Capability.TYPE_TEXT,
                Capability.PRESS_KEY,
                Capability.INVOKE,
                Capability.TOGGLE,
                Capability.SELECT,
                Capability.SET_VALUE,
                Capability.SCREENSHOT,
            }
        )

    def is_available(self) -> bool:
        return sys.platform == "win32"


class JavaBackend(_MarkerBackend):
    """Java Access Bridge backend — Swing / Oracle Forms / JavaFX.

    Marker for the JAB stack. Real automation lives on
    :class:`JavaAccessBridge`, :class:`JABLocator`, and the
    Forms-aware :class:`OracleFormsApp`. Full accessibility contract
    modulo drag (JAB does not surface a drag pattern).
    """

    id = "java"
    platform = "windows"
    _stub_msg = (
        "JavaBackend is a marker — use Desktop.launch_java() / "
        "Desktop.launch_oracle_forms() which return JAB-backed facades."
    )

    def capabilities(self) -> frozenset[Capability]:
        return STANDARD_ACCESSIBILITY | frozenset(
            {
                Capability.SCREENSHOT,
                Capability.SCROLL,
            }
        )

    def is_available(self) -> bool:
        return sys.platform == "win32"


# Registry

#: Built-in backend classes (registered at module load time).
_BUILT_IN: list[type[Backend]] = [
    UIABackend,
    Win32Backend,
    QtBackend,
    ImageBackend,
    MacOSAccessibilityBackend,
    LinuxATSPIBackend,
    CDPBackend,
    MainframeBackend,
    DelphiBackend,
    SapBackend,
    JavaBackend,
]

_REGISTRY: dict[str, type[Backend]] = {cls.id: cls for cls in _BUILT_IN}
_plugins_loaded = False
#: Guards the ``_plugins_loaded`` check-then-set + the ``entry_points``
#: discovery pass. Concurrent Desktop() constructions on multiple
#: threads (common under pytest-xdist) would otherwise race through the
#: naive ``if not loaded: loaded = True`` and each run the discovery
#: pass — emitting duplicate ``RuntimeWarning``s and doing wasted work.
_plugins_lock = threading.Lock()


def _load_plugins() -> None:
    """Discover and register backends from the ``dolphin_desktop.backends`` entry-point group.

    Uses each backend's declared ``cls.id`` as the registry key (not the
    entry-point name). Entry-point name and ``cls.id`` should match by
    convention; a mismatch emits a :class:`RuntimeWarning` so the plugin
    author can align them.

    Silent-skip cases (do NOT raise, plugin discovery must be robust):

    * Entry point that fails to load (broken import): warning + skip.
    * Entry point pointing at a non-``Backend`` object: warning + skip.
    * Entry point pointing at a Backend subclass with no
      ``id: str`` class attribute (or blank): warning + skip.
    * Entry point whose ``cls.id`` collides with an existing
      registered backend under a DIFFERENT class object: warning +
      skip (built-ins take precedence over plugin overrides).
    """
    global _plugins_loaded
    # Fast path: already-loaded flag is a plain bool, so a lock-free read
    # is safe on CPython (bool assignment is atomic). The lock serialises
    # the FULL discovery+registration pass — flipping ``_plugins_loaded``
    # before registration completes would let a concurrent caller
    # short-circuit past the fast-path check while the registry is still
    # being populated.
    if _plugins_loaded:
        return
    with _plugins_lock:
        if _plugins_loaded:
            return
        _load_plugins_locked()
        _plugins_loaded = True


def _load_plugins_locked() -> None:
    """Actual discovery + registration pass. Caller must hold
    ``_plugins_lock``."""
    try:
        from importlib.metadata import entry_points

        eps = entry_points(group="dolphin_desktop.backends")
    except Exception:
        return

    import warnings

    for ep in eps:
        try:
            cls = ep.load()
        except Exception as exc:
            warnings.warn(
                f"dolphin_desktop.backends entry point {ep.name!r} failed to "
                f"load: {type(exc).__name__}: {exc}. Skipping.",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        if not (isinstance(cls, type) and issubclass(cls, Backend)):
            warnings.warn(
                f"dolphin_desktop.backends entry point {ep.name!r} does not "
                f"point at a Backend subclass "
                f"(got {type(cls).__name__}: {cls!r}). Skipping.",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        # Backend subclass but no valid ``id: str`` class attribute
        # — cannot key the registry, cannot quote the id in error
        # messages. Warn and skip rather than let cls.id below crash
        # entry-point discovery for every subsequent plugin. Require
        # actual str, not just truthy.
        cls_id = getattr(cls, "id", None)
        if not isinstance(cls_id, str) or not cls_id:
            warnings.warn(
                f"dolphin_desktop.backends entry point {ep.name!r} points at "
                f"{cls.__module__}.{cls.__qualname__} whose ``id`` attribute "
                f"is not a non-empty str "
                f"(got {type(cls_id).__name__}: {cls_id!r}). Set "
                f"``id = {ep.name!r}`` on the class. Skipping.",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        # Prefer cls.id — that is what require_capability quotes in the
        # error message. Entry-point name is advisory; warn on mismatch.
        if ep.name != cls.id:
            warnings.warn(
                f"dolphin_desktop.backends entry point name {ep.name!r} does "
                f"not match Backend.id {cls.id!r} on "
                f"{cls.__module__}.{cls.__qualname__}. The registry uses "
                f"cls.id — align them to avoid confusion in error messages.",
                RuntimeWarning,
                stacklevel=2,
            )
        if cls.id in _REGISTRY and _REGISTRY[cls.id] is not cls:
            # Do not silently override built-ins via entry points.
            warnings.warn(
                f"dolphin_desktop.backends entry point {ep.name!r} tries to "
                f"replace already-registered backend {cls.id!r}. Ignoring.",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        _REGISTRY[cls.id] = cls


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

    Silent-overwrite protection: re-registering the same class under
    the same id is a no-op (idempotent, safe for tests that call
    ``register()`` in setUp). Registering a **different** class under
    an already-taken id emits a ``RuntimeWarning`` — most such calls
    are unintentional collisions with a built-in id (``"uia"``,
    ``"cdp"``, …) and would silently replace the built-in behaviour
    otherwise.

    Argument validation:

    * *cls* must be a class (``TypeError`` otherwise — mirrors
      ``@register`` decorator misuse on an instance / function).
    * *cls* must be a :class:`Backend` subclass (``TypeError`` otherwise
      — the registry only stores backends).
    * *cls* must declare a non-empty ``id: str`` class attribute
      (``TypeError`` — this is the registry key; a missing / blank id
      would silently register under ``""`` and break resolve()).
    """
    if not isinstance(cls, type):
        raise TypeError(
            f"register() expected a Backend subclass, got "
            f"{type(cls).__name__}: {cls!r}. Pass the class itself, not "
            f"an instance."
        )
    if not issubclass(cls, Backend):
        raise TypeError(
            f"register() expected a Backend subclass, got "
            f"{cls.__module__}.{cls.__qualname__}. Import Backend from "
            f"dolphin_desktop and inherit from it."
        )
    bid = getattr(cls, "id", None)
    if not isinstance(bid, str) or not bid:
        raise TypeError(
            f"register() expected {cls.__qualname__} to declare a "
            f"non-empty class attribute ``id: str`` (the registry key). "
            f"Got {type(bid).__name__}: {bid!r}. "
            f"Set e.g. ``id = 'my_backend'`` on the class."
        )
    if cls.id in _REGISTRY and _REGISTRY[cls.id] is not cls:
        import warnings

        existing = _REGISTRY[cls.id]
        warnings.warn(
            f"Backend id {cls.id!r} already registered "
            f"({existing.__module__}.{existing.__qualname__}); "
            f"replacing with {cls.__module__}.{cls.__qualname__}. "
            f"Use a different id if you did not intend to override the "
            f"existing backend.",
            RuntimeWarning,
            stacklevel=2,
        )
    _REGISTRY[cls.id] = cls
    return cls


# Resolver


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

    cls = _REGISTRY[backend_id]
    try:
        return cls()
    except Exception as exc:
        # A plugin backend whose __init__ raises would give the caller
        # a bare exception with no hint about which backend was tried.
        # Wrap in ValueError with the backend id and class in the
        # message so the failure is self-locating.
        raise ValueError(
            f"Backend {backend_id!r} ({cls.__module__}.{cls.__qualname__}) "
            f"could not be instantiated: {type(exc).__name__}: {exc}. "
            f"Fix the plugin's __init__ or fall back to a different "
            f"backend id via Desktop(backend='…')."
        ) from exc


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
    ``description``, ``source`` (``"built-in"`` or entry-point value),
    ``capabilities`` (list of :class:`Capability` value strings).

    Plugin backends from ``dolphin_desktop.backends`` entry points are
    included automatically.
    """
    _load_plugins()

    built_in_ids = {cls.id for cls in _BUILT_IN}

    result: list[dict[str, Any]] = []
    for bid, cls in sorted(_REGISTRY.items()):
        # A single broken plugin must not sink list_backends() —
        # every field is best-effort with a sensible default so the
        # remaining backends still surface.
        try:
            instance = cls()
        except Exception as exc:
            result.append(
                {
                    "id": bid,
                    "platform": getattr(cls, "platform", "unknown"),
                    "class": f"{cls.__module__}.{cls.__qualname__}",
                    "available": False,
                    "description": f"(instantiation failed: {exc})",
                    "source": "built-in" if bid in built_in_ids else "plugin",
                    "capabilities": [],
                }
            )
            continue
        result.append(
            {
                "id": bid,
                "platform": cls.platform,
                "class": f"{cls.__module__}.{cls.__qualname__}",
                "available": _safe_call(instance.is_available, default=False),
                "description": _safe_call(instance.description, default=""),
                "source": "built-in" if bid in built_in_ids else "plugin",
                "capabilities": sorted(_safe_capabilities(instance)),
            }
        )
    return result


def _safe_call(fn, *, default):
    """Call fn() swallowing any exception; return *default* on failure.

    Used inside :func:`list_backends` so a broken plugin does not
    sink the whole registry listing.
    """
    try:
        return fn()
    except Exception:
        return default


def _safe_capabilities(instance: Backend) -> list[str]:
    """Extract a list of capability value strings from *instance*.

    Handles every misbehaviour a plugin ``capabilities()`` can throw
    at :func:`list_backends`: raising, returning ``None``, returning a
    non-iterable, returning an iterable of the wrong type. Any failure
    yields ``[]`` so the enclosing entry still surfaces with useful
    metadata for the other registered backends.
    """
    try:
        raw = instance.capabilities()
    except Exception:
        return []
    if raw is None:
        return []
    try:
        return [c.value for c in raw if isinstance(c, Capability)]
    except TypeError:
        return []


def _find_supporting_backends(
    capability: Capability, *, exclude_id: str | None = None
) -> list[str]:
    """Return ids of registered backends whose ``capabilities()`` include *capability*.

    Used by :meth:`Backend.require_capability` to build an actionable
    hint pointing at working alternatives. The initiating backend is
    excluded so the hint never suggests "use yourself".
    """
    _load_plugins()
    hits: list[str] = []
    for bid, cls in _REGISTRY.items():
        if exclude_id is not None and bid == exclude_id:
            continue
        try:
            if capability in cls().capabilities():
                hits.append(bid)
        except Exception:
            continue
    return hits


def supported_backends(capability: Capability) -> list[str]:
    """Public helper: return every registered backend id supporting *capability*.

    Used by callers who want to introspect which backend is right for
    a given operation before instantiating a Desktop::

        from dolphin_desktop import Capability, supported_backends

        for bid in supported_backends(Capability.INVOKE):
            print(bid, "can .invoke()")

    Rejects non-Capability arguments with a :class:`TypeError` — a
    silent empty list for a typo argument would look like "no
    backend supports it" and hide the real bug from the caller.
    """
    _check_capability_arg(capability, "supported_backends")
    return _find_supporting_backends(capability)
