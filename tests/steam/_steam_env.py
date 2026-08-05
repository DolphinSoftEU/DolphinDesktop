"""Environment probes for the Steam CEF+CDP test suite.

Steam is a CEF (Chromium Embedded Framework) host, not Electron — the
launch flag is ``-cef-enable-debugging`` (single dash, Steam's own arg
parser) and the CDP port is hardcoded to ``8080``. Once the port is up
the protocol is standard CDP, so :class:`dolphin_desktop.CDPSession`
works 1:1.

All stdlib access goes through ``dolphin_desktop`` public helpers to
preserve the autonomous-library contract.
"""

from __future__ import annotations

from dolphin_desktop import env_var, http_ok, is_cdp_available, path_exists

STEAM_CDP_PORT = 8080
STEAM_CDP_ENDPOINT = f"http://127.0.0.1:{STEAM_CDP_PORT}"


def find_steam() -> str | None:
    """Return the ``steam.exe`` path, or None if Steam is not installed."""
    program_files = env_var("ProgramFiles") or r"C:\Program Files"
    program_files_x86 = env_var("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    candidates = [
        rf"{program_files_x86}\Steam\steam.exe",
        rf"{program_files}\Steam\steam.exe",
    ]
    for c in candidates:
        if path_exists(c):
            return c
    return None


def steam_cdp_up() -> bool:
    """True if a Steam client is already listening on the CDP debug port."""
    return http_ok(f"{STEAM_CDP_ENDPOINT}/json/version")


has_playwright = is_cdp_available
STEAM = find_steam()
