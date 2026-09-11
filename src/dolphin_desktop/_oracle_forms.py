"""Oracle Forms automation on top of the Java Access Bridge.

Oracle Forms applications are Java Swing programs deployed through
Oracle Forms Services (browser applet, Java Web Start, or FSAL — Forms
Standalone Launcher). The client-side runtime is a JVM (frequently
Adoptium 8 or 11) that renders Oracle's custom Swing components — text
items, buttons, canvases, tabbed pages — and reacts to Function-key
input semantics defined by the Forms framework (F7=EnterQuery,
F8=ExecuteQuery, F9=List of Values, F10=Save). The exact bindings come
from the runtime's key-binding resource file — see :class:`OracleFormsKey`,
which documents why that table must be adopted as a whole.

dolphin_desktop automates Oracle Forms by:

* Enabling the Java Access Bridge (already installed by the Adoptium
  or Oracle JDK — dolphin's :class:`JavaAccessBridge` handles this).
* Wrapping the launch call so the JVM starts with
  ``-Djava.accessibility=true`` and the JAB DLL is loaded before the
  form paints.
* Providing an Oracle-Forms-aware surface (:class:`OracleFormsApp`)
  that speaks Forms concepts — blocks, items, function keys, LOV,
  status line — on top of the generic :class:`JABLocator` walker.

Typical usage::

    from dolphin_desktop import Desktop, OracleFormsKey

    app = Desktop().launch_oracle_forms(
        jnlp="http://forms.example.com/forms/frmservlet",
    )
    app.form().wait_ready()
    app.block("EMPLOYEES").item("EMPNO").type_text("7369")
    app.execute_query()
    assert app.status_line().startswith("Record 1 of")
    app.save()
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterable
from typing import Any

from ._application import Application
from ._exceptions import DolphinError, ElementNotFoundError, WaitTimeoutError
from ._java import JABLocator, JavaAccessBridge
from ._keyboard import Keyboard
from ._logging import get_logger

_log = get_logger("oracle_forms")

__all__ = [
    "OracleFormsApp",
    "OracleFormsBlock",
    "OracleFormsError",
    "OracleFormsItem",
    "OracleFormsKey",
    "OracleFormsLov",
    "OracleFormsMenu",
    "OracleFormsWindow",
]


class OracleFormsError(DolphinError):
    """Raised when an Oracle-Forms-specific operation fails."""


# --------------------------------------------------------------------------- #
# Function keys — Oracle Forms semantics                                       #
# --------------------------------------------------------------------------- #


class OracleFormsKey:
    """Mnemonic constants for the Oracle Forms function-key contract.

    Attributes are the shortcut names understood by
    :func:`OracleFormsApp.function_key` and by pywinauto's ``type_keys``
    escape sequence (``{F7}``, ``{F8}``, ``+{F7}``…).

    .. warning::

       **This table is an all-or-nothing choice, not a list of independent
       constants.** The Forms runtime loads exactly one key-binding resource
       file, and every key comes from that one file. These values follow the
       **PC-style** family (``fmrpcweb.res``). A host running the *stock*
       ``fmrweb.res`` uses a different family, and mixing entries from the two
       is worse than picking the wrong one wholesale — on a stock-file host
       these bindings mean:

       * ``DUPLICATE_RECORD`` → **Exit**, discarding uncommitted changes
       * ``INSERT_RECORD`` → **Clear Record**
       * ``DELETE_RECORD`` → **Duplicate Record**
       * ``CLEAR_FORM`` → **Next Primary Key**

       Before relying on these, open the form and press **Ctrl+K** ("Show
       Keys") to see the bindings your runtime actually loaded. If they do not
       match, override the whole block rather than individual entries.

    Entries marked *unverified* below are consistent with the PC-style family
    but have not been confirmed against a published resource file.
    """

    HELP = "F1"
    LIST_OF_VALUES = "F9"
    ENTER_QUERY = "F7"
    EXECUTE_QUERY = "F8"
    COUNT_QUERY = "Shift+F2"  # unverified
    CANCEL_QUERY = "Ctrl+Q"
    SAVE = "F10"
    COMMIT = "F10"
    EXIT = "Ctrl+Q"
    NEXT_RECORD = "Shift+Down"
    PREVIOUS_RECORD = "Shift+Up"
    NEXT_ITEM = "Tab"
    PREVIOUS_ITEM = "Shift+Tab"
    NEXT_BLOCK = "Shift+Page_Down"  # unverified
    PREVIOUS_BLOCK = "Shift+Page_Up"  # unverified
    CLEAR_ITEM = "Ctrl+U"
    CLEAR_RECORD = "Shift+F4"
    CLEAR_BLOCK = "Shift+F5"  # unverified
    CLEAR_FORM = "Shift+F7"  # unverified
    DUPLICATE_ITEM = "F3"  # unverified
    DUPLICATE_RECORD = "F4"
    INSERT_RECORD = "F6"
    DELETE_RECORD = "Shift+F6"


_PYWINAUTO_KEY_MAP = {
    "F1": "{F1}",
    "F2": "{F2}",
    "F3": "{F3}",
    "F4": "{F4}",
    "F5": "{F5}",
    "F6": "{F6}",
    "F7": "{F7}",
    "F8": "{F8}",
    "F9": "{F9}",
    "F10": "{F10}",
    "F11": "{F11}",
    "F12": "{F12}",
    "Tab": "{TAB}",
    "Escape": "{ESC}",
    "Up": "{UP}",
    "Down": "{DOWN}",
    "Left": "{LEFT}",
    "Right": "{RIGHT}",
    "Enter": "{ENTER}",
    "Home": "{HOME}",
    "End": "{END}",
    "Page_Up": "{PGUP}",
    "Page_Down": "{PGDN}",
}


def _translate_key(shortcut: str) -> str:
    """Convert an OracleFormsKey shortcut into pywinauto's type_keys syntax.

    ``"F7"`` → ``"{F7}"``.
    ``"Shift+F5"`` → ``"+{F5}"``.
    ``"Ctrl+Page_Down"`` → ``"^{PGDN}"``.
    """
    parts = shortcut.split("+")
    modifiers = ""
    base_key = parts[-1]
    for mod in parts[:-1]:
        m = mod.strip().lower()
        if m in ("ctrl", "control"):
            modifiers += "^"
        elif m == "shift":
            modifiers += "+"
        elif m == "alt":
            modifiers += "%"
    base = _PYWINAUTO_KEY_MAP.get(base_key.strip(), base_key.strip())
    return modifiers + base


# --------------------------------------------------------------------------- #
# Item / block / menu wrappers                                                 #
# --------------------------------------------------------------------------- #


class OracleFormsItem:
    """A single writable field or button inside a form block.

    Wraps a :class:`JABLocator` — every action delegates to JAB. Kept
    as a distinct type so tests can assert against a stable Oracle-
    Forms surface and so future backends (image-based fallback,
    injected agent) can supply a drop-in replacement.
    """

    def __init__(
        self,
        jab: JABLocator,
        *,
        name: str,
        app: OracleFormsApp | None = None,
    ) -> None:
        self._jab = jab
        self._name = name
        self._app = app

    @property
    def name(self) -> str:
        return self._name

    def value(self) -> str:
        return self._jab.value()

    def text(self) -> str:
        return self._jab.text()

    def type_text(self, text: str, *, clear: bool = True) -> OracleFormsItem:
        """Set the item's text.

        Uses JAB's ``setTextContents`` (no mouse interaction) to avoid
        the ``SetCursorPos`` failure mode that plagues headless / RDP /
        virtual-desktop test runs. Set ``clear=False`` to append; that
        path falls back to focus + type_text (keystroke replay), which
        requires the window to be in the foreground — the app is
        brought to the front automatically first.
        """
        if clear:
            self._jab.set_text(text)
        else:
            if self._app is not None:
                self._app.bring_to_foreground()
            self._jab.focus()
            self._jab.type_text(text)
        return self

    def set_text(self, text: str) -> OracleFormsItem:
        """Overwrite via JAB set-text (fewer keystrokes than type_text)."""
        self._jab.set_text(text)
        return self

    def clear(self) -> OracleFormsItem:
        self._jab.clear()
        return self

    def click(self) -> OracleFormsItem:
        self._jab.click()
        return self

    def focus(self) -> OracleFormsItem:
        self._jab.focus()
        return self

    def press_key(self, key: str) -> OracleFormsItem:
        self._jab.press_key(key)
        return self

    def is_visible(self) -> bool:
        return self._jab.is_visible()

    def is_enabled(self) -> bool:
        return self._jab.is_enabled()

    def bounding_box(self) -> dict[str, int]:
        return self._jab.bounding_box()

    def __repr__(self) -> str:
        return f"OracleFormsItem(name={self._name!r})"


class OracleFormsBlock:
    """A named data block containing one or more items.

    In Forms, a "block" is a group of items bound to a table (data
    block) or free-standing (control block). Naming convention is
    ``BLOCK_NAME.ITEM_NAME`` for accessibility labels. This class
    resolves items by that name.
    """

    def __init__(self, app: OracleFormsApp, *, name: str) -> None:
        self._app = app
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def item(self, name: str) -> OracleFormsItem:
        """Return the OracleFormsItem whose accessibility name matches.

        Oracle Forms labels items either as ``ITEMNAME`` (plain) or
        ``BLOCK.ITEMNAME`` (fully qualified). We try the block-scoped
        name first, then fall back to the plain name.
        """
        candidates = [f"{self._name}.{name}", name]
        for candidate in candidates:
            try:
                jab = self._app._locator(name=candidate)
                if jab.exists():
                    return OracleFormsItem(jab, name=candidate, app=self._app)
            except ElementNotFoundError:
                continue
        raise ElementNotFoundError(
            f"item {name!r} not found in block {self._name!r} (tried names: {candidates})",
            hint=(
                f"verify the JTextField's accessibleName is exactly {self._name}.{name!r} — Oracle "
                f"Forms convention is BLOCK.ITEM; use JAB spy to inspect the accessible tree"
            ),
        )

    def current_record(self) -> int:
        """Return the 1-indexed current record number, or 0 if unknown.

        Uses the Oracle Forms status line convention "Record N of M".
        """
        line = self._app.status_line()
        m = _RECORD_STATUS_RE.search(line)
        if m is None:
            return 0
        return int(m.group(1))

    def __repr__(self) -> str:
        return f"OracleFormsBlock(name={self._name!r})"


class OracleFormsMenu:
    """Menu-bar helper. ``menu('Action').select('Save')`` clicks the
    Save entry under the Action menu."""

    def __init__(self, app: OracleFormsApp, *, name: str) -> None:
        self._app = app
        self._name = name

    def select(self, path: str | Iterable[str]) -> None:
        """Open this menu and click a submenu path.

        Accepts a single label (``"Save"``) or an iterable for nested
        menus (``["Query", "Enter"]``).
        """
        # Focus and open the menu via keyboard: Alt + first-letter.
        first_char = self._name[0].upper()
        Keyboard.press(f"%{first_char}")
        time.sleep(0.15)
        labels = [path] if isinstance(path, str) else list(path)
        for label in labels:
            jab = self._app._locator(name=label, role="menu item")
            if not jab.exists():
                # Fall back to any role with matching name.
                jab = self._app._locator(name=label)
            jab.click()
            time.sleep(0.1)


class OracleFormsLov:
    """Wrapper around the List-of-Values popup opened via F9."""

    def __init__(self, app: OracleFormsApp) -> None:
        self._app = app

    def is_open(self) -> bool:
        try:
            return self._app._locator(title_re=".*[Ll]ist [Oo]f [Vv]alues.*").exists(timeout=0.5)
        except (ElementNotFoundError, OracleFormsError):
            return False

    def select(self, value: str) -> None:
        """Highlight an entry matching *value* and press Enter."""
        list_locator = self._app._locator(name=value)
        if not list_locator.exists():
            raise ElementNotFoundError(f"LOV entry {value!r} not visible")
        list_locator.click()
        Keyboard.press("{ENTER}")

    def cancel(self) -> None:
        Keyboard.press("{ESC}")


_RECORD_STATUS_RE = re.compile(r"[Rr]ecord\s+(\d+)\s+of\s+(\d+|\?)")


class OracleFormsWindow:
    """The main form window itself — used as a scope for lookups."""

    def __init__(self, app: OracleFormsApp) -> None:
        self._app = app

    def title(self) -> str:
        try:
            return self._app._application.window().title()
        except Exception:
            return ""

    def wait_ready(self, timeout: float = 15.0) -> None:
        """Block until the main form window responds to lookups.

        Considers the window ready once JAB returns a root context for
        it — the point at which the JVM has published its accessibility
        tree. The context is released again immediately; this is a
        readiness probe, not a traversal.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                hwnd = self._app._primary_hwnd()
                if hwnd:
                    from ._java import _JABSession

                    session = _JABSession.get_or_create()
                    session.pump(10, 0.05)
                    ctx = session.get_root_context(hwnd)
                    if ctx is not None:
                        session.release(*ctx)
                        return
            except Exception:
                pass
            time.sleep(0.25)
        raise WaitTimeoutError(
            f"Oracle Forms window did not become ready within {timeout}s",
            hint=(
                "verify Java Access Bridge is enabled (jabswitch /enable) and the JVM was started "
                "with -Djava.accessibility=true"
            ),
        )


# --------------------------------------------------------------------------- #
# Top-level facade                                                             #
# --------------------------------------------------------------------------- #


class OracleFormsApp:
    """Facade over an Oracle Forms Java client.

    Built by :meth:`Desktop.launch_oracle_forms` (or
    :meth:`Desktop.attach_oracle_forms`). Wraps the underlying
    :class:`Application` process and exposes Forms-native selectors
    (block, item, menu, LOV) plus function-key shortcuts.
    """

    #: Fronts the JavaBackend (Oracle Forms sits on JAB).
    backend_id: str = "java"

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

    def __init__(
        self,
        application: Application,
        *,
        title_re: str | None = None,
    ) -> None:
        self._application = application
        self._title_re = title_re

    # ---- accessors ------------------------------------------------------ #

    @property
    def application(self) -> Application:
        return self._application

    def form(self) -> OracleFormsWindow:
        return OracleFormsWindow(self)

    def block(self, name: str) -> OracleFormsBlock:
        return OracleFormsBlock(self, name=name)

    def item(self, name: str) -> OracleFormsItem:
        """Return an item by name — searches the whole form.

        Prefer ``block(...).item(...)`` when the block is known; use
        this shortcut for globally-unique items (e.g. header buttons).
        """
        try:
            jab = self._locator(name=name)
        except OracleFormsError as exc:
            # Resolving the JAB locator also resolves the top-level window.
            # Keep that domain error, but add the public operation and item
            # name so a failed facade call remains diagnosable.
            reason = exc.args[0] if exc.args else "lookup failed"
            raise OracleFormsError(
                f"item {name!r} lookup failed for Oracle Forms app: {reason}",
                hint=exc.hint,
            ) from exc
        return OracleFormsItem(jab, name=name, app=self)

    def menu(self, name: str) -> OracleFormsMenu:
        return OracleFormsMenu(self, name=name)

    def lov(self) -> OracleFormsLov:
        return OracleFormsLov(self)

    # ---- function keys -------------------------------------------------- #

    def function_key(self, key: str) -> None:
        """Send an Oracle-Forms function-key shortcut to the form window.

        *key* is either an :class:`OracleFormsKey` attribute value
        (``"F7"``, ``"Shift+F5"`` …) or any raw key spec accepted by
        :func:`_translate_key`.

        The delivery mechanism is layered — most reliable first:

        1. **JAB focus request** for the top-level accessible context
           so Swing's InputMap sees the JFrame as focused. Otherwise
           the WHEN_IN_FOCUSED_WINDOW binding does not fire when the
           OS keyboard focus lives in another window.
        2. **PostMessage(WM_KEYDOWN)** to the Java HWND. Delivers to
           the JVM's WndProc without needing OS foreground focus.
           Unmodified keys only — PostMessage cannot set the target
           thread's modifier state.
        3. **bring_to_foreground + global SendInput** for modifier
           combos and for hosts that filter PostMessage keys entirely.
        """
        try:
            hwnd = self._primary_hwnd()
        except OracleFormsError:
            hwnd = 0

        if hwnd:
            self._request_java_focus(hwnd)
        if hwnd and _post_key_to_window(hwnd, key):
            return
        # Fallback: global keyboard replay (needs foreground focus).
        self.bring_to_foreground()
        Keyboard.press(_translate_key(key))

    def _request_java_focus(self, hwnd: int) -> None:
        """Ask the JVM to focus the top-level accessible context of *hwnd*.

        Best-effort — missing JAB API or a non-Java window is a no-op.
        Must be called before any PostMessage-based key delivery.
        """
        try:
            from ._java import _JABSession

            session = _JABSession.get_or_create()
            session.pump(5, 0.01)
            ctx = session.get_root_context(hwnd)
            if ctx is None:
                return
            vm_id, ac = ctx
            try:
                session.request_focus(vm_id, ac)
            finally:
                session.release(vm_id, ac)
        except Exception:
            pass

    def enter_query(self) -> None:
        """F7 — put the current block into query-entry mode."""
        self.function_key(OracleFormsKey.ENTER_QUERY)

    def execute_query(self) -> None:
        """F8 — run the pending query and fetch matching records."""
        self.function_key(OracleFormsKey.EXECUTE_QUERY)

    def cancel_query(self) -> None:
        self.function_key(OracleFormsKey.CANCEL_QUERY)

    def save(self) -> None:
        """F10 — commit outstanding changes to the database."""
        self.function_key(OracleFormsKey.SAVE)

    def next_record(self) -> None:
        self.function_key(OracleFormsKey.NEXT_RECORD)

    def previous_record(self) -> None:
        self.function_key(OracleFormsKey.PREVIOUS_RECORD)

    def next_block(self) -> None:
        self.function_key(OracleFormsKey.NEXT_BLOCK)

    def previous_block(self) -> None:
        self.function_key(OracleFormsKey.PREVIOUS_BLOCK)

    def list_of_values(self) -> None:
        """F9 — open the List of Values popup for the current item."""
        self.function_key(OracleFormsKey.LIST_OF_VALUES)

    # ---- state readers -------------------------------------------------- #

    def wait_for_status(
        self,
        needle: str,
        *,
        timeout: float = 5.0,
        poll_interval: float = 0.1,
    ) -> str:
        """Block until the status line contains *needle*, then return it.

        Preferred over ``sleep(x); assert needle in app.status_line()``
        — a PostMessage-delivered function key is scheduled on the
        Swing event dispatch thread, so the status text lags the API
        call by an unpredictable interval. This poll avoids both
        flakiness and wasted wait time in the fast path.

        Empty ``needle`` is rejected — ``"" in any_string`` is
        universally True, so ``wait_for_status("")`` would return
        immediately regardless of the actual status line.
        """
        if not needle:
            raise ValueError(
                "wait_for_status(needle='') always matches immediately — "
                "pass a real substring; use status_line() for the raw current value"
            )
        import time as _time

        deadline = _time.monotonic() + timeout
        last = ""
        while _time.monotonic() < deadline:
            last = self.status_line()
            if needle in last:
                return last
            _time.sleep(poll_interval)
        raise WaitTimeoutError(
            f"status line did not contain {needle!r} within {timeout}s (last value: {last!r})",
            hint=(
                "increase timeout=, or verify the app publishes status via "
                "AccessibleContext.setAccessibleDescription — dolphin reads from "
                "description first, name second"
            ),
        )

    def status_line(self) -> str:
        """Return the current text of the status bar at the bottom of the form.

        Looks for a component named ``StatusLine`` (the dolphin
        convention + the Swing mock) and returns its accessible
        description — which the form's programme is expected to keep
        in sync with the visible status text via
        ``AccessibleContext.setAccessibleDescription``.

        Falls back to role-based lookups (JAB role ``"status bar"``) and,
        as a last resort, any label whose accessible name matches the
        ``Record N of M`` regex.
        """
        try:
            jab = self._locator(name="StatusLine")
            if jab.exists():
                # Prefer description (dynamic text). If empty, try text().
                desc = jab.description() or ""
                if desc:
                    return desc
                text = jab.text() or ""
                if text:
                    return text
        except ElementNotFoundError:
            pass
        try:
            jab = self._locator(role="status bar")
            if jab.exists():
                return jab.text() or jab.description() or ""
        except ElementNotFoundError:
            pass
        try:
            labels = list(self._all_locators(role="label"))
        except ElementNotFoundError:
            labels = []
        # Each label owns a JNI global reference until it is closed, and
        # returning early from the scan abandons every one of them.
        try:
            for jab in labels:
                for reader in (jab.text, jab.description):
                    try:
                        text = reader()
                    except Exception:
                        continue
                    if _RECORD_STATUS_RE.search(text or ""):
                        return text
        finally:
            for jab in labels:
                jab.close()
        return ""

    def title(self) -> str:
        return self.form().title()

    # ---- lifecycle ------------------------------------------------------ #

    def close(self) -> None:
        try:
            self._application.kill()
        except Exception:
            pass

    def __enter__(self) -> OracleFormsApp:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- JAB integration ------------------------------------------------ #

    def _locator(
        self,
        *,
        name: str | None = None,
        role: str | None = None,
        title_re: str | None = None,
    ) -> JABLocator:
        """Build a JABLocator scoped to this app's top-level Java window.

        Args:
            name: Accessible name of the target component (Swing's
                setAccessibleName / setName).
            role: JAB role — ``"push button"``, ``"text"``,
                ``"menu item"``, ``"status bar"``, ``"label"``,
                ``"list"``, ``"frame"``…
            title_re: Regex against the accessible name.

        The JAB API works per-window, not per-process, so we resolve the
        primary top-level window handle once and pass it to every
        subsequent locator. Falls back to the pywinauto top-window if
        no explicit ``title_re`` was configured on the app.
        """
        hwnd = self._primary_hwnd()
        return JABLocator(
            hwnd,
            control_type=role,
            title=name,
            title_re=title_re,
        )

    def bring_to_foreground(self) -> None:
        """Force the Java window to the foreground so keyboard replay works.

        pywinauto's ``send_keys`` uses global SendInput which requires the
        target window to have foreground focus. Java Swing windows on
        Windows tend to lose focus quickly after launch — call this
        before any :meth:`OracleFormsItem.type_text` /
        :meth:`function_key` sequence in headless/CI environments.

        Uses the well-known AttachThreadInput trick to bypass Windows'
        foreground-lock: a background thread cannot call
        SetForegroundWindow unless it is attached to the currently-
        foreground thread's input queue.
        """
        try:
            hwnd = self._primary_hwnd()
        except OracleFormsError as exc:
            # Same hazard as a refused activation below — warn, don't fail.
            _log.warning("could not locate the Forms window to foreground it: %s", exc)
            return
        try:
            import ctypes
            import ctypes.wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            fg_hwnd = user32.GetForegroundWindow()
            fg_pid = ctypes.wintypes.DWORD()
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(fg_pid))
            our_tid = kernel32.GetCurrentThreadId()

            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            attached = False
            if fg_tid and fg_tid != our_tid:
                attached = bool(user32.AttachThreadInput(our_tid, fg_tid, True))
            try:
                user32.BringWindowToTop(hwnd)
                # These report failure by returning 0, not by raising, so the
                # except clause below never saw the case it was written for:
                # under a foreground lock (locked workstation, RDP session,
                # another process owning the foreground) SetForegroundWindow
                # just returns FALSE and the shortcut then goes to whatever
                # window is actually in front.
                ok = bool(user32.SetForegroundWindow(hwnd))
                user32.SetFocus(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(our_tid, fg_tid, False)
            if not ok or user32.GetForegroundWindow() != hwnd:
                _log.warning(
                    "the Forms window did not come to the foreground (hwnd=%s) — "
                    "modifier shortcuts will be delivered to whatever window is "
                    "focused instead",
                    hwnd,
                )
        except Exception as exc:
            # Best-effort, but never silent: every modifier shortcut is
            # delivered by SendInput to the foreground window, so a failure
            # here sends Shift+F5 to whatever window happens to be focused.
            _log.warning("could not bring the Forms window to the foreground: %s", exc)

    def _primary_hwnd(self) -> int:
        """Return the HWND of the main Java Swing window.

        Filters pywinauto's ``windows()`` list to the frames whose class
        is Java-related (SunAwtFrame etc.) and picks the first with the
        matching ``title_re`` supplied at construction, or the first
        overall.
        """
        try:
            wins = self._application._app.windows()
        except Exception:
            wins = []
        # Prefer Java-class windows.
        java_wins = [w for w in wins if "sunawt" in (w.class_name() or "").lower()]
        candidates = java_wins or wins
        if self._title_re is not None:
            import re

            for w in candidates:
                if re.search(self._title_re, w.window_text() or ""):
                    return w.handle
            raise OracleFormsError(
                f"no top-level window matched title_re={self._title_re!r}",
                hint=(
                    "verify the Oracle Forms title selector and wait for the "
                    "client window to finish starting"
                ),
            )
        if candidates:
            return candidates[0].handle
        raise OracleFormsError(
            "no top-level window found for Oracle Forms app",
            hint=(
                "the JVM window may not be surfaced to JAB yet — increase launch "
                "startup_delay=, or check that windowsaccessbridge-64.dll is on PATH"
            ),
        )

    def _all_locators(self, **criteria: Any) -> Iterable[JABLocator]:
        """Iterate over every component matching *criteria*."""
        yield from self._locator(**criteria).all()


# --------------------------------------------------------------------------- #
# Factory helpers used by Desktop                                              #
# --------------------------------------------------------------------------- #


def _launch_oracle_forms(
    desktop: Any,
    *,
    jnlp: str | None,
    jar: str | None,
    main_class: str | None,
    classpath: str | None,
    java_args: list[str] | None,
    title_re: str | None,
    timeout: float,
    startup_delay: float,
) -> OracleFormsApp:
    """Launch a Forms client and return an :class:`OracleFormsApp`.

    Exactly one of *jnlp*, *jar* or *main_class* must be provided.
    """
    provided = sum(1 for x in (jnlp, jar, main_class) if x)
    if provided != 1:
        raise OracleFormsError("exactly one of jnlp=, jar= or main_class= must be supplied")
    JavaAccessBridge.ensure_enabled()

    java = _find_java()
    if jnlp:
        # Java Web Start path — javaws replaces the JVM.
        jws = _find_javaws() or java
        args = [jws, str(jnlp)]
    else:
        args = [
            java,
            "-Djava.accessibility=true",
            "-Doracle.forms.accessible=true",
        ]
        args += list(java_args or [])
        if jar:
            args += ["-jar", jar]
        else:
            if classpath:
                args += ["-cp", classpath]
            args.append(str(main_class))

    cmd = " ".join(_quote(a) for a in args)
    app = desktop.launch_java(cmd, timeout=timeout, startup_delay=startup_delay)
    return OracleFormsApp(app, title_re=title_re)


def _attach_oracle_forms(
    desktop: Any,
    *,
    title: str | None,
    title_re: str | None,
    process: int | None,
    timeout: float,
) -> OracleFormsApp:
    """Attach to an already-running Forms client and return the wrapper."""
    JavaAccessBridge.ensure_enabled()
    criteria = desktop._build_attach_criteria(
        title=title,
        title_re=title_re,
        process=process,
        method_name="attach_oracle_forms",
        error_class=OracleFormsError,
        accepts=("title", "title_re", "process"),
    )
    app = desktop.connect(timeout=timeout, **criteria)
    return OracleFormsApp(app, title_re=title_re)


def _find_java() -> str:
    """Locate the ``java`` executable, preferring the current JAVA_HOME."""
    java_home = JavaAccessBridge.java_home()
    if java_home:
        candidate = os.path.join(java_home, "bin", "java.exe")
        if os.path.isfile(candidate):
            return candidate
    return "java"


def _find_javaws() -> str | None:
    """Locate the Java Web Start binary (present in Oracle JDK 8/11)."""
    java_home = JavaAccessBridge.java_home()
    if java_home:
        candidate = os.path.join(java_home, "bin", "javaws.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


# Virtual-key codes for the Windows PostMessage key-injection path. See
# https://learn.microsoft.com/windows/win32/inputdev/virtual-key-codes.
_VK_MAP = {
    "F1": 0x70,
    "F2": 0x71,
    "F3": 0x72,
    "F4": 0x73,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "TAB": 0x09,
    "ESC": 0x1B,
    "ENTER": 0x0D,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "HOME": 0x24,
    "END": 0x23,
    "PGUP": 0x21,
    "PGDN": 0x22,
    "SHIFT": 0x10,
    "CTRL": 0x11,
    "ALT": 0x12,
}

#: Keys whose posted lParam needs KF_EXTENDED — the grey navigation block.
#: Without the bit AWT resolves each one to its numpad twin, so {UP} scrolls
#: as KP_8 and Forms never sees the record-navigation key it was sent.
_EXTENDED_VKS = frozenset(
    {
        _VK_MAP["UP"],
        _VK_MAP["DOWN"],
        _VK_MAP["LEFT"],
        _VK_MAP["RIGHT"],
        _VK_MAP["HOME"],
        _VK_MAP["END"],
        _VK_MAP["PGUP"],
        _VK_MAP["PGDN"],
    }
)


def _post_key_to_window(hwnd: int, key_shortcut: str) -> bool:
    """Post a WM_KEYDOWN/WM_KEYUP pair to *hwnd*.

    Returns True when the messages were queued; False when the shortcut
    cannot be delivered this way, in which case the caller must fall back
    to global keyboard replay.

    Modifier combos are always declined: PostMessage queues a message
    without touching the target thread's keyboard state, so ``GetKeyState``
    on the AWT event thread still reports the modifier up and Swing
    dispatches ``Shift+F7`` as a plain ``F7`` — a different Forms command.
    Only SendInput against the foreground window sets that state.
    """
    import ctypes

    user32 = ctypes.windll.user32
    parts = key_shortcut.split("+")
    if len(parts) > 1:
        return False
    vk = _VK_MAP.get(parts[-1].strip().upper())
    if vk is None:
        return False
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    # lParam carries a repeat count of 1 and, for the navigation keys, the
    # KF_EXTENDED bit. Posting 0 made AWT read every arrow / Home / End /
    # PgUp / PgDn as its numpad twin.
    extended = 0x01000000 if vk in _EXTENDED_VKS else 0
    lparam_down = 1 | extended
    lparam_up = lparam_down | 0xC0000000
    # PostMessageW signals failure by returning 0 — it does not raise. An
    # unchecked return reported the keystroke as delivered and suppressed the
    # SendInput fallback, so a form running at a different integrity level (or
    # a stale hwnd after the frame was recreated) silently received nothing
    # and the failure surfaced later as a status-line timeout.
    if not user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lparam_down):
        return False
    if not user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam_up):
        # The key-down is already queued, so reporting failure here makes the
        # caller replay the whole keystroke through SendInput — a second press
        # on top of one AWT has half-received. Retry the release, and report
        # success if it lands: at that point the key really was delivered.
        # F10 delivered twice commits the form twice.
        if user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam_up):
            return True
        return False
    return True


def _quote(a: str) -> str:
    if not a:
        return '""'
    if any(c in a for c in ' \t"'):
        return '"' + a.replace('"', '\\"') + '"'
    return a
