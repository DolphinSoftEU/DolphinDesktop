"""Crash dump — captures stack trace, UIA tree, and environment info into a ZIP archive.

Written to ``dolphin-crashes/crash-<timestamp>.zip`` (or a custom directory).
Not wired into the pytest plugin — call it explicitly:

    from dolphin_desktop._crash import write_crash_dump
    zip_path = write_crash_dump(exc=some_exception)
"""

from __future__ import annotations

import datetime
import os
import platform
import sys
import time
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Any

_DEFAULT_DIR = Path("dolphin-crashes")
_UIA_BUDGET = 5.0


def write_crash_dump(
    exc: BaseException | None = None,
    output_dir: Path | None = None,
    extra: dict[str, Any] | None = None,
    *,
    pid: int | None = None,
    all_windows: bool = False,
    uia_budget: float = _UIA_BUDGET,
) -> Path:
    """Write ``crash-<timestamp>.zip`` with full diagnostics.

    Args:
        exc: The exception that triggered the dump (``None`` → captures current stack).
        output_dir: Where to write the ZIP (default: ``dolphin-crashes/``).
        extra: Additional key/value pairs written verbatim to ``extra.txt``.
        pid: Restrict the UIA capture to this process.  Defaults to every
            process dolphin launched during the session plus every process it
            is attached to.
        all_windows: Walk every visible window on the desktop instead.  Off by
            default: the window text of unrelated applications (browser tabs,
            mail subjects, password-manager captions) is not dolphin's to
            collect.
        uia_budget: Seconds the UIA walk may take before it is truncated.

    Returns:
        Absolute path of the written ZIP file.
    """
    # Two dumps in the same second must not overwrite each other, and mode "w"
    # would silently do exactly that on a second-resolution stamp alone.
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = output_dir or _DEFAULT_DIR
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"crash-{ts}_{os.getpid()}_{uuid.uuid4().hex[:6]}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("stack.txt", _fmt_stack(exc))
        zf.writestr("uia_tree.txt", _fmt_uia_tree(_target_pids(pid), all_windows, uia_budget))
        zf.writestr("environment.txt", _fmt_env())
        if extra:
            body = "\n".join(f"{k}: {v}" for k, v in extra.items())
            zf.writestr("extra.txt", body)

    return zip_path.resolve()


# Internal helpers


def _fmt_stack(exc: BaseException | None) -> str:
    if exc is not None:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return "".join(traceback.format_stack())


def _target_pids(pid: int | None) -> set[int]:
    if pid is not None:
        return {pid}
    from . import _application

    # ``_live_pids``/``_session_pids`` hold launched processes only — they are
    # the kill lists the pytest plugin drains, and an attached process must
    # never enter them. Attached PIDs are tracked separately, and the attribute
    # is read defensively so a crash dump still works against an _application
    # build that predates it.
    pids: set[int] = set()
    for attr in ("_live_pids", "_session_pids", "_attached_pids"):
        pids |= set(getattr(_application, attr, ()))
    return pids


def _fmt_uia_tree(pids: set[int], all_windows: bool, budget: float) -> str:
    if not all_windows and not pids:
        return (
            "(no dolphin-managed process to scope the capture to: nothing was "
            "launched and no attach was recorded; pass all_windows=True to "
            "walk the whole desktop)"
        )
    try:
        import pywinauto

        deadline = time.monotonic() + budget
        lines: list[str] = []
        for win in pywinauto.Desktop(backend="uia").windows():
            if time.monotonic() >= deadline:
                lines.append(f"<truncated: {budget}s UIA capture budget exhausted>")
                break
            try:
                if not all_windows and win.process_id() not in pids:
                    continue
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
