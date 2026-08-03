"""Minimal ``dolphinsoft`` public API surrogate for cross-install tests."""

from __future__ import annotations

import contextlib
import json
import sys
import time
from collections.abc import Iterator

__all__ = ["_event_mode_active", "step"]

# Flag flipped True by the pytest11 hook when the plugin is loaded.
_event_mode_active = False


def _emit(kind: str, **payload: object) -> None:
    if not _event_mode_active:
        return
    line = "DOLPHINSOFT_EVENT: " + json.dumps({"kind": kind, **payload})
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


@contextlib.contextmanager
def step(name: str) -> Iterator[None]:
    """Emit ``step_start`` / ``step_finish`` events for the wrapped block."""
    started_at = time.monotonic()
    _emit("step_start", name=name)
    try:
        yield
    finally:
        _emit(
            "step_finish",
            name=name,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )
