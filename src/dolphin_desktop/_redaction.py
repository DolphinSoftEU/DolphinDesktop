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

from ._logging import _redact, unwrap_secret

_PARAMETER_VALUES: ContextVar[set[str] | None] = ContextVar(
    "dolphin_parameter_values", default=None
)


def _sensitive_values(value: Any, *, serialized: bool = False) -> set[str]:
    """Collect scalar leaves and their diagnostic spellings without changing input.

    Allure represents parameters with repr(); pytest supplies the original
    objects. Decode only serialized literals, then use the same bounded walk
    for both sources. Mapping keys describe the structure; its values are the
    sensitive payload, including values below ordinary keys such as 'code'.
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
    visited: set[int] = set()

    def collect(current: Any, depth: int) -> None:
        if depth > 32:
            return
        current = unwrap_secret(current)
        if isinstance(current, Enum):
            collect(str(current), depth + 1)
            collect(current.name, depth + 1)
            collect(current.value, depth + 1)
            return
        if isinstance(current, (PurePath, UUID, Decimal)):
            current = str(current)
        if isinstance(current, (dict, list, tuple, set, frozenset)):
            identity = id(current)
            if identity in visited:
                return
            visited.add(identity)
            children = current.values() if isinstance(current, dict) else current
            for child in children:
                collect(child, depth + 1)
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

    collect(value, 0)
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
