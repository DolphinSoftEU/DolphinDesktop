"""Bundled Qt agent DLLs.

This package contains pre-built ``dolphin_qt5_agent.dll`` and
``dolphin_qt6_agent.dll`` — small native libraries injected into a target
Qt 5 / Qt 6 process by :mod:`dolphin_desktop._qt_inject` to expose the
``QObject`` tree, QML scene graph, and ``QGraphicsView`` items over a
named-pipe JSON protocol.

Both bundled builds are **x86-64 only**, so injection is supported only
into 64-bit Qt processes from a 64-bit Python.

End users normally never import from this package directly; access goes
through :class:`dolphin_desktop.Application` (``app.qt_agent``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent

QT5_AGENT_DLL = _HERE / "dolphin_qt5_agent.dll"
QT6_AGENT_DLL = _HERE / "dolphin_qt6_agent.dll"

#: SHA-256 + size of each bundled agent DLL, recorded when the binary was
#: committed. The C++ source is not in this repository, so the manifest is the
#: provenance anchor: it lets a build pin ``hash → approved artifact`` and lets
#: :func:`agent_dll_for` refuse a DLL that was swapped on disk before injection.
AGENT_MANIFEST = _HERE / "agent_manifest.json"


class AgentIntegrityError(RuntimeError):
    """Raised when a bundled agent DLL does not match its recorded hash."""


def _load_manifest() -> dict[str, dict[str, object]]:
    try:
        return json.loads(AGENT_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def dll_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of the file at *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_agent_dll(path: Path) -> None:
    """Raise :class:`AgentIntegrityError` if *path* is not the recorded binary.

    A DLL whose name is not in the manifest (a future addition, a rename) is
    left unverified rather than rejected — the manifest names what we shipped,
    not an allowlist of every possible file. When the manifest itself cannot
    be read, verification is skipped: it is a hardening check, not a gate that
    should break a working install if the JSON is absent.
    """
    manifest = _load_manifest()
    entry = manifest.get(path.name)
    if entry is None:
        return
    actual = dll_sha256(path)
    expected = str(entry.get("sha256", ""))
    if expected and actual != expected:
        raise AgentIntegrityError(
            f"agent DLL {path.name} does not match its recorded SHA-256 "
            f"(expected {expected}, found {actual}). The bundled binary was modified "
            "or replaced — refusing to inject it into a target process. Reinstall "
            "dolphin-desktop from a trusted wheel."
        )


def agent_dll_for(qt_version: str, *, verify: bool = True) -> Path:
    """Return path to the agent DLL for ``qt_version`` (``"5"`` or ``"6"``).

    The file's SHA-256 is checked against :data:`AGENT_MANIFEST` before the
    path is returned (pass ``verify=False`` to skip, e.g. in a test that
    substitutes a stub DLL).

    Raises:
        FileNotFoundError: the DLL is missing (wheel build issue or an
            unpatched source tree).
        AgentIntegrityError: the DLL is present but its hash does not match.
    """
    if qt_version == "5":
        path = QT5_AGENT_DLL
    elif qt_version == "6":
        path = QT6_AGENT_DLL
    else:
        raise ValueError(f"unknown Qt version: {qt_version!r}")
    if not path.is_file():
        raise FileNotFoundError(
            f"Qt {qt_version} agent DLL missing: {path}. "
            "Reinstall dolphin-desktop from a wheel — the agent DLLs are "
            "bundled binaries and are not built from this source tree."
        )
    if verify:
        verify_agent_dll(path)
    return path
