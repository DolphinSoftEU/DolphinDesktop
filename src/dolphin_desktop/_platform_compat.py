"""Small compatibility helpers for modules that expose Windows-only APIs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn


def _unsupported(feature: str, *_args: Any, **_kwargs: Any) -> NoReturn:
    """Raise a useful error when a Windows-only feature is used elsewhere."""
    raise RuntimeError(f"{feature} is only available on Windows")


def _unsupported_callable(feature: str) -> Callable[..., NoReturn]:
    """Return a callable placeholder for a deferred Windows API."""

    def fail(*args: Any, **kwargs: Any) -> NoReturn:
        _unsupported(feature, *args, **kwargs)

    return fail


class _UnavailableObject:
    """Proxy whose methods fail only when a Windows API is actually used."""

    def __init__(self, feature: str) -> None:
        self._feature = feature

    def __getattr__(self, name: str) -> Callable[..., NoReturn]:
        return _unsupported_callable(f"{self._feature}.{name}")


def _unavailable_class(name: str, feature: str) -> type:
    """Build a named class placeholder for a Windows-only dependency."""

    def init(self: Any, *args: Any, **kwargs: Any) -> NoReturn:
        _unsupported(feature, *args, **kwargs)

    return type(name, (), {"__init__": init})
