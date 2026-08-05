"""Delphi / VCL (and Lazarus / LCL) native application automation.

Delphi and its free counterpart Lazarus build Windows-native desktop
apps using the VCL / LCL component library. Every window is a
``TForm``; every widget descends from ``TWinControl`` and owns a real
``HWND``. Standard controls (`TButton`, `TEdit`, `TMemo`,
`TCheckBox`, `TComboBox`, `TListBox`, `TPageControl`, `TStringGrid`,
`TListView`) are visible to Windows UIA out of the box.

On Delphi 10.4+ the VCL registers UIA property providers that surface
the developer-assigned ``TComponent.Name`` (``Button1``,
``EdtEmployee``, ``MemoLog``) as the UIA ``AutomationId``. This gives
tests stable, developer-controlled selectors.

Older Delphi and Lazarus (LCL) do **not** register the automation-id
provider — for those the locator falls back to a multi-strategy
resolver:

1. ``auto_id == name`` (VCL 10.4+ / MSAA name pass)
2. ``control_type == vcl→uia(cls) AND title == name`` — for buttons,
   check/radio, tabs, labels the caption is what LCL publishes.
3. ``control_type == vcl→uia(cls) AND index == n`` — an ordinal
   pick used when the caller passes ``index=``.
4. ``near_label=`` — adjacency heuristic: find the control of the
   requested class whose bounding box is closest to the label whose
   text matches ``near_label``. Idiomatic for LCL edits, which have
   no caption.
5. ``title_re=`` — regex on caption, for dynamic / templated captions.

Third-party VCL frameworks (DevExpress, TMS, EhLib) draw their own
canvases and are not reachable via UIA. Those need the injected Delphi
RTTI agent DLL — deferred to v0.3+.

Public entry point::

    from dolphin_desktop import Desktop

    app = Desktop().launch_delphi(r"C:\\path\\to\\my_vcl_app.exe")
    form = app.form(title_re=r".*My VCL.*")
    form.wait_ready(timeout=10)

    # Delphi 10.4+ — by TComponent.Name → AutomationId
    form.component(name="EdtName", cls="TEdit").set_text("Alice")

    # Lazarus / LCL — by caption or adjacent label
    form.component(cls="TButton", title="Save").click()
    form.component(cls="TEdit", near_label="Name:").set_text("Alice")
    form.component(cls="TEdit", index=1).set_text("30")
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._application import Application
from ._exceptions import DolphinError, ElementNotFoundError, WaitTimeoutError
from ._helpers import _escape_keys as _escape_type_keys

if TYPE_CHECKING:
    from ._desktop import Desktop

__all__ = [
    "DelphiApp",
    "DelphiComponent",
    "DelphiError",
    "DelphiForm",
]


class DelphiError(DolphinError):
    """Raised when a Delphi / VCL-specific operation fails."""


# --------------------------------------------------------------------------- #
# Tunables — every magic number in one place                                   #
# --------------------------------------------------------------------------- #

#: Descendant walk depth cap when enumerating a form's controls.
_WALK_MAX_DEPTH = 6

#: Poll interval used while ``component()`` retries within its timeout.
_COMPONENT_POLL_INTERVAL_S = 0.15

#: Poll interval used by ``wait_ready``.
_FORM_READY_POLL_INTERVAL_S = 0.2

#: Pixels of tolerance before the ``_find_near_label`` heuristic penalises
#: a candidate for sitting above or to the left of the label. Small enough
#: to catch obvious mismatches, generous enough to survive DPI rounding.
_LABEL_DIRECTION_TOLERANCE_PX = 4

#: Score penalty added when a candidate sits entirely above or entirely
#: to the left of the label. Big enough to dwarf any Manhattan distance
#: within a typical form (~1000 px), so wrong-direction candidates never
#: win the race.
_LABEL_WRONG_DIRECTION_PENALTY = 400


# --------------------------------------------------------------------------- #
# VCL class → UIA control_type + LCL class-name aliases                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _VclMapping:
    """Static mapping between a Delphi class and its UIA presentation.

    ``control_type`` is the portable filter — same on Delphi VCL,
    Lazarus LCL, and third-party wrappers of standard controls.
    ``class_aliases`` are the Windows window-class names UIA reports
    for the widget: VCL uses the Delphi class name (``TButton``), LCL
    swaps in the underlying Win32 class (``Button``, ``LCLListBox``).
    """

    control_type: str
    class_aliases: tuple[str, ...]


_VCL_TO_UIA: dict[str, _VclMapping] = {
    "TButton": _VclMapping("Button", ("TButton", "Button")),
    "TCheckBox": _VclMapping("CheckBox", ("TCheckBox", "Button")),
    "TRadioButton": _VclMapping("RadioButton", ("TRadioButton", "Button")),
    "TEdit": _VclMapping("Edit", ("TEdit", "Edit")),
    # LCL memos are Edit ES_MULTILINE — the class name is still "Edit".
    "TMemo": _VclMapping("Edit", ("TMemo", "Edit")),
    "TLabel": _VclMapping("Text", ("TLabel", "Static")),
    "TComboBox": _VclMapping("ComboBox", ("TComboBox", "LCLComboBox", "ComboBox")),
    "TListBox": _VclMapping("List", ("TListBox", "LCLListBox", "ListBox")),
    "TPageControl": _VclMapping("Tab", ("TPageControl", "SysTabControl32")),
    "TTabSheet": _VclMapping("TabItem", ("TTabSheet",)),
    "TStringGrid": _VclMapping("Table", ("TStringGrid", "TDrawGrid")),
    "TListView": _VclMapping("List", ("TListView", "SysListView32")),
    "TTreeView": _VclMapping("Tree", ("TTreeView", "SysTreeView32")),
    "TGroupBox": _VclMapping("Group", ("TGroupBox", "Button")),
    "TPanel": _VclMapping("Pane", ("TPanel",)),
    "TStatusBar": _VclMapping("StatusBar", ("TStatusBar", "msctls_statusbar32")),
    "TProgressBar": _VclMapping("ProgressBar", ("TProgressBar", "msctls_progress32")),
    "TTrackBar": _VclMapping("Slider", ("TTrackBar", "msctls_trackbar32")),
    "TSpinEdit": _VclMapping("Spinner", ("TSpinEdit",)),
    "TDateTimePicker": _VclMapping("Custom", ("TDateTimePicker", "SysDateTimePick32")),
}


def _uia_control_type(cls: str | None) -> str | None:
    if not cls:
        return None
    hit = _VCL_TO_UIA.get(cls)
    return hit.control_type if hit else None


def _class_aliases(cls: str | None) -> tuple[str, ...]:
    if not cls:
        return ()
    hit = _VCL_TO_UIA.get(cls)
    return hit.class_aliases if hit else (cls,)


# --------------------------------------------------------------------------- #
# Small internal helpers                                                       #
# --------------------------------------------------------------------------- #


def _first_that_works(getters: Iterable[Callable[..., Any]], default: Any = None) -> Any:
    """Try each getter in order; return the first result that is truthy.

    ``getters`` is an iterable of zero-arg callables. Exceptions from any
    callable are swallowed. The first callable that returns a truthy
    value wins; if none do, ``default`` is returned.

    This replaces the long ``try/except → try/except → try/except``
    chains that every DelphiComponent state-reader used to repeat.
    """
    for getter in getters:
        try:
            v = getter()
        except Exception:
            continue
        if v:
            return v
    return default


def _safe(fn: Callable[..., Any], default: Any = None) -> Any:
    """Call ``fn`` and swallow any exception, returning ``default``."""
    try:
        return fn()
    except Exception:
        return default


def _first_action_that_works(
    actions: Iterable[Callable[[], Any]],
    *,
    operation: str,
    target: Any,
    hint: str,
) -> None:
    """Run *actions* until one completes without raising.

    Unlike :func:`_first_that_works` the verdict is the absence of an
    exception, not a truthy return value. When every strategy raises the
    action did NOT happen, so this raises :class:`DelphiError` rather than
    letting the caller report a click that never landed.
    """
    failures: list[str] = []
    for act in actions:
        try:
            act()
            return
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
    raise DelphiError(
        f"{operation}() failed on {target!r} — every strategy raised: " + "; ".join(failures),
        hint=hint,
    )


# --------------------------------------------------------------------------- #
# Component wrapper                                                            #
# --------------------------------------------------------------------------- #


class DelphiComponent:
    """A single VCL / LCL control on a form — button, edit, memo, list…

    Wraps a pywinauto UIA control-wrapper. Every method delegates to
    the underlying locator; the class exists so tests can assert on a
    Delphi-shaped surface (``component(...).click()``,
    ``.set_text()``, ``.lines()``) instead of on raw pywinauto calls.

    Do not instantiate directly — use :meth:`DelphiForm.component`.
    """

    def __init__(
        self,
        wrapper: Any,
        *,
        name: str,
        cls: str | None = None,
    ) -> None:
        self._w = wrapper
        self._name = name
        self._cls = cls

    # ---- identity ------------------------------------------------------- #

    @property
    def name(self) -> str:
        """The identifier used to resolve this control (TComponent.Name
        on VCL 10.4+, caption or ordinal on LCL)."""
        return self._name

    @property
    def cls(self) -> str | None:
        """The Delphi class name (e.g. ``"TButton"``), if known."""
        return self._cls

    def __repr__(self) -> str:
        return f"DelphiComponent(name={self._name!r}, cls={self._cls!r})"

    # ---- state reads ---------------------------------------------------- #

    def text(self) -> str:
        """Return the component's text (Caption / Text property).

        LCL edits do not surface WM_GETTEXT through UIA — they only
        publish the value via ValuePattern. Fall through several
        getters so this method is portable across Delphi VCL and LCL:

        1. ``window_text()`` — caption / static text (buttons, labels)
        2. ``get_value()`` — ValuePattern.Value (edits, combos)
        3. ``legacy_properties().Value`` — IAccessible.accValue
        4. ``element_info.rich_text`` then ``.value`` — pattern content

        Returns ``""`` when none of them yields anything. ``element_info.name``
        is deliberately not consulted — see ``_element_info_attrs`` below.
        """

        def _legacy_value() -> str:
            legacy = self._w.legacy_properties()
            return legacy.get("Value", "") if isinstance(legacy, dict) else ""

        def _element_info_attrs() -> str:
            # Only read attributes that carry the CONTENT of the widget —
            # ``rich_text`` (TextPattern) and ``value`` (ValuePattern).
            # Deliberately excludes ``name`` because on Delphi VCL 10.4+
            # that is the developer-assigned TComponent.Name (a stable
            # AutomationId, not the widget's text), and returning it as
            # ``.text()`` when the widget is actually empty would fool
            # assertions like ``assert edit.text() == ""``.
            info = self._w.element_info
            for attr in ("rich_text", "value"):
                val = getattr(info, attr, "")
                if val:
                    return val
            return ""

        return _first_that_works(
            (
                lambda: self._w.window_text(),
                lambda: self._w.get_value(),
                _legacy_value,
                _element_info_attrs,
            ),
            default="",
        )

    def value(self) -> str:
        """Return the ValuePattern.Value (edit / memo / combobox text)."""
        v = _first_that_works(
            (
                lambda: self._w.get_value(),
                lambda: self._w.legacy_properties().get("Value", ""),
            ),
            default=None,
        )
        return v if v else self.text()

    def is_visible(self) -> bool:
        return bool(_safe(lambda: self._w.is_visible(), default=False))

    def is_enabled(self) -> bool:
        return bool(_safe(lambda: self._w.is_enabled(), default=False))

    def is_checked(self) -> bool:
        """For TCheckBox / TRadioButton — True when checked.

        Reads the MSAA ``LegacyIAccessible.State`` bitfield and returns
        True iff ``STATE_SYSTEM_CHECKED`` (0x10) is set. Falls through
        to ``get_toggle_state()`` when the wrapper does not expose
        legacy properties (some custom UIA providers).

        Deliberately does **not** cascade through
        ``is_selected()`` — LCL widgets frequently report selection
        (focus / row highlight) independently of the checkbox state,
        which would cause :meth:`uncheck` to see the box as still
        checked after successful state flip and re-toggle it back.
        """
        try:
            raw = self._w.legacy_properties().get("State", None)
        except Exception:
            raw = None
        if isinstance(raw, int):
            return bool(raw & 0x10)  # STATE_SYSTEM_CHECKED
        if isinstance(raw, str):
            words = {w.strip() for w in raw.lower().split(",")}
            return "checked" in words
        # Wrapper does not publish MSAA state — fall back to UIA
        # TogglePattern. 0 = OFF, 1 = ON, 2 = INDETERMINATE.
        try:
            return int(self._w.get_toggle_state()) == 1
        except Exception:
            return False

    def bounding_box(self) -> dict[str, int]:
        rect = self._w.rectangle()
        return {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
            "width": rect.width(),
            "height": rect.height(),
        }

    # ---- actions -------------------------------------------------------- #

    def click(self) -> DelphiComponent:
        """Click the control. Uses UIA Invoke pattern where available,
        falls back to mouse click.

        Raises :class:`DelphiError` when no strategy succeeds — a click
        that never landed must not be reported as a pass.
        """
        # Lambdas defer the attribute lookup so wrappers missing a given
        # method fall through to the next strategy cleanly.
        _first_action_that_works(
            (
                lambda: self._w.invoke(),
                lambda: self._w.click(),
                lambda: self._w.click_input(),
            ),
            operation="click",
            target=self,
            hint=(
                "the control may be disabled or off-screen — check "
                "is_enabled()/is_visible(), or bring the form to the "
                "foreground before clicking"
            ),
        )
        return self

    def double_click(self) -> DelphiComponent:
        _first_action_that_works(
            (
                lambda: self._w.double_click_input(),
                lambda: self._w.click_input(double=True),
            ),
            operation="double_click",
            target=self,
            hint="double-click needs a visible, enabled control on the foreground form",
        )
        return self

    def right_click(self) -> DelphiComponent:
        _first_action_that_works(
            (
                lambda: self._w.right_click_input(),
                lambda: self._w.click_input(button="right"),
            ),
            operation="right_click",
            target=self,
            hint="right-click needs a visible, enabled control on the foreground form",
        )
        return self

    def focus(self) -> DelphiComponent:
        _safe(lambda: self._w.set_focus())
        return self

    def set_text(self, text: str) -> DelphiComponent:
        """Overwrite the text (TEdit / TMemo / TComboBox editable)."""
        for setter in (
            lambda: self._w.set_edit_text(text),
            lambda: self._w.set_text(text),
        ):
            try:
                setter()
                return self
            except Exception:
                continue
        # Last-resort keystroke path. The text is caller data, so it goes
        # through the metacharacter escape — unescaped, "P@ss+1" would send
        # Shift+1 and a "(" or "{" would raise inside pywinauto.
        self.focus()
        _safe(lambda: self._w.type_keys("^a{DELETE}", with_spaces=True))
        _first_action_that_works(
            (
                lambda: self._w.type_keys(
                    _escape_type_keys(text),
                    with_spaces=True,
                    with_tabs=True,
                    with_newlines=True,
                ),
            ),
            operation="set_text",
            target=self,
            hint=(
                "no programmatic setter worked and the keystroke fallback "
                "failed too — check the control is visible, enabled and on "
                "the foreground form"
            ),
        )
        return self

    def type_text(self, text: str, *, clear: bool = True) -> DelphiComponent:
        """Type into the control. When ``clear=True`` the field is
        overwritten via :meth:`set_text`; otherwise the text is appended
        as keystrokes."""
        if clear:
            return self.set_text(text)
        self.focus()
        self._w.type_keys(
            _escape_type_keys(text), with_spaces=True, with_tabs=True, with_newlines=True
        )
        return self

    def press_key(self, key: str) -> DelphiComponent:
        """Send a single key (or key combination) to the control.

        Accepts pywinauto key syntax: ``"{ENTER}"``, ``"^s"``, ``"{TAB}"``.
        """
        self.focus()
        self._w.type_keys(key, with_spaces=True)
        return self

    def toggle(self) -> DelphiComponent:
        """Toggle TCheckBox / TRadioButton state."""
        try:
            self._w.toggle()
            return self
        except Exception:
            pass
        return self.click()

    def check(self) -> DelphiComponent:
        if not self.is_checked():
            self.toggle()
        return self

    def uncheck(self) -> DelphiComponent:
        if self.is_checked():
            self.toggle()
        return self

    def select(self, item: int | str) -> DelphiComponent:
        """Pick an item — TComboBox / TListBox / TPageControl (by index
        or by string caption).

        The path taken depends on what the underlying control lets us
        do:

        1. pywinauto's native ``.select(item)`` — works on real VCL
           combos and on LCL TabControls / ListBoxes.
        2. LCL ``TComboBox`` — pywinauto's Selection walker returns
           the OS taskbar's window titles rather than the combo items,
           so we fall through to keyboard navigation: focus the combo
           and either type the string (csDropDown editable) or send
           HOME + N × DOWN (csDropDownList).
        """
        ct = _safe(lambda: getattr(self._w.element_info, "control_type", "") or "", default="")
        is_combo = ct == "ComboBox"

        if not is_combo:
            for setter_call in (
                lambda: self._w.select(item),
                lambda: self._w.select_item(item),
            ):
                try:
                    setter_call()
                    return self
                except Exception:
                    continue

        self.focus()
        try:
            if isinstance(item, int):
                self._w.type_keys("{HOME}")
                for _ in range(item):
                    self._w.type_keys("{DOWN}")
                self._w.type_keys("{ENTER}")
            else:
                # Editable combos accept typed text as the value.
                # Clear then type — using Ctrl+A + Del so any prefilled
                # default gets wiped.
                self._w.type_keys(
                    "^a{DEL}" + _escape_type_keys(item),
                    with_spaces=True,
                    with_tabs=True,
                    with_newlines=True,
                )
        except Exception:
            # Last resort — try the native call, in case detection was
            # wrong on this platform.
            _safe(lambda: self._w.select(item))
        return self

    # ---- list / memo helpers ------------------------------------------- #

    def lines(self) -> list[str]:
        """Return the lines of a TMemo / TListBox / TStringGrid column.

        For TMemo we prefer pywinauto's ``.line_count()`` + ``.get_line(n)``
        because LCL / VCL multi-line edits only surface their content
        via the underlying EM_GETLINE messages — UIA's
        ``window_text()``/ValuePattern often return an empty string.
        """
        # Prefer per-line getters (memo / multi-line edit).
        try:
            n = int(self._w.line_count())
            if n > 0:
                lines_out = [
                    _safe(lambda i=i: self._w.get_line(i), default="") or "" for i in range(n)
                ]
                if any(lines_out):
                    return [x for x in lines_out if x]
        except Exception:
            pass
        texts = _safe(lambda: list(self._w.texts()), default=[])
        if len(texts) > 1:
            return [t for t in texts[1:] if t]
        return [ln for ln in (self.text() or "").splitlines() if ln]

    def item_count(self) -> int:
        """For TListBox / TComboBox — the number of items."""

        def _count_list_items() -> int:
            return sum(
                1
                for c in self._w.children()
                if getattr(c.element_info, "control_type", "") in {"ListItem", "TabItem"}
            )

        return int(
            _first_that_works(
                (
                    lambda: self._w.item_count(),
                    lambda: len(self._w.item_texts()),
                    _count_list_items,
                    lambda: len(self.lines()),
                ),
                default=0,
            )
        )

    def items(self) -> list[str]:
        texts = _safe(lambda: list(self._w.item_texts()), default=[])
        if texts:
            return texts
        # LCL TListView / SysListView32 — items live under a Header,
        # each DataItem/ListItem holds the row and its text children
        # carry every column.
        out: list[str] = []
        try:
            for c in self._w.descendants():
                ct = getattr(c.element_info, "control_type", "") or ""
                if ct in {"DataItem", "ListItem", "TabItem"}:
                    name = (getattr(c.element_info, "name", "") or "").strip()
                    # Concat any nested Text children for multi-column rows.
                    for gk in _safe(c.children, default=()) or ():
                        gct = getattr(gk.element_info, "control_type", "") or ""
                        if gct == "Text":
                            extra = (getattr(gk.element_info, "name", "") or "").strip()
                            if extra and extra not in name:
                                name = f"{name} {extra}".strip()
                    if name:
                        out.append(name)
        except Exception:
            pass
        return out or self.lines()

    # ---- grid / list-view escape --------------------------------------- #

    def cell(self, *, row: int, col: int) -> str:
        """Read a single cell text — TStringGrid, TListView with report.

        1-indexed row/col to match the Delphi Object Inspector.
        """
        for getter in (
            lambda: self._w.get_item(row - 1, col - 1).text(),
            lambda: self._w.grid_item(row - 1, col - 1).text(),
        ):
            try:
                v = getter()
                if v is not None:
                    return v or ""
            except Exception:
                continue
        lines = self.lines()
        if 0 <= row - 1 < len(lines):
            return lines[row - 1]
        return ""

    # ---- auto-waiting counterparts ------------------------------------- #

    def wait_for_text(
        self,
        text: str | None = None,
        *,
        text_re: str | None = None,
        contains: bool = True,
        timeout: float = 5.0,
        poll_interval: float = 0.15,
    ) -> DelphiComponent:
        """Poll :meth:`text` until it matches — no ``sleep()`` in tests.

        Auto-waiting replacement for
        ``sleep(N); assert "x" in component.text()``. Same contract as
        :meth:`dolphin_desktop._locator.Locator.wait_for_text`.

        Args:
            text: Substring to look for (or exact string when
                ``contains=False``).
            text_re: Regex to match. Mutually exclusive with ``text``.
            contains: Substring vs exact match.
            timeout: Seconds to wait.
            poll_interval: Seconds between reads.
        """
        if (text is None) == (text_re is None):
            raise ValueError("wait_for_text requires exactly one of text= or text_re=")
        if text == "" and contains:
            raise ValueError(
                "wait_for_text(text='', contains=True) always matches immediately — "
                "use contains=False to wait for an exactly-empty field"
            )
        import re as _re

        pat = _re.compile(text_re) if text_re else None
        deadline = time.monotonic() + timeout
        current = ""
        while time.monotonic() < deadline:
            try:
                current = self.text() or ""
            except Exception:
                current = ""
            if text is not None:
                if (text in current) if contains else (current == text):
                    return self
            elif pat is not None and pat.search(current):
                return self
            time.sleep(poll_interval)
        target = text if text is not None else f"regex {text_re!r}"
        raise WaitTimeoutError(
            f"Delphi component {self._name!r} text did not match {target!r} "
            f"within {timeout}s (last seen: {current!r})"
        )

    def wait_for_checked(
        self,
        *,
        checked: bool = True,
        timeout: float = 5.0,
        poll_interval: float = 0.15,
    ) -> DelphiComponent:
        """Poll :meth:`is_checked` until it equals ``checked``.

        Auto-waiting replacement for
        ``sleep(N); assert component.is_checked() == expected``.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if bool(self.is_checked()) == checked:
                    return self
            except Exception:
                pass
            time.sleep(poll_interval)
        raise WaitTimeoutError(
            f"Delphi component {self._name!r} did not become "
            f"{'checked' if checked else 'unchecked'} within {timeout}s"
        )

    # ---- pywinauto escape hatch ---------------------------------------- #

    @property
    def pywinauto(self) -> Any:
        """The underlying pywinauto wrapper for advanced use.

        Escape hatch for anything the DelphiComponent surface does not
        cover (custom UIA patterns, screenshot with region, etc.).
        Prefer public methods when possible so tests stay portable.
        """
        return self._w


