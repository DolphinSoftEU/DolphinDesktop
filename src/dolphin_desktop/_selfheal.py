"""Self-healing selector telemetry.

Records fallback activations to a JSONL file and provides stats access.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("dolphin_desktop.selfheal")

_STATS_FILE = Path(os.environ.get("DOLPHIN_SELFHEAL_FILE", ".dolphin-selfheal.jsonl"))


def record_fallback(
    primary: dict[str, Any],
    fallback_used: dict[str, Any],
    test_name: str | None = None,
) -> None:
    """Append a fallback event to the telemetry log and emit a warning."""
    entry: dict[str, Any] = {
        "ts": time.time(),
        "test": test_name if test_name is not None else _current_test(),
        "primary": primary,
        "fallback": fallback_used,
    }
    try:
        with _STATS_FILE.open("a", encoding="utf-8") as f:
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
    path = file if file is not None else _STATS_FILE
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
