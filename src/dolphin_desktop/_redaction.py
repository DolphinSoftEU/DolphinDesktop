"""Shared, test-scoped redaction of sensitive parameter values."""

from __future__ import annotations

from ast import literal_eval
from contextvars import ContextVar
from typing import Any

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
    """Apply ordinary redaction and literal masking of known parameter leaves."""
    text = _redact(text)
    for value in values:
        text = text.replace(value, "***")
    return text
