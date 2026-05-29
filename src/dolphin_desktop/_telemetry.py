"""Optional Sentry telemetry for dolphin-internal crashes.

Enabled only when **both** conditions hold:
  1. ``DOLPHIN_TELEMETRY=on`` is set in the environment.
  2. ``sentry-sdk`` is installed (``pip install dolphin-desktop[telemetry]``).

Privacy-first design:
  * Off by default — users must explicitly opt in.
  * ``_before_send`` drops any event whose exception type is not from the
    ``dolphin_desktop.*`` namespace, so user-test failures are never uploaded.
  * No DSN is wired until dolphin has a real Sentry project; the hook is a
    no-op until then.
"""

from __future__ import annotations

import os

_initialized = False

# Set to dolphin's own Sentry DSN once a project is created.
_DOLPHIN_DSN: str = ""


def init() -> None:
    """Initialize Sentry telemetry.  Idempotent; silent if disabled or not installed."""
    global _initialized
    if _initialized:
        return
    if os.environ.get("DOLPHIN_TELEMETRY", "").lower() != "on":
        return
    if not _DOLPHIN_DSN:
        return
    try:
        import sentry_sdk  # type: ignore[import]

        sentry_sdk.init(
            dsn=_DOLPHIN_DSN,
            traces_sample_rate=0.0,
            before_send=_before_send,
        )
        _initialized = True
    except ImportError:
        pass


def _before_send(event: dict, hint: dict) -> dict | None:  # type: ignore[type-arg]
    """Drop events that are not dolphin-internal — never send user-test exceptions."""
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_type = exc_info[0]
        module = getattr(exc_type, "__module__", "") or ""
        if not module.startswith("dolphin"):
            return None
    return event


def capture_exception(exc: BaseException) -> None:
    """Send a dolphin-internal exception to Sentry.  No-op when telemetry is disabled."""
    if not _initialized:
        return
    try:
        import sentry_sdk  # type: ignore[import]

        sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def is_enabled() -> bool:
    """Return ``True`` when Sentry telemetry is active."""
    return _initialized
