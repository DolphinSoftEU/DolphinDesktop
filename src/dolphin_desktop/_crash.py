"""Crash dump — captures stack trace, UIA tree, and environment info into a ZIP archive.

Written to ``dolphin-crashes/crash-<timestamp>.zip`` (or a custom directory).
Triggered automatically by the pytest plugin on dolphin-internal errors.
Can also be called manually:

    from dolphin_desktop._crash import write_crash_dump
    zip_path = write_crash_dump(exc=some_exception)
"""

from __future__ import annotations

import datetime
import platform
import sys
import traceback
import zipfile
from pathlib import Path
from typing import Any

_DEFAULT_DIR = Path("dolphin-crashes")


def write_crash_dump(
    exc: BaseException | None = None,
    output_dir: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write ``crash-<timestamp>.zip`` with full diagnostics.

    Args:
        exc: The exception that triggered the dump (``None`` → captures current stack).
        output_dir: Where to write the ZIP (default: ``dolphin-crashes/``).
        extra: Additional key/value pairs written verbatim to ``extra.txt``.

    Returns:
        Absolute path of the written ZIP file.
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = output_dir or _DEFAULT_DIR
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"crash-{ts}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("stack.txt", _fmt_stack(exc))
        zf.writestr("uia_tree.txt", _fmt_uia_tree())
        zf.writestr("environment.txt", _fmt_env())
        if extra:
            body = "\n".join(f"{k}: {v}" for k, v in extra.items())
            zf.writestr("extra.txt", body)

    return zip_path.resolve()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fmt_stack(exc: BaseException | None) -> str:
    if exc is not None:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return "".join(traceback.format_stack())


def _fmt_uia_tree() -> str:
    try:
        import pywinauto

        lines: list[str] = []
        for win in pywinauto.Desktop(backend="uia").windows():
            try:
                title = win.window_text()
                cls = win.class_name()
                lines.append(f"[{cls}] {title!r}")
                for child in win.children()[:30]:
                    try:
                        lines.append(f"  [{child.class_name()}] {child.window_text()!r}")
                    except Exception:
                        lines.append("  <child: error>")
            except Exception:
                lines.append("<window: error>")
        return "\n".join(lines) or "(no windows)"
    except Exception as e:
        return f"(UIA capture failed: {e})"


def _fmt_env() -> str:
    try:
        from dolphin_desktop import __version__ as ver
    except Exception:
        ver = "unknown"

    parts = [
        f"dolphin: {ver}",
        f"python: {sys.version}",
        f"platform: {platform.platform()}",
    ]
    for pkg in ("pywinauto", "comtypes", "PIL", "yaml"):
        try:
            mod = __import__(pkg)
            parts.append(f"{pkg}: {getattr(mod, '__version__', 'installed')}")
        except ImportError:
            parts.append(f"{pkg}: not installed")
    return "\n".join(parts)
