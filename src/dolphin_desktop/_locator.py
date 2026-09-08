"""Locator — lazy, chainable element finder (core of the dolphin API)."""

from __future__ import annotations

import copy
import inspect
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

if sys.platform == "win32":
    from pywinauto.keyboard import send_keys as _send_keys
else:
    from ._platform_compat import _unsupported_callable

    _send_keys = _unsupported_callable("pywinauto.keyboard.send_keys")

from ._config import get_poll_interval as _get_poll_interval
from ._config import get_timeout as _get_timeout
from ._exceptions import ElementNotFoundError, UnsupportedPatternError, WaitTimeoutError
from ._helpers import _MISSING, _escape_keys


def _wrapper_of(element: Any) -> Any:
    """Return the concrete pywinauto wrapper behind *element*.

    ``_resolve()`` normally hands back a ``WindowSpecification``, whose
    ``__getattribute__`` answers an unknown name with
    ``child_window(best_match=name)`` — another ``WindowSpecification`` —
    instead of raising. ``getattr(spec, name, None)`` can therefore never
    report a missing wrapper method; only the wrapper itself can be probed.
    Objects that are already wrappers (fallback / tree-walk / image results)
    are returned unchanged.
    """
    if getattr(type(element), "wrapper_object", None) is None:
        return element
    try:
        return element.wrapper_object()
    except Exception:
        return element


def _ensure_element_present(element: Any) -> None:
    """Raise when a previously resolved UIA element is no longer usable.

    ``UIAWrapper.is_visible()`` is not a liveness check: a live hidden element
    is valid, while a stale element can still be represented by the Python
    wrapper.  UIA's raw ``IUIAutomationElement`` exposes current properties
    through COM, and those calls fail with ``ElementNotAvailable`` after the
    control is destroyed or its provider is torn down.  Probe a non-visual
    property so hidden controls remain present.

    Non-UIA wrappers and lightweight test doubles do not expose the raw UIA
    element; their existing behaviour is left unchanged.
    """
    element_info = getattr(element, "element_info", None)
    raw_element = getattr(element_info, "element", None)
    if raw_element is None:
        return
    try:
        _ = raw_element.CurrentProcessId
    except Exception as exc:
        raise ElementNotFoundError(
            "the previously resolved UIA element is no longer available"
        ) from exc


# comtypes surfaces a pattern that exists but refuses the call as a bare
# COMError; E_ACCESSDENIED is what ValuePattern.SetValue returns on a
# read-only element.
_E_ACCESSDENIED = -2147024891  # 0x80070005


def _takes_exactly(method: Any, args: tuple) -> bool:
    """Return True when *method* accepts exactly the arguments we will pass.

    Dispatch is by method NAME, and several pywinauto wrappers spell an
    unrelated operation with the same name: ``TabControlWrapper.select(item)``
    and ``TreeViewWrapper.select(path)`` would raise a bare ``TypeError``,
    while ``EditWrapper.select(start=0, end=None)`` is worse — it selects the
    edit's text and reports the SelectionItem action as done. A signature that
    cannot be introspected (C functions, mocks, ``*args`` wrappers) is
    accepted; only a positively mismatched one disqualifies the method.
    """
    try:
        params = list(inspect.signature(method).parameters.values())
    except (TypeError, ValueError):
        return True
    if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params):
        return True
    return len(params) == len(args)


def _pattern_action(
    self: Locator,
    *,
    action_name: str,
    pattern_name: str,
    method_name: str,
    method_args: tuple = (),
    hint_extra: str = "",
    iface_fallback: Callable[..., Any] | None = None,
) -> Locator:
    """Shared implementation for every programmatic-pattern action.

    Resolves the underlying UIA wrapper, dispatches ``method_name`` on it
    (passing ``method_args``), and converts pywinauto's
    ``NoPatternInterfaceError`` — plus any ``AttributeError`` when the
    wrapper class does not expose that method at all — into a
    :class:`UnsupportedPatternError` with a hint pointing at the
    matching input-based action.

    *iface_fallback* is called as ``iface_fallback(wrapper, *method_args)``
    when the wrapper class has no ``method_name``; it lets an action reach a
    pattern that pywinauto publishes only as a raw ``iface_*`` interface.

    Deliberate: **no fallback to a physical click**. Programmatic
    actions must fail cleanly when the pattern is missing so tests can
    either target a different element or drop back to the physical
    path explicitly. Silent fallback would defeat the "headless-safe"
    contract.
    """
    element = None
    resolved = False
    target: Any = None
    try:
        element = self._resolve()
        resolved = True
        target = _wrapper_of(element)
        method = getattr(target, method_name, None)
        if method is not None and not _takes_exactly(method, method_args):
            method = None
        if method is None and iface_fallback is not None:
            iface_fallback(target, *method_args)
        elif method is None:
            raise UnsupportedPatternError(
                f"{action_name}() called on an element that does not expose "
                f"{pattern_name} (wrapper {type(target).__name__!r} has no "
                f"{method_name!r} method taking {len(method_args)} argument(s))",
                hint=hint_extra
                or "use the input-based action instead (e.g. click(), type_text()), "
                "or verify the selector matched the intended widget",
            )
        else:
            method(*method_args)
    except UnsupportedPatternError as exc:
        _trace_step(action_name, self._criteria, element=element, error=str(exc))
        raise
    except Exception as exc:
        # pywinauto raises NoPatternInterfaceError (subclass of Exception)
        # when the element exists but does not implement the pattern.
        # Detect by class name to avoid a hard pywinauto import here.
        # An AttributeError means the same thing one level up: either the
        # wrapper class has no such method, or a WindowSpecification stood in
        # for it and raised "Neither GUI element (wrapper) nor wrapper method
        # '...' were found" from __call__.
        # E_ACCESSDENIED is the pattern answering "I implement this but refuse
        # the write" — a read-only Edit does publish ValuePattern.
        # ``resolved`` gates all of it: a failure inside ``_resolve()`` is not
        # a pattern verdict, and ``target`` would still be None.
        if resolved and (
            type(exc).__name__ == "NoPatternInterfaceError"
            or isinstance(exc, AttributeError)
            or getattr(exc, "hresult", None) == _E_ACCESSDENIED
        ):
            _trace_step(action_name, self._criteria, element=element, error=str(exc))
            raise UnsupportedPatternError(
                f"{action_name}() failed — element does not implement "
                f"{pattern_name} (wrapper {type(target).__name__!r})",
                hint=hint_extra
                or "the widget exposes the wrapper but not the pattern; "
                "use the input-based action (click(), type_text()) in headed mode",
            ) from exc
        _trace_step(action_name, self._criteria, element=element, error=str(exc))
        raise
    _trace_step(action_name, self._criteria, element=element)
    return self


def _set_value_via_iface(wrapper: Any, text: str) -> None:
    """Set *wrapper*'s value through ``IUIAutomationValuePattern``.

    ``set_value`` is defined only on ``uia_controls.SliderWrapper`` and the
    win32 trackbar; Edit / ComboBox / plain ``UIAWrapper`` reach ValuePattern
    exclusively through ``iface_value``.
    """
    wrapper.iface_value.SetValue(text)


def _toggle_via_iface(wrapper: Any) -> None:
    """Flip *wrapper*'s toggle state through ``IUIAutomationTogglePattern``.

    ``toggle()`` is defined only on ``uia_controls.ButtonWrapper``; checkable
    MenuItem / ListItem / TreeItem inherit plain ``UIAWrapper``.
    """
    wrapper.iface_toggle.Toggle()


def _wrapper_object_until(spec: Any, deadline: float, timeout: float) -> Any:
    """Resolve a ``WindowSpecification`` without borrowing pywinauto's 5 s wait.

    ``WindowSpecification.wrapper_object()`` does not expose a timeout and
    delegates to pywinauto's global ``Timings.window_find_timeout``.  Its
    private resolver does accept one, so use that path when the object is a
    real pywinauto ``WindowSpecification``.  The public method remains the
    fallback for compatible test doubles and other specification-like objects.

    A single COM/UIA search can still take as long as the underlying provider
    needs; this bounds pywinauto's retry loop and prevents its independent
    multi-second wait from extending a DolphinDesktop action timeout.
    """
    resolve_control = getattr(type(spec), "_WindowSpecification__resolve_control", None)
    criteria = getattr(spec, "criteria", None)
    if not callable(resolve_control) or criteria is None:
        return spec.wrapper_object()

    remaining = max(0.0, deadline - time.monotonic()) if timeout > 0 else 0.0
    controls = resolve_control(
        spec,
        criteria,
        timeout=remaining,
        retry_interval=_get_poll_interval(),
    )
    return controls[-1]


