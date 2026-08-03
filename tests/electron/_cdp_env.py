"""Environment probes shared across the CDP test files.

Every stdlib call routes through :mod:`dolphin_desktop` public helpers —
this file has ZERO non-dolphin imports so the "autonomous library"
contract holds even for shared test scaffolding.
"""

from __future__ import annotations

from dolphin_desktop import env_var, is_cdp_available, path_exists


def find_vscode() -> str | None:
    """Return the ``Code.exe`` path, or None if VS Code is not installed."""
    local = env_var("LOCALAPPDATA") or ""
    candidates = [
        rf"{local}\Programs\Microsoft VS Code\Code.exe",
        r"C:\Program Files\Microsoft VS Code\Code.exe",
        r"C:\Program Files (x86)\Microsoft VS Code\Code.exe",
    ]
    for c in candidates:
        if path_exists(c):
            return c
    return None


# Re-export under the old name so existing test files keep working.
has_playwright = is_cdp_available

VSCODE = find_vscode()
