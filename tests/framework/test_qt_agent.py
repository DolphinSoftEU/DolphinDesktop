"""Tests for bundled Qt agent resource selection."""

from pathlib import Path

import pytest

import dolphin_desktop._qt_agent as qt_agent


@pytest.mark.parametrize(
    ("version", "attribute"), [("5", "QT5_AGENT_DLL"), ("6", "QT6_AGENT_DLL")]
)
def test_agent_dll_for_returns_the_requested_existing_dll(
    tmp_path, monkeypatch, version, attribute
) -> None:
    expected = tmp_path / f"qt{version}.dll"
    expected.touch()
    monkeypatch.setattr(qt_agent, attribute, expected)

    assert qt_agent.agent_dll_for(version) == expected


def test_agent_dll_for_rejects_an_unknown_qt_version() -> None:
    with pytest.raises(ValueError, match="unknown Qt version: '7'"):
        qt_agent.agent_dll_for("7")


def test_agent_dll_for_reports_a_missing_bundled_dll(tmp_path, monkeypatch) -> None:
    missing = Path(tmp_path / "missing.dll")
    monkeypatch.setattr(qt_agent, "QT5_AGENT_DLL", missing)

    with pytest.raises(FileNotFoundError, match="Qt 5 agent DLL missing"):
        qt_agent.agent_dll_for("5")
