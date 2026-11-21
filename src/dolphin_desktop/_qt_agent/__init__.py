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

#: SHA-256 and size for each bundled agent DLL. The native build scripts add
#: source/build metadata when they regenerate a DLL from a clean source commit.
AGENT_MANIFEST = _HERE / "agent_manifest.json"


class AgentIntegrityError(RuntimeError):
    """Raised when a bundled agent DLL has no valid integrity record."""


def _load_manifest() -> dict[str, dict[str, object]]:
    try:
        manifest = json.loads(AGENT_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AgentIntegrityError(
            f"Qt agent manifest is missing or invalid: {AGENT_MANIFEST}; refusing to "
            "load an agent DLL without an integrity record"
        ) from exc
    if not isinstance(manifest, dict):
        raise AgentIntegrityError(
            f"Qt agent manifest has an invalid top-level value: {AGENT_MANIFEST}"
        )
    return manifest


def dll_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of the file at *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_agent_dll(path: Path) -> None:
    """Raise :class:`AgentIntegrityError` unless *path* is an exact manifest entry."""
    manifest = _load_manifest()
    entry = manifest.get(path.name)
    if not isinstance(entry, dict):
        raise AgentIntegrityError(
            f"agent DLL {path.name} has no integrity record in {AGENT_MANIFEST}; "
            "refusing to inject an untracked native binary"
        )
    expected = entry.get("sha256")
    expected_size = entry.get("size")
    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(ch not in "0123456789abcdef" for ch in expected)
        or not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or expected_size <= 0
    ):
        raise AgentIntegrityError(
            f"agent DLL {path.name} has an incomplete integrity record in "
            f"{AGENT_MANIFEST}; refusing to inject it"
        )
    try:
        actual_size = path.stat().st_size
        actual = dll_sha256(path)
    except OSError as exc:
        raise AgentIntegrityError(f"cannot read agent DLL {path}: refusing to inject it") from exc
    if actual_size != expected_size or actual != expected:
        raise AgentIntegrityError(
            f"agent DLL {path.name} does not match its recorded size/SHA-256 "
            f"(expected {expected_size} bytes/{expected}, found "
            f"{actual_size} bytes/{actual}). The bundled binary was modified "
            "or replaced — refusing to inject it into a target process. Reinstall "
            "dolphin-desktop from a trusted wheel."
        )


def agent_dll_for(qt_version: str, *, verify: bool = True) -> Path:
    """Return path to the agent DLL for ``qt_version`` (``"5"`` or ``"6"``).

    The file's size and SHA-256 are checked against :data:`AGENT_MANIFEST`
    before the path is returned (pass ``verify=False`` to skip, e.g. in a test that
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
            "Rebuild it from src_cpp/qt_agent using the matching build_qt5.ps1 "
            "or build_qt6.ps1 script, or reinstall dolphin-desktop from a wheel."
        )
    if verify:
        verify_agent_dll(path)
    return path
