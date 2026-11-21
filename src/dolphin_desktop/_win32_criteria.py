"""Translate UIA-style ``control_type`` criteria for pywinauto's ``win32`` backend.

The public selectors speak UI Automation control types: ``Window.edit()``
asks for ``control_type="Edit"``, ``combo_box()`` for ``"ComboBox"``, and
so on. pywinauto's ``win32`` backend reads ``control_type`` as something
else entirely: the .NET type a WinForms control reports through
``WM_GETCONTROLNAME`` (``"System.Windows.Forms.TextBox"``), and an empty
string for every classic HWND (MFC, VB6, Delphi). An unmodified UIA name
therefore never matches on ``win32``, so the locator waits out its whole
timeout for a control that is on screen (KAN-747).

On ``win32`` a UIA control type is rewritten into a ``predicate_func`` that
matches pywinauto's own friendly class name for the window. That name comes
from pywinauto's wrapper registry, which already knows the classic classes,
WinForms (``WindowsForms10.EDIT.*``), VB6 (``ThunderRT6TextBox``) and VCL
(``TMemo``) spellings, and which tells a check box from a push button by
its ``BS_*`` style. A UIA type with no Win32 window class (``MenuItem``,
``TreeItem``, ``Hyperlink``…) raises :class:`UnsupportedCapabilityError`
at once instead of timing out.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ._exceptions import UnsupportedCapabilityError

# UIA ControlType name -> pywinauto win32 friendly class names it covers.
_UIA_TO_WIN32: dict[str, frozenset[str]] = {
    "Button": frozenset({"Button"}),
    "CheckBox": frozenset({"CheckBox"}),
    "RadioButton": frozenset({"RadioButton"}),
    "Group": frozenset({"GroupBox"}),
    "Edit": frozenset({"Edit"}),
    "ComboBox": frozenset({"ComboBox", "ComboBoxEx"}),
    "List": frozenset({"ListBox", "ListView"}),
    "Tree": frozenset({"TreeView"}),
    "Tab": frozenset({"TabControl"}),
    "ToolBar": frozenset({"Toolbar"}),
    "StatusBar": frozenset({"StatusBar"}),
    "ProgressBar": frozenset({"Progress"}),
    "Slider": frozenset({"Trackbar"}),
    "Spinner": frozenset({"UpDown"}),
    "Header": frozenset({"Header"}),
    "Text": frozenset({"Static"}),
    "Calendar": frozenset({"Calendar"}),
}

# Friendly class names pywinauto's win32 wrappers report. Callers coming from
# Win32 tooling (Spy++, pywinauto's own print_control_identifiers) spell the
# type this way, so these are accepted as control types verbatim.
_WIN32_FRIENDLY_NAMES: frozenset[str] = frozenset(
    {
        "Animation",
        "Button",
        "Calendar",
        "CheckBox",
        "ComboBox",
        "ComboBoxEx",
        "DateTimePicker",
        "Dialog",
        "Edit",
        "GroupBox",
        "Header",
        "Hotkey",
        "IPAddress",
        "ListBox",
        "ListView",
        "Pager",
        "PopupMenu",
        "Progress",
        "RadioButton",
        "ReBar",
        "Static",
        "StatusBar",
        "TabControl",
        "Toolbar",
        "ToolTips",
        "Trackbar",
        "TreeView",
        "UpDown",
    }
)

# UIA ControlType names that have no Win32 window class of their own.
_UIA_WITHOUT_WIN32_CLASS: frozenset[str] = frozenset(
    {
        "AppBar",
        "Custom",
        "DataGrid",
        "DataItem",
        "Document",
        "HeaderItem",
        "Hyperlink",
        "Image",
        "ListItem",
        "Menu",
        "MenuBar",
        "MenuItem",
        "Pane",
        "ScrollBar",
        "SemanticZoom",
        "Separator",
        "SplitButton",
        "TabItem",
        "Table",
        "Thumb",
        "TitleBar",
        "ToolTip",
        "TreeItem",
        "Window",
    }
)


def backend_name(scope: Any) -> str | None:
    """Return ``"win32"`` / ``"uia"`` for a pywinauto spec or wrapper, else ``None``.

    ``WindowSpecification`` and pywinauto wrappers both carry a ``backend``
    registry entry with a string ``name``. Anything else (a test double, a
    non-pywinauto scope) yields ``None`` and is left untranslated.
    """
    name = getattr(getattr(scope, "backend", None), "name", None)
    return name if isinstance(name, str) else None


def win32_friendly_class_name(element_info: Any) -> str | None:
    """pywinauto's friendly class name for a win32 element, or ``None`` if gone."""
    try:
        from pywinauto.controls.hwndwrapper import (  # type: ignore[import-untyped]
            HwndWrapper,
        )

        return str(HwndWrapper(element_info).friendly_class_name())
    except Exception:
        return None


