"""Read effective monitor scaling; configure only disposable GitHub CI displays.

Windows exposes no documented setter for per-monitor DPI scaling. The isolated
CI setter uses the Settings device-info packets (-3/-4), verifies the effective
scale through GetScaleFactorForMonitor, and restores the original setting.
These packets are intentionally not part of the Dolphin runtime API.
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

_SCALES = (100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500)


class _Luid(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_int32)]


class _Adapter(ctypes.Structure):
    _fields_ = [
        ("device", ctypes.c_wchar * 32),
        ("handle", ctypes.c_uint32),
        ("luid", _Luid),
        ("source", ctypes.c_uint32),
    ]


class _Header(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_int32),
        ("size", ctypes.c_uint32),
        ("luid", _Luid),
        ("source", ctypes.c_uint32),
    ]


class _GetScale(ctypes.Structure):
    _fields_ = [
        ("header", _Header),
        ("minimum", ctypes.c_int32),
        ("current", ctypes.c_int32),
        ("maximum", ctypes.c_int32),
    ]


class _SetScale(ctypes.Structure):
    _fields_ = [("header", _Header), ("relative", ctypes.c_int32)]


def relative_scale(percent: int, minimum: int, maximum: int) -> int:
    """Translate an absolute percentage to the driver's recommended-scale offset."""
    relative = _SCALES.index(percent) + minimum
    if not minimum <= relative <= maximum:
        raise RuntimeError(f"Display cannot provide {percent}% scaling")
    return relative


class DisplayScale:
    """Native scale queries for the primary display or the monitor of an HWND."""

    def __init__(self, hwnd: int | None = None) -> None:
        import win32api

        self.monitor = int(
            win32api.MonitorFromWindow(hwnd, 1)
            if hwnd is not None
            else win32api.MonitorFromPoint((0, 0), 1)
        )
        self.device = win32api.GetMonitorInfo(self.monitor)["Device"]
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._scale = ctypes.WinDLL("shcore").GetScaleFactorForMonitor
        self._scale.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)]
        self._scale.restype = ctypes.c_long
        for name, packet in (
            ("DisplayConfigGetDeviceInfo", _GetScale),
            ("DisplayConfigSetDeviceInfo", _SetScale),
        ):
            function = getattr(self._user32, name)
            function.argtypes = [ctypes.POINTER(packet)]
            function.restype = ctypes.c_long
        self._gdi32.D3DKMTOpenAdapterFromGdiDisplayName.argtypes = [ctypes.POINTER(_Adapter)]
        self._gdi32.D3DKMTOpenAdapterFromGdiDisplayName.restype = ctypes.c_long
        self._gdi32.D3DKMTCloseAdapter.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
        self._gdi32.D3DKMTCloseAdapter.restype = ctypes.c_long

    def read(self) -> int:
        """Return actual monitor scaling, independent of the querying process DPI mode."""
        percent = ctypes.c_int32()
        status = self._scale(self.monitor, ctypes.byref(percent))
        if status != 0:
            raise OSError(f"GetScaleFactorForMonitor failed: {status:#x}")
        return percent.value

    def setting(self) -> _GetScale:
        """Query the driver's current scaling offset and supported range."""
        adapter = _Adapter(device=self.device)
        status = self._gdi32.D3DKMTOpenAdapterFromGdiDisplayName(ctypes.byref(adapter))
        if status != 0:
            raise OSError(f"D3DKMTOpenAdapterFromGdiDisplayName failed: {status:#x}")
        try:
            packet = _GetScale(_Header(-3, ctypes.sizeof(_GetScale), adapter.luid, adapter.source))
            status = self._user32.DisplayConfigGetDeviceInfo(ctypes.byref(packet))
            if status != 0:
                raise OSError(f"DisplayConfigGetDeviceInfo(DPI) failed: {status}")
            return packet
        finally:
            handle = ctypes.c_uint32(adapter.handle)
            status = self._gdi32.D3DKMTCloseAdapter(ctypes.byref(handle))
            if status != 0:
                raise OSError(f"D3DKMTCloseAdapter failed: {status:#x}")

    def apply(self, setting: _GetScale, relative: int) -> None:
        """Change scale on a disposable hosted runner; caller restores the old offset."""
        if (
            os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        ):
            raise RuntimeError("Display changes are restricted to disposable GitHub-hosted runners")
        packet = _SetScale(
            _Header(-4, ctypes.sizeof(_SetScale), setting.header.luid, setting.header.source),
            relative,
        )
        status = self._user32.DisplayConfigSetDeviceInfo(ctypes.byref(packet))
        if status != 0:
            raise OSError(f"DisplayConfigSetDeviceInfo(DPI) failed: {status}")

    def wait(self, expected: int, timeout: float = 10.0) -> int:
        """Wait for the effective monitor scale to confirm a configuration change."""
        deadline = time.monotonic() + timeout
        while (actual := self.read()) != expected:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Required {expected}% monitor scaling, actual {actual}%")
            time.sleep(0.1)
        return actual


@contextmanager
def scaled_display(percent: int, evidence: dict):
    """Ensure and verify the required scale, restoring a changed CI display on exit."""
    display = DisplayScale()
    original = display.read()
    evidence.update(device=display.device, before_percent=original, requested_percent=percent)
    setting = None
    try:
        if original != percent:
            if (
                os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            ):
                raise RuntimeError(
                    "Display changes are restricted to disposable GitHub-hosted runners"
                )
            setting = display.setting()
            target = relative_scale(percent, setting.minimum, setting.maximum)
            display.apply(setting, target)
        evidence["effective_percent"] = display.wait(percent)
        yield
    finally:
        if setting is not None:
            display.apply(setting, setting.current)
        evidence["restored_percent"] = display.wait(original)


def record_window_scale(hwnd: int, output: Path) -> None:
    """Record the AUT's actual monitor scale and enforce the CI requirement, if set."""
    display = DisplayScale(hwnd)
    expected = os.environ.get("DOLPHIN_EXPECT_DISPLAY_SCALE")
    evidence = {"hwnd": hwnd, "device": display.device, "actual_percent": display.read()}
    output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    if expected is not None and evidence["actual_percent"] != int(expected):
        raise AssertionError(f"Required {expected}% on AUT monitor, observed {evidence}")
