"""Backend capability contract for dolphin_desktop.

Every :class:`~dolphin_desktop._backend.Backend` declares which
operations it can perform through :meth:`Backend.capabilities`. The
central registry (this module) publishes the vocabulary of those
capabilities as an :class:`enum.Enum` — one stable identifier per
operation — so third-party plugin backends can announce their
capability set without depending on any string constants that could
drift between versions.

Why an enum, not strings:

* Static IDE autocomplete + type checking on capability comparisons.
* One-place-to-look canonical list of every operation dolphin
  understands — plugin authors read this file, pick which caps their
  backend supports, and register.
* ``Enum`` members are hashable and set-friendly — capability
  membership tests reduce to ``cap in backend.capabilities()``.

Design principle: each capability represents a **user-observable
operation**, not an internal implementation detail. Adding a new
capability means dolphin exposes a new public verb; refactoring how
an existing verb is implemented does NOT touch this list.
"""

from __future__ import annotations

import enum


class Capability(enum.Enum):
    """The set of operations a backend may expose.

    Groups (for documentation only — enum members are flat):

    * **Discovery**: LOCATE, GET_TREE
    * **State reads**: READ_TEXT, READ_STATE
    * **Input simulation** (needs an input desktop): CLICK, DOUBLE_CLICK,
      RIGHT_CLICK, HOVER, DRAG, TYPE_TEXT, PRESS_KEY, SCROLL
    * **Programmatic actions** (headless-safe): INVOKE, TOGGLE, EXPAND,
      COLLAPSE, SELECT, SET_VALUE
    * **Media**: SCREENSHOT

    A backend's :meth:`capabilities` must return a frozenset of
    ``Capability`` members it fully supports. Partial support (some
    control types work, others do not) is reported through per-element
    :class:`~dolphin_desktop.UnsupportedPatternError`, NOT by omitting
    the capability here.
    """

    # ---- Discovery ------------------------------------------------------- #
    LOCATE = "locate"
    GET_TREE = "get_tree"

    # ---- State reads ----------------------------------------------------- #
    READ_TEXT = "read_text"
    READ_STATE = "read_state"

    # ---- Input simulation ------------------------------------------------ #
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    HOVER = "hover"
    DRAG = "drag"
    TYPE_TEXT = "type_text"
    PRESS_KEY = "press_key"
    SCROLL = "scroll"

    # ---- Programmatic actions (headless-safe) ---------------------------- #
    INVOKE = "invoke"
    TOGGLE = "toggle"
    EXPAND = "expand"
    COLLAPSE = "collapse"
    SELECT = "select"
    SET_VALUE = "set_value"

    # ---- Media ----------------------------------------------------------- #
    SCREENSHOT = "screenshot"

    def __str__(self) -> str:
        return self.value


# Convenience constants — the two typical capability set shapes used by
# the built-in backends. Plugin authors can compose their own sets
# freely; these are just the reference points our own backends align to.

#: Every capability dolphin currently defines. Useful as a starting
#: point for a UIA-class backend that wraps a full accessibility API.
ALL_CAPABILITIES: frozenset[Capability] = frozenset(Capability)

#: The subset expected of any backend that models a real accessibility
#: tree (UIA, JAB, CDP). Discovery + read + input + programmatic. Only
#: :attr:`Capability.SCREENSHOT` is optional (an accessibility-only
#: backend may legitimately not expose pixel capture).
STANDARD_ACCESSIBILITY: frozenset[Capability] = frozenset(
    {
        Capability.LOCATE,
        Capability.GET_TREE,
        Capability.READ_TEXT,
        Capability.READ_STATE,
        Capability.CLICK,
        Capability.DOUBLE_CLICK,
        Capability.RIGHT_CLICK,
        Capability.HOVER,
        Capability.TYPE_TEXT,
        Capability.PRESS_KEY,
        Capability.INVOKE,
        Capability.TOGGLE,
        Capability.EXPAND,
        Capability.COLLAPSE,
        Capability.SELECT,
        Capability.SET_VALUE,
    }
)

#: The minimum set a pixel-only backend (template match, OCR) can
#: publish. No tree walking, no programmatic actions, no state reads.
IMAGE_ONLY: frozenset[Capability] = frozenset(
    {
        Capability.LOCATE,
        Capability.CLICK,
        Capability.DOUBLE_CLICK,
        Capability.RIGHT_CLICK,
        Capability.HOVER,
        Capability.TYPE_TEXT,
        Capability.PRESS_KEY,
        Capability.SCREENSHOT,
    }
)


__all__ = [
    "ALL_CAPABILITIES",
    "IMAGE_ONLY",
    "STANDARD_ACCESSIBILITY",
    "Capability",
]
