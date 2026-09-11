"""Self-healing selector telemetry.

Records fallback activations to a JSONL file and provides stats access.
"""

from __future__ import annotations

import errno
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ._logging import _redact, get_logger

logger = get_logger("selfheal")

_DEFAULT_STATS_FILE = ".dolphin-selfheal.jsonl"
_MAX_BYTES = 1_048_576
_WINDOWS_CONTENTION_ERRNOS = frozenset(
    {errno.EACCES, errno.EAGAIN, errno.EDEADLK},
)
_WINDOWS_CONTENTION_WINERRORS = frozenset({32, 33})
_WINDOWS_RETRY_DELAY = 0.01


def _stats_file() -> Path:
    """Resolve the telemetry path on every call.

    Binding ``DOLPHIN_SELFHEAL_FILE`` at import time would freeze whatever the
    environment held before ``dolphin_desktop`` was first imported, which is
    usually earlier than the point where a test suite sets it.
    """
    return Path(os.environ.get("DOLPHIN_SELFHEAL_FILE", _DEFAULT_STATS_FILE))


def _rotate(path: Path) -> None:
    """Keep at most one previous generation so an unattended suite cannot fill the disk."""
    try:
        if path.stat().st_size < _MAX_BYTES:
            return
        os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass


def _is_windows_contention(error: OSError) -> bool:
    winerror = getattr(error, "winerror", None)
    if winerror is not None:
        return winerror in _WINDOWS_CONTENTION_WINERRORS
    return error.errno in _WINDOWS_CONTENTION_ERRNOS


def _retry_windows_contention(operation: Callable[[], Any]) -> Any:
    """Retry recognized Windows sharing or lock failures until they clear."""
    while True:
        try:
            return operation()
        except OSError as error:
            if os.name != "nt" or not _is_windows_contention(error):
                raise
            # A lock held by another process can legitimately outlast any
            # fixed retry budget; poll only while Windows identifies this as
            # sharing/lock contention so the eventual write is not dropped.
            time.sleep(_WINDOWS_RETRY_DELAY)


@contextmanager
def _stats_lock(path: Path) -> Iterator[None]:
    """Serialise journal rotation and reads across independent processes.

    The lock is deliberately a sidecar file: rotating the journal itself
    while another process has it open would not protect the other process's
    append on Windows.  ``fcntl`` and ``msvcrt`` are both standard-library
    platform primitives, so this does not add a runtime dependency.
    """
    lock_path = path.with_name(path.name + ".lock")
    lock_file = _retry_windows_contention(lambda: lock_path.open("a+b"))
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                _retry_windows_contention(lambda: lock_file.write(b"\0"))
                _retry_windows_contention(lock_file.flush)

            def acquire_lock() -> None:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)

            _retry_windows_contention(acquire_lock)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _redact_values(value: Any) -> Any:
    """Redact every string inside *value*, leaving the structure intact.

    Recursive because the journal's ``primary`` and ``fallback`` are selector
    dicts: redacting only the top level let a secret inside
    ``{"title": "password=…"}`` through, which is exactly where a selector
    carries one.
    """
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, dict):
        return {k: _redact_values(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_values(v) for v in value]
    return value


def record_fallback(
    primary: dict[str, Any],
    fallback_used: dict[str, Any],
    test_name: str | None = None,
) -> None:
    """Append a fallback event to the telemetry log and emit a warning.

    Selector criteria carry application text, so the serialised line is passed
    through the same redaction the logging path applies — this is a direct
    file write that no log handler ever sees.
    """
    entry: dict[str, Any] = {
        "ts": time.time(),
        "test": test_name if test_name is not None else _current_test(),
        "primary": primary,
        "fallback": fallback_used,
    }
    # Redact the values, then serialise — never the other way round. Running
    # the pattern over finished JSON rewrote the document itself: a numeric
    # value became {"token": ***} and an escaped inner quote lost its closing
    # delimiter, both unparseable. selfheal_stats() swallows JSONDecodeError,
    # so a single corrupt line silently truncated the whole history from there
    # on — the failure mode was invisible.
    entry = _redact_values(entry)
    path = _stats_file()
    try:
        with _stats_lock(path):
            _rotate(path)
            serialized_entry = json.dumps(entry) + "\n"
            stats_file = _retry_windows_contention(
                lambda: path.open("a", encoding="utf-8"),
            )
            try:
                _retry_windows_contention(lambda: stats_file.write(serialized_entry))
                _retry_windows_contention(stats_file.flush)
            finally:
                stats_file.close()
    except OSError:
        pass

    logger.warning(
        "Self-healing: primary selector %r not found; used fallback %r. "
        "Consider updating the primary selector.",
        primary,
        fallback_used,
    )


def selfheal_stats(n: int = 10, file: Path | None = None) -> list[dict[str, Any]]:
    """Return the last *n* fallback events from the telemetry log.

    Args:
        n: Maximum number of recent records to return.
        file: Path to the JSONL telemetry file.  Defaults to the value of
            ``DOLPHIN_SELFHEAL_FILE`` env var or ``.dolphin-selfheal.jsonl``.

    Returns:
        List of event dicts with keys ``ts``, ``test``, ``primary``, ``fallback``.
    """
    path = file if file is not None else _stats_file()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with _stats_lock(path):
            if not path.exists():
                return []
            text = _retry_windows_contention(lambda: path.read_text(encoding="utf-8"))
            for line in text.splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass
    return records[-n:]


def _current_test() -> str:
    """Return the running pytest node id (set by pytest via PYTEST_CURRENT_TEST)."""
    return os.environ.get("PYTEST_CURRENT_TEST", "")
