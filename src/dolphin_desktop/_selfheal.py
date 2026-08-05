"""Self-healing selector telemetry.

Records fallback activations to a JSONL file and provides stats access.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ._logging import _redact, get_logger

logger = get_logger("selfheal")

_DEFAULT_STATS_FILE = ".dolphin-selfheal.jsonl"
_MAX_BYTES = 1_048_576


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
        _rotate(path)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
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
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass
    return records[-n:]


def _current_test() -> str:
    """Return the running pytest node id (set by pytest via PYTEST_CURRENT_TEST)."""
    return os.environ.get("PYTEST_CURRENT_TEST", "")
