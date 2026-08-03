"""Optional Sentry telemetry for dolphin-internal crashes.

Enabled only when **both** conditions hold:
  1. ``DOLPHIN_TELEMETRY=on`` is set in the environment.
  2. ``sentry-sdk`` is installed (``pip install dolphin-desktop[telemetry]``).

Privacy-first design:
  * Off by default — users must explicitly opt in.
  * Sentry's default integrations are disabled and replaced by an explicit
    list. LoggingIntegration would otherwise turn every ``logging.error()``
    in the host process into an event carrying the user's own log text;
    AtexitIntegration is kept because without it queued events — including a
    crash captured just before exit — are never flushed.
  * Local variables are never collected — their reprs contain test data and
    typed credentials.
  * ``_before_send`` drops any event that was not raised inside
    ``dolphin_desktop``, and strips the source context Sentry reads off disk
    for every non-dolphin frame, so no line of the user's own test code
    leaves the machine.
  * No DSN is wired until dolphin has a real Sentry project; the hook is a
    no-op until then.
"""

from __future__ import annotations

import os
from typing import Any, cast

_initialized = False

# Set to dolphin's own Sentry DSN once a project is created.
_DOLPHIN_DSN: str = ""

_PACKAGE = "dolphin_desktop"


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
            default_integrations=False,
            auto_enabling_integrations=False,
            integrations=_integrations(),
            include_local_variables=False,
            send_default_pii=False,
            before_send=cast(Any, _before_send),
        )
        _initialized = True
    except ImportError:
        pass


def _integrations() -> list:  # type: ignore[type-arg]
    """The integrations that must survive ``default_integrations=False``.

    AtexitIntegration is the only one dolphin needs: it drains the transport
    queue on interpreter shutdown, without which an exception captured just
    before exit is silently dropped.
    """
    try:
        from sentry_sdk.integrations.atexit import (  # type: ignore[import]
            AtexitIntegration,
        )
    except ImportError:
        return []
    return [AtexitIntegration()]


def _is_dolphin_module(module: str | None) -> bool:
    return module == _PACKAGE or bool(module and module.startswith(f"{_PACKAGE}."))


def _raised_in_dolphin(event: dict) -> bool:  # type: ignore[type-arg]
    """True when the innermost frame of the event's exception is dolphin code."""
    values = (event.get("exception") or {}).get("values") or []
    if not values:
        return False
    frames = (values[-1].get("stacktrace") or {}).get("frames") or []
    if not frames:
        return False
    return _is_dolphin_module(frames[-1].get("module"))


_SOURCE_KEYS = ("context_line", "pre_context", "post_context", "vars")


def _strip_user_source(event: dict) -> dict:  # type: ignore[type-arg]
    """Remove the source lines Sentry read off disk for non-dolphin frames.

    ``include_local_variables=False`` suppresses only ``vars``; the frame
    still carries ``context_line`` / ``pre_context`` / ``post_context`` copied
    verbatim out of the user's own test file.
    """
    for value in (event.get("exception") or {}).get("values") or []:
        for frame in (value.get("stacktrace") or {}).get("frames") or []:
            if _is_dolphin_module(frame.get("module")):
                continue
            for key in _SOURCE_KEYS:
                frame.pop(key, None)
    return event


def _before_send(event: dict, hint: dict) -> dict | None:  # type: ignore[type-arg]
    """Drop events that are not dolphin-internal, and strip user source lines.

    An event without ``exc_info`` did not come from ``capture_exception`` (it
    is a log record or a message), so it is discarded outright; the remainder
    must carry an exception type defined in ``dolphin_desktop`` or have been
    raised in a ``dolphin_desktop`` frame.  What survives keeps only the
    source context of dolphin's own frames.
    """
    exc_info = hint.get("exc_info")
    if not exc_info:
        return None
    module = getattr(exc_info[0], "__module__", "") or ""
    if _is_dolphin_module(module):
        return _strip_user_source(event)
    return _strip_user_source(event) if _raised_in_dolphin(event) else None


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
