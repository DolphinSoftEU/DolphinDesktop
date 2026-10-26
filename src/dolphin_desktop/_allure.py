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

_PLUGIN_NAME = "dolphin_desktop.redaction"
# IDs, attachment paths and schema enums are references/control data rather
# than diagnostic text. Masking an incidental substring would break links or
# change a test's verdict (for example when a marked secret is "failed").
_STRUCTURAL_FIELDS = frozenset(
    {"uuid", "id", "historyId", "testCaseId", "source", "children", "status", "stage", "type"}
)


def _redact_item(item: Any, *, depth: int = 0) -> Any:
    """Sanitize nested Allure models, including parameters keyed by secret names."""
    if depth > 32:
        return "***"
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
                else _redact_item(value, depth=depth + 1),
            )
        return item
    if isinstance(item, dict):
        return {
            key: "***" if is_sensitive_key(key) else _redact_item(value, depth=depth + 1)
            for key, value in item.items()
        }
    if isinstance(item, (list, tuple)):
        values = [_redact_item(value, depth=depth + 1) for value in item]
        return tuple(values) if isinstance(item, tuple) else values
    return redact_value(item)


class _AllureRedactor:
    """Run before file/memory reporters, regardless of plugin registration order."""

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