# --------------------------------------------------------------------------- #
# Form wrapper                                                                 #
# --------------------------------------------------------------------------- #


class DelphiForm:
    """A single Delphi ``TForm`` — the top-level window.

    Provides the primary lookup: :meth:`component` resolves a child by
    ``TComponent.Name`` (the identifier the developer assigned in the
    Object Inspector). Falls back to control-type + caption or adjacent
    label for older Delphi / Lazarus builds that do not publish
    AutomationId.
    """

    def __init__(self, app: DelphiApp, window: Any, *, name: str | None = None) -> None:
        self._app = app
        self._win = window
        self._name = name or ""
        # Descendant walk cache — cleared per component() attempt so
        # different retries within a single timeout window re-query
        # the tree (elements may appear late), but a single attempt
        # does not walk the whole form four times.
        self._walk_cache: list[Any] | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def pywinauto(self) -> Any:
        return self._win

    def title(self) -> str:
        return _safe(lambda: self._win.window_text(), default="") or ""

    def wait_ready(self, timeout: float = 15.0) -> DelphiForm:
        """Block until the form window responds to UIA lookups."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready = _safe(
                lambda: self._win.exists() and self._win.is_visible(),
                default=False,
            )
            if ready:
                return self
            time.sleep(_FORM_READY_POLL_INTERVAL_S)
        raise WaitTimeoutError(
            f"Delphi form {self._name!r} did not become ready within {timeout}s",
            hint=(
                "verify the app launched by inspecting `app.application.process_id` or dolphin spy"
            ),
        )

    # ---- component resolution ------------------------------------------ #

    def component(
        self,
        *,
        name: str | None = None,
        cls: str | None = None,
        title: str | None = None,
        title_re: str | None = None,
        index: int | None = None,
        near_label: str | None = None,
        timeout: float = 10.0,
    ) -> DelphiComponent:
        """Resolve a child component.

        Args:
            name: The component name assigned in the Delphi Object
                Inspector (``Button1``, ``EdtEmployee``, ``MemoLog``).
                On Delphi 10.4+ maps to UIA AutomationId. On LCL,
                treated as a fallback caption match.
            cls: Optional Delphi class name (``"TButton"``,
                ``"TEdit"``, ``"TMemo"``). Portable — resolves to the
                UIA control_type + class-name aliases so the same lookup
                works on Delphi VCL and Lazarus LCL.
            title: Exact caption of the control (``"Save"``).
                Preferred selector on LCL for buttons / checkboxes /
                radios / labels.
            title_re: Regex on the control's caption — for dynamic /
                templated captions (status labels, progress bars).
            index: 0-based ordinal — pick the N-th control of the
                requested class (``cls="TEdit", index=0`` → first
                TEdit). Idiomatic for LCL edits that carry no caption.
            near_label: The text of a nearby TLabel — find the control
                of ``cls`` whose bounding box is closest to it. Robust
                for LCL / anonymous edits.
            timeout: How long to wait for the component to exist.
        """
        if not any([name, title, title_re, index is not None, near_label]):
            raise DelphiError(
                "component() requires at least one of name / title / title_re / index / near_label",
                hint="pass name= for VCL 10.4+; title= or near_label= for LCL edits",
            )

        control_type = _uia_control_type(cls)
        aliases = _class_aliases(cls)
        marker = (
            name or title or title_re or near_label or (f"#{index}" if index is not None else "")
        )

        deadline = time.monotonic() + timeout
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            # Every retry sees a fresh view of the tree.
            self._walk_cache = None

            # --- adjacency: find control near a label ------------------- #
            if near_label:
                found = self._find_near_label(
                    near_label=near_label,
                    control_type=control_type,
                    aliases=aliases,
                )
                if found is not None:
                    return DelphiComponent(found, name=marker, cls=cls)

            # --- ordinal index over control_type or class alias -------- #
            if index is not None:
                found = self._find_by_index(
                    index=index,
                    control_type=control_type,
                    aliases=aliases,
                )
                if found is not None:
                    return DelphiComponent(found, name=marker, cls=cls)

            # --- regex title match -------------------------------------- #
            if title_re:
                found = self._find_by_title_re(
                    title_re=title_re,
                    control_type=control_type,
                    aliases=aliases,
                )
                if found is not None:
                    return DelphiComponent(found, name=marker, cls=cls)

            # --- name/title/auto_id passes ----------------------------- #
            for criteria in self._build_criteria_variants(
                name=name, title=title, control_type=control_type, aliases=aliases
            ):
                try:
                    child = self._win.child_window(**criteria)
                    if child.exists():
                        return DelphiComponent(child, name=marker, cls=cls)
                except Exception as exc:
                    # TypeError → wrong backend (win32 rejects
                    # automation_id); anything else → transient tree
                    # instability. In both cases keep trying other
                    # variants and only propagate the last one when the
                    # whole loop times out.
                    last_exc = exc
            time.sleep(_COMPONENT_POLL_INTERVAL_S)

        raise ElementNotFoundError(
            f"Delphi component {marker!r}"
            + (f" (class {cls!r})" if cls else "")
            + f" not found on form {self._name!r} within {timeout}s",
            hint=(
                "on Delphi 10.4+ verify TComponent.Name in the Object Inspector. "
                "On Lazarus / LCL pass title= (caption) for buttons/checkboxes, "
                "near_label= for edits, or index= for anonymous controls. "
                "Use `dolphin spy --delphi --pid <pid>` to inspect the tree."
            ),
        ) from last_exc

    # ---- component resolution helpers ---------------------------------- #

    def _build_criteria_variants(
        self,
        *,
        name: str | None,
        title: str | None,
        control_type: str | None,
        aliases: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        variants: list[dict[str, Any]] = []
        if name:
            if control_type:
                variants.append({"auto_id": name, "control_type": control_type})
            variants.append({"auto_id": name})
            if control_type:
                variants.append({"title": name, "control_type": control_type})
            variants.append({"title": name})
            for alias in aliases:
                variants.append({"title": name, "class_name": alias})
        if title:
            if control_type:
                variants.append({"title": title, "control_type": control_type})
            variants.append({"title": title})
            for alias in aliases:
                variants.append({"title": title, "class_name": alias})
        return variants

    def _walk_children(self) -> list[Any]:
        """Return every descendant control, up to ``_WALK_MAX_DEPTH``.

        Cached per ``component()`` retry — see the note in :meth:`component`.
        """
        if self._walk_cache is not None:
            return self._walk_cache

        out: list[Any] = []

        def _walk(node: Any, depth: int) -> None:
            if depth > _WALK_MAX_DEPTH:
                return
            kids = _safe(lambda: node.children(), default=None)
            if not kids:
                return
            for k in kids:
                out.append(k)
                _walk(k, depth + 1)

        _walk(self._win, 0)
        self._walk_cache = out
        return out

    def _matches_class(
        self, wrapper: Any, control_type: str | None, aliases: tuple[str, ...]
    ) -> bool:
        info = _safe(lambda: wrapper.element_info, default=None)
        if info is None:
            return False
        cn = getattr(info, "class_name", "") or ""
        ct = getattr(info, "control_type", "") or ""
        if aliases:
            # Aliases are authoritative when supplied — class must match
            # one of them exactly (this is how we tell TListBox apart
            # from TListView, which both surface as UIA List).
            return cn in aliases
        if control_type:
            return ct == control_type
        return True

    def _find_by_title_re(
        self,
        *,
        title_re: str,
        control_type: str | None,
        aliases: tuple[str, ...],
    ) -> Any | None:
        pat = re.compile(title_re)
        for w in self._walk_children():
            if aliases and not self._matches_class(w, control_type, aliases):
                continue
            text = _safe(lambda w=w: w.window_text(), default="") or ""
            if pat.search(text):
                return w
        return None

    def _find_by_index(
        self,
        *,
        index: int,
        control_type: str | None,
        aliases: tuple[str, ...],
    ) -> Any | None:
        matches = [
            w for w in self._walk_children() if self._matches_class(w, control_type, aliases)
        ]
        if 0 <= index < len(matches):
            return matches[index]
        return None

    def _resolve_label(self, near_label: str, children: list[Any]) -> Any | None:
        """Find the label element whose text matches ``near_label``.

        Preference order: (a) actual Static / TLabel controls first,
        (b) any other element whose text happens to match.
        """
        wanted = near_label.strip().rstrip(":")

        def _text_matches(el: Any) -> bool:
            info = _safe(lambda: el.element_info, default=None)
            if info is None:
                return False
            text = (getattr(info, "name", "") or "").strip().rstrip(":")
            return text == wanted

        def _is_label_role(el: Any) -> bool:
            info = _safe(lambda: el.element_info, default=None)
            if info is None:
                return False
            ct = getattr(info, "control_type", "") or ""
            cn = getattr(info, "class_name", "") or ""
            return ct in {"Text", "Static"} or "label" in cn.lower()

        with_role = [c for c in children if _text_matches(c) and _is_label_role(c)]
        if with_role:
            return with_role[0]
        any_text = [c for c in children if _text_matches(c)]
        return any_text[0] if any_text else None

    def _find_near_label(
        self,
        *,
        near_label: str,
        control_type: str | None,
        aliases: tuple[str, ...],
    ) -> Any | None:
        """Find the control of the requested class whose bounding box
        is nearest to a label matching ``near_label``.

        Distance metric: label center to the nearest edge/corner of the
        candidate control's rectangle. This handles both patterns:

        * **left label** — ``Name: [    edit    ]`` — nearest point is
          the edit's left edge, aligned vertically with the label.
        * **top label** — ``Log:`` above ``[  memo  ]`` — nearest point
          is the memo's top edge, aligned horizontally.

        Controls whose entire body sits above / to the left of the
        label get a large penalty so the search never picks up an
        unrelated widget on the opposite side of the form.
        """
        children = self._walk_children()
        label = self._resolve_label(near_label, children)
        if label is None:
            return None
        lr = _safe(lambda: label.rectangle(), default=None)
        if lr is None:
            return None
        lcx = (lr.left + lr.right) // 2
        lcy = (lr.top + lr.bottom) // 2

        best = None
        best_score: tuple[int, int] | None = None
        for c in children:
            if not self._matches_class(c, control_type, aliases):
                continue
            r = _safe(lambda c=c: c.rectangle(), default=None)
            if r is None:
                continue
            # Nearest point of rectangle r to label center.
            nx = max(r.left, min(lcx, r.right))
            ny = max(r.top, min(lcy, r.bottom))
            manhattan = abs(nx - lcx) + abs(ny - lcy)
            # Direction penalties — control entirely above or entirely
            # left of the label is likely unrelated.
            direction = 0
            if r.bottom <= lr.top - _LABEL_DIRECTION_TOLERANCE_PX:
                direction += _LABEL_WRONG_DIRECTION_PENALTY
            if r.right <= lr.left - _LABEL_DIRECTION_TOLERANCE_PX:
                direction += _LABEL_WRONG_DIRECTION_PENALTY
            score = (direction, manhattan)
            if best_score is None or score < best_score:
                best_score = score
                best = c
        return best

    # ---- enumeration ---------------------------------------------------- #

    def components(self, *, cls: str | None = None) -> list[DelphiComponent]:
        """Enumerate every descendant component of this form.

        When ``cls`` is provided, only components of that Delphi class
        are returned (``cls="TButton"`` → every button on the form).
        """
        aliases = _class_aliases(cls)
        control_type = _uia_control_type(cls)
        out: list[DelphiComponent] = []
        for c in self._walk_children():
            info = _safe(lambda c=c: c.element_info, default=None)
            if info is None:
                continue
            class_name = getattr(info, "class_name", "") or ""
            if cls and not self._matches_class(c, control_type, aliases):
                continue
            name = (
                getattr(info, "automation_id", "")
                or _safe(lambda c=c: c.window_text(), default="")
                or ""
            )
            out.append(DelphiComponent(c, name=name, cls=class_name or None))
        return out


# --------------------------------------------------------------------------- #
# App facade                                                                   #
# --------------------------------------------------------------------------- #


class DelphiApp:
    """Facade over a Delphi / VCL / Lazarus LCL native process.

    Built by :meth:`Desktop.launch_delphi` /
    :meth:`Desktop.attach_delphi`. Wraps the underlying
    :class:`Application` and exposes VCL-native selectors — forms and
    components resolved via ``TForm.Name`` / ``TComponent.Name`` on
    Delphi 10.4+ or caption / adjacency heuristics on LCL.

    Fronts the ``delphi`` backend. Query supported operations via
    ``DelphiApp.backend_supports(Capability.INVOKE)`` etc.
    """

    #: Registered :class:`Backend` id this facade fronts.
    backend_id: str = "delphi"

    @classmethod
    def backend(cls):
        from ._backend import resolve as _resolve

        return _resolve(cls.backend_id)

    @classmethod
    def backend_supports(cls, capability) -> bool:
        return cls.backend().supports(capability)

    @classmethod
    def require_capability(cls, capability) -> None:
        cls.backend().require_capability(capability)

    def __init__(self, application: Application, *, title_re: str | None = None) -> None:
        self._application = application
        self._title_re = title_re

    @property
    def application(self) -> Application:
        return self._application

    # ---- form lookup ---------------------------------------------------- #

    def form(
        self,
        *,
        name: str | None = None,
        title: str | None = None,
        title_re: str | None = None,
        timeout: float = 15.0,
    ) -> DelphiForm:
        """Resolve a top-level TForm.

        Args:
            name: The Delphi ``TForm.Name`` (Object Inspector Name).
                Preferred selector on Delphi 10.4+ where it maps to
                UIA AutomationId.
            title: Exact window caption (TForm.Caption).
            title_re: Regex on the window caption.
            timeout: Seconds to wait for the form to appear.
        """
        criteria: dict[str, Any] = {}
        if name is not None:
            criteria["auto_id"] = name
        elif title is not None:
            criteria["title"] = title
        elif title_re is not None:
            criteria["title_re"] = title_re
        elif self._title_re is not None:
            criteria["title_re"] = self._title_re

        try:
            win = self._application._app.window(**criteria)
            win.wait("exists visible", timeout=timeout)
        except Exception as exc:
            raise ElementNotFoundError(
                f"Delphi form not found (criteria={criteria!r})",
                hint=(
                    "check the Delphi Object Inspector for the TForm.Name, "
                    "or use dolphin spy --delphi to enumerate top-level windows."
                ),
            ) from exc
        return DelphiForm(self, win, name=name or "")

    def forms(self) -> list[DelphiForm]:
        """Return every top-level TForm in the process."""
        wins = _safe(lambda: self._application._app.windows(), default=[])
        out: list[DelphiForm] = []
        for w in wins:
            info = _safe(lambda w=w: w.element_info, default=None)
            if info is None:
                continue
            class_name = getattr(info, "class_name", "") or ""
            # Accept both VCL ("TForm") and LCL ("Window") shells.
            if class_name and not (
                "form" in class_name.lower()
                or class_name.startswith("T")
                or class_name in {"Window", "#32770"}
            ):
                continue
            name = getattr(info, "automation_id", "") or ""
            out.append(DelphiForm(self, w, name=name))
        return out

    # ---- lifecycle ------------------------------------------------------ #

    def close(self) -> None:
        _safe(lambda: self._application.kill())

    def __enter__(self) -> DelphiApp:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"DelphiApp(pid={self._application.process_id})"


# --------------------------------------------------------------------------- #
# Factory                                                                      #
# --------------------------------------------------------------------------- #


def _launch_delphi(
    desktop: Desktop,
    cmd: str,
    *,
    timeout: float,
    startup_delay: float,
    work_dir: str | None,
    title_re: str | None,
) -> DelphiApp:
    """Launch a Delphi / VCL executable and return a :class:`DelphiApp`.

    Delphi apps do not need any environment variable prep — UIA
    exposure is automatic on Delphi 10.4+. Older Delphi and Lazarus
    still surface standard controls via UIA (with caption-based
    identifiers) and MSAA.

    The Delphi backend requires the UIA pywinauto backend because the
    component resolver uses ``automation_id`` / ``control_type`` filters
    the Win32 backend does not accept. We ask :class:`Desktop` for a
    UIA-backed :class:`Application` explicitly so any future
    hidden-mode / spawn improvements on ``Desktop`` propagate here for
    free.
    """
    application = desktop._launch_raw(
        cmd,
        backend="uia",
        timeout=timeout,
        work_dir=work_dir,
        startup_delay=startup_delay,
    )
    return DelphiApp(application, title_re=title_re)


def _attach_delphi(
    desktop: Desktop,
    *,
    title: str | None,
    title_re: str | None,
    process: int | None,
    path: str | None,
    timeout: float,
) -> DelphiApp:
    """Attach to an already-running Delphi / Lazarus process on UIA."""
    criteria = desktop._build_attach_criteria(
        title=title,
        title_re=title_re,
        process=process,
        path=path,
        method_name="attach_delphi",
        error_class=DelphiError,
    )
    application = desktop._connect_raw(backend="uia", timeout=timeout, **criteria)
    return DelphiApp(application, title_re=title_re)
