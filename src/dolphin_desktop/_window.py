"""Window — wraps a top-level or child window."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._element import (
    Button,
    CheckBox,
    ComboBox,
    Edit,
    ListBox,
    Menu,
    RadioButton,
    Tab,
    Toolbar,
    Tree,
)
from ._exceptions import WaitTimeoutError
from ._locator import Locator

if TYPE_CHECKING:
    from PIL.Image import Image

_XPATH_SEGMENT_START_RE = re.compile(r"/{1,2}(?P<tag>[A-Za-z]+|\*)")
_XPATH_PREDICATE_RE = re.compile(
    r"\[@(?P<attribute>Name|AutomationId|ClassName)="
    r"(?P<quote>['\"])(?P<value>[^'\"\[\]]*)(?P=quote)\]"
)
_XPATH_ATTR_MAP = {
    "Name": "title",
    "AutomationId": "auto_id",
    "ClassName": "class_name",
}

# The only ``locator()`` criteria a JABLocator can evaluate.
_JAB_CRITERIA = frozenset({"control_type", "title", "title_re"})


def _parse_xpath(root: Any, xpath: str) -> Locator:
    """Parse a complete simplified XPath expression into a Locator chain."""
    expression = xpath.strip()
    if not expression:
        raise ValueError(f"No valid XPath segments found in: {xpath!r}")

    segments: list[dict[str, Any]] = []
    position = 0
    while position < len(expression):
        segment_match = _XPATH_SEGMENT_START_RE.match(expression, position)
        if segment_match is None:
            if not segments:
                raise ValueError(f"No valid XPath segments found in: {xpath!r}")
            raise ValueError(f"Invalid simplified XPath syntax at position {position}: {xpath!r}")

        position = segment_match.end()
        tag = segment_match.group("tag")
        criteria: dict[str, Any] = {}
        if tag != "*":
            criteria["control_type"] = tag

        while predicate_match := _XPATH_PREDICATE_RE.match(expression, position):
            criteria[_XPATH_ATTR_MAP[predicate_match.group("attribute")]] = predicate_match.group(
                "value"
            )
            position = predicate_match.end()

        segments.append(criteria)

    current: Any = root
    for criteria in segments:
        current = Locator(current, **criteria)
    return current


class Window:
    """Represents a single window (top-level or dialog).

    Obtain via :meth:`Application.window` or :meth:`Application.top_window`.

    Usage::

        window = app.window(title_re=".*Notepad")
        window.get_by_role("Edit").type_text("hello")
        window.screenshot("snap.png")
    """

    def __init__(self, spec: Any, *, application: Any = None) -> None:
        self._spec = spec
        # Optional back-pointer to owning Application — populated by
        # Application._make_window so that the Qt-agent helpers (qml /
        # qt_widget / graphics_view) can access the agent without forcing
        # the user to juggle two objects.
        self._application = application

        # Memoised answer of _is_java_window(); None until first probed.
        self._is_java: bool | None = None

    # Locator factories  (getBy-style lookups)

    def locator(self, **criteria: Any) -> Any:
        """Find elements by arbitrary pywinauto criteria (title, control_type, auto_id, …).

        On a Java Swing window the search runs through the Java Access Bridge,
        which can only express ``control_type`` / ``title`` / ``title_re`` —
        any other criterion raises rather than being dropped.
        """
        if self._is_java_window():
            return self._java_locator(criteria)
        return Locator(self, **criteria)

    def get_by_title(self, title: str, *, control_type: str | None = None) -> Any:
        """Find an element by its accessible name / window text."""
        if self._is_java_window():
            from ._java import JABLocator

            return JABLocator(self._java_hwnd(), control_type=control_type, title=title)
        criteria: dict[str, Any] = {"title": title}
        if control_type:
            criteria["control_type"] = control_type
        return Locator(self, **criteria)

    def get_by_role(self, role: str, *, name: str | None = None) -> Any:
        """Find an element by its UIA control type (e.g. 'Button', 'Edit', 'List')."""
        if self._is_java_window():
            from ._java import JABLocator

            return JABLocator(self._java_hwnd(), control_type=role, title=name)
        criteria: dict[str, Any] = {"control_type": role}
        if name:
            criteria["title"] = name
        return Locator(self, **criteria)

    def get_by_automation_id(self, automation_id: str) -> Locator:
        """Find an element by its UIA AutomationId.

        Raises :class:`ValueError` on a Java Swing window — the Access Bridge
        publishes no AutomationId, so the lookup could only ever be answered
        by ignoring the argument.
        """
        self._reject_for_java("get_by_automation_id", "AutomationId")
        return Locator(self, auto_id=automation_id)

    def get_by_object_name(self, object_name: str) -> Locator:
        """Find a Qt widget by its ``objectName``.

        Qt exposes ``objectName`` as a **hierarchical dotted path** in UIA
        ``AutomationId`` (e.g.
        ``QApplication.main_window.tabs.tab_buttons.qt_btn_ok``).  This method
        matches on the **leaf segment** so callers only need to specify the
        widget's own ``objectName``.

        Internally this resolves by walking descendants of the current window
        and picking the first whose ``AutomationId`` equals *object_name* or
        ends with ``.<object_name>``.

        Usage::

            window.get_by_object_name("qt_btn_ok").click()

        Raises :class:`ValueError` on a Java Swing window — ``objectName`` is a
        Qt concept with no Access Bridge equivalent.
        """
        from ._locator import _QtObjectNameLocator

        self._reject_for_java("get_by_object_name", "objectName")
        return _QtObjectNameLocator(self, object_name)

    def get_by_class(self, class_name: str) -> Locator:
        """Find an element by its Win32 class name.

        Raises :class:`ValueError` on a Java Swing window — Swing components
        are not HWNDs and have no Win32 class name.
        """
        self._reject_for_java("get_by_class", "a Win32 class name")
        return Locator(self, class_name=class_name)

    # Agent-backed Qt helpers (QML / QObject / QGraphicsView)

    def _require_application(self) -> Any:
        if self._application is None:
            raise RuntimeError(
                "Window was constructed without an Application back-pointer — "
                "the Qt-agent helpers (qml/qt_widget/graphics_view) need the "
                "owning Application to reach the agent. Use Application.window(...)."
            )
        return self._application

    def qml(self, object_name: str) -> Any:
        """Find a QML item by ``objectName`` and return a :class:`QmlElement`.

        Equivalent to ``self.application.qml(object_name)`` — exposed at the
        Window level so test authors can stay in a single object scope.
        """
        return self._require_application().qml(object_name)

    def qt_widget(
        self,
        *,
        object_name: str | None = None,
        class_name: str | None = None,
        text: str | None = None,
    ) -> Any:
        """Find a QObject / QWidget via the Qt agent and wrap it."""
        return self._require_application().qt_widget(
            object_name=object_name, class_name=class_name, text=text
        )

    def graphics_view(self, *, object_name: str | None = None) -> Any:
        """Return a :class:`GraphicsViewElement` wrapping the first matching QGraphicsView."""
        return self._require_application().graphics_view(object_name=object_name)

    def get_by_text(self, text: str) -> Any:
        """Alias for get_by_title — find by exact visible text."""
        if self._is_java_window():
            from ._java import JABLocator

            return JABLocator(self._java_hwnd(), title=text)
        return Locator(self, title=text)

    # Specialized control factories (shorthand for common control types)

    def button(self, name: str | None = None, **kw: Any) -> Button:
        """Find a Button control.

        Usage::

            window.button(name="OK").click()
            window.button(name="Apply").is_enabled()
        """
        criteria: dict[str, Any] = {"control_type": "Button", **kw}
        if name is not None:
            criteria["title"] = name
        return Button(self, **criteria)

    def edit(self, name: str | None = None, **kw: Any) -> Edit:
        """Find an Edit (text input) control.

        Usage::

            window.edit(name="Username").type_text("admin")
            window.edit().set_text("hello")
        """
        criteria: dict[str, Any] = {"control_type": "Edit", **kw}
        if name is not None:
            criteria["title"] = name
        return Edit(self, **criteria)

    def combo_box(self, name: str | None = None, **kw: Any) -> ComboBox:
        """Find a ComboBox (drop-down) control.

        Usage::

            window.combo_box(name="Language").select_item("English")
        """
        criteria: dict[str, Any] = {"control_type": "ComboBox", **kw}
        if name is not None:
            criteria["title"] = name
        return ComboBox(self, **criteria)

    def check_box(self, name: str | None = None, **kw: Any) -> CheckBox:
        """Find a CheckBox control.

        Usage::

            window.check_box(name="Remember me").check()
        """
        criteria: dict[str, Any] = {"control_type": "CheckBox", **kw}
        if name is not None:
            criteria["title"] = name
        return CheckBox(self, **criteria)

    def radio_button(self, name: str | None = None, **kw: Any) -> RadioButton:
        """Find a RadioButton control.

        Usage::

            window.radio_button(name="Option A").select()
        """
        criteria: dict[str, Any] = {"control_type": "RadioButton", **kw}
        if name is not None:
            criteria["title"] = name
        return RadioButton(self, **criteria)

    def menu(self, title: str | None = None, **kw: Any) -> Menu:
        """Find a top-level menu item (e.g. File, Edit, View).

        Usage::

            window.menu("File").item("Save").click()
            window.menu("Edit").item("Find...").click()
        """
        criteria: dict[str, Any] = {"control_type": "MenuItem", **kw}
        if title is not None:
            criteria["title"] = title
        return Menu(self, **criteria)

    def tree(self, name: str | None = None, **kw: Any) -> Tree:
        """Find a Tree (tree view) control.

        Usage::

            window.tree().expand_item("Root", "Branch")
            window.tree().select_item_by_path("Root", "Node")
        """
        criteria: dict[str, Any] = {"control_type": "Tree", **kw}
        if name is not None:
            criteria["title"] = name
        return Tree(self, **criteria)

    def list_box(self, name: str | None = None, **kw: Any) -> ListBox:
        """Find a ListBox control.

        Usage::

            window.list_box().select_item("Option A")
            names = window.list_box().items()
        """
        criteria: dict[str, Any] = {"control_type": "List", **kw}
        if name is not None:
            criteria["title"] = name
        return ListBox(self, **criteria)

    def tab(self, name: str | None = None, **kw: Any) -> Tab:
        """Find a Tab (notebook) control.

        Usage::

            window.tab().select_tab("General")
        """
        criteria: dict[str, Any] = {"control_type": "Tab", **kw}
        if name is not None:
            criteria["title"] = name
        return Tab(self, **criteria)

    def toolbar(self, name: str | None = None, **kw: Any) -> Toolbar:
        """Find a Toolbar control.

        Usage::

            window.toolbar().button("Bold").click()
        """
        criteria: dict[str, Any] = {"control_type": "ToolBar", **kw}
        if name is not None:
            criteria["title"] = name
        return Toolbar(self, **criteria)

    def element(self, alias: str) -> Any:
        """Find an element by alias from the Object Repository.

        Looks up *alias* in the children of the current window's alias first
        (if this window was created via ``app.window("alias")``), then falls
        back to a flat top-level search.

        Usage::

            objects.load("objects/login.yaml")
            win = app.window("login_window")
            win.element("email_input").type_text("user@example.com")
        """
        from .objects import _repository

        parent_alias: str | None = getattr(self, "_alias", None)
        if parent_alias:
            entry = _repository.resolve_child(parent_alias, alias)
        else:
            entry = _repository.resolve(alias)

        locator = Locator(self, fallback=entry.fallback or None, **entry.selector)
        locator._object_repository = _repository
        locator._object_alias = alias
        locator._object_parent_alias = parent_alias
        locator._object_selector_keys = set(entry.selector)
        return locator

    def image(
        self,
        template: str | Path,
        *,
        confidence: float = 0.85,
        scales: list[float] | None = None,
    ) -> Any:
        """Find an element by template image matching, scoped to this window's bounds.

        Uses OpenCV template matching (requires ``dolphin-desktop[vision]``).

        Usage::

            window.image("btn_ok.png").click()
            window.image("icon.png", confidence=0.9).wait_for(timeout=5)
            window.image("logo.png", scales=[0.8, 1.0, 1.2]).exists()
        """
        from ._image import ImageLocator

        try:
            bb = self.bounding_box()
            region: tuple[int, int, int, int] | None = (
                bb["left"],
                bb["top"],
                bb["right"],
                bb["bottom"],
            )
        except Exception:
            region = None

        return ImageLocator(template, threshold=confidence, scales=scales, region=region)

    def find_by_xpath(self, xpath: str) -> Locator:
        """Find an element using a simplified XPath expression.

        This is a small, strict grammar for navigating the UIA element tree,
        not a full XPath engine. Leading and trailing whitespace is ignored;
        whitespace elsewhere is only allowed inside quoted values.

        Usage::

            window.find_by_xpath("//Button[@Name='OK']").click()
            window.find_by_xpath("//Edit[@AutomationId='tbSearch']").type_text("q")
            window.find_by_xpath("//MenuBar//MenuItem[@Name='File']")

        Supported grammar::

            expression ::= segment+
            segment ::= ("/" | "//") tag predicate*
            tag ::= ASCII letters+ | "*"
            predicate ::= "[@" attribute "=" quoted_value "]"
            attribute ::= "Name" | "AutomationId" | "ClassName"
            quoted_value ::= "'" value "'" | '"' value '"'
            value ::= zero or more characters other than quotes or brackets

        ``value`` may be empty and cannot contain quote or square-bracket
        characters. The three attributes map to ``title``, ``auto_id``, and
        ``class_name`` respectively. Segments are chained as lazy Locators.
        Unsupported or malformed syntax raises ``ValueError`` before a
        partial Locator is constructed.

        * ``//Tag`` or ``/Tag`` — find descendant with that control type
        * ``[@Name='val']``         → ``title='val'``
        * ``[@AutomationId='val']`` → ``auto_id='val'``
        * ``[@ClassName='val']``    → ``class_name='val'``

        Multiple segments chain locators::

            //MenuBar//MenuItem[@Name='File']

        Rejected examples include ``//Button[contains(@Name, 'Save')]``,
        ``//Button[@HelpText='Save document']``, numeric predicates, unknown
        attributes, unsupported operators, and malformed or trailing syntax.
        """
        return _parse_xpath(self, xpath)

    # Window actions

    def close(self) -> None:
        self._spec.close()

    def maximize(self) -> None:
        self._spec.maximize()

    def minimize(self) -> None:
        self._spec.minimize()

    def restore(self) -> None:
        self._spec.restore()

    def focus(self) -> None:
        self._spec.set_focus()

    def move(self, x: int, y: int) -> None:
        rect = self._spec.rectangle()
        self._move_window(x, y, rect.right - rect.left, rect.bottom - rect.top)

    def resize(self, width: int, height: int) -> None:
        rect = self._spec.rectangle()
        self._move_window(rect.left, rect.top, width, height)

    def _move_window(self, x: int, y: int, width: int, height: int) -> None:
        """Move/resize the window via Win32 (works on both uia and win32 backends).

        pywinauto's ``move_window`` exists only on the win32 ``HwndWrapper``; the
        UIA wrapper lacks it, so we drive ``win32gui.MoveWindow`` by HWND directly.
        """
        import win32gui  # type: ignore[import-untyped]

        win32gui.MoveWindow(self._spec.handle, x, y, width, height, True)

    # Queries

    def title(self) -> str:
        return self._spec.window_text()

    def exists(self) -> bool:
        return bool(self._spec.exists())

    def is_visible(self) -> bool:
        return bool(self._spec.is_visible())

    def is_active(self) -> bool:
        return bool(self._spec.is_active())

    def bounding_box(self) -> dict[str, int]:
        rect = self._spec.rectangle()
        return {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
        }

    # Screenshot

    def screenshot(self, path: str | Path | None = None) -> Image:
        """Capture the window as a PIL Image, optionally saving to *path*."""
        img = self._spec.capture_as_image()
        if path:
            output = Path(path)
            output.parent.mkdir(parents=True, exist_ok=True)
            img.save(output)
        return img

    # Waiting

    def wait_for_close(self, timeout: float = 10.0) -> None:
        """Wait until the window is no longer visible."""
        try:
            self._spec.wait_not("visible", timeout=timeout)
        except Exception as exc:
            raise WaitTimeoutError(f"Window did not close after {timeout}s") from exc

    def wait_until_ready(self, timeout: float = 10.0) -> Window:
        """Wait until the window is ready (not busy)."""
        try:
            self._spec.wait("ready", timeout=timeout)
        except Exception as exc:
            raise WaitTimeoutError(f"Window not ready after {timeout}s") from exc
        return self

    # Internal

    def _java_locator(self, criteria: dict[str, Any]) -> Any:
        """Build a :class:`JABLocator` from *criteria*, refusing what JAB cannot express.

        The Access Bridge matches on role and accessible name only. Forwarding
        just the expressible subset would turn ``locator(auto_id="txtName")``
        into "the first node in the tree" — wrong, and silently so.
        """
        from ._java import JABLocator

        unsupported = sorted(set(criteria) - _JAB_CRITERIA)
        if unsupported:
            raise ValueError(
                f"Java Access Bridge cannot match on {unsupported} — Swing "
                "components expose a role and an accessible name only; use "
                "control_type=, title= or title_re="
            )
        return JABLocator(self._java_hwnd(), **criteria)

    def _reject_for_java(self, method: str, concept: str) -> None:
        """Raise when *method* is called on a Java window it cannot serve."""
        if self._is_java_window():
            raise ValueError(
                f"{method}() is not available on a Java Swing window — the "
                f"Access Bridge publishes no {concept}; use get_by_role() / "
                "get_by_title() / locator(control_type=..., title=...)"
            )

    def _is_java_window(self) -> bool:
        """True when this window is a Java Swing frame.

        Every locator factory on this class asks first, and answering costs
        a full UIA resolve to reach the HWND — which made it the single
        largest cost in building a locator. An HWND's class name is fixed
        for its lifetime, so the answer is cached once obtained.

        A failed probe is deliberately not cached: it usually means the
        window has not appeared yet, and caching ``False`` there would
        permanently mis-route a Java window to the UIA locators.
        """
        if self._is_java is None:
            try:
                import win32gui  # type: ignore[import-untyped]

                self._is_java = win32gui.GetClassName(self._java_hwnd()) == "SunAwtFrame"
            except Exception:
                return False
        return self._is_java

    def _java_hwnd(self) -> int:
        return self._spec.wrapper_object().handle

    def _get_spec(self) -> Any:
        return self._spec

    def __repr__(self) -> str:
        return "Window()"
