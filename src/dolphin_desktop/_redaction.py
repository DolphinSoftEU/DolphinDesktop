"""Shared, test-scoped redaction of sensitive parameter values."""

from __future__ import annotations

import re
from ast import literal_eval
from contextvars import ContextVar
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any
from uuid import UUID

from ._logging import _redact, is_sensitive_key, unwrap_secret

_PARAMETER_VALUES: ContextVar[set[str] | None] = ContextVar(
    "dolphin_parameter_values", default=None
)


def _sensitive_values(value: Any, *, serialized: bool = False, sensitive: bool = True) -> set[str]:
    """Collect scalar leaves and their diagnostic spellings without changing input.

    Allure represents parameters with repr(); pytest supplies the original
    objects. Decode only serialized literals, then use the same bounded walk
    for both sources. A sensitive input makes every payload leaf sensitive.
    With sensitive=False, traverse public containers looking for sensitive
    dictionary keys, then collect only their subtrees. Include sensitivity in
    cycle detection so a shared object visited publicly can still be private.
    """
    if (
        serialized
        and isinstance(value, str)
        and value.startswith(("'", '"', "b'", 'b"', "{", "[", "("))
    ):
        try:
            value = literal_eval(value)
        except (ValueError, SyntaxError):
            pass
    values: set[str] = set()
    visited: set[tuple[int, bool]] = set()

    def collect(current: Any, depth: int, sensitive: bool) -> None:
        if depth > 32:
            return
        current = unwrap_secret(current)
        if isinstance(current, Enum):
            if sensitive:
                collect(str(current), depth + 1, True)
                collect(current.name, depth + 1, True)
            collect(current.value, depth + 1, sensitive)
            return
        if isinstance(current, (PurePath, UUID, Decimal)):
            current = str(current)
        if isinstance(current, (dict, list, tuple, set, frozenset)):
            identity = (id(current), sensitive)
            if identity in visited:
                return
            visited.add(identity)
            if isinstance(current, dict):
                for key, child in current.items():
                    collect(child, depth + 1, sensitive or is_sensitive_key(key))
            else:
                for child in current:
                    collect(child, depth + 1, sensitive)
            return
        if not sensitive:
            return
        if isinstance(current, bytes):
            current = current.decode("utf-8", errors="backslashreplace")
        if isinstance(current, (str, int, float, bool)) or current is None:
            text = str(current)
            if text and text != "***":
                values.update(
                    (
                        text,
                        text.encode("unicode_escape").decode("ascii"),
                        repr(text)[1:-1],
                        ascii(text)[1:-1],
                    )
                )

    collect(value, 0, sensitive)
    return values


def _redact_with_values(text: str, values: tuple[str, ...]) -> str:
    """Mask short leaves as whole tokens; longer leaves also inside other text.

    A one-character PIN must not destroy every word containing that character.
    For leaves of up to three characters, Unicode word boundaries preserve
    unrelated words/numbers. Callers must avoid embedding these short secrets
    in larger tokens, or sanitize those strings explicitly before publishing.
    """
    text = _redact(text)
    for value in values:
        if not value:
            continue
        if len(value) <= 3:
            text = re.sub(r"(?<!\w)" + re.escape(value) + r"(?!\w)", "***", text)
        else:
            text = text.replace(value, "***")
    return text


def _redact_identifier(identifier: str, values: set[str] | None = None) -> str:
    """Sanitize a displayed/stored identifier while retaining pytest's original ID."""
    if values is None:
        values = _PARAMETER_VALUES.get() or set()
    return _redact_with_values(identifier, tuple(sorted(values, key=len, reverse=True)))
