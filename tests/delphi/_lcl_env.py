"""Environment probes for the Delphi / LCL sample tests.

All stdlib access routes through dolphin_desktop public helpers to
preserve the autonomous-library contract.
"""

from __future__ import annotations

from dolphin_desktop import dirname, path_exists, path_join

_SAMPLE_DIR = path_join(dirname(__file__), "sample_lcl")


def find_sample_exe() -> str | None:
    """Return the compiled sample_lcl.exe path, or None if not built."""
    for name in ("sample_lcl.exe", "sample_lcl"):
        candidate = path_join(_SAMPLE_DIR, name)
        if path_exists(candidate):
            return candidate
    return None


SAMPLE_EXE = find_sample_exe()
