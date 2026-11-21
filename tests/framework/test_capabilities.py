"""Unit tests for the backend capability vocabulary."""

from __future__ import annotations

import importlib

import pytest

from dolphin_desktop._capabilities import (
    ALL_CAPABILITIES,
    IMAGE_ONLY,
    STANDARD_ACCESSIBILITY,
    Capability,
)

_CAPABILITY_SPECS = (
    ("LOCATE", "locate"),
    ("GET_TREE", "get_tree"),
    ("READ_TEXT", "read_text"),
    ("READ_STATE", "read_state"),
    ("CLICK", "click"),
    ("DOUBLE_CLICK", "double_click"),
    ("RIGHT_CLICK", "right_click"),
    ("HOVER", "hover"),
    ("DRAG", "drag"),
    ("TYPE_TEXT", "type_text"),
    ("PRESS_KEY", "press_key"),
    ("SCROLL", "scroll"),
    ("INVOKE", "invoke"),
    ("TOGGLE", "toggle"),
    ("EXPAND", "expand"),
    ("COLLAPSE", "collapse"),
    ("SELECT", "select"),
    ("SET_VALUE", "set_value"),
    ("SCREENSHOT", "screenshot"),
)

_STANDARD_ACCESSIBILITY_NAMES = {
    "LOCATE",
    "GET_TREE",
    "READ_TEXT",
    "READ_STATE",
    "CLICK",
    "DOUBLE_CLICK",
    "RIGHT_CLICK",
    "HOVER",
    "TYPE_TEXT",
    "PRESS_KEY",
    "INVOKE",
    "TOGGLE",
    "EXPAND",
    "COLLAPSE",
    "SELECT",
    "SET_VALUE",
}

_IMAGE_ONLY_NAMES = {
    "LOCATE",
    "CLICK",
    "DOUBLE_CLICK",
    "RIGHT_CLICK",
    "HOVER",
    "TYPE_TEXT",
    "PRESS_KEY",
    "SCREENSHOT",
}


def test_capability_values_are_unique_and_stringify_to_their_wire_value() -> None:
    values = [capability.value for capability in Capability]

    assert len(values) == len(set(values))
    assert [str(capability) for capability in Capability] == values


def test_capability_registry_has_the_stable_public_order_and_values() -> None:
    assert [(capability.name, capability.value) for capability in Capability] == list(
        _CAPABILITY_SPECS
    )
    assert list(Capability.__members__) == [name for name, _ in _CAPABILITY_SPECS]
    assert all(isinstance(capability.value, str) for capability in Capability)


@pytest.mark.parametrize(("name", "value"), _CAPABILITY_SPECS)
def test_each_capability_supports_name_and_wire_value_lookup(name: str, value: str) -> None:
    member = getattr(Capability, name)

    assert Capability[name] is member
    assert Capability(value) is member
    assert member.name == name
    assert member.value == value
    assert str(member) == value
    assert format(member) == value


def test_all_capabilities_contains_every_enum_member() -> None:
    assert ALL_CAPABILITIES == frozenset(Capability)


def test_capability_presets_are_immutable_sets_of_capability_members() -> None:
    module = importlib.import_module("dolphin_desktop._capabilities")

    for preset in (ALL_CAPABILITIES, STANDARD_ACCESSIBILITY, IMAGE_ONLY):
        assert type(preset) is frozenset
        assert all(isinstance(capability, Capability) for capability in preset)
        with pytest.raises(AttributeError):
            preset.add(Capability.LOCATE)  # type: ignore[attr-defined]

    assert module.ALL_CAPABILITIES is ALL_CAPABILITIES
    assert module.STANDARD_ACCESSIBILITY is STANDARD_ACCESSIBILITY
    assert module.IMAGE_ONLY is IMAGE_ONLY


def test_standard_accessibility_contract_has_exact_members() -> None:
    assert STANDARD_ACCESSIBILITY <= ALL_CAPABILITIES
    assert {capability.name for capability in STANDARD_ACCESSIBILITY} == (
        _STANDARD_ACCESSIBILITY_NAMES
    )
    assert STANDARD_ACCESSIBILITY == frozenset(
        getattr(Capability, name) for name in _STANDARD_ACCESSIBILITY_NAMES
    )
    assert Capability.SCREENSHOT not in STANDARD_ACCESSIBILITY
    assert Capability.DRAG not in STANDARD_ACCESSIBILITY
    assert Capability.SCROLL not in STANDARD_ACCESSIBILITY


def test_image_only_contract_contains_no_tree_or_programmatic_actions() -> None:
    assert IMAGE_ONLY <= ALL_CAPABILITIES
    assert {capability.name for capability in IMAGE_ONLY} == _IMAGE_ONLY_NAMES
    assert IMAGE_ONLY == frozenset(getattr(Capability, name) for name in _IMAGE_ONLY_NAMES)
    assert not IMAGE_ONLY & {
        Capability.GET_TREE,
        Capability.READ_TEXT,
        Capability.READ_STATE,
        Capability.INVOKE,
        Capability.TOGGLE,
        Capability.EXPAND,
        Capability.COLLAPSE,
        Capability.SELECT,
        Capability.SET_VALUE,
    }
    assert not IMAGE_ONLY & {
        Capability.READ_STATE,
        Capability.DRAG,
        Capability.SCROLL,
        Capability.TOGGLE,
    }
