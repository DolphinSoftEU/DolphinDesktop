"""Tests for the public ``dolphin_desktop.spy`` facade."""

import dolphin_desktop._spy as implementation
import dolphin_desktop.spy as public_spy


def test_public_spy_exports_the_complete_supported_api() -> None:
    expected = {
        "SCHEMA_VERSION",
        "format_sap_tree",
        "format_tree",
        "inspect",
        "pick",
        "sap_inspect",
        "sap_pick",
    }

    assert set(public_spy.__all__) == expected
    assert all(hasattr(public_spy, name) for name in expected)


def test_public_spy_reexports_the_implementation_objects() -> None:
    for name in public_spy.__all__:
        assert getattr(public_spy, name) is getattr(implementation, name)