def _wait_until_visible(spec: Any, timeout: float) -> None:
    """Block until *spec* resolves to a visible element, else raise.

    Replaces ``spec.wait("exists visible")``, which costs **three** full UIA
    tree scans per poll: one for the ``exists`` check, a second re-resolve to
    call ``is_visible()`` on, and a third for the wrapper ``wait()`` returns —
    which the caller here discards, since it goes on to use the
    WindowSpecification. A scan is ~0.4 s against a small window, so those
    three dominated every resolve. One scan answers both questions.

    An ambiguous match propagates immediately rather than being retried: more
    matches will not appear, so polling until timeout would only turn a
    precise "narrow your criteria" into a misleading "not found".
    """
    from pywinauto.findwindows import (  # type: ignore[import-untyped]
        ElementAmbiguousError as _PwAmbiguousError,
    )
    from pywinauto.timings import TimeoutError as _PwTimeoutError

    deadline = time.monotonic() + timeout
    poll = _get_poll_interval()
    last_exc: Exception | None = None
    first_attempt = True
    while True:
        if not first_attempt and timeout > 0 and time.monotonic() >= deadline:
            break
        first_attempt = False
        try:
            # Not ``_wrapper_of``: that helper answers a "not found" by
            # returning the WindowSpecification unchanged, whose
            # ``__getattribute__`` would then turn ``is_visible`` into a
            # child_window lookup instead of raising.
            visible = _wrapper_object_until(spec, deadline, timeout).is_visible()
            if visible:
                # A zero timeout means "try once" throughout this module.
                # For a positive timeout, however, the probe itself must also
                # finish before the deadline; otherwise a slow UIA resolve
                # silently turns a timed-out action into a successful one.
                if timeout <= 0 or time.monotonic() < deadline:
                    return
                break
            last_exc = None
        except _PwAmbiguousError:
            raise
        except Exception as exc:
            last_exc = exc
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    raise _PwTimeoutError(f"element did not become visible within {timeout}s") from last_exc


def _wait_until_present(spec: Any, timeout: float) -> None:
    """Block until *spec* yields a wrapper, without checking visibility."""
    from pywinauto.findwindows import (  # type: ignore[import-untyped]
        ElementAmbiguousError as _PwAmbiguousError,
    )
    from pywinauto.timings import TimeoutError as _PwTimeoutError

    deadline = time.monotonic() + timeout
    poll = _get_poll_interval()
    last_exc: Exception | None = None
    first_attempt = True
    while True:
        if not first_attempt and timeout > 0 and time.monotonic() >= deadline:
            break
        first_attempt = False
        try:
            _wrapper_object_until(spec, deadline, timeout)
            if timeout <= 0 or time.monotonic() < deadline:
                return
            break
        except _PwAmbiguousError:
            raise
        except Exception as exc:
            last_exc = exc
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    raise _PwTimeoutError(f"element did not become present within {timeout}s") from last_exc


def _is_foreground(spec: Any) -> bool:
    """True when *spec* is the active top-level window.

    Answers "would ``set_focus()`` be a no-op?", so an unknown answer must be
    ``False``: the caller only skips the focus on a definite yes, and a
    background AUT that never gets brought forward silently sends every click
    to whatever window is in front of it.
    """
    try:
        import win32gui  # type: ignore[import-untyped]

        return bool(win32gui.GetForegroundWindow() == spec.wrapper_object().handle)
    except Exception:
        return False


def _trace_step(
    action: str,
    criteria: dict,
    element: Any = None,
    error: str | None = None,
) -> None:
    from . import _trace

    session = _trace.current_session()
    if session is not None:
        session.record_step(action, repr(criteria), element, error)


if TYPE_CHECKING:
    from PIL.Image import Image

    from ._window import Window


# Friendly aliases for pywinauto criteria keys — the same dialect the YAML
# Object Repository accepts (see ``objects.py``). Unmapped, these spellings
# reach pywinauto's ``find_elements`` as unknown kwargs and resolve to a
# silent "not found".
_CRITERIA_ALIASES: dict[str, str] = {
    "automation_id": "auto_id",
    "role": "control_type",
    "name": "title",
}


def _normalize_criteria(criteria: dict[str, Any]) -> dict[str, Any]:
    """Rewrite alias keys to their canonical pywinauto spellings, in place."""
    for alias, canonical in _CRITERIA_ALIASES.items():
        if alias not in criteria:
            continue
        value = criteria.pop(alias)
        if canonical in criteria and criteria[canonical] != value:
            raise ValueError(
                f"conflicting criteria: {alias}={value!r} and "
                f"{canonical}={criteria[canonical]!r} — {alias!r} is an alias "
                f"for {canonical!r}; pass only one"
            )
        criteria[canonical] = value
    return criteria


