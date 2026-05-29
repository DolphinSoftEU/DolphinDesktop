"""JavaAccessBridge — helpers for testing Java Swing/AWT applications."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import time
from typing import Any


class JavaAccessBridge:
    """Utilities for enabling and checking the Java Access Bridge."""

    @staticmethod
    def is_enabled() -> bool:
        """Return True if Java Access Bridge appears to be active."""
        import winreg  # type: ignore[import-untyped]

        # Check the Accessibility ATs registry key for jabswitch
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows NT\CurrentVersion\Accessibility\ATs",
            )
            with key:
                i = 0
                while True:
                    try:
                        name, _value, _type = winreg.EnumValue(key, i)
                        if "jabswitch" in name.lower() or "jab" in name.lower():
                            return True
                        i += 1
                    except OSError:
                        break
        except OSError:
            pass

        # Check the Configuration value (set by jabswitch /enable on modern JDK)
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows NT\CurrentVersion\Accessibility",
            )
            with key:
                try:
                    val, _ = winreg.QueryValueEx(key, "Configuration")
                    low = val.lower()
                    if "javaaccessbridge" in low or "oracle_javaaccessbridge" in low:
                        return True
                except OSError:
                    pass
        except OSError:
            pass

        # Fallback: check for windowsaccessbridge-64.dll in common locations
        java_home = JavaAccessBridge.java_home()
        if java_home:
            for name in ("WindowsAccessBridge-64.dll", "windowsaccessbridge-64.dll"):
                if os.path.isfile(os.path.join(java_home, "bin", name)):
                    return True

        windir = os.environ.get("WINDIR", r"C:\Windows")
        for sub in ("SysWOW64", "System32"):
            for name in ("WindowsAccessBridge-64.dll", "windowsaccessbridge-64.dll"):
                if os.path.isfile(os.path.join(windir, sub, name)):
                    return True

        return False

    @staticmethod
    def enable() -> None:
        """Run jabswitch.exe /enable; raises RuntimeError on failure."""
        java_home = JavaAccessBridge.java_home()
        jabswitch = "jabswitch.exe"
        if java_home:
            candidate = os.path.join(java_home, "bin", "jabswitch.exe")
            if os.path.isfile(candidate):
                jabswitch = candidate
        try:
            result = subprocess.run(
                [jabswitch, "/enable"],
                capture_output=True,
                timeout=15,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "jabswitch.exe not found — ensure a JRE/JDK with Java Access Bridge is "
                "installed and jabswitch.exe is on PATH or JAVA_HOME is set."
            ) from exc
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            raise RuntimeError(f"jabswitch.exe /enable failed (exit {result.returncode}): {stderr}")

    @staticmethod
    def ensure_enabled() -> None:
        """Enable Java Access Bridge only if it is not already enabled."""
        if not JavaAccessBridge.is_enabled():
            JavaAccessBridge.enable()

    @staticmethod
    def java_home() -> str | None:
        """Return the JRE/JDK home directory, or None if not found."""
        env_home = os.environ.get("JAVA_HOME")
        if env_home and os.path.isdir(env_home):
            return env_home

        import winreg  # type: ignore[import-untyped]

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (
                r"SOFTWARE\JavaSoft\Java Runtime Environment",
                r"SOFTWARE\JavaSoft\JRE",
                r"SOFTWARE\JavaSoft\JDK",
            ):
                try:
                    key = winreg.OpenKey(root, sub)
                    with key:
                        try:
                            current, _ = winreg.QueryValueEx(key, "CurrentVersion")
                            ver_key = winreg.OpenKey(key, current)
                            with ver_key:
                                home, _ = winreg.QueryValueEx(ver_key, "JavaHome")
                                if home and os.path.isdir(home):
                                    return home
                        except OSError:
                            pass
                except OSError:
                    pass

        try:
            result = subprocess.run(
                ["where.exe", "java"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                java_exe = result.stdout.decode(errors="replace").splitlines()[0].strip()
                home = os.path.dirname(os.path.dirname(java_exe))
                if os.path.isdir(home):
                    return home
        except Exception:
            pass

        return None


# ---------------------------------------------------------------------------
# JAB ctypes structures
# ---------------------------------------------------------------------------


class _ACI(ctypes.Structure):
    """AccessibleContextInfo — mirrors the C struct from AccessBridgePackages.h."""

    _fields_ = [
        ("name", ctypes.c_wchar * 1024),
        ("description", ctypes.c_wchar * 1024),
        ("role", ctypes.c_wchar * 256),
        ("role_en_US", ctypes.c_wchar * 256),
        ("states", ctypes.c_wchar * 256),
        ("states_en_US", ctypes.c_wchar * 256),
        ("indexInParent", ctypes.c_int),
        ("childrenCount", ctypes.c_int),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("accessibleComponent", ctypes.c_int),  # BOOL = 4 bytes on Windows
        ("accessibleAction", ctypes.c_int),
        ("accessibleSelection", ctypes.c_int),
        ("accessibleText", ctypes.c_int),
        ("accessibleInterfaces", ctypes.c_int),
    ]


class _AVI(ctypes.Structure):
    """AccessibleValueInfo."""

    _fields_ = [
        ("current", ctypes.c_wchar * 256),
        ("minimum", ctypes.c_wchar * 256),
        ("maximum", ctypes.c_wchar * 256),
    ]


# ---------------------------------------------------------------------------
# JAB session singleton
# ---------------------------------------------------------------------------


class _JABSession:
    """Manages the windowsaccessbridge-64.dll client session (singleton)."""

    _instance: _JABSession | None = None

    @classmethod
    def get_or_create(cls) -> _JABSession:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        java_home = JavaAccessBridge.java_home()
        candidates = ["windowsaccessbridge-64.dll"]
        if java_home:
            candidates.append(os.path.join(java_home, "bin", "windowsaccessbridge-64.dll"))

        self._wab: Any = None
        for path in candidates:
            try:
                self._wab = ctypes.WinDLL(path)
                break
            except OSError:
                continue

        if self._wab is None:
            raise RuntimeError(
                "Could not load windowsaccessbridge-64.dll. "
                "Copy it from $JAVA_HOME/bin to C:\\Windows\\System32\\ "
                "or ensure JAVA_HOME is set."
            )

        self._setup_prototypes()
        self._wab.Windows_run()

    def _setup_prototypes(self) -> None:
        w = self._wab
        w.Windows_run.restype = None
        w.Windows_run.argtypes = []

        w.isJavaWindow.restype = ctypes.c_bool
        w.isJavaWindow.argtypes = [ctypes.wintypes.HWND]

        w.getAccessibleContextFromHWND.restype = ctypes.c_bool
        w.getAccessibleContextFromHWND.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_int64),
        ]

        w.getAccessibleContextInfo.restype = ctypes.c_bool
        w.getAccessibleContextInfo.argtypes = [
            ctypes.c_long,
            ctypes.c_int64,
            ctypes.POINTER(_ACI),
        ]

        w.getAccessibleChildFromContext.restype = ctypes.c_int64
        w.getAccessibleChildFromContext.argtypes = [
            ctypes.c_long,
            ctypes.c_int64,
            ctypes.c_int,
        ]

        # AccessibleValue — might not be needed but probe gently
        try:
            w.getAccessibleValueFromContext.restype = ctypes.c_bool
            w.getAccessibleValueFromContext.argtypes = [
                ctypes.c_long,
                ctypes.c_int64,
                ctypes.POINTER(_AVI),
            ]
            self._has_value_api = True
        except AttributeError:
            self._has_value_api = False

    def pump(self, count: int = 50, interval: float = 0.05) -> None:
        msg = ctypes.wintypes.MSG()
        for _ in range(count):
            while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(interval)

    def is_java_window(self, hwnd: int) -> bool:
        return bool(self._wab.isJavaWindow(hwnd))

    def get_root_context(self, hwnd: int) -> tuple[int, int] | None:
        vm_id = ctypes.c_long()
        ac = ctypes.c_int64()
        if self._wab.getAccessibleContextFromHWND(hwnd, ctypes.byref(vm_id), ctypes.byref(ac)):
            return vm_id.value, ac.value
        return None

    def get_info(self, vm_id: int, ac: int) -> _ACI | None:
        if not ac:
            return None
        info = _ACI()
        if self._wab.getAccessibleContextInfo(vm_id, ac, ctypes.byref(info)):
            return info
        return None

    def get_child(self, vm_id: int, ac: int, index: int) -> int:
        return int(self._wab.getAccessibleChildFromContext(vm_id, ac, index))

    def get_value(self, vm_id: int, ac: int) -> str | None:
        if not self._has_value_api:
            return None
        avi = _AVI()
        if self._wab.getAccessibleValueFromContext(vm_id, ac, ctypes.byref(avi)):
            return avi.current
        return None


# ---------------------------------------------------------------------------
# JABLocator
# ---------------------------------------------------------------------------

# Maps dolphin/UIA control types to JAB role_en_US strings (lowercase)
_CT_TO_JAB: dict[str, str] = {
    "button": "push button",
    "edit": "text",
    "combobox": "combo box",
    "list": "list",
    "listitem": "list item",
    "checkbox": "check box",
    "radiobutton": "radio button",
    "text": "label",
    "pane": "panel",
    "window": "frame",
    "menu": "menu",
    "menuitem": "menu item",
}


class JABLocator:
    """Lazy element locator backed directly by the Java Access Bridge API.

    Used automatically by :class:`~dolphin_desktop._window.Window` for Java Swing
    windows (class ``SunAwtFrame``) where UIA does not expose child controls.
    """

    def __init__(
        self,
        hwnd: int,
        *,
        control_type: str | None = None,
        title: str | None = None,
        title_re: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._hwnd = hwnd
        self._control_type = control_type
        self._title = title
        self._title_re = title_re
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session(self) -> _JABSession:
        return _JABSession.get_or_create()

    def _jab_role(self) -> str | None:
        if self._control_type is None:
            return None
        return _CT_TO_JAB.get(self._control_type.lower())

    def _matches(self, info: _ACI) -> bool:
        import re as _re

        jab_role = self._jab_role()
        if jab_role and info.role_en_US.lower() != jab_role:
            return False
        if self._title and info.name != self._title:
            return False
        if self._title_re and not _re.search(self._title_re, info.name):
            return False
        # Require at least one criterion
        return bool(jab_role or self._title or self._title_re)

    def _resolve_context(self) -> tuple[int, int]:
        """Return (vm_id, root_ac), pumping messages until JAB handshake is received."""
        session = self._session()
        deadline = time.monotonic() + self._timeout
        while True:
            ctx = session.get_root_context(self._hwnd)
            if ctx is not None:
                return ctx
            session.pump(10, 0.1)
            if time.monotonic() >= deadline:
                from ._exceptions import ElementNotFoundError

                raise ElementNotFoundError(
                    f"Java window (HWND {self._hwnd}) not accessible via JAB. "
                    "Ensure jabswitch /enable has been run."
                )

    def _search(self, vm_id: int, ac: int, depth: int = 20) -> tuple[int, _ACI] | None:
        """DFS search for a matching element."""
        if not ac or depth < 0:
            return None
        session = self._session()
        info = session.get_info(vm_id, ac)
        if info is None:
            return None
        if self._matches(info):
            return ac, info
        for i in range(info.childrenCount):
            child_ac = session.get_child(vm_id, ac, i)
            result = self._search(vm_id, child_ac, depth - 1)
            if result is not None:
                return result
        return None

    def _find(self) -> tuple[int, int, _ACI] | None:
        """Find the element; returns (vm_id, ac, info) or None."""
        try:
            vm_id, root_ac = self._resolve_context()
            result = self._search(vm_id, root_ac)
            if result is not None:
                ac, info = result
                return vm_id, ac, info
            return None
        except Exception:
            return None

    def _wait_find(self) -> tuple[int, int, _ACI]:
        """Like _find but retries until timeout."""
        deadline = time.monotonic() + self._timeout
        last_exc: Exception | None = None
        while True:
            try:
                vm_id, root_ac = self._resolve_context()
                result = self._search(vm_id, root_ac)
                if result is not None:
                    ac, info = result
                    return vm_id, ac, info
            except Exception as exc:
                last_exc = exc
            if time.monotonic() >= deadline:
                from ._exceptions import ElementNotFoundError

                raise ElementNotFoundError(
                    f"Java element not found after {self._timeout}s: "
                    f"control_type={self._control_type!r} title={self._title!r}"
                ) from last_exc
            time.sleep(0.3)

    @staticmethod
    def _center(info: _ACI) -> tuple[int, int]:
        return (info.x + info.width // 2, info.y + info.height // 2)

    # ------------------------------------------------------------------
    # Internal: window focus
    # ------------------------------------------------------------------

    def _bring_to_front(self) -> None:
        """Bring the Java window to the foreground before keyboard/mouse operations."""
        try:
            import win32gui  # type: ignore[import-untyped]

            win32gui.SetForegroundWindow(self._hwnd)
            time.sleep(0.05)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        return self._find() is not None

    def is_visible(self) -> bool:
        result = self._find()
        if result is None:
            return False
        _, _, info = result
        return "visible" in info.states_en_US.lower()

    def is_enabled(self) -> bool:
        result = self._find()
        if result is None:
            return False
        _, _, info = result
        return "enabled" in info.states_en_US.lower()

    def is_checked(self) -> bool:
        result = self._find()
        if result is None:
            return False
        _, _, info = result
        return "checked" in info.states_en_US.lower()

    def bounding_box(self) -> dict[str, int]:
        _, _, info = self._wait_find()
        return {
            "left": info.x,
            "top": info.y,
            "right": info.x + info.width,
            "bottom": info.y + info.height,
            "width": info.width,
            "height": info.height,
        }

    def text(self) -> str:
        from pywinauto import keyboard, mouse  # type: ignore[import-untyped]

        from ._clipboard import Clipboard

        _, _, info = self._wait_find()
        self._bring_to_front()
        mouse.click(coords=self._center(info))
        time.sleep(0.1)
        Clipboard.clear()
        keyboard.send_keys("^a^c", pause=0.05)
        time.sleep(0.15)
        return Clipboard.get_text() or ""

    def value(self) -> str:
        return self.text()

    def get_attribute(self, name: str) -> str | None:
        result = self._find()
        if result is None:
            return None
        _, _, info = result
        return {
            "name": info.name,
            "description": info.description,
            "role": info.role,
            "role_en_US": info.role_en_US,
            "states": info.states,
            "states_en_US": info.states_en_US,
        }.get(name)

    # ------------------------------------------------------------------
    # Action methods
    # ------------------------------------------------------------------

    def click(self) -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        _, _, info = self._wait_find()
        self._bring_to_front()
        mouse.click(coords=self._center(info))
        return self

    def double_click(self) -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        _, _, info = self._wait_find()
        mouse.double_click(coords=self._center(info))
        return self

    def right_click(self) -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        _, _, info = self._wait_find()
        mouse.right_click(coords=self._center(info))
        return self

    def hover(self) -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        _, _, info = self._wait_find()
        mouse.move(coords=self._center(info))
        return self

    def focus(self) -> JABLocator:
        return self.click()

    def type_text(self, text: str, pause: float = 0.05) -> JABLocator:
        from pywinauto import keyboard  # type: ignore[import-untyped]

        keyboard.send_keys(text, pause=pause, with_spaces=True)
        return self

    def set_text(self, text: str) -> JABLocator:
        from pywinauto import keyboard, mouse  # type: ignore[import-untyped]

        _, _, info = self._wait_find()
        self._bring_to_front()
        mouse.click(coords=self._center(info))
        time.sleep(0.1)
        keyboard.send_keys("^a", pause=0.05)
        if text:
            keyboard.send_keys(text, pause=0.05, with_spaces=True)
        else:
            keyboard.send_keys("{DELETE}", pause=0.05)
        return self

    def clear(self) -> JABLocator:
        from pywinauto import keyboard, mouse  # type: ignore[import-untyped]

        _, _, info = self._wait_find()
        self._bring_to_front()
        mouse.click(coords=self._center(info))
        time.sleep(0.1)
        keyboard.send_keys("^a{DELETE}", pause=0.05)
        return self

    def press_key(self, key: str) -> JABLocator:
        from pywinauto import keyboard  # type: ignore[import-untyped]

        keyboard.send_keys(key)
        return self

    def select_item(self, item: int | str) -> JABLocator:
        """Select a ComboBox item by index or text."""
        vm_id, ac, _info = self._wait_find()

        if isinstance(item, int):
            index: int = item
        else:
            idx = self._combo_text_index(vm_id, ac, item)
            if idx is None:
                raise ValueError(f"ComboBox item {item!r} not found")
            index = idx

        session = self._session()
        wab = session._wab
        # addAccessibleSelectionFromContext calls JComboBox.setSelectedIndex(i)
        # which fires ActionEvent → updates all listeners
        if not hasattr(wab, "_add_sel_setup"):
            wab.addAccessibleSelectionFromContext.argtypes = [
                ctypes.c_long,
                ctypes.c_int64,
                ctypes.c_int,
            ]
            wab.addAccessibleSelectionFromContext.restype = None
            wab._add_sel_setup = True
        wab.addAccessibleSelectionFromContext(vm_id, ac, index)
        time.sleep(0.1)
        return self

    def _combo_text_index(self, vm_id: int, ac: int, text: str) -> int | None:
        """Return the index of *text* in a ComboBox's item list."""
        session = self._session()

        def find_list(vm_id: int, ac: int, depth: int = 0) -> int | None:
            if depth > 8:
                return None
            info = session.get_info(vm_id, ac)
            if info is None:
                return None
            if info.role_en_US == "list":
                for i in range(info.childrenCount):
                    item_ac = session.get_child(vm_id, ac, i)
                    if not item_ac:
                        continue
                    item_info = session.get_info(vm_id, item_ac)
                    if item_info and item_info.name == text:
                        return i
                return None
            for i in range(min(info.childrenCount, 20)):
                child_ac = session.get_child(vm_id, ac, i)
                if child_ac:
                    result = find_list(vm_id, child_ac, depth + 1)
                    if result is not None:
                        return result
            return None

        return find_list(vm_id, ac)

    def check(self) -> JABLocator:
        if not self.is_checked():
            self.click()
        return self

    def uncheck(self) -> JABLocator:
        if self.is_checked():
            self.click()
        return self

    def scroll(self, direction: str, amount: int = 3) -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        _, _, info = self._wait_find()
        cx, cy = self._center(info)
        wheel = amount if direction in ("up", "right") else -amount
        mouse.scroll(coords=(cx, cy), wheel_dist=wheel)
        return self

    def drag_to(self, target: Any, *, duration: float = 0.5, button: str = "left") -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        _, _, src_info = self._wait_find()
        src = self._center(src_info)
        if hasattr(target, "_wait_find"):
            _, _, dst_info = target._wait_find()
            dst = self._center(dst_info)
        else:
            dst = target
        mouse.press(coords=src, button=button)
        time.sleep(duration)
        mouse.release(coords=dst, button=button)
        return self

    def select_text(self) -> JABLocator:
        from pywinauto import keyboard, mouse  # type: ignore[import-untyped]

        _, _, info = self._wait_find()
        mouse.click(coords=self._center(info))
        keyboard.send_keys("^a")
        return self

    def scroll_into_view(self) -> JABLocator:
        return self

    def screenshot(self) -> Any:
        from PIL import ImageGrab  # type: ignore[import-untyped]

        _, _, info = self._wait_find()
        return ImageGrab.grab(bbox=(info.x, info.y, info.x + info.width, info.y + info.height))

    # ------------------------------------------------------------------
    # Waiting
    # ------------------------------------------------------------------

    def wait_for(self, state: str, timeout: float | None = None) -> JABLocator:
        t = timeout if timeout is not None else self._timeout
        deadline = time.monotonic() + t
        while True:
            if state in ("visible", "exists") and self.exists():
                return self
            if state == "enabled" and self.is_enabled():
                return self
            if state in ("hidden", "invisible") and not self.is_visible():
                return self
            if time.monotonic() >= deadline:
                from ._exceptions import WaitTimeoutError

                raise WaitTimeoutError(f"Timeout waiting for Java element state {state!r}")
            time.sleep(0.3)

    def wait_until_hidden(self, timeout: float | None = None) -> JABLocator:
        return self.wait_for("hidden", timeout)

    def wait_until_enabled(self, timeout: float | None = None) -> JABLocator:
        return self.wait_for("enabled", timeout)

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------

    def count(self) -> int:
        return len(self.all())

    def all(self, depth: int | None = None) -> list[JABLocator]:
        results: list[JABLocator] = []
        try:
            vm_id, root_ac = self._resolve_context()
            self._collect(vm_id, root_ac, results, 20 if depth is None else depth)
        except Exception:
            pass
        return results

    def _collect(self, vm_id: int, ac: int, results: list, depth: int) -> None:
        if not ac or depth < 0:
            return
        session = self._session()
        info = session.get_info(vm_id, ac)
        if info is None:
            return
        if self._matches(info):
            results.append(_JABResolvedLocator(self._hwnd, vm_id, ac, info, self._timeout))
        for i in range(info.childrenCount):
            child_ac = session.get_child(vm_id, ac, i)
            self._collect(vm_id, child_ac, results, depth - 1)

    def nth(self, index: int) -> JABLocator:
        items = self.all()
        if index < 0 or index >= len(items):
            from ._exceptions import ElementNotFoundError

            raise ElementNotFoundError(f"No Java element at index {index}")
        return items[index]

    # ------------------------------------------------------------------
    # Chaining
    # ------------------------------------------------------------------

    def timeout(self, secs: float) -> JABLocator:
        return JABLocator(
            self._hwnd,
            control_type=self._control_type,
            title=self._title,
            title_re=self._title_re,
            timeout=secs,
        )

    def locator(self, **criteria: Any) -> JABLocator:
        return JABLocator(
            self._hwnd,
            control_type=criteria.get("control_type", self._control_type),
            title=criteria.get("title", self._title),
            title_re=criteria.get("title_re", self._title_re),
            timeout=self._timeout,
        )

    def __repr__(self) -> str:
        return (
            f"JABLocator(hwnd={self._hwnd}, "
            f"control_type={self._control_type!r}, title={self._title!r})"
        )


class _JABResolvedLocator(JABLocator):
    """Already-found JABLocator; skips tree search."""

    def __init__(self, hwnd: int, vm_id: int, ac: int, info: _ACI, timeout: float) -> None:
        super().__init__(hwnd, timeout=timeout)
        self._vm_id = vm_id
        self._ac = ac
        self._info = info

    def _find(self) -> tuple[int, int, _ACI] | None:
        return self._vm_id, self._ac, self._info

    def _wait_find(self) -> tuple[int, int, _ACI]:
        return self._vm_id, self._ac, self._info

    def timeout(self, secs: float) -> _JABResolvedLocator:
        return _JABResolvedLocator(self._hwnd, self._vm_id, self._ac, self._info, secs)
