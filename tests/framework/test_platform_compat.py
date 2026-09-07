"""Unit tests for deferred Windows-only compatibility objects."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from types import ModuleType

import pytest

import dolphin_desktop
from dolphin_desktop._platform_compat import (
    _unavailable_class,
    _UnavailableObject,
    _unsupported,
    _unsupported_callable,
)


def _exec_module_as_non_windows(module_name: str, monkeypatch) -> dict[str, object]:
    package_dir = Path(dolphin_desktop.__file__).parent
    source_path = package_dir / f"{module_name}.py"
    module = ModuleType(f"dolphin_desktop._compat_test_{module_name}")
    module.__package__ = "dolphin_desktop"
    module.__file__ = str(source_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    source = compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")
    exec(source, module.__dict__)
    return module.__dict__


def test_unsupported_reports_the_platform_requirement() -> None:
    with pytest.raises(RuntimeError, match="demo feature is only available on Windows"):
        _unsupported("demo feature", 1, enabled=True)


def test_unsupported_callable_defers_failure_until_invocation() -> None:
    fail = _unsupported_callable("demo callable")

    with pytest.raises(RuntimeError, match="demo callable is only available on Windows"):
        fail("argument", enabled=True)


def test_unavailable_object_defers_failure_until_attribute_invocation() -> None:
    proxy = _UnavailableObject("demo object")

    with pytest.raises(RuntimeError, match=r"demo object\.open is only available on Windows"):
        proxy.open("file.txt")


def test_unavailable_class_defers_failure_until_instantiation() -> None:
    unavailable = _unavailable_class("Demo", "demo class")

    assert unavailable.__name__ == "Demo"
    with pytest.raises(RuntimeError, match="demo class is only available on Windows"):
        unavailable("argument", enabled=True)


def test_windows_only_modules_load_with_deferred_non_windows_apis(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delattr(ctypes, "WINFUNCTYPE", raising=False)

    application = _exec_module_as_non_windows("_application", monkeypatch)
    desktop = _exec_module_as_non_windows("_desktop", monkeypatch)
    dialogs = _exec_module_as_non_windows("_dialogs", monkeypatch)
    keyboard = _exec_module_as_non_windows("_keyboard", monkeypatch)
    locator = _exec_module_as_non_windows("_locator", monkeypatch)
    mouse = _exec_module_as_non_windows("_mouse", monkeypatch)
    qt_inject = _exec_module_as_non_windows("_qt_inject", monkeypatch)
    recorder = _exec_module_as_non_windows("_recorder", monkeypatch)

    with pytest.raises(RuntimeError, match=r"pywinauto\.Application"):
        application["_PyWinApp"]()
    with pytest.raises(RuntimeError, match=r"pywinauto\.findwindows\.find_elements"):
        application["_find_elements"]()
    with pytest.raises(RuntimeError, match=r"pywinauto\.Application"):
        desktop["_PyWinApp"]()
    with pytest.raises(RuntimeError, match=r"pywinauto\.Desktop"):
        dialogs["_PWDesktop"]()

    assert keyboard["_CODES"] == {}
    with pytest.raises(RuntimeError, match=r"pywinauto\.keyboard\.send_keys"):
        keyboard["_send_keys"]("{ENTER}")
    with pytest.raises(RuntimeError, match=r"pywinauto\.keyboard\.send_keys"):
        locator["_send_keys"]("{ENTER}")
    with pytest.raises(RuntimeError, match=r"pywinauto\.mouse\.click"):
        mouse["_mouse"].click()
    with pytest.raises(RuntimeError, match=r"Qt agent injection\.OpenProcess"):
        qt_inject["_kernel32"].OpenProcess()
    with pytest.raises(RuntimeError, match=r"recorder\.SetWindowsHookExW"):
        recorder["_user32"].SetWindowsHookExW()
