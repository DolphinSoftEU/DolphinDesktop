"""dolphin_desktop exception hierarchy.

Every dolphin-specific error derives from :class:`DolphinError`. The
concrete subclasses carry a ``hint`` argument — a one-line suggested
next step that gets appended to the message so callers see the fix
alongside the failure.
"""

from __future__ import annotations

from typing import Any


class DolphinError(Exception):
    """Base exception for dolphin_desktop.

    Every dolphin-defined exception accepts an optional ``hint=`` kwarg
    that gets appended to the message as ``  hint: ...``. The intent is
    that users see a *suggested next step* right next to the failure —
    no need to look up the docstring or scroll through docs.

    Example::

        raise ElementNotFoundError(
            f"button {name!r} not found",
            hint=f"try wait_for_selector({name!r}, state='visible')",
        )
    """

    def __init__(self, *args: Any, hint: str | None = None) -> None:
        super().__init__(*args)
        self.hint = hint

    def __str__(self) -> str:
        base = super().__str__()
        if self.hint:
            return f"{base}\n  hint: {self.hint}"
        return base


class ElementNotFoundError(DolphinError):
    """Raised when an element cannot be found within the timeout.

    The message should include the failing selector / role / name.
    """


class AmbiguousMatchError(DolphinError):
    """Raised when locator criteria match more than one element.

    Distinct from :class:`ElementNotFoundError` on purpose: the elements
    are there — the criteria are under-specified. Narrow them (add
    ``control_type``, ``title``…) or pick a match with ``found_index=N``.
    """


class WaitTimeoutError(DolphinError):
    """Raised when a wait condition is not met within the allowed time.

    Prefer the ``hint=`` kwarg to point at the wait primitive that would
    have made the flake avoidable (``wait_for``, ``wait_for_status``,
    ``wait_for_url``…).
    """


class ApplicationError(DolphinError):
    """Raised when application launch or connection fails."""


class WindowNotFoundError(DolphinError):
    """Raised when a window cannot be found."""


class AliasNotFoundError(DolphinError):
    """Raised when an alias is not found in the Object Repository."""


class UnsupportedCapabilityError(DolphinError):
    """Raised when an operation is requested against a backend that does
    NOT declare the required :class:`~dolphin_desktop.Capability`.

    Distinct from :class:`UnsupportedPatternError`:

    * ``UnsupportedCapabilityError`` fires when the **backend as a
      whole** does not support the operation — e.g. calling
      ``.invoke()`` in a test that resolved a pure ``ImageBackend``
      locator (image matching has no accessibility patterns at all).
    * ``UnsupportedPatternError`` fires when the **specific element**
      does not implement the required pattern — the backend supports
      the operation in principle, but this widget does not (a JLabel
      is not toggleable even though the JAB backend supports toggle
      in general).

    Emitted by :meth:`~dolphin_desktop._backend.Backend.require_capability`
    and by every operation that gates on a capability check.
    """


class UnsupportedPatternError(DolphinError):
    """Raised when a programmatic UIA / JAB action is called on an element
    that does not implement the required pattern.

    Signals to the caller that they must either (a) target a different
    element that implements the pattern, (b) drop back to the physical
    action (``click`` / ``type_text``), or (c) verify their selector
    matched the intended widget. Emitted by :meth:`Locator.invoke`,
    :meth:`Locator.toggle`, :meth:`Locator.expand`,
    :meth:`Locator.collapse`, :meth:`Locator.select`,
    :meth:`Locator.set_value` and their :class:`JABLocator`
    counterparts.
    """