class Locator:
    """Represents a way to find one or more UI elements.

    Locators are lazy — they do not search for elements until an action or
    assertion method is called.

    Criteria accept the canonical pywinauto keys (``auto_id``,
    ``control_type``, ``title``…) plus the YAML-friendly aliases
    ``automation_id``, ``role`` and ``name`` — the same dialect the
    Object Repository uses.

    Usage::

        btn = window.get_by_title("OK", control_type="Button")
        btn.click()

        edit = window.get_by_role("Edit")
        edit.type_text("hello")
        assert edit.text() == "hello"
    """

    def __init__(
        self,
        parent: Window | Locator,
        **criteria: Any,
    ) -> None:
        self._parent = parent
        self._fallback: list[dict[str, Any]] = [
            _normalize_criteria(dict(fb)) for fb in (criteria.pop("fallback", None) or [])
        ]
        self._image_fallback: Any = criteria.pop("image_fallback", None)
        self._criteria = _normalize_criteria(criteria)
        self._timeout: float = _get_timeout()

    # Configuration

    def _clone(self, **criteria: Any) -> Self:
        """Return a copy of this locator with *criteria* merged in.

        Copy-based rather than ``type(self)(parent, **criteria)``: subclasses
        (:class:`MenuItem`, ``_QtObjectNameLocator``, ``_ResolvedLocator``)
        carry extra state and do not all share the base ``__init__``
        signature. Rebuilding them positionally would silently downgrade a
        ``MenuItem`` to a plain ``Locator`` and lose its parent-menu-opening
        ``_resolve``.
        """
        clone = copy.copy(self)
        clone._criteria = {**self._criteria, **_normalize_criteria(criteria)}
        return clone

    def timeout(self, seconds: float) -> Self:
        """Return a new Locator with a different timeout (does not mutate self)."""
        if not math.isfinite(seconds):
            raise ValueError("timeout must be finite")
        if seconds < 0:
            raise ValueError("timeout must be non-negative")
        clone = self._clone()
        clone._timeout = seconds
        return clone

    # Chaining

    def locator(self, **criteria: Any) -> Locator:
        """Find a descendant element matching *criteria* inside this element."""
        return Locator(self, **criteria)

    def nth(self, index: int) -> Self:
        """Select the nth match (0-based) from a set of matching elements.

        Subclasses whose ``_resolve`` cannot honour ``found_index`` override
        this and raise — an index that is silently dropped would return match
        #0 while the caller believes it addressed match #N.
        """
        return self._clone(found_index=index)

    # Resolution (internal)

    def _get_parent_spec(self) -> Any:
        if isinstance(self._parent, Locator):
            return self._parent._resolve()
        return self._parent._get_spec()

    def _get_parent_presence_spec(self) -> Any:
        if isinstance(self._parent, Locator):
            return self._parent._resolve_presence()
        return self._parent._get_spec()

    def _resolve(self) -> Any:
        """Find and wait for the element, raise on timeout."""
        parent_spec = self._get_parent_spec()

        # A parent that already resolved to a raw wrapper (a chained
        # _ResolvedLocator / _QtObjectNameLocator, a fallback or tree-walk
        # match) has no child_window — that lives on WindowSpecification only.
        if not hasattr(parent_spec, "child_window"):
            return _find_under_wrapper(parent_spec, self._criteria, self._timeout)

        from pywinauto.findwindows import (  # type: ignore[import-untyped]
            ElementAmbiguousError as _PwAmbiguousError,
        )

        primary_exc: Exception | None = None
        criteria = self._criteria
        try:
            if _is_negative_found_index(criteria):
                resolved = _resolve_negative_found_index(parent_spec, criteria, self._timeout)
                if isinstance(resolved, dict):
                    criteria = resolved
                else:
                    return resolved
            spec = parent_spec.child_window(**criteria)
            _wait_until_visible(spec, self._timeout)
            return spec
        except _PwAmbiguousError as exc:
            # More than one element matched. Falling through to fallbacks
            # would misreport this as "not found" — the elements are there,
            # the criteria are under-specified.
            from ._exceptions import AmbiguousMatchError

            raise AmbiguousMatchError(
                f"{self._criteria!r} matched more than one element — "
                f"narrow the criteria or pick one with found_index=N"
            ) from exc
        except Exception as exc:
            primary_exc = exc

        # Try fallbacks in definition order (immediate check — primary already timed out)
        from . import _selfheal

        for fb in self._fallback:
            try:
                fb_spec = parent_spec.child_window(**fb)
                fb_spec.wait("visible", timeout=0)
                _selfheal.record_fallback(self._criteria, fb)
                return fb_spec
            except Exception:
                continue

        # Last resort: image-based fallback
        if self._image_fallback is not None:
            try:
                result = self._image_fallback.find_with_size()
                if result is not None:
                    cx, cy, tw, th = result
                    from ._image import _ImageElement

                    _selfheal.record_fallback(
                        self._criteria,
                        {"image": str(self._image_fallback._template_path)},
                    )
                    return _ImageElement(cx, cy, tw, th)
            except Exception:
                pass

        # TreeWalker fallback: IUIAutomation::FindAll misses some elements exposed
        # via TreeWalker (e.g. ToolbarWindow32 button children in Notepad++).
        tree_result = _tree_walk_find(parent_spec, self._criteria)
        if tree_result is not None:
            return tree_result

        raise ElementNotFoundError(
            _NotFoundMessage(self._criteria, self._timeout, parent_spec)
        ) from primary_exc

    def _resolve_readonly(self) -> Any:
        """Resolve without mutating the UI — the path predicates use.

        ``is_visible`` / ``is_enabled`` / ``exists`` must answer a question,
        not perform an action. Subclasses whose ``_resolve`` activates
        something (``MenuItem`` opens its parent menu) override this.
        """
        return self._resolve()

    def _focus_for_input(self) -> None:
        """Best-effort foreground focus before physical (pointer) input.

        Physical input lands on whatever window owns the screen coordinates,
        so an AUT sitting behind another window would receive the event on the
        wrong target — generated test scripts run in a separate pytest
        process, which makes this the common case. Failures are swallowed: a
        refused ``set_focus`` must not abort an action ``click_input`` can
        still deliver.

        The **root window** is focused, never the immediate parent: resolving
        a parent locator can itself drive input (``MenuItem._resolve`` clicks
        its parent menu open), and the action's own ``_resolve`` would then
        repeat that click and toggle the menu shut again. Walking to the root
        also spares every pointer action a redundant parent-chain resolve.

        An already-active window is left alone. Skipping the call is not an
        optimisation: activating a window closes the transient popups it
        owns, so re-focusing the window a combo-box dropdown or context menu
        belongs to would dismiss the very list the caller is about to click
        in. Bringing a *background* AUT forward is worth that risk; doing it
        to the foreground one buys nothing and costs the popup.
        """
        try:
            node: Any = self
            while isinstance(node, Locator):
                parent = getattr(node, "_parent", None)
                if parent is None:
                    # _ResolvedLocator carries no parent chain — focusing the
                    # element it already holds is the equivalent gesture.
                    node._resolve().set_focus()
                    return
                node = parent
            spec = node._get_spec()
            if not _is_foreground(spec):
                spec.set_focus()
        except Exception:
            pass

    # Actions

    def click(self, timeout_ms: int | None = None) -> Locator:
        if timeout_ms is not None:
            self.timeout(timeout_ms / 1000.0).click()
            return self
        element = None
        try:
            self._focus_for_input()
            element = self._resolve()
            element.click_input()
        except Exception as exc:
            _trace_step("click", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("click", self._criteria, element=element)
        return self

    def invoke(self) -> Locator:
        """Fire the element's default action via UIA ``InvokePattern``.

        The programmatic — headless-safe — counterpart to :meth:`click`.
        Sends the same command a screen reader or accessibility tool
        would send: no cursor movement, no mouse events, no window
        focus requirements. Works under ``dolphin-run`` / headless
        mode and over locked / RDP sessions.

        Applicable to elements whose UIA control type implements
        ``IUIAutomationInvokePattern`` — Buttons, MenuItems, Hyperlinks,
        TreeItem expanders on some themes.

        Raises :class:`UnsupportedPatternError` when the element does
        NOT implement InvokePattern (typically CheckBoxes → use
        :meth:`toggle`, ListItems → use :meth:`select`, etc.). No
        silent fallback: the caller must pick the right primitive so
        the difference stays visible in tests.

        Usage::

            window.button(name="Save").invoke()
            window.menu_item(name="Exit").invoke()

        See also: :meth:`click` for the input-simulation counterpart
        (fires hover/mouseover handlers but needs a real desktop).
        """
        return _pattern_action(
            self,
            action_name="invoke",
            pattern_name="InvokePattern",
            method_name="invoke",
            hint_extra=(
                "for checkboxes use .toggle(); for radio buttons / list "
                "items use .select(); for tree nodes use .expand() / "
                ".collapse(); for headed E2E fallback use .click()"
            ),
        )

    def toggle(self) -> Locator:
        """Flip the element's checked state via UIA ``TogglePattern``.

        Programmatic (headless-safe) counterpart to clicking a checkbox
        or toggle button. Applicable to elements implementing
        ``IUIAutomationTogglePattern`` — CheckBoxes, checkable
        MenuItems, ToolBar toggle buttons.

        Raises :class:`UnsupportedPatternError` when the element is not
        toggleable — use :meth:`select` for radio buttons or
        :meth:`invoke` for regular buttons instead.

        Usage::

            window.check_box(name="Enable feature").toggle()
        """
        return _pattern_action(
            self,
            action_name="toggle",
            pattern_name="TogglePattern",
            method_name="toggle",
            iface_fallback=_toggle_via_iface,
            hint_extra=(
                "for radio buttons use .select(); for regular buttons use "
                ".invoke(); for headed E2E use .click()"
            ),
        )

    def expand(self) -> Locator:
        """Expand the element via UIA ``ExpandCollapsePattern``.

        Applicable to Tree nodes, ComboBoxes, menu expanders, tab
        headers with drop-down arrows — anything that exposes an
        expand/collapse UI without simulating a mouse click.

        Raises :class:`UnsupportedPatternError` when the element is not
        expandable.

        Usage::

            window.tree_item(name="Root").expand()
            window.combo_box(name="Country").expand()
        """
        return _pattern_action(
            self,
            action_name="expand",
            pattern_name="ExpandCollapsePattern",
            method_name="expand",
            hint_extra=(
                "the element does not expose expand/collapse; use .click() "
                "in headed mode if the widget only responds to mouse events"
            ),
        )

    def collapse(self) -> Locator:
        """Collapse the element via UIA ``ExpandCollapsePattern``.

        Inverse of :meth:`expand`. Raises
        :class:`UnsupportedPatternError` when the element is not
        collapsible.

        Usage::

            window.tree_item(name="Root").collapse()
        """
        return _pattern_action(
            self,
            action_name="collapse",
            pattern_name="ExpandCollapsePattern",
            method_name="collapse",
            hint_extra=(
                "the element does not expose expand/collapse; use .click() "
                "in headed mode if the widget only responds to mouse events"
            ),
        )

    def select(self) -> Locator:
        """Select the element via UIA ``SelectionItemPattern``.

        Applicable to elements INSIDE a selectable container —
        ListItems, TabItems, RadioButtons, TreeItems participating in
        a selection group. Not for opening menus (use :meth:`invoke`)
        or checking boxes (use :meth:`toggle`).

        Raises :class:`UnsupportedPatternError` when the element is
        not a selection item.

        Usage::

            window.list_item(name="Third row").select()
            window.tab(name="Advanced").select()
            window.radio_button(name="Premium").select()
        """
        return _pattern_action(
            self,
            action_name="select",
            pattern_name="SelectionItemPattern",
            method_name="select",
            hint_extra=(
                "for buttons use .invoke(); for checkboxes use .toggle(); "
                "for combo/list containers use .expand() then .select() on "
                "the child item; for headed E2E use .click()"
            ),
        )

    def set_value(self, text: str) -> Locator:
        """Set the element's text via UIA ``ValuePattern.SetValue`` (no keystrokes).

        Programmatic (headless-safe) counterpart to :meth:`type_text` /
        :meth:`set_text`. Applicable to Edits, ComboBoxes with editable
        text, Slider values published through ValuePattern.

        Because this bypasses the keyboard entirely, ``KeyDown`` /
        ``KeyUp`` handlers on the target widget will NOT fire. Prefer
        :meth:`type_text` when the test needs to exercise real-time
        input validation; prefer :meth:`set_value` when the test only
        cares about the resulting field value.

        Raises :class:`UnsupportedPatternError` when the element does
        not implement ValuePattern (some XAML TextBox variants) — and
        equally when it implements it read-only, where ``SetValue``
        answers ``E_ACCESSDENIED`` instead of raising
        ``NoPatternInterfaceError``.

        Usage::

            window.edit(auto_id="txtName").set_value("Alice")
        """
        return _pattern_action(
            self,
            action_name="set_value",
            pattern_name="ValuePattern",
            method_name="set_value",
            method_args=(text,),
            iface_fallback=_set_value_via_iface,
            hint_extra=(
                "the element is either read-only or does not expose "
                "ValuePattern; use .type_text() to send keystrokes instead"
            ),
        )

    def double_click(self, timeout_ms: int | None = None) -> Locator:
        if timeout_ms is not None:
            self.timeout(timeout_ms / 1000.0).double_click()
            return self
        element = None
        try:
            self._focus_for_input()
            element = self._resolve()
            element.double_click_input()
        except Exception as exc:
            _trace_step("double_click", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("double_click", self._criteria, element=element)
        return self

    def right_click(self) -> Locator:
        element = None
        try:
            self._focus_for_input()
            element = self._resolve()
            element.right_click_input()
        except Exception as exc:
            _trace_step("right_click", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("right_click", self._criteria, element=element)
        return self

    def type_text(
        self,
        text: str,
        *,
        with_spaces: bool = True,
        pause: float = 0.05,
        escape: bool = True,
    ) -> Locator:
        """Type text character by character (sends WM_CHAR events).

        *text* is literal: pywinauto's ``type_keys`` metacharacters
        (``+ ^ % ~ ( ) { }``) are escaped, so ``"50% off"`` types a percent
        sign instead of pressing Alt and ``"(x86)"`` keeps its parentheses.
        Pass ``escape=False`` to send a raw pywinauto key sequence
        (``"{ENTER}"``, ``"^c"``) — :meth:`press_key` is the preferred
        spelling for that.

        The default pause of 0.05 s between keystrokes matches pywinauto's own
        default and prevents missed/doubled keys in modern WinUI/XAML controls.
        """
        element = None
        try:
            element = self._resolve()
            payload = _escape_keys(text) if escape else text
            # with_tabs / with_newlines are not optional the way with_spaces is:
            # parse_keys silently discards a literal \t or \n when its flag is
            # off, and escaping has already turned pywinauto's "~" newline alias
            # into a literal "{~}" — so type_text("line1\nline2") typed
            # "line1line2" and reported success.
            element.type_keys(
                payload,
                with_spaces=with_spaces,
                with_tabs=True,
                with_newlines=True,
                pause=pause,
            )
        except Exception as exc:
            _trace_step("type_text", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("type_text", self._criteria, element=element)
        return self

    def set_text(self, text: str) -> Locator:
        """Replace the entire text content of an edit control.

        Falls back to select-all + keyboard input for Document/RichEdit controls
        (e.g. Windows 11 Notepad) that do not support IValueProvider.SetValue.
        """
        element = None
        try:
            element = self._resolve()
            try:
                element.set_edit_text(text)
                _trace_step("set_text", self._criteria, element=element)
                return self
            except Exception:
                pass
            element.set_focus()
            time.sleep(0.05)
            _send_keys("^a")
            if text:
                # All three flags, not just with_spaces: parse_keys drops a
                # literal \t or \n outright when its flag is off, and escaping
                # turns pywinauto's own "~" newline alias into a literal "{~}",
                # so there is no surviving path for either. Without them
                # set_text("line1\nline2") wrote "line1line2" and reported
                # success.
                element.type_keys(
                    _escape_keys(text),
                    with_spaces=True,
                    with_tabs=True,
                    with_newlines=True,
                    pause=0.05,
                )
            else:
                _send_keys("{DELETE}")
        except Exception as exc:
            _trace_step("set_text", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("set_text", self._criteria, element=element)
        return self

    def clear(self) -> Locator:
        """Clear the text of an edit control."""
        element = None
        try:
            element = self._resolve()
            try:
                element.set_edit_text("")
                _trace_step("clear", self._criteria, element=element)
                return self
            except Exception:
                pass
            element.set_focus()
            time.sleep(0.05)
            _send_keys("^a{DELETE}")
        except Exception as exc:
            _trace_step("clear", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("clear", self._criteria, element=element)
        return self

    def press_key(self, key: str, timeout_ms: int | None = None) -> Locator:
        """Send a key sequence to the element using pywinauto key syntax."""
        if timeout_ms is not None:
            self.timeout(timeout_ms / 1000.0).press_key(key)
            return self
        element = None
        try:
            element = self._resolve()
            element.type_keys(key)
        except Exception as exc:
            _trace_step("press_key", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("press_key", self._criteria, element=element)
        return self

    def select_item(self, item: str | int) -> Locator:
        """Select an item in a list/combo box by text or 0-based index."""
        element = None
        try:
            element = self._resolve()
        except Exception as exc:
            _trace_step("select_item", self._criteria, error=str(exc))
            raise
        try:
            # Win32 backend and UIA ComboBox expose select(item).
            element.select(item)
        except TypeError:
            # UIA ListBox: UIAWrapper.select() takes no arguments.
            # Find the child ListItem and call select() on it directly.
            if isinstance(item, int):
                element.children(control_type="ListItem")[item].select()
            else:
                element.child_window(title=item, control_type="ListItem").wrapper_object().select()
        except ValueError:
            # WinForms ComboBox (UIA): pywinauto's select() raises ValueError from
            # selected_index() when no item is pre-selected and ISelectionItemProvider
            # is unavailable on child elements.  Fall back to expand → invoke.
            if isinstance(item, int):
                try:
                    element.expand()
                    time.sleep(0.05)
                except Exception:
                    pass
                items = element.children(control_type="ListItem")
                if not items:
                    # WinForms pattern: items live under a List child
                    lists = element.children(control_type="List")
                    if lists:
                        items = lists[0].children(control_type="ListItem")
                if items:
                    items[item].invoke()
                else:
                    element.descendants(control_type="ListItem")[item].click_input()
            else:
                element.child_window(title=item, control_type="ListItem").wrapper_object().select()
        except Exception as exc:
            # Win32 combo box (e.g. VCL TUIStateAwareComboBox): not recognised by
            # pywinauto as ComboBoxWrapper → no select() method → use CB_* messages.
            try:
                import win32gui

                hwnd = element.handle
                if isinstance(item, str):
                    idx = win32gui.SendMessage(hwnd, 0x0158, -1, item)  # CB_FINDSTRINGEXACT
                    if idx == -1:
                        idx = win32gui.SendMessage(hwnd, 0x014C, -1, item)  # CB_FINDSTRING
                else:
                    idx = item
                if idx != -1:
                    win32gui.SendMessage(hwnd, 0x014E, idx, 0)  # CB_SETCURSEL
                    # Notify parent of selection change (VCL needs CBN_SELCHANGE = 1)
                    parent_hwnd = win32gui.GetParent(hwnd)
                    ctrl_id = win32gui.GetDlgCtrlID(hwnd)
                    win32gui.SendMessage(
                        parent_hwnd, 0x0111, (1 << 16) | ctrl_id, hwnd
                    )  # WM_COMMAND/CBN_SELCHANGE
                    _trace_step("select_item", self._criteria, element=element)
                    return self
            except Exception:
                pass
            # Qt ComboBox: the dropdown opens as a detached popup window in the UIA
            # tree — child_window() finds nothing.  After expand(), scan the process's
            # top-level windows for a popup that contains ListItems.
            _select_via_popup(element, item, self._timeout, cause=exc)
        _trace_step("select_item", self._criteria, element=element)
        return self

    def select_item_keyboard(self, item: str | int) -> Locator:
        """Select an item by simulating keyboard navigation.

        Workaround for Qt's ``QComboBox`` / ``QListWidget`` quirk: the
        UIA ``SelectionItem.Select`` pattern only flips ``IsSelected`` on the
        item without driving the model, so ``currentTextChanged`` /
        ``currentIndexChanged`` never fire and your application code never
        sees the selection change.

        Strategy:

        1. Focus the widget.
        2. ``Alt+Down`` to open the dropdown (no-op for QListWidget).
        3. Type the first character of *item* (or use arrow keys for ``int``)
           to jump to the matching row.  Qt's typeahead handles disambiguation
           when multiple items share the same first letter — this gets the
           first match, which is the same as a real user typing.
        4. ``Enter`` to confirm — this fires ``currentTextChanged``.

        Use this instead of :meth:`select_item` for Qt combo boxes and list
        widgets when you need the signal-handling code to fire.  For
        non-Qt apps (WinForms, WPF) :meth:`select_item` is faster and more
        precise.

        Usage::

            window.combo_box(name="Language").select_item_keyboard("Polish")
            window.list_box(name="Fruits").select_item_keyboard("Banana")
        """
        element = None
        try:
            element = self._resolve()
            element.set_focus()
            time.sleep(0.1)
            if isinstance(item, int):
                # Alt+Down opens the combo dropdown; Home jumps to first item,
                # then Down N times.  For a QListWidget the dropdown step is a
                # no-op (no harm — Alt+Down does nothing on a focused list).
                _send_keys("%{DOWN}")
                time.sleep(0.15)
                _send_keys("{HOME}")
                if item > 0:
                    _send_keys("{DOWN " + str(item) + "}")
                time.sleep(0.1)
                _send_keys("{ENTER}")
            elif isinstance(item, str) and item:
                _send_keys("%{DOWN}")
                time.sleep(0.15)
                # Send first character to trigger Qt's type-ahead jump.
                _send_keys(_escape_keys(item[0]))
                time.sleep(0.15)
                _send_keys("{ENTER}")
            else:
                raise ValueError("select_item_keyboard requires a non-empty string or int")
        except Exception as exc:
            _trace_step("select_item_keyboard", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("select_item_keyboard", self._criteria, element=element)
        return self

    def check(self) -> Locator:
        """Check a checkbox.

        Raises :class:`UnsupportedPatternError` when the element publishes no
        readable check state — toggling blindly would invert an already-checked
        box instead of leaving it alone.
        """
        element = None
        try:
            element = self._resolve()
            # ``!= 1``, not ``== 0``: state 2 (indeterminate) is not checked
            # either, and leaving a tri-state box alone would report success
            # while ``is_checked()`` still answers False.
            if _require_check_state(element, "check") != 1:
                _toggle_element(element)
        except Exception as exc:
            _trace_step("check", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("check", self._criteria, element=element)
        return self

    def uncheck(self) -> Locator:
        """Uncheck a checkbox.

        Raises :class:`UnsupportedPatternError` when the element publishes no
        readable check state — reporting success without changing anything is
        worse than failing.
        """
        element = None
        try:
            element = self._resolve()
            if _require_check_state(element, "uncheck") != 0:
                _toggle_element(element)
        except Exception as exc:
            _trace_step("uncheck", self._criteria, element=element, error=str(exc))
            raise
        _trace_step("uncheck", self._criteria, element=element)
        return self

    def focus(self) -> Locator:
        self._resolve().set_focus()
        return self

    def scroll_into_view(self) -> Locator:
        element = self._resolve()
        # UIA wrappers have no scroll_into_view(); use the ScrollItemPattern
        # (iface_scroll_item) when available, falling back to set_focus(), which
        # also brings the element into view for most scrollable containers.
        try:
            element.iface_scroll_item.ScrollIntoView()
        except Exception:
            try:
                element.set_focus()
            except Exception:
                pass
        return self

    # Queries

    def text(self) -> str:
        """Return the text content of the element.

        For standard Edit controls this is window_text().  For Document/RichEdit
        controls (e.g. Windows 11 Notepad RichEditD2DPT) window_text() returns
        an empty string, so we fall back to select-all + clipboard.
        """
        element = self._resolve_readonly()
        t = element.window_text()
        if t:
            return t
        # IValueProvider.Value works for most edit controls including Qt
        # QLineEdit / QPlainTextEdit. If get_value() succeeds (even returning
        # an empty string for a legitimately-empty field), trust it — falling
        # through to the clipboard fallback would do a focus + select-all +
        # Ctrl+C dance that can hang on Qt UIA's SetFocus implementation.
        got_value = False
        try:
            t = element.get_value()
            got_value = True
        except Exception:
            pass
        if got_value:
            return t or ""
        return _read_text_via_clipboard(element)

    def value(self) -> str:
        """Return the value of an edit/spinner control."""
        element = self._resolve_readonly()
        try:
            return element.get_value()
        except AttributeError:
            return element.window_text()

    def is_visible(self) -> bool:
        # The TreeWalker last resort stays on here even though it costs a COM
        # walk per poll: skipping it would report a TreeWalker-only element
        # (a ToolbarWindow32 button child, say) as invisible while click()
        # resolves it fine, and wait_until_hidden() would return immediately.
        try:
            return bool(self.timeout(0)._resolve_readonly().is_visible())
        except Exception:
            return False

    def is_enabled(self) -> bool:
        try:
            return bool(self.timeout(0)._resolve_readonly().is_enabled())
        except Exception:
            return False

    def is_checked(self) -> bool:
        """Return True when the element is checked.

        Raises :class:`UnsupportedPatternError` when the check state cannot be
        read — a blanket ``False`` would make ``assert not
        cb.is_checked()`` pass without ever inspecting the widget.
        """
        return _require_check_state(self._resolve_readonly(), "is_checked") == 1

    def _resolve_presence(self) -> Any:
        """Resolve an element without requiring it to be visible."""
        parent_spec = self._get_parent_presence_spec()
        if not hasattr(parent_spec, "child_window"):
            return _find_under_wrapper(
                parent_spec,
                self._criteria,
                self._timeout,
                visible_only=False,
            )

        from pywinauto.findwindows import (  # type: ignore[import-untyped]
            ElementAmbiguousError as _PwAmbiguousError,
        )

        primary_exc: Exception | None = None
        try:
            criteria = self._criteria
            if _is_negative_found_index(criteria):
                resolved = _resolve_negative_found_index(parent_spec, criteria, self._timeout)
                if isinstance(resolved, dict):
                    criteria = resolved
                else:
                    return resolved
            spec = parent_spec.child_window(**criteria)
            _wait_until_present(spec, self._timeout)
            return spec
        except _PwAmbiguousError as exc:
            from ._exceptions import AmbiguousMatchError

            raise AmbiguousMatchError(
                f"{self._criteria!r} matched more than one element — "
                "narrow the criteria or pick one with found_index=N"
            ) from exc
        except Exception as exc:
            primary_exc = exc

        # Presence checks use the same fallback order as actions, but probe
        # only whether the handle can be materialised. Hidden controls are
        # valid UIA elements and must not be rejected by a visibility wait.
        from . import _selfheal

        for fb in self._fallback:
            try:
                fb_spec = parent_spec.child_window(**fb)
                _wait_until_present(fb_spec, 0)
                _selfheal.record_fallback(self._criteria, fb)
                return fb_spec
            except _PwAmbiguousError as exc:
                from ._exceptions import AmbiguousMatchError

                raise AmbiguousMatchError(
                    f"{fb!r} matched more than one element — "
                    "narrow the criteria or pick one with found_index=N"
                ) from exc
            except Exception:
                continue

        if self._image_fallback is not None:
            try:
                result = self._image_fallback.find_with_size()
                if result is not None:
                    cx, cy, tw, th = result
                    from ._image import _ImageElement

                    _selfheal.record_fallback(
                        self._criteria,
                        {"image": str(self._image_fallback._template_path)},
                    )
                    return _ImageElement(cx, cy, tw, th)
            except Exception:
                pass

        tree_result = _tree_walk_find(parent_spec, self._criteria)
        if tree_result is not None:
            return tree_result

        raise ElementNotFoundError(
            _NotFoundMessage(self._criteria, self._timeout, parent_spec)
        ) from primary_exc

    def exists(self, timeout: float = 0.0) -> bool:
        """Return True if the element exists within *timeout* seconds.

        Ambiguous criteria (multiple matches) count as *existing* — there
        is at least one such element. Actions on the same locator still
        raise :class:`AmbiguousMatchError` so the ambiguity cannot go
        unnoticed where it matters.
        """
        from ._exceptions import AmbiguousMatchError

        try:
            self.timeout(timeout)._resolve_presence()
            return True
        except AmbiguousMatchError:
            return True
        except ValueError:
            raise
        except Exception:
            return False

    def bounding_box(self) -> dict[str, int]:
        """Return {left, top, right, bottom, width, height} in screen coords."""
        rect = self._resolve_readonly().rectangle()
        return {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
        }

    # Waiting

    def wait_for(self, *, state: str = "visible", timeout: float | None = None) -> Locator:
        """Wait until the element reaches *state* ('visible', 'enabled', 'exists', 'hidden').

        Resolution goes through the same path as every action, so declared
        ``fallback`` selectors and the image fallback count here too — an
        element that ``click()`` can reach must not time out in ``wait_for``.
        A never-appearing element therefore raises
        :class:`ElementNotFoundError` (as ``click()`` does);
        :class:`WaitTimeoutError` is reserved for an element that exists but
        never reaches *state*.
        """
        t = timeout if timeout is not None else self._timeout
        if state == "hidden":
            return self.wait_until_hidden(timeout=t)
        if state == "exists":
            self.timeout(t)._resolve_presence()
            return self
        spec = self.timeout(t)._resolve()
        if state == "visible":
            return self
        # ``wait`` is a WindowSpecification method; the fallback, image and
        # tree-walk branches of _resolve hand back raw wrappers / _ImageElement
        # instead, so the state has to be polled directly on those.
        waiter = getattr(spec, "wait", None)
        if callable(waiter):
            try:
                waiter(state, timeout=t)
            except Exception as exc:
                raise WaitTimeoutError(
                    f"Element {self._criteria!r} did not reach state '{state}' after {t}s"
                ) from exc
            return self
        probe_name = _STATE_PROBES.get(state)
        probe = getattr(spec, probe_name, None) if probe_name else None
        if not callable(probe):
            raise WaitTimeoutError(
                f"Element {self._criteria!r} resolved to a "
                f"{type(spec).__name__!r}, which cannot report state {state!r}"
            )
        deadline = time.monotonic() + t
        while True:
            try:
                if probe():
                    return self
            except Exception:
                pass
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        raise WaitTimeoutError(
            f"Element {self._criteria!r} did not reach state '{state}' after {t}s"
        )

    def wait_until_hidden(self, timeout: float = 10.0) -> Locator:
        """Wait until the element is no longer visible."""
        deadline = time.monotonic() + timeout
        while True:
            if not self.is_visible():
                return self
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        raise WaitTimeoutError(f"Element {self._criteria!r} still visible after {timeout}s")

    def wait_for_checked(
        self,
        *,
        checked: bool = True,
        timeout: float | None = None,
        poll_interval: float = 0.15,
    ) -> Locator:
        """Poll :meth:`is_checked` until it equals ``checked``.

        Auto-waiting counterpart to
        ``sleep(N); assert locator.is_checked() == expected`` for
        checkboxes / toggle buttons / radio buttons. Returns as soon
        as the state matches so tests do not pay a fixed delay per
        toggle.
        """
        t = timeout if timeout is not None else self._timeout
        deadline = time.monotonic() + t
        while True:
            try:
                if bool(self.is_checked()) == checked:
                    return self
            except Exception:
                pass
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_interval)
        raise WaitTimeoutError(
            f"Element {self._criteria!r} did not become "
            f"{'checked' if checked else 'unchecked'} within {t}s"
        )

    def wait_for_text(
        self,
        text: str | None = None,
        *,
        text_re: str | None = None,
        contains: bool = True,
        timeout: float | None = None,
        poll_interval: float = 0.15,
    ) -> Locator:
        """Poll the element until its text matches — the auto-waiting
        counterpart to reading ``.text()`` after a programmatic action.

        Replaces the ``sleep(N); assert ... in locator.text()`` antipattern
        with a deterministic wait: the poll stops the moment the text
        matches, so fast assertions pass without a fixed delay and slow
        state changes still get their full timeout budget.

        Args:
            text: Substring the element's text must contain
                (or match exactly when ``contains=False``).
            text_re: Regular expression the text must match. Mutually
                exclusive with ``text``.
            contains: When True (default) ``text`` is matched as a
                substring; when False, exact equality is required.
            timeout: Seconds to wait. Defaults to the locator's own
                timeout (usually the global ``dolphin_desktop.config``
                value).
            poll_interval: Seconds between checks (default 0.15).

        Returns ``self`` for chaining.

        Raises :class:`WaitTimeoutError` when the text never matches —
        the exception message includes the current text so debugging
        does not need a screenshot.

        Usage::

            win.button(name="Sign In").invoke()
            win.get_by_automation_id("status").wait_for_text("Signed in")
        """
        if (text is None) == (text_re is None):
            raise ValueError("wait_for_text requires exactly one of text= or text_re=")
        # Reject `text=""` with `contains=True` — the naive `"" in current`
        # check would return immediately regardless of the element's actual
        # text, hiding real state from the test. Callers who genuinely want
        # "wait until the field is empty" must pass `contains=False`.
        if text == "" and contains:
            raise ValueError(
                "wait_for_text(text='', contains=True) always matches immediately — "
                "use contains=False to wait for an exactly-empty field"
            )

        import re as _re

        pat = _re.compile(text_re) if text_re else None
        t = timeout if timeout is not None else self._timeout
        deadline = time.monotonic() + t
        current = ""
        while True:
            try:
                # Coerce None → "" so `text in current` cannot raise TypeError
                # on wrappers whose text getter returns None on edge cases
                # (e.g. before the element is fully painted).
                current = self.text() or ""
            except Exception:
                current = ""
            if text is not None:
                if contains:
                    if text in current:
                        return self
                else:
                    if current == text:
                        return self
            elif pat is not None:
                if pat.search(current):
                    return self
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_interval)
        target = text if text is not None else f"regex {text_re!r}"
        raise WaitTimeoutError(
            f"Element {self._criteria!r} text did not match {target!r} "
            f"within {t}s (last seen: {current!r})"
        )

    def wait_until_enabled(self, timeout: float = 10.0) -> Locator:
        return self.wait_for(state="enabled", timeout=timeout)

    # Screenshot

    def screenshot(self, path: str | Path | None = None, timeout_ms: int | None = None) -> Image:
        """Capture the element as a PIL Image, optionally saving to *path*."""
        if timeout_ms is not None:
            return self.timeout(timeout_ms / 1000.0).screenshot(path)
        img = self._resolve().capture_as_image()
        if path:
            output = Path(path)
            output.parent.mkdir(parents=True, exist_ok=True)
            img.save(output)
        return img

    def fill(self, text: str, timeout_ms: int | None = None) -> Locator:
        """Replace the element text using generated-code semantics."""
        target = self if timeout_ms is None else self.timeout(timeout_ms / 1000.0)
        target.set_text(text)
        return self

    # Collections

    def hover(self, timeout_ms: int | None = None) -> Locator:
        """Move the mouse pointer over the element's centre.

        Deliberately does NOT raise the window to the foreground: hovering is
        a non-activating gesture, and stealing focus would change the very
        state a hover test is usually checking.
        """
        if timeout_ms is not None:
            self.timeout(timeout_ms / 1000.0).hover()
            return self
        import pywinauto.mouse as _mouse  # type: ignore[import-untyped]

        bb = self.bounding_box()
        cx = bb["left"] + bb["width"] // 2
        cy = bb["top"] + bb["height"] // 2
        _mouse.move(coords=(cx, cy))
        return self

    def drag_to(
        self,
        target: Locator | tuple[int, int],
        *,
        duration: float = 0.5,
        button: str = "left",
    ) -> Locator:
        """Drag from this element's centre to *target*."""
        import pywinauto.mouse as _mouse  # type: ignore[import-untyped]

        self._focus_for_input()
        bb = self.bounding_box()
        src_x = bb["left"] + bb["width"] // 2
        src_y = bb["top"] + bb["height"] // 2

        if isinstance(target, tuple):
            dst_x, dst_y = target
        else:
            tbb = target.bounding_box()
            dst_x = tbb["left"] + tbb["width"] // 2
            dst_y = tbb["top"] + tbb["height"] // 2

        steps = 30
        step_sleep = duration / steps

        # The button is physically held down desktop-wide between press and
        # release: an exception (or Ctrl-C) inside the move loop must still
        # release it, otherwise every later click on the machine is a drag.
        cur_x, cur_y = src_x, src_y
        _mouse.press(button=button, coords=(src_x, src_y))
        try:
            for i in range(1, steps + 1):
                cur_x = src_x + (dst_x - src_x) * i // steps
                cur_y = src_y + (dst_y - src_y) * i // steps
                _mouse.move(coords=(cur_x, cur_y))
                time.sleep(step_sleep)
        finally:
            _mouse.release(button=button, coords=(cur_x, cur_y))
        return self

    def scroll(
        self,
        direction: str = "down",
        amount: int = 3,
        timeout_ms: int | None = None,
    ) -> Locator:
        """Scroll at the element's centre. direction: 'up' or 'down'."""
        if timeout_ms is not None:
            self.timeout(timeout_ms / 1000.0).scroll(direction, amount)
            return self
        import warnings

        import pywinauto.mouse as _mouse  # type: ignore[import-untyped]

        bb = self.bounding_box()
        cx = bb["left"] + bb["width"] // 2
        cy = bb["top"] + bb["height"] // 2

        if direction == "up":
            wheel_dist = amount
        elif direction == "down":
            wheel_dist = -amount
        else:
            warnings.warn(
                f"scroll direction {direction!r} is not supported; ignoring.",
                stacklevel=2,
            )
            return self

        _mouse.scroll(coords=(cx, cy), wheel_dist=wheel_dist)
        return self

    def get_attribute(self, name: str, default: Any = _MISSING) -> Any:
        """Return an attribute of the underlying element_info by *name*.

        Raises ``AttributeError`` when the element publishes no such
        attribute. Returning ``None`` there would make a misspelled name
        (``"AutomationId"`` for ``automation_id``) or one the active
        backend does not expose (``automation_id`` is UIA-only — the
        win32 backend has no such field) indistinguishable from an
        attribute that is genuinely empty, so an assertion written
        against it would pass without ever reading the UI.

        Pass *default* to opt back into a non-raising lookup.
        """
        info = self._resolve_readonly().element_info
        value = getattr(info, name, _MISSING)
        if value is _MISSING:
            if default is not _MISSING:
                return default
            published = ", ".join(sorted(a for a in dir(info) if not a.startswith("_")))
            raise AttributeError(
                f"{type(info).__name__} publishes no attribute {name!r}. Available: {published}"
            )
        return value

    def select_text(self) -> Locator:
        """Select all text in the element (focus + Ctrl+A)."""
        self._resolve().set_focus()
        time.sleep(0.05)
        _send_keys("^a")
        return self

    def all(self, *, depth: int | None = None) -> list[Locator]:
        """Return all matching elements as a list of Locators.

        If *depth* is None, only direct children are returned.
        If *depth* is given (including 0), descendants up to that depth are returned.
        """
        parent_spec = self._get_parent_spec()
        try:
            if depth is None:
                elements = parent_spec.children(**self._criteria)
            else:
                elements = parent_spec.descendants(depth=depth, **self._criteria)
        except Exception:
            elements = []
        return [_ResolvedLocator(el) for el in elements]

    def count(self) -> int:
        return len(self.all())

    def __repr__(self) -> str:
        return f"Locator({self._criteria!r})"


class _NotFoundMessage:
    """Deferred ``ElementNotFoundError`` message carrying the element tree.

    ``_resolve`` fails on every poll of ``is_visible`` / ``exists`` /
    ``wait_until_hidden``, and :func:`_dump_tree` is a full COM walk whose
    result is discarded unless the exception is actually printed. Building the
    string in ``__str__`` keeps a 10 s ``wait_until_hidden`` from paying ~100
    tree walks it never shows anyone.
    """

    __slots__ = ("_criteria", "_spec", "_timeout")

    def __init__(self, criteria: dict[str, Any], timeout: float, spec: Any) -> None:
        self._criteria = dict(criteria)
        self._timeout = timeout
        self._spec = spec

    def __str__(self) -> str:
        return (
            f"Element {self._criteria!r} not found after {self._timeout}s. "
            f"Last seen tree:\n{_dump_tree(self._spec)}"
        )

    def __repr__(self) -> str:
        return str(self)


def _dump_tree(spec: Any, max_depth: int = 3) -> str:
    """Return a compact text snapshot of *spec*'s child tree (for error messages)."""
    lines: list[str] = []
    try:
        _collect_tree(spec, lines, 0, max_depth)
    except Exception:
        return "(could not capture tree)"
    return "\n".join(lines) if lines else "(empty)"


def _collect_tree(
    node: Any, lines: list[str], depth: int, max_depth: int, max_children: int = 20
) -> None:
    if depth > max_depth:
        return
    try:
        children = node.children()
    except Exception:
        return
    for child in children[:max_children]:
        try:
            info = child.element_info
            ctrl = str(getattr(info, "control_type", "") or "")
            name = str(getattr(info, "name", "") or "")
            auto_id = str(getattr(info, "automation_id", "") or "")
            indent = "  " * depth
            parts = [ctrl or "?"]
            if name:
                parts.append(f"name={name!r}")
            if auto_id:
                parts.append(f"id={auto_id!r}")
            lines.append(f"{indent}{' '.join(parts)}")
        except Exception:
            lines.append(f"{'  ' * depth}?")
        _collect_tree(child, lines, depth + 1, max_depth, max_children)


def _select_via_popup(
    element: Any, item: str | int, timeout: float, *, cause: Exception | None = None
) -> None:
    """Select a ComboBox item that appears in a detached popup window (Qt pattern).

    Qt QComboBox opens its dropdown as a separate top-level window in the UIA
    tree, so pywinauto's child_window() cannot find the items.  This helper
    expands the combo, waits for the popup, scans top-level windows belonging
    to the same process for ListItem elements, and clicks the target item.

    *cause* is the error that made :meth:`Locator.select_item` fall through to
    this last resort; it is chained onto the failure so the original
    ``select()`` error is not lost.
    """
    from pywinauto import Desktop as _Desktop

    try:
        element.expand()
    except Exception:
        pass
    time.sleep(0.2)

    proc_id = element.element_info.process_id
    # Wall-clock time is NOT safe for deadline arithmetic — NTP
    # adjustments, DST rollovers, or the user changing the system clock
    # would make the loop either return before the actual timeout or
    # run forever. ``time.monotonic()`` is guaranteed to advance
    # regardless of the wall clock.
    deadline = time.monotonic() + timeout
    while True:
        for win in _Desktop(backend="uia").windows():
            try:
                if win.element_info.process_id != proc_id:
                    continue
                all_items = win.descendants(control_type="ListItem")
                if not all_items:
                    continue
                if isinstance(item, str):
                    for li in all_items:
                        try:
                            if li.window_text() == item:
                                li.click_input()
                                return
                        except Exception:
                            continue
                else:
                    if item < len(all_items):
                        all_items[item].click_input()
                        return
            except Exception:
                continue
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    raise ElementNotFoundError(
        f"ComboBox popup item {item!r} not found after {timeout}s"
    ) from cause


class _QtObjectNameLocator(Locator):
    """Locator that finds a Qt widget by the leaf segment of its objectName path.

    Qt's UIA bridge exposes ``QObject::objectName()`` as a hierarchical dotted
    ``AutomationId`` (``QApplication.win.tabs.tab1.qt_btn_ok``). pywinauto's
    ``find_elements`` only supports exact ``auto_id`` matching, so we walk
    descendants of the resolved parent and pick the first whose AutomationId
    equals *object_name* or ends with ``.<object_name>``.

    The walk is O(N) but Qt apps rarely exceed a few hundred descendants;
    this beats requiring users to know the full prefix.
    """

    def __init__(self, parent: Window | Locator, object_name: str) -> None:
        # Initialize the base Locator with no pywinauto criteria — our custom
        # ``_resolve`` walks descendants rather than calling child_window().
        super().__init__(parent)
        self._object_name = object_name
        # Read by ``_resolve`` (``found_index``) and by trace/repr.
        self._criteria = {"qt_object_name": object_name}

    def _resolve(self) -> Any:
        parent_spec = self._get_parent_spec()
        suffix = f".{self._object_name}"
        index = self._criteria.get("found_index", 0)
        negative_index = isinstance(index, int) and index < 0
        # ``time.monotonic()`` is immune to wall-clock jumps (NTP, DST)
        # that would otherwise cause the Qt objectName scan to either
        # early-abort or loop forever.
        deadline = time.monotonic() + self._timeout
        last_exc: Exception | None = None
        while True:
            try:
                descendants = parent_spec.descendants()
            except Exception as exc:
                last_exc = exc
                descendants = []
            seen = 0
            matches: list[Any] = []
            for desc in descendants:
                try:
                    aid = desc.element_info.automation_id or ""
                except Exception:
                    continue
                if aid == self._object_name or aid.endswith(suffix):
                    if negative_index:
                        matches.append(desc)
                    elif seen == index:
                        return desc
                    seen += 1
            if negative_index:
                resolved_index = len(matches) + index
                if 0 <= resolved_index < len(matches):
                    return matches[resolved_index]
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        raise ElementNotFoundError(
            f"Qt widget with objectName ending in {self._object_name!r} "
            f"(match #{index}) not found after {self._timeout}s"
        ) from last_exc


def _read_text_via_clipboard(element: Any) -> str:
    """Read text from a Document/RichEdit control via select-all + clipboard.

    Used as a fallback when window_text() returns empty (e.g. Windows 11
    Notepad's RichEditD2DPT which does not expose text via INameProvider).

    The clipboard is cleared first so that when the editor is empty (and ^c
    copies nothing), GetClipboardData raises instead of returning stale data.

    SendInput-based key sending occasionally fails on slow/loaded systems
    (``SendInput() inserted only 0 out of 2`` when focus shifts mid-call);
    we swallow that error and return ``""`` rather than propagating it,
    since this is a best-effort fallback.
    """
    import win32clipboard
    import win32con

    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
        finally:
            # An open clipboard is a machine-wide lock: leaving it open blocks
            # copy/paste in every other application until this process exits.
            win32clipboard.CloseClipboard()
    except Exception:
        pass

    try:
        element.set_focus()
    except Exception:
        return ""
    time.sleep(0.05)
    try:
        _send_keys("^a^c")
    except Exception:
        # SendInput rate-limited / foreground stolen → give up cleanly.
        return ""
    time.sleep(0.1)

    try:
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except Exception:
            return ""  # Empty clipboard → editor was empty
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""


def _get_check_state(element: Any) -> int | None:
    """Return 0/1/2 (unchecked/checked/indeterminate), or None when unreadable.

    Win32 controls expose get_check_state(); UIA controls expose get_toggle_state().
    ``None`` is deliberately distinct from ``0``: an element that publishes no
    check state at all must not be mistaken for an unchecked one, or
    ``uncheck()`` becomes a no-op that reports success and ``check()`` inverts
    an already-checked box.
    """
    try:
        return element.get_check_state()
    except AttributeError:
        pass
    try:
        return element.get_toggle_state()
    except Exception:
        pass
    # get_toggle_state() is defined only on uia_controls.ButtonWrapper.
    # Checkable MenuItem / ListItem / TreeItem inherit plain UIAWrapper and
    # implement TogglePattern with no wrapper method in front of it.
    try:
        return int(element.iface_toggle.CurrentToggleState)
    except Exception:
        return None


def _require_check_state(element: Any, action: str) -> int:
    """Return the element's check state or raise :class:`UnsupportedPatternError`."""
    target = _wrapper_of(element)
    state = _get_check_state(target)
    if state is None:
        raise UnsupportedPatternError(
            f"{action}() called on an element that publishes no check state "
            f"(wrapper {type(target).__name__!r} implements neither "
            "get_check_state() nor TogglePattern)",
            hint=(
                "for radio buttons use .select(); for regular buttons use "
                ".invoke(); verify the selector matched the intended widget"
            ),
        )
    return state


def _toggle_element(element: Any) -> None:
    """Toggle a checkbox using the UIA Toggle pattern, with click_input() fallback.

    toggle() (IToggleProvider) updates UIA state synchronously; click_input() may
    have a brief delay before UIA reflects the new state.

    ``toggle()`` is a method of ``uia_controls.ButtonWrapper`` only, so
    checkable MenuItem / ListItem / TreeItem go through ``iface_toggle``
    before a physical click is considered.
    """
    target = _wrapper_of(element)
    try:
        target.toggle()
        return
    except AttributeError:
        pass
    except Exception as exc:
        if type(exc).__name__ != "NoPatternInterfaceError":
            raise
        element.click_input()
        return
    try:
        _toggle_via_iface(target)
    except Exception:
        element.click_input()


_TREE_WALK_KEYS = frozenset({"title", "control_type", "auto_id", "found_index"})

# Locator.wait_for state → the wrapper predicate that answers it, for results
# that are raw wrappers instead of a WindowSpecification with .wait().
_STATE_PROBES = {"enabled": "is_enabled", "active": "is_active"}


def _is_negative_found_index(criteria: dict[str, Any]) -> bool:
    index = criteria.get("found_index")
    return isinstance(index, int) and index < 0


def _resolve_negative_found_index(
    parent_spec: Any,
    criteria: dict[str, Any],
    timeout: float,
) -> dict[str, Any] | Any:
    """Resolve a negative index against the complete match set.

    ``pywinauto`` does not consistently handle negative ``found_index`` values
    across its UIA and wrapper paths. Poll the complete ``descendants()``
    collection ourselves first, then use the TreeWalker fallback for criteria
    it can evaluate when FindAll exposes no usable match. This keeps a
    depth-limited TreeWalker result from changing the requested ordering of the
    complete descendant set.
    """
    if not _is_negative_found_index(criteria):
        return criteria

    index = int(criteria["found_index"])
    match_criteria = {key: value for key, value in criteria.items() if key != "found_index"}
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None

    from pywinauto.timings import TimeoutError as _PwTimeoutError

    while True:
        try:
            matches = parent_spec.wrapper_object().descendants(**match_criteria)
            resolved_index = len(matches) + index
            if 0 <= resolved_index < len(matches):
                return {**criteria, "found_index": resolved_index}
        except Exception as exc:
            last_exc = exc

        if set(match_criteria) <= _TREE_WALK_KEYS:
            tree_result = _tree_walk_find(parent_spec, criteria)
            if tree_result is not None:
                return tree_result

        if time.monotonic() >= deadline:
            break
        time.sleep(_get_poll_interval())

    raise _PwTimeoutError(
        f"negative index {index} was not available within {timeout}s"
    ) from last_exc


def _find_under_wrapper(
    parent: Any,
    criteria: dict[str, Any],
    timeout: float,
    *,
    visible_only: bool = True,
) -> Any:
    """Find a descendant of an already-resolved pywinauto *wrapper*.

    ``child_window`` is defined on ``WindowSpecification`` only, so a locator
    chained off ``_ResolvedLocator`` / ``_QtObjectNameLocator`` — or off any
    fallback match — has to go through ``find_elements`` instead.
    """
    from pywinauto.findwindows import find_elements

    backend_name = getattr(getattr(parent, "backend", None), "name", None)
    wrapper_cls = getattr(getattr(parent, "backend", None), "generic_wrapper_class", None)
    if backend_name is None or wrapper_cls is None:
        raise ElementNotFoundError(
            f"Cannot search for {criteria!r} inside a "
            f"{type(parent).__name__!r}: it is not a pywinauto wrapper",
            hint="chain .locator() off a Window or an unresolved Locator instead",
        )
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    negative_index = int(criteria["found_index"]) if _is_negative_found_index(criteria) else None
    search_criteria = (
        {key: value for key, value in criteria.items() if key != "found_index"}
        if negative_index is not None
        else criteria
    )
    while True:
        try:
            found = find_elements(
                parent=parent,
                top_level_only=False,
                backend=backend_name,
                depth=None,
                visible_only=visible_only,
                **search_criteria,
            )
            if negative_index is not None:
                resolved_index = len(found) + negative_index
                if 0 <= resolved_index < len(found):
                    return wrapper_cls(found[resolved_index])
            elif found:
                return wrapper_cls(found[0])
        except Exception as exc:
            last_exc = exc
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    raise ElementNotFoundError(
        f"Element {criteria!r} not found after {timeout}s under {type(parent).__name__!r}"
    ) from last_exc


def _tree_walk_find(parent_spec: Any, criteria: dict[str, Any]) -> Any | None:
    """Find an element via UIAElementInfo.children() (IUIAutomation::TreeWalker).

    pywinauto's child_window()/descendants() use IUIAutomation::FindAll which
    misses elements only reachable via TreeWalker, e.g. toolbar buttons inside
    Win32 ToolbarWindow32 controls.  This fallback uses UIAElementInfo.children()
    which calls TreeWalker internally and can reach those elements.
    Returns a pywinauto wrapper on success, or None.
    """
    # Only ``title``, ``control_type`` and ``auto_id`` can be evaluated against
    # a raw UIAElementInfo here. Matching on a subset of the caller's criteria
    # would return the wrong element, so any other key disqualifies this
    # fallback entirely — the caller must get an ElementNotFoundError instead.
    if not set(criteria) <= _TREE_WALK_KEYS:
        return None
    title = criteria.get("title", "")
    ct = criteria.get("control_type", "")
    auto_id = criteria.get("auto_id", "")
    if not title and not ct and not auto_id:
        return None
    # ``nth()`` merges found_index into the criteria; skipping that many
    # matches here keeps .nth(N) with the same fallbacks as the bare locator.
    found_index = int(criteria.get("found_index", 0) or 0)
    remaining = [found_index]
    negative_matches: list[Any] = []

    try:
        root_info = parent_spec.wrapper_object().element_info
    except Exception:
        return None

    def _search(info: Any, depth: int) -> Any | None:
        if depth <= 0:
            return None
        try:
            children = info.children()
        except Exception:
            return None
        for child in children:
            try:
                child_name = (child.name or "").split("\t")[0]
                child_ct = child.control_type or ""
                child_auto_id = getattr(child, "automation_id", "") or ""
            except Exception:
                continue
            if (
                (not title or child_name == title)
                and (not ct or child_ct == ct)
                and (not auto_id or child_auto_id == auto_id)
            ):
                if found_index < 0:
                    negative_matches.append(child)
                elif remaining[0] > 0:
                    remaining[0] -= 1
                else:
                    try:
                        import pywinauto as _pw

                        wrapper_cls = _pw.Application(backend="uia").backend.generic_wrapper_class
                        return wrapper_cls(child)
                    except Exception:
                        pass
            result = _search(child, depth - 1)
            if result is not None:
                return result
        return None

    result = _search(root_info, 8)
    if result is not None:
        return result
    if found_index < 0:
        try:
            child = negative_matches[found_index]
            import pywinauto as _pw

            wrapper_cls = _pw.Application(backend="uia").backend.generic_wrapper_class
            return wrapper_cls(child)
        except Exception:
            return None

    return None


class _ResolvedLocator(Locator):
    """A Locator wrapping an already-resolved pywinauto element."""

    def __init__(self, element: Any) -> None:
        self._element = element
        self._criteria: dict[str, Any] = {}
        self._fallback: list[dict[str, Any]] = []
        self._image_fallback: Any = None
        self._timeout = _get_timeout()

    def nth(self, index: int) -> Self:
        """Reject any index but 0 — this locator wraps one resolved element.

        ``_resolve`` hands back the element it was built from, so a
        ``found_index`` merged into ``_criteria`` would be discarded and match
        #0 returned under the caller's belief it addressed match #N. Index the
        list returned by :meth:`Locator.all` instead.
        """
        if index != 0:
            raise ValueError(
                f"nth({index}) is not supported on an already-resolved locator — "
                "index the list returned by .all() instead"
            )
        return self._clone()

    def _get_parent_spec(self) -> Any:
        return self._element

    def _resolve_presence(self) -> Any:
        """Return the wrapped element when its resolved handle is usable."""
        return self._resolve()

    def _resolve(self) -> Any:
        _ensure_element_present(self._element)
        return self._element
