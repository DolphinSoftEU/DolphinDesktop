"""Redact completed Allure models before reporters serialize them.

Imported only when the optional Allure SDK is available. Exception messages
and fixture diagnostics are built independently of pytest's TestReport, so
redacting that report alone does not protect Allure's result/container JSON.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import allure_commons
import pytest
from attr import fields, has

from ._logging import is_sensitive_key, redact_value
from ._redaction import _PARAMETER_VALUES, _redact_with_values, _sensitive_values

_PLUGIN_NAME = "dolphin_desktop.redaction"
# IDs, attachment paths and schema enums are references/control data rather
# than diagnostic text. Masking an incidental substring would break links or
# change a test's verdict (for example when a marked secret is "failed").
_STRUCTURAL_FIELDS = frozenset(
    {"uuid", "id", "historyId", "testCaseId", "source", "children", "status", "stage", "type"}
)


def _parameter_values(item: Any, *, depth: int = 0) -> set[str]:
    """Collect secret parameter/label values before their metadata is masked.

    Allure stores string parameters as repr(value). Pytest can repeat the
    unquoted value in a default or custom parameter ID, so mask both its plain
    and escaped spellings throughout the model's human-readable fields.
    """
    if depth > 32:
        return set()
    values: set[str] = set()
    if has(type(item)):
        if is_sensitive_key(getattr(item, "name", None)) and hasattr(item, "value"):
            values.update(_sensitive_values(item.value, serialized=True))
        children = [
            getattr(item, field.name)
            for field in fields(type(item))
            if field.name not in _STRUCTURAL_FIELDS
        ]
    elif isinstance(item, dict):
        children = list(item.values())
    elif isinstance(item, (list, tuple)):
        children = list(item)
    else:
        return values
    for child in children:
        values.update(_parameter_values(child, depth=depth + 1))
    return values


def _redact_item(item: Any, *, depth: int = 0, secrets: tuple[str, ...] | None = None) -> Any:
    """Sanitize nested Allure models, including parameters keyed by secret names."""
    if depth > 32:
        return "***"
    if secrets is None:
        secrets = tuple(
            sorted(
                _parameter_values(item) | (_PARAMETER_VALUES.get() or set()), key=len, reverse=True
            )
        )
    if has(type(item)):
        sensitive_value = is_sensitive_key(getattr(item, "name", None))
        for field in fields(type(item)):
            if field.name in _STRUCTURAL_FIELDS:
                continue
            value = getattr(item, field.name)
            setattr(
                item,
                field.name,
                "***"
                if field.name == "value" and sensitive_value
                else _redact_item(value, depth=depth + 1, secrets=secrets),
            )
        return item
    if isinstance(item, dict):
        return {
            key: "***"
            if is_sensitive_key(key)
            else _redact_item(value, depth=depth + 1, secrets=secrets)
            for key, value in item.items()
        }
    if isinstance(item, (list, tuple)):
        values = [_redact_item(value, depth=depth + 1, secrets=secrets) for value in item]
        return tuple(values) if isinstance(item, tuple) else values
    if isinstance(item, str):
        return _redact_with_values(item, secrets)
    return redact_value(item)


class _AllureRedactor:
    """Run before file/memory reporters, regardless of plugin registration order."""

    @allure_commons.hookimpl(optionalhook=True)
    def add_parameter(self, name: str, value: Any) -> None:
        current = _PARAMETER_VALUES.get()
        if current is not None and is_sensitive_key(name):
            current.update(_sensitive_values(value))

    @allure_commons.hookimpl(hookwrapper=True, tryfirst=True)
    def report_result(self, result: Any) -> Iterator[None]:
        _redact_item(result)
        yield

    @allure_commons.hookimpl(hookwrapper=True, tryfirst=True)
    def report_container(self, container: Any) -> Iterator[None]:
        _redact_item(container)
        yield

    @allure_commons.hookimpl(hookwrapper=True, tryfirst=True, optionalhook=True)
    def report_globals(self, globals_item: Any) -> Iterator[None]:
        _redact_item(globals_item)
        yield


def _install_redaction(config: pytest.Config) -> None:
    """Register once and remove our hook when this pytest configuration is closed."""
    manager = allure_commons.plugin_manager
    if manager.get_plugin(_PLUGIN_NAME) is not None:
        return
    redactor = _AllureRedactor()
    manager.register(redactor, name=_PLUGIN_NAME)
    config.add_cleanup(lambda: manager.unregister(redactor))
