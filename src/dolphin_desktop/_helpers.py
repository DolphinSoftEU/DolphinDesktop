"""Public helper functions exposed at the package root.

These wrap stdlib calls (``time.sleep``, ``time.monotonic``,
``sys.executable``, ``subprocess.run``) so that test code can be written
against a single ``dolphin_desktop`` import surface without reaching into
stdlib modules. Keeping tests self-contained matches the "autonomous
library" design goal — anyone authoring tests sees one import line.
"""

from __future__ import annotations

import base64 as _base64
import os as _os
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile
import time as _time
from collections.abc import Iterator as _Iterator
from contextlib import contextmanager as _contextmanager
from itertools import count as _count
from pathlib import Path as _Path
from typing import Any as _Any

# Distinguishes "caller passed no default" from "caller passed None as the
# default" — what every ``get_attribute`` needs to decide between raising and
# returning. Lives here so the UIA, SAP and JAB locators can share one
# sentinel without importing each other.
_MISSING: _Any = object()


def sleep(seconds: float) -> None:
    """Block the current thread for *seconds* seconds.

    Public-API wrapper over ``time.sleep`` so callers don't need to import
    the standard ``time`` module.
    """
    _time.sleep(seconds)


def monotonic() -> float:
    """Return a monotonically increasing seconds counter.

    Public-API wrapper over ``time.monotonic`` — useful for measuring
    elapsed durations in tests without importing ``time``.
    """
    return _time.monotonic()


def python_executable() -> str:
    """Return the absolute path of the current Python interpreter.

    Wraps ``sys.executable`` so test code can spawn child Pythons via
    :meth:`Desktop.launch_python_script` without importing ``sys``.
    """
    return _sys.executable


def running_processes() -> list[tuple[str, int]]:
    """Return ``[(image_name, pid), ...]`` for every visible process.

    Implemented via the Windows ``tasklist`` CLI to avoid forcing the test
    code to depend on ``win32api`` or ``psutil``. Returns an empty list if
    ``tasklist`` is unavailable or fails — never raises.
    """
    try:
        out = _subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            text=True,
            timeout=10,
            stderr=_subprocess.DEVNULL,
        )
    except Exception:
        return []
    result: list[tuple[str, int]] = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            result.append((parts[0], int(parts[1])))
        except ValueError:
            continue
    return result


def env_var(name: str) -> str | None:
    """Return the value of environment variable *name* or ``None`` if unset.

    Public-API wrapper over ``os.environ.get`` so tests verifying env-var
    contracts (e.g. ``QT_ACCESSIBILITY`` not leaking out of
    :meth:`Desktop.launch_qt`) don't need to import ``os``.
    """
    return _os.environ.get(name)


def is_windows() -> bool:
    """Return True when running on Windows.

    Wraps ``sys.platform == "win32"`` so test files can write
    ``pytest.mark.skipif(not is_windows(), ...)`` without importing ``sys``.
    """
    return _sys.platform == "win32"


@_contextmanager
def tempdir(prefix: str = "dolphin_", *, ignore_errors: bool = True) -> _Iterator[str]:
    """Context-managed temporary directory (str path, auto-cleaned).

    Public-API wrapper over ``tempfile.TemporaryDirectory`` so tests do
    not need to import ``tempfile``. ``ignore_errors=True`` (default) is
    what real-world Electron / Chrome tests want — child processes hold
    cache-file handles briefly after ``kill()``, so strict cleanup would
    raise ``PermissionError`` on Windows for reasons unrelated to the
    test logic. Set ``ignore_errors=False`` to opt into strict cleanup.

    Example::

        from dolphin_desktop import tempdir, Desktop

        with tempdir(prefix="dolphin_electron_") as d:
            desktop = Desktop().launch_electron_cdp(
                f'"{app}" --user-data-dir="{d}"', debug_port=9223
            )
    """
    with _tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=ignore_errors) as d:
        yield d


def temp_file(
    prefix: str = "dolphin_",
    suffix: str = "",
    *,
    content: str | bytes | None = None,
) -> str:
    """Create a temp file, optionally seeded with *content*, and return its path.

    The file is NOT auto-deleted — call :func:`remove_file` when done.
    Useful in tests that need a stable file path to feed into
    :meth:`CDPLocator.set_input_files` or similar.
    """
    mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
    fd, path = _tempfile.mkstemp(prefix=prefix, suffix=suffix)
    try:
        if content is None:
            _os.close(fd)
        else:
            try:
                handle = _os.fdopen(fd, mode)
            except Exception:
                _os.close(fd)
                raise
            # ``fdopen`` took ownership of *fd*: its context manager closes the
            # descriptor even when ``write`` raises, so closing it again here
            # would surface as ``OSError: [Errno 9] Bad file descriptor`` and
            # mask the real failure.
            with handle as f:
                f.write(content)  # type: ignore[arg-type]
    except Exception:
        _Path(path).unlink(missing_ok=True)
        raise
    return path


def path_exists(path: str) -> bool:
    """Return True when *path* refers to a real file or directory."""
    return _Path(path).exists()


def path_basename(path: str) -> str:
    """Return the last path segment (file name or leaf directory)."""
    return _Path(path).name


