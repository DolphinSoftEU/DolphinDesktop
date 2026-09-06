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


def test_sap_preflight_rejects_missing_required_environment(monkeypatch) -> None:
    monkeypatch.setenv("DOLPHIN_SAP_PREFLIGHT", "1")
    monkeypatch.setattr(preflight, "_sap_has_ready_session", lambda: False)
    for name in ("DOLPHIN_SAP_CONNECTION", "DOLPHIN_SAP_USER", "DOLPHIN_SAP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(pytest.UsageError, match="DOLPHIN_SAP_CONNECTION"):
        preflight.run_preflight()


def test_sap_preflight_accepts_a_ready_attached_session(monkeypatch) -> None:
    monkeypatch.setenv("DOLPHIN_SAP_PREFLIGHT", "1")
    monkeypatch.setattr(preflight, "_sap_has_ready_session", lambda: True)
    for name in ("DOLPHIN_SAP_CONNECTION", "DOLPHIN_SAP_USER", "DOLPHIN_SAP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    preflight.run_preflight()


def test_required_preflight_propagates_failure_as_usage_error(monkeypatch) -> None:
    monkeypatch.setenv("DOLPHIN_COMPONENT_PREFLIGHT", "1")
    failing = Mock(side_effect=RuntimeError("connection failed"))
    monkeypatch.setattr(preflight, "_preflight_component", failing)

    with pytest.raises(pytest.UsageError, match="connection failed"):
        preflight.run_preflight()


def test_one_external_stack_does_not_trigger_other_stack_checks(monkeypatch) -> None:
    monkeypatch.setenv("DOLPHIN_PUB400_PREFLIGHT", "1")
    monkeypatch.delenv("DOLPHIN_SAP_PREFLIGHT", raising=False)
    monkeypatch.delenv("DOLPHIN_STEAM_PREFLIGHT", raising=False)
    pub400 = Mock(side_effect=RuntimeError("pub400 failed"))
    sap = Mock()
    steam = Mock()
    monkeypatch.setattr(preflight, "_preflight_pub400", pub400)
    monkeypatch.setattr(preflight, "_preflight_sap", sap)
    monkeypatch.setattr(preflight, "_preflight_steam", steam)

    with pytest.raises(pytest.UsageError, match="pub400 failed"):
        preflight.run_preflight()

    pub400.assert_called_once_with()
    sap.assert_not_called()
    steam.assert_not_called()
