"""Global configuration store for dolphin_desktop."""

from __future__ import annotations

import math
import os
import warnings

MAX_VIDEO_FPS = 30
VALID_TRACE_MODES = ("off", "on-failure", "always")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "ERROR")


def _video_modes() -> tuple[str, ...]:
    from ._video import VALID_MODES

    return VALID_MODES


def _env_number(
    name: str,
    default: float,
    cast: type,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Read a numeric env var, applying the range rules ``config()`` enforces.

    A typo in the environment must not make ``import dolphin_desktop`` fail —
    the import happens before any test code can catch the error — so an
    out-of-range value warns and yields *default* rather than raising.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError:
        warnings.warn(
            f"Ignoring {name}={raw!r}: not a valid number, using {default!r}.",
            stacklevel=2,
        )
        return default
    # NaN compares False against every bound, so the range check below lets it
    # through; a NaN timeout makes ``time.monotonic() >= deadline`` permanently
    # False and every poll loop spins forever instead of timing out.
    if not math.isfinite(value):
        warnings.warn(
            f"Ignoring {name}={raw!r}: not a finite number, using {default!r}.",
            stacklevel=2,
        )
        return default
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        warnings.warn(
            f"Ignoring {name}={raw!r}: outside the accepted range "
            f"[{minimum}, {maximum}], using {default!r}.",
            stacklevel=2,
        )
        return default
    return value


def _env_choice(name: str, default: str, choices: tuple[str, ...]) -> str:
    """Read an enum env var, applying the allow-list ``config()`` enforces.

    Matching is case-insensitive: an environment variable is hand-typed far
    more often than a ``config()`` argument, and ``DOLPHIN_VIDEO=keepAll``
    silently discarding every recording is worse than accepting the spelling.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value not in choices:
        warnings.warn(
            f"Ignoring {name}={raw!r}: expected one of {', '.join(choices)}, using {default!r}.",
            stacklevel=2,
        )
        return default
    return value


def _env_log_level() -> str:
    """Read the preferred logging environment variable and validate it.

    The product-prefixed variable owns precedence when both variables exist.
    That choice is made before validation so an invalid preferred value safely
    falls back to INFO rather than unexpectedly using the fallback variable.
    """
    name = (
        "DOLPHIN_DESKTOP_LOG_LEVEL"
        if "DOLPHIN_DESKTOP_LOG_LEVEL" in os.environ
        else "DOLPHIN_LOG_LEVEL"
    )
    choices = tuple(level.lower() for level in VALID_LOG_LEVELS)
    return _env_choice(name, "info", choices).upper()


_defaults: dict[str, float | int | str] = {
    "timeout": _env_number("DOLPHIN_TIMEOUT", 10.0, float, minimum=0),
    "poll_interval": 0.1,
    "trace_mode": _env_choice("DOLPHIN_TRACE", "on-failure", VALID_TRACE_MODES),
    "video_mode": _env_choice("DOLPHIN_VIDEO", "keepfailedonly", _video_modes()),
    "video_fps": int(_env_number("DOLPHIN_VIDEO_FPS", 10, int, minimum=1, maximum=MAX_VIDEO_FPS)),
    "log_level": _env_log_level(),
    "retry_count": int(_env_number("DOLPHIN_RETRY", 0, int, minimum=0)),
}


def config(
    *,
    timeout: float | None = None,
    poll_interval: float | None = None,
    trace_mode: str | None = None,
    video_mode: str | None = None,
    video_fps: int | None = None,
    log_level: str | None = None,
    retry_count: int | None = None,
) -> None:
    """Set global dolphin defaults.

    Args:
        timeout: Default element-wait timeout in seconds (default 10.0).
            Also settable via the ``DOLPHIN_TIMEOUT`` environment variable or
            the ``--dolphin-timeout`` pytest CLI option.
        poll_interval: Polling interval for retry loops, in seconds (default 0.1).
        trace_mode: Trace capture mode — ``"off"``, ``"on-failure"`` (default),
            or ``"always"``.  Also settable via ``DOLPHIN_TRACE`` env var or
            the ``--dolphin-trace`` pytest CLI option.
        video_mode: Video recording mode — ``"off"``, ``"keepfailedonly"`` (default),
            or ``"keepall"``.  Also settable via ``DOLPHIN_VIDEO`` env var or
            the ``--dolphin-video`` pytest CLI option.
        video_fps: Capture frame rate in frames/second (default 10, max 30).
            Also settable via ``DOLPHIN_VIDEO_FPS`` env var.
        log_level: Logging verbosity — ``"DEBUG"``, ``"INFO"`` (default), or
            ``"ERROR"``.  Applied to the ``dolphin_desktop`` logger immediately.
            Also settable via ``DOLPHIN_DESKTOP_LOG_LEVEL`` / ``DOLPHIN_LOG_LEVEL``
            env vars or the ``--dolphin-desktop-log-level`` pytest CLI option.
        retry_count: Number of times to retry a test that fails with a transient
            dolphin error (``ElementNotFoundError`` / ``WaitTimeoutError``).
            Default ``0`` (no retry).  Also settable via ``DOLPHIN_RETRY`` env var
            or the ``--dolphin-retry`` pytest CLI option.
    """
    if timeout is not None:
        if not math.isfinite(timeout):
            raise ValueError("timeout must be finite")
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        _defaults["timeout"] = float(timeout)
    if poll_interval is not None:
        if not math.isfinite(poll_interval):
            raise ValueError("poll_interval must be finite")
        if poll_interval < 0:
            raise ValueError("poll_interval must be non-negative")
        _defaults["poll_interval"] = float(poll_interval)
    if trace_mode is not None:
        if trace_mode not in VALID_TRACE_MODES:
            raise ValueError(f"Invalid trace_mode: {trace_mode!r}")
        _defaults["trace_mode"] = trace_mode
    if video_mode is not None:
        if video_mode not in _video_modes():
            raise ValueError(f"Invalid video_mode: {video_mode!r}")
        _defaults["video_mode"] = video_mode
    if video_fps is not None:
        if not 1 <= video_fps <= MAX_VIDEO_FPS:
            raise ValueError(f"video_fps must be between 1 and {MAX_VIDEO_FPS}")
        _defaults["video_fps"] = int(video_fps)
    if log_level is not None:
        normalized_log_level = log_level.upper() if isinstance(log_level, str) else None
        if normalized_log_level not in VALID_LOG_LEVELS:
            raise ValueError(
                f"Invalid log_level: {log_level!r}; expected one of {', '.join(VALID_LOG_LEVELS)}"
            )
        from ._logging import apply_log_level

        _defaults["log_level"] = normalized_log_level
        apply_log_level(normalized_log_level)
    if retry_count is not None:
        if retry_count < 0:
            raise ValueError("retry_count must be non-negative")
        _defaults["retry_count"] = int(retry_count)


def get_timeout() -> float:
    return float(_defaults["timeout"])


def get_poll_interval() -> float:
    return float(_defaults["poll_interval"])


def get_trace_mode() -> str:
    return str(_defaults["trace_mode"])


def get_video_mode() -> str:
    return str(_defaults["video_mode"])


def get_video_fps() -> int:
    return int(_defaults["video_fps"])


def get_log_level() -> str:
    return str(_defaults["log_level"])


def get_retry_count() -> int:
    return int(_defaults["retry_count"])
