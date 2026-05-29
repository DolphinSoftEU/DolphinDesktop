"""dolphin-run headless CI launcher via CreateDesktopW.

Creates a hidden Windows desktop in the current user session and re-launches
the given command on that desktop.  Sets ``DOLPHIN_HEADLESS=1`` in the child
environment so the dolphin library and pytest plugin activate headless behaviour
automatically.

Usage::

    dolphin-run pytest tests/
    dolphin-run pytest tests/ -k test_notepad --dolphin-backend=uia

Architecture note: ``IUIAutomation.GetRootElement()`` sees only the current
thread's desktop.  Therefore the *entire* test process (pytest + dolphin) must
run on ``DolphinHidden`` — which is exactly what ``dolphin-run`` arranges.
``subprocess.STARTUPINFO.lpDesktop`` is not forwarded to Win32 ``CreateProcess``
by CPython; a ctypes struct is required instead.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import sys

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DESKTOP_NAME = "DolphinHidden"
_DESKTOP_ACCESS = 0x10000000  # GENERIC_ALL

_INFINITE = 0xFFFFFFFF
_STARTF_USESTDHANDLES = 0x00000100
_UOI_FLAGS = 1
_WSF_VISIBLE = 0x0001


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("lpReserved", ctypes.wintypes.LPWSTR),
        ("lpDesktop", ctypes.wintypes.LPWSTR),
        ("lpTitle", ctypes.wintypes.LPWSTR),
        ("dwX", ctypes.wintypes.DWORD),
        ("dwY", ctypes.wintypes.DWORD),
        ("dwXSize", ctypes.wintypes.DWORD),
        ("dwYSize", ctypes.wintypes.DWORD),
        ("dwXCountChars", ctypes.wintypes.DWORD),
        ("dwYCountChars", ctypes.wintypes.DWORD),
        ("dwFillAttribute", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("wShowWindow", ctypes.wintypes.WORD),
        ("cbReserved2", ctypes.wintypes.WORD),
        ("lpReserved2", ctypes.c_char_p),
        ("hStdInput", ctypes.wintypes.HANDLE),
        ("hStdOutput", ctypes.wintypes.HANDLE),
        ("hStdError", ctypes.wintypes.HANDLE),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.wintypes.HANDLE),
        ("hThread", ctypes.wintypes.HANDLE),
        ("dwProcessId", ctypes.wintypes.DWORD),
        ("dwThreadId", ctypes.wintypes.DWORD),
    ]


class _UserObjectFlags(ctypes.Structure):
    _fields_ = [
        ("fInherit", ctypes.wintypes.BOOL),
        ("fReserved", ctypes.wintypes.BOOL),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


# ---------------------------------------------------------------------------
# Desktop management
# ---------------------------------------------------------------------------


def create_hidden_desktop(name: str = DESKTOP_NAME) -> int:
    """Create a named hidden desktop and return its handle.

    ``CreateDesktopW`` on an already-existing name returns a new handle to the
    same object — the caller must still call :func:`close_desktop` on it.
    """
    h_desk = _user32.CreateDesktopW(name, None, None, 0, _DESKTOP_ACCESS, None)
    if not h_desk:
        raise OSError(f"CreateDesktopW({name!r}) failed: error {ctypes.get_last_error()}")
    return h_desk


def close_desktop(handle: int) -> None:
    """Close a desktop handle obtained from :func:`create_hidden_desktop`."""
    _user32.CloseDesktop(handle)


def close_process_handle(handle: int) -> None:
    """Close a process handle returned by :func:`launch_on_desktop` or
    :func:`launch_cmd_on_desktop`."""
    _kernel32.CloseHandle(handle)


def switch_thread_to_desktop(handle: int) -> None:
    """Switch the calling thread to the desktop identified by *handle*.

    Must be called before the thread creates any windows — once a thread has
    windows it cannot switch desktops.  Makes ``IUIAutomation.GetRootElement()``
    see the hidden desktop on this thread.
    """
    if not _user32.SetThreadDesktop(handle):
        raise OSError(f"SetThreadDesktop failed: error {ctypes.get_last_error()}")


# ---------------------------------------------------------------------------
# Desktop / station introspection
# ---------------------------------------------------------------------------


def get_current_desktop_name() -> str:
    """Return the name of the calling thread's current desktop."""
    tid = _kernel32.GetCurrentThreadId()
    h_desk = _user32.GetThreadDesktop(tid)
    buf = ctypes.create_unicode_buffer(256)
    needed = ctypes.wintypes.DWORD()
    _user32.GetUserObjectInformationW(h_desk, 2, buf, 512, ctypes.byref(needed))
    return buf.value


def has_interactive_station() -> bool:
    """Return ``True`` if the process window station has a visible (interactive) display.

    Returns ``False`` in non-interactive sessions where no user desktop is
    attached (e.g. Windows Session 0, headless CI without an RDP virtual
    display).  On GitHub Actions and Azure DevOps Windows runners this returns
    ``True`` because those environments maintain an active RDP session.
    """
    h_win_sta = _user32.GetProcessWindowStation()
    if not h_win_sta:
        return False
    flags = _UserObjectFlags()
    needed = ctypes.wintypes.DWORD()
    ok = _user32.GetUserObjectInformationW(
        h_win_sta,
        _UOI_FLAGS,
        ctypes.byref(flags),
        ctypes.sizeof(flags),
        ctypes.byref(needed),
    )
    if not ok:
        return False
    return bool(flags.dwFlags & _WSF_VISIBLE)


