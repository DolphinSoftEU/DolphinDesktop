"""Tests for the top-level public package contract."""

import importlib
import warnings
from importlib.metadata import version

import dolphin_desktop


def test_every_declared_public_name_is_available() -> None:
    """The documented facade must not advertise stale or private names."""
    assert dolphin_desktop.__all__
    assert all(hasattr(dolphin_desktop, name) for name in dolphin_desktop.__all__)


def test_package_version_matches_metadata() -> None:
    """The public version must match the installed distribution metadata."""
    assert dolphin_desktop.__version__ == version("dolphin-desktop")


def test_top_level_reexports_keep_their_canonical_objects() -> None:
    """Consumers can use the facade without importing implementation modules."""
    from dolphin_desktop import _capabilities, _oracle_forms

    assert dolphin_desktop.Capability is _capabilities.Capability
    assert dolphin_desktop.OracleFormsApp is _oracle_forms.OracleFormsApp
    assert dolphin_desktop.OracleFormsKey is _oracle_forms.OracleFormsKey


def test_non_windows_import_warns_about_backend_support(monkeypatch) -> None:
    monkeypatch.setattr(dolphin_desktop._platform, "system", lambda: "Linux")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.reload(dolphin_desktop)

    assert any("designed for Windows" in str(warning.message) for warning in caught)