def _class_predicate(
    accepted: frozenset[str],
    user_predicate: Callable[[Any], bool] | None,
) -> Callable[[Any], bool]:
    def matches(element_info: Any) -> bool:
        if win32_friendly_class_name(element_info) not in accepted:
            return False
        return user_predicate is None or bool(user_predicate(element_info))

    return matches


def accepted_win32_classes(control_type: str) -> frozenset[str] | None:
    """Friendly class names *control_type* selects on win32.

    ``None`` means the value is passed through untouched: a dotted .NET type
    such as ``"System.Windows.Forms.TextBox"`` (pywinauto's native win32
    meaning of ``control_type``) or a name this module does not recognise.

    Raises:
        UnsupportedCapabilityError: for a UIA control type with no Win32
            window class.
    """
    if "." in control_type:
        return None
    mapped = _UIA_TO_WIN32.get(control_type)
    if mapped is not None:
        return mapped
    if control_type in _WIN32_FRIENDLY_NAMES:
        return frozenset({control_type})
    if control_type in _UIA_WITHOUT_WIN32_CLASS:
        supported = ", ".join(sorted(set(_UIA_TO_WIN32) | _WIN32_FRIENDLY_NAMES))
        raise UnsupportedCapabilityError(
            f"control_type={control_type!r} is a UI Automation type with no Win32 "
            "window class, so the win32 backend cannot locate it",
            hint=(
                "select it with class_name / class_name_re / title instead, use the "
                f"uia backend, or use one of: {supported}"
            ),
        )
    return None


def criteria_for_backend(scope: Any, criteria: dict[str, Any]) -> dict[str, Any]:
    """Return *criteria* as the backend of *scope* understands them.

    Only the ``win32`` backend is affected. Its UIA-style ``control_type`` is
    replaced by a ``predicate_func`` on pywinauto's friendly class name,
    combined with any ``predicate_func`` the caller already passed. The input
    dict is never modified.
    """
    control_type = criteria.get("control_type")
    if not isinstance(control_type, str) or not control_type:
        return criteria
    if backend_name(scope) != "win32":
        return criteria
    accepted = accepted_win32_classes(control_type)
    if accepted is None:
        return criteria
    translated = {key: value for key, value in criteria.items() if key != "control_type"}
    translated["predicate_func"] = _class_predicate(accepted, criteria.get("predicate_func"))
    return translated


def find_win32_elements(
    parent: Any,
    criteria: dict[str, Any],
    *,
    visible_only: bool,
    depth: int | None = None,
) -> list[Any]:
    """Return the elements under *parent* that ``child_window(**criteria)`` selects from.

    ``HwndElementInfo.children()`` and ``descendants()`` honour only
    ``class_name``, an exact ``title`` and ``control_type``, and silently ignore
    every other keyword (``control_id``, ``title_re``, ``class_name_re``,
    ``predicate_func``...). Counting or listing through them therefore disagrees
    with the lookup that follows. ``find_elements`` is the search ``child_window``
    itself runs and it enumerates the same windows, so anything computed from
    this list holds for the final lookup. *criteria* must not hold ``found_index``.
    """
    from pywinauto.findwindows import find_elements  # type: ignore[import-untyped]

    search = {"top_level_only": False, "visible_only": visible_only, **criteria}
    found: list[Any] = find_elements(parent=parent, backend="win32", depth=depth, **search)
    return found