# ---------------------------------------------------------------------------
# Process launch on a specific desktop
# ---------------------------------------------------------------------------


def launch_on_desktop(
    args: list[str],
    desktop_name: str = DESKTOP_NAME,
    *,
    inherit_stdio: bool = True,
) -> tuple[int, int]:
    """Launch *args* as a new process on *desktop_name*, forwarding stdio.

    Returns ``(pid, h_process)``.  The caller must close *h_process*.

    Used by :func:`run_hidden` (the ``dolphin-run`` entry point) to re-launch
    pytest or any other command on the hidden desktop while keeping stdout and
    stderr connected to the calling terminal.
    """
    si = _StartupInfoW()
    si.cb = ctypes.sizeof(_StartupInfoW)
    si.lpDesktop = desktop_name

    if inherit_stdio:
        si.dwFlags = _STARTF_USESTDHANDLES
        si.hStdInput = _kernel32.GetStdHandle(ctypes.c_ulong(-10))  # STD_INPUT_HANDLE
        si.hStdOutput = _kernel32.GetStdHandle(ctypes.c_ulong(-11))  # STD_OUTPUT_HANDLE
        si.hStdError = _kernel32.GetStdHandle(ctypes.c_ulong(-12))  # STD_ERROR_HANDLE

    pi = _ProcessInformation()
    cmd_str = _build_cmd_string(args)

    ok = _kernel32.CreateProcessW(
        None,
        cmd_str,
        None,  # process security attrs
        None,  # thread security attrs
        True,  # bInheritHandles — stdio handles must be inheritable
        0,
        None,  # lpEnvironment=NULL → inherit parent env (including DOLPHIN_HEADLESS)
        None,  # lpCurrentDirectory=NULL → inherit parent cwd
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        raise OSError(f"CreateProcessW({cmd_str!r}) failed: error {ctypes.get_last_error()}")
    _kernel32.CloseHandle(pi.hThread)
    return pi.dwProcessId, pi.hProcess


def launch_cmd_on_desktop(
    cmd: str,
    desktop_name: str = DESKTOP_NAME,
    *,
    work_dir: str | None = None,
) -> tuple[int, int]:
    """Launch a command string on *desktop_name* without inheriting stdio.

    Returns ``(pid, h_process)``.  The caller must close *h_process* via
    :func:`close_process_handle`.

    Used by :meth:`Desktop.launch` when headless mode is active.
    """
    si = _StartupInfoW()
    si.cb = ctypes.sizeof(_StartupInfoW)
    si.lpDesktop = desktop_name

    pi = _ProcessInformation()

    ok = _kernel32.CreateProcessW(
        None,
        cmd,
        None,
        None,
        False,  # bInheritHandles
        0,
        None,  # inherit parent env
        work_dir,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        raise OSError(f"CreateProcessW({cmd!r}) failed: error {ctypes.get_last_error()}")
    _kernel32.CloseHandle(pi.hThread)
    return pi.dwProcessId, pi.hProcess


def _build_cmd_string(args: list[str]) -> str:
    """Join *args* into a Win32 command string, quoting tokens with spaces."""
    parts = []
    for a in args:
        if not a or " " in a or '"' in a:
            parts.append('"' + a.replace('"', '\\"') + '"')
        else:
            parts.append(a)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# dolphin-run entry point
# ---------------------------------------------------------------------------


def run_hidden(args: list[str]) -> int:
    """Create a hidden desktop and run *args* on it.  Returns the exit code.

    If the calling process is already on ``DolphinHidden`` (a nested
    ``dolphin-run`` call), *args* is executed directly without creating another
    desktop.  ``DOLPHIN_HEADLESS=1`` is injected into the environment before
    launch so the dolphin library and pytest plugin activate headless behaviour
    without extra flags.
    """
    if get_current_desktop_name() == DESKTOP_NAME:
        import subprocess

        return subprocess.run(args).returncode

    h_desk = create_hidden_desktop()
    os.environ["DOLPHIN_HEADLESS"] = "1"
    try:
        _pid, h_process = launch_on_desktop(args, DESKTOP_NAME, inherit_stdio=True)
        _kernel32.WaitForSingleObject(h_process, _INFINITE)
        exit_code = ctypes.wintypes.DWORD()
        _kernel32.GetExitCodeProcess(h_process, ctypes.byref(exit_code))
        _kernel32.CloseHandle(h_process)
        return exit_code.value
    finally:
        os.environ.pop("DOLPHIN_HEADLESS", None)
        close_desktop(h_desk)


def main() -> None:
    """Entry point for the ``dolphin-run`` CLI command."""
    if len(sys.argv) < 2:
        print("Usage: dolphin-run <command> [args...]", file=sys.stderr)
        print("", file=sys.stderr)
        print("Examples:", file=sys.stderr)
        print("  dolphin-run pytest tests/", file=sys.stderr)
        print("  dolphin-run pytest tests/ -k test_notepad --dolphin-backend=uia", file=sys.stderr)
        sys.exit(1)
    sys.exit(run_hidden(sys.argv[1:]))
