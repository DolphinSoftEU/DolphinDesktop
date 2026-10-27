"""Shared parameter leaf extraction must cover pytest values and Allure reprs."""

from __future__ import annotations

import pytest

from dolphin_desktop._redaction import _redact_with_values, _sensitive_values
from tests._allure_redaction import typed_parameter_cases


@pytest.mark.parametrize(("value", "expected"), typed_parameter_cases())
def test_typed_and_nested_enum_payloads_protect_their_display_spellings(value, expected):
    leaves = _sensitive_values(value)
    assert expected <= leaves
    secrets = tuple(sorted(leaves, key=len, reverse=True))
    for text in expected:
        assert _redact_with_values("operation " + text, secrets) == "operation ***"


@pytest.mark.parametrize("value", ["o", "1", "12", "123", ".", "ą"])
def test_short_secrets_use_literal_unicode_token_boundaries(value):
    text = f"operation attempt 1001 [ {value} ] '{value}' customer-{value}"
    assert _redact_with_values(text, (value,)) == (
        "operation attempt 1001 [ *** ] '***' customer-***"
    )
    assert _redact_with_values(f"prefix{value}suffix", (value,)) == f"prefix{value}suffix"


def test_long_secrets_still_mask_embedded_values_and_empty_values_are_ignored():
    assert _redact_with_values("prefix739184suffix", ("739184", "")) == "prefix***suffix"


@pytest.mark.parametrize(
    "value",
    ["739184", 739184, b"739184", {"code": "739184"}, [{"code": ("739184",)}], {"739184"}],
)
def test_original_and_serialized_values_share_the_same_leaf_redaction(value):
    for serialized, supplied in ((False, value), (True, repr(value))):
        leaves = _sensitive_values(supplied, serialized=serialized)
        assert "739184" in leaves
        secrets = tuple(sorted(leaves, key=len, reverse=True))
        assert (
            _redact_with_values("Authenticating with 739184", secrets) == "Authenticating with ***"
        )


def test_escaped_nested_values_are_masked_in_container_reprs():
    value = {"code": "quoted'\"value\n\u56db"}
    leaves = _sensitive_values(value)
    secrets = tuple(sorted(leaves, key=len, reverse=True))
    assert _redact_with_values(repr(value), secrets) == "{'code': '***'}"
    assert _redact_with_values(ascii(value), secrets) == "{'code': '***'}"


def test_extraction_handles_cycles_shared_containers_and_excessive_depth():
    child = {"code": "739184"}
    cycle = [child, child]
    cycle.append(cycle)
    assert "739184" in _sensitive_values(cycle)
    deep = "too-deep-canary"
    for _ in range(34):
        deep = [deep]
    assert not _sensitive_values(deep)


def test_explicit_secret_and_opaque_objects_are_handled_without_calling_user_repr(monkeypatch):
    from dolphin_desktop import Secret, _logging

    monkeypatch.setattr(_logging, "_marked_secrets", {})

    class Opaque:
        def __repr__(self):
            raise RuntimeError("must not execute arbitrary representation code")

    assert not _sensitive_values(Opaque())
    assert _sensitive_values(Secret("739184")) == {"739184"}
    assert _sensitive_values("") == _sensitive_values("***") == set()
