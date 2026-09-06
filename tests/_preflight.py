"""Preflight checks for jobs that require external processes or services.

These checks live outside stack-specific ``conftest.py`` files because pytest
loads nested conftests during collection.  A session hook in this module is
called from the root conftest, before collection starts, so a configured job
cannot become green by collecting only skipped tests.
"""

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


def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    from dolphin_desktop import tcp_reachable

    return tcp_reachable(host, port, timeout=timeout)


def _find_pid_by_image_name(name: str) -> int | None:
    from dolphin_desktop import find_pid_by_image_name

    return find_pid_by_image_name(name)


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


def _preflight_pub400() -> None:
    _require_ws3270(
        "external mainframe job requires wc3270/ws3270.exe; "
        "install it and set DOLPHIN_WS3270_PATH before starting pytest"
    )
    if not _tcp_reachable("pub400.com", 23, timeout=3):
        raise RuntimeError("external mainframe job cannot reach pub400.com:23")


def _preflight_sap() -> None:
    missing: list[str] = []
    if not (_env_var("DOLPHIN_SAP_CONNECTION") or "").strip():
        missing.append("DOLPHIN_SAP_CONNECTION")
    if (
        not (_env_var("DOLPHIN_SAP_USER") or "").strip()
        or not (_env_var("DOLPHIN_SAP_PASSWORD") or "").strip()
    ):
        missing.append("DOLPHIN_SAP_USER/DOLPHIN_SAP_PASSWORD")
    if missing:
        raise RuntimeError("external SAP job preflight failed; missing: " + ", ".join(missing))


def _preflight_steam() -> None:
    from tests.steam._steam_env import STEAM, has_playwright, steam_cdp_up

    missing: list[str] = []
    if not has_playwright():
        missing.append("dolphin-desktop[cdp]")
    if STEAM is None:
        missing.append("Steam")
    if not steam_cdp_up() and (_env_var("DOLPHIN_STEAM_ALLOW_LAUNCH") or "").strip() != "1":
        missing.append("Steam CEF debug endpoint (8080) or DOLPHIN_STEAM_ALLOW_LAUNCH=1")
    if missing:
        raise RuntimeError("external Steam job preflight failed; missing: " + ", ".join(missing))


def _preflight_qt_real_apps() -> None:
    candidates = (
        "RadeonSoftware.exe",
        "AMD Ryzen Master.exe",
        "lghub.exe",
        "lghub_agent.exe",
    )
    if not _is_windows() or not any(_find_pid_by_image_name(name) for name in candidates):
        raise RuntimeError(
            "Qt real-app job requires at least one configured target process "
            "(RadeonSoftware, AMD Ryzen Master, or Logitech G HUB)"
        )


def run_preflight() -> None:
    """Run checks selected by the CI job environment."""
    checks = []
    if _enabled("DOLPHIN_COMPONENT_PREFLIGHT"):
        checks.append(_preflight_component)
    if _enabled("DOLPHIN_PUB400_PREFLIGHT"):
        checks.append(_preflight_pub400)
    if _enabled("DOLPHIN_SAP_PREFLIGHT"):
        checks.append(_preflight_sap)
    if _enabled("DOLPHIN_STEAM_PREFLIGHT"):
        checks.append(_preflight_steam)
    if _enabled("DOLPHIN_QT_ENVIRONMENT_JOB"):
        checks.append(_preflight_qt_real_apps)

    for check in checks:
        try:
            check()
        except RuntimeError as exc:
            import pytest

            raise pytest.UsageError(str(exc)) from exc
