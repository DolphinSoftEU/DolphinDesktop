"""Global configuration store for dolphin_desktop."""

from __future__ import annotations

import os

_defaults: dict[str, float | int | str] = {
    "timeout": float(os.environ.get("DOLPHIN_TIMEOUT", "10.0")),
    "poll_interval": 0.1,
    "trace_mode": os.environ.get("DOLPHIN_TRACE", "on-failure"),
    "video_mode": os.environ.get("DOLPHIN_VIDEO", "keepfailedonly"),
    "video_fps": int(os.environ.get("DOLPHIN_VIDEO_FPS", "10")),
    "log_level": os.environ.get("DOLPHIN_LOG_LEVEL", "INFO"),
    "retry_count": int(os.environ.get("DOLPHIN_RETRY", "0")),
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
            ``"ERROR"``.  Also settable via ``DOLPHIN_LOG_LEVEL`` env var or
            the ``--dolphin-log-level`` pytest CLI option.
        retry_count: Number of times to retry a test that fails with a transient
            dolphin error (``ElementNotFoundError`` / ``WaitTimeoutError``).
            Default ``0`` (no retry).  Also settable via ``DOLPHIN_RETRY`` env var
            or the ``--dolphin-retry`` pytest CLI option.
    """
    if timeout is not None:
        _defaults["timeout"] = float(timeout)
    if poll_interval is not None:
        _defaults["poll_interval"] = float(poll_interval)
    if trace_mode is not None:
        if trace_mode not in ("off", "on-failure", "always"):
            raise ValueError(f"Invalid trace_mode: {trace_mode!r}")
        _defaults["trace_mode"] = trace_mode
    if video_mode is not None:
        from ._video import VALID_MODES

        if video_mode not in VALID_MODES:
            raise ValueError(f"Invalid video_mode: {video_mode!r}")
        _defaults["video_mode"] = video_mode
    if video_fps is not None:
        _defaults["video_fps"] = int(video_fps)
    if log_level is not None:
        _defaults["log_level"] = log_level.upper()
    if retry_count is not None:
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
