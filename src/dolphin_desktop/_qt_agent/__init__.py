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

from pathlib import Path

_HERE = Path(__file__).resolve().parent

QT5_AGENT_DLL = _HERE / "dolphin_qt5_agent.dll"
QT6_AGENT_DLL = _HERE / "dolphin_qt6_agent.dll"


def agent_dll_for(qt_version: str) -> Path:
    """Return path to the agent DLL for ``qt_version`` (``"5"`` or ``"6"``).

    Raises FileNotFoundError if the DLL is missing (wheel build issue or
    user installed an unpatched source tree).
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
    return path
