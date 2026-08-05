"""Environment probes for the pub400.com mainframe test suite.

All stdlib access goes through :mod:`dolphin_desktop` public helpers so
the "autonomous library" contract holds for the test scaffolding too.
"""

from __future__ import annotations

from dolphin_desktop import env_var, path_exists

# Well-known wc3270 install directories on Windows. If the environment
# variable DOLPHIN_WS3270_PATH is set it wins — that is how CI is
# expected to point at a pre-installed binary.
_LOCALAPPDATA = env_var("LOCALAPPDATA") or ""


def find_ws3270() -> str | None:
    """Return the absolute path to ``ws3270.exe``, or None if not installed."""
    explicit = env_var("DOLPHIN_WS3270_PATH")
    if explicit and path_exists(explicit):
        return explicit
    candidates = [
        rf"{_LOCALAPPDATA}\wc3270\ws3270.exe",
        rf"{_LOCALAPPDATA}\Programs\wc3270\ws3270.exe",
        r"C:\Program Files\wc3270\ws3270.exe",
        r"C:\Program Files (x86)\wc3270\ws3270.exe",
    ]
    for c in candidates:
        if path_exists(c):
            return c
    return None


WS3270 = find_ws3270()