def remove_file(path: str, *, missing_ok: bool = True) -> None:
    """Delete a file at *path*. When ``missing_ok`` is False, raise if absent."""
    # ``Path.unlink(missing_ok=...)`` (stdlib since 3.8) does the
    # check-and-delete in one syscall — the previous ``if not exists:
    # ... unlink()`` sequence had a TOCTOU window: a temp file cleaned
    # up by a peer process between the two calls would surface as a
    # bare ``FileNotFoundError`` that a caller passing ``missing_ok=True``
    # explicitly asked to have suppressed.
    _Path(path).unlink(missing_ok=missing_ok)


def b64encode(data: bytes | str) -> str:
    """Return base64-encoded *data* (bytes or utf-8 string) as an ASCII string."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return _base64.b64encode(data).decode("ascii")


def b64decode(data: str) -> bytes:
    """Decode a base64-encoded ASCII string back into bytes."""
    return _base64.b64decode(data)


def counter(start: int = 0) -> _Iterator[int]:
    """Return an infinite integer iterator (wraps ``itertools.count``).

    Useful when tests need a monotonically-increasing sequence (e.g. picking
    unique debug ports for parallel Electron launches) without importing
    ``itertools``.
    """
    return _count(start)


def start_thread(target: _Any, *, args: tuple = (), daemon: bool = True) -> _Any:
    """Spawn a daemon thread running *target(\\*args)* and return it.

    Public wrapper over :class:`threading.Thread` for concurrency
    tests that would otherwise need to import :mod:`threading` directly.

    The returned object has ``.join(timeout=...)`` and ``.is_alive()``
    for callers that need to synchronise.
    """
    import threading as _threading

    t = _threading.Thread(target=target, args=args, daemon=daemon)
    t.start()
    return t


def add_import_path(path: str) -> None:
    """Prepend *path* to :data:`sys.path` if not already present.

    Public wrapper so test scaffolding can add a sibling directory to
    the module search path without importing :mod:`sys` directly.
    Idempotent — a repeat call with the same path is a no-op.
    """
    if path not in _sys.path:
        _sys.path.insert(0, path)


def dirname(path: str) -> str:
    """Return the directory portion of *path*.

    Public wrapper over :func:`os.path.dirname` so tests can compute
    ``os.path.dirname(__file__)`` without importing stdlib.
    """
    return _os.path.dirname(path)


def path_join(*parts: str) -> str:
    """Join path components using the platform separator.

    Public wrapper over :func:`os.path.join`.
    """
    return _os.path.join(*parts)


def which(binary_name: str) -> str | None:
    """Return the full path to *binary_name* on PATH, or None.

    Public wrapper over :func:`shutil.which` so tests can locate helper
    binaries (java, javac, ws3270…) without importing stdlib themselves.
    """
    import shutil

    return shutil.which(binary_name)


def tcp_reachable(host: str, port: int, *, timeout: float = 3.0) -> bool:
    """Return True when a TCP connection to ``host:port`` succeeds.

    Non-raising probe used to skip network-dependent tests when the
    target host is unreachable. Wraps :mod:`socket` so tests can avoid
    importing it themselves.
    """
    import socket as _socket

    try:
        s = _socket.create_connection((host, port), timeout=timeout)
    except (TimeoutError, OSError):
        return False
    try:
        s.close()
    except OSError:
        pass
    return True


def http_ok(url: str, *, timeout: float = 1.0) -> bool:
    """Return True when a plain HTTP GET to *url* returns status 200.

    Public probe so tests can check whether a debug endpoint (CDP port,
    devtools port, health-check URL) is live without importing
    ``urllib`` themselves. Any transport error, timeout, or non-200
    status returns False — never raises.

    Example::

        from dolphin_desktop import http_ok

        if not http_ok("http://127.0.0.1:8080/json/version"):
            pytest.skip("target debug port not open")
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def _escape_keys(text: str) -> str:
    """Escape pywinauto ``type_keys``/``send_keys`` metacharacters in *text*.

    ``type_keys`` treats ``+ ^ % ~ ( ) { }`` as modifiers and key-sequence
    delimiters, so passing raw user data through unescaped silently maps
    those characters onto Shift / Ctrl / Alt or drops them. Wrapping each
    one in braces is the pywinauto-documented literal escape.

    Every code path that feeds caller-supplied *literal text* into
    ``type_keys`` must route it through here; paths that accept a pywinauto
    key *sequence* (``{ENTER}``, ``^c``) deliberately must not.
    """
    return "".join("{" + c + "}" if c in "+^%~(){}" else c for c in text)


def find_pid_by_image_name(image_name: str) -> int | None:
    """Return the first PID whose executable name equals *image_name*.

    Comparison is case-insensitive. Returns ``None`` if no match exists or
    the process snapshot is unavailable. Never raises.

    Example::

        from dolphin_desktop import find_pid_by_image_name, Desktop

        pid = find_pid_by_image_name("qtcreator.exe")
        if pid is None:
            pytest.skip("Qt Creator not running")
        app = Desktop().connect(process=pid)
    """
    target = image_name.lower()
    for name, pid in running_processes():
        if name.lower() == target:
            return pid
    return None
