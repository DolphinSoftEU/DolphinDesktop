"""Preflight for configured local mainframe component jobs."""

from __future__ import annotations

import os


def _env_var(name: str) -> str | None:
    return os.environ.get(name)


def _is_windows() -> bool:
    from dolphin_desktop import is_windows

    return is_windows()


def _path_exists(path: str) -> bool:
    from dolphin_desktop import path_exists

    return path_exists(path)


def _enabled(name: str) -> bool:
    return (_env_var(name) or "").strip() == "1"


def _find_ws3270() -> str | None:
    explicit = _env_var("DOLPHIN_WS3270_PATH")
    if explicit and _path_exists(explicit):
        return explicit

    local_appdata = _env_var("LOCALAPPDATA") or ""
    candidates = (
        rf"{local_appdata}\wc3270\ws3270.exe",
        rf"{local_appdata}\Programs\wc3270\ws3270.exe",
        r"C:\Program Files\wc3270\ws3270.exe",
        r"C:\Program Files (x86)\wc3270\ws3270.exe",
    )
    return next((candidate for candidate in candidates if _path_exists(candidate)), None)


def _require_ws3270(message: str) -> None:
    if not _is_windows() or _find_ws3270() is None:
        raise RuntimeError(message)


def _preflight_component() -> None:
    _require_ws3270(
        "mainframe component job requires wc3270/ws3270.exe; "
        "install it and set DOLPHIN_WS3270_PATH before starting pytest"
    )


def run_preflight() -> None:
    if _enabled("DOLPHIN_COMPONENT_PREFLIGHT"):
        import pytest

        try:
            _preflight_component()
        except RuntimeError as exc:
            raise pytest.UsageError(str(exc)) from exc
