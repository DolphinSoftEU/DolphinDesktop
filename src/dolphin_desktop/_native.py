"""Loading native libraries from trusted, absolute locations only.

``ctypes.WinDLL("name.dll")`` hands a bare name to the Windows loader, whose
search order includes directories an attacker may control (the application
directory, on older policies the current directory and ``PATH``). A planted
``windowsaccessbridge-64.dll`` or ``EHLAPI32.DLL`` would then execute inside
the test runner. Every native library dolphin loads therefore goes through
:func:`load_trusted_dll`, which:

* accepts only an absolute path to an existing file, and
* loads it with ``LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32``
  so the library's *own* dependencies resolve from its directory and
  ``System32`` — never from the working directory or ``PATH``.

The resolved path is logged so a diagnostic log shows exactly which file was
executed.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Iterable
from typing import Any

from ._logging import get_logger

_LOG = get_logger("native")

#: ``LoadLibraryExW`` flags — see ``libloaderapi.h``.
LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800

#: The search mode every dolphin DLL load uses.
TRUSTED_LOAD_FLAGS = LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32


class NativeLibraryError(OSError):
    """Raised when a native library cannot be loaded from a trusted location."""


def system32_dir() -> str:
    """Return the real ``System32`` directory (``%SystemRoot%\\System32``)."""
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    return os.path.join(root, "System32")


def program_files_dirs() -> list[str]:
    """Return the distinct Program Files roots known to this process."""
    roots: list[str] = []
    for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(var)
        if value and value not in roots:
            roots.append(value)
    return roots


def existing_candidates(directories: Iterable[str], names: Iterable[str]) -> list[str]:
    """Return ``dir\\name`` for every combination that exists on disk, in order."""
    names = list(names)
    found: list[str] = []
    for directory in directories:
        if not directory:
            continue
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                found.append(path)
    return found


def load_trusted_dll(path: str, *, what: str = "native library") -> Any:
    """Load *path* with a restricted search order and return the ``WinDLL``.

    Raises:
        NativeLibraryError: if *path* is not absolute, does not exist, or the
            loader refuses it. The message names the path that was tried so
            the failure is unambiguous.
    """
    if not isinstance(path, str) or not path:
        raise NativeLibraryError(f"{what}: no path given")
    if not os.path.isabs(path):
        raise NativeLibraryError(
            f"{what}: refusing to load {path!r} by name — only an absolute path to a "
            "trusted location is accepted (the DLL search order would otherwise "
            "include directories a local attacker can write to)"
        )
    if not os.path.isfile(path):
        raise NativeLibraryError(f"{what}: {path} does not exist")
    try:
        dll = ctypes.WinDLL(path, winmode=TRUSTED_LOAD_FLAGS)  # type: ignore[attr-defined]
    except OSError as exc:
        raise NativeLibraryError(f"{what}: LoadLibraryEx({path}) failed: {exc}") from exc
    _LOG.info("%s loaded from verified path %s", what, path)
    return dll
