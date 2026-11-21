"""Unit tests for the explicit environment preflight dispatcher."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import tests._preflight as preflight


def test_component_preflight_is_disabled_without_its_environment_flag(monkeypatch) -> None:
    monkeypatch.delenv("DOLPHIN_COMPONENT_PREFLIGHT", raising=False)
    check = Mock()
    monkeypatch.setattr(preflight, "_preflight_component", check)

    preflight.run_preflight()

    check.assert_not_called()


def test_component_preflight_accepts_an_available_component(monkeypatch) -> None:
    monkeypatch.setenv("DOLPHIN_COMPONENT_PREFLIGHT", "1")
    monkeypatch.setattr(preflight, "_is_windows", lambda: True)
    monkeypatch.setattr(preflight, "_find_ws3270", lambda: r"C:\wc3270\ws3270.exe")

    preflight.run_preflight()


def test_component_preflight_rejects_a_missing_component(monkeypatch) -> None:
    monkeypatch.setenv("DOLPHIN_COMPONENT_PREFLIGHT", "1")
    monkeypatch.setattr(preflight, "_is_windows", lambda: True)
    monkeypatch.setattr(preflight, "_find_ws3270", lambda: None)

    with pytest.raises(pytest.UsageError, match="requires wc3270"):
        preflight.run_preflight()


def test_invalid_component_path_is_not_treated_as_available(monkeypatch) -> None:
    monkeypatch.setenv("DOLPHIN_WS3270_PATH", r"C:\missing\ws3270.exe")
    monkeypatch.setattr(preflight, "_path_exists", lambda _path: False)
    monkeypatch.setattr(
        preflight,
        "_env_var",
        lambda name: r"C:\missing\ws3270.exe" if name == "DOLPHIN_WS3270_PATH" else None,
    )

    assert preflight._find_ws3270() is None


def test_required_preflight_propagates_failure_as_usage_error(monkeypatch) -> None:
    monkeypatch.setenv("DOLPHIN_COMPONENT_PREFLIGHT", "1")
    failing = Mock(side_effect=RuntimeError("connection failed"))
    monkeypatch.setattr(preflight, "_preflight_component", failing)

    with pytest.raises(pytest.UsageError, match="connection failed"):
        preflight.run_preflight()
