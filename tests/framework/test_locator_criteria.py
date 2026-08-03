"""Locator criteria aliases — automation_id / role / name.

The YAML Object Repository always accepted the friendly spellings; the
Locator itself used to forward them verbatim into pywinauto, where an
unknown kwarg dissolved into a silent "not found". Now both layers speak
the same dialect.
"""

from __future__ import annotations

import pytest

from dolphin_desktop._locator import Locator


class _FakeWindow:
    def _get_spec(self):  # pragma: no cover — never resolved in these tests
        raise AssertionError("criteria tests must not resolve")


def test_automation_id_maps_to_auto_id():
    loc = Locator(_FakeWindow(), automation_id="txtUser")
    assert loc._criteria == {"auto_id": "txtUser"}


def test_role_and_name_map_to_control_type_and_title():
    loc = Locator(_FakeWindow(), role="Edit", name="Username")
    assert loc._criteria == {"control_type": "Edit", "title": "Username"}


def test_canonical_keys_pass_through_unchanged():
    loc = Locator(_FakeWindow(), auto_id="a", control_type="Button", title="OK")
    assert loc._criteria == {"auto_id": "a", "control_type": "Button", "title": "OK"}


def test_conflicting_alias_and_canonical_raise():
    with pytest.raises(ValueError, match="alias"):
        Locator(_FakeWindow(), automation_id="a", auto_id="b")


def test_alias_agreeing_with_canonical_collapses():
    loc = Locator(_FakeWindow(), automation_id="a", auto_id="a")
    assert loc._criteria == {"auto_id": "a"}


def test_fallback_criteria_are_normalized_too():
    loc = Locator(
        _FakeWindow(),
        auto_id="primary",
        fallback=[{"automation_id": "fb1"}, {"name": "Save", "role": "Button"}],
    )
    assert loc._fallback == [
        {"auto_id": "fb1"},
        {"title": "Save", "control_type": "Button"},
    ]


def test_clone_keeps_normalized_form():
    loc = Locator(_FakeWindow(), automation_id="x").timeout(5)
    assert loc._criteria == {"auto_id": "x"}
