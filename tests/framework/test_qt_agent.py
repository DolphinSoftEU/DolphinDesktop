"""Tests for bundled Qt agent resource selection."""

from pathlib import Path

import pytest

import dolphin_desktop._qt_agent as qt_agent


@pytest.mark.parametrize(("version", "attribute"), [("5", "QT5_AGENT_DLL"), ("6", "QT6_AGENT_DLL")])
def test_agent_dll_for_returns_the_requested_existing_dll(
    tmp_path, monkeypatch, version, attribute
) -> None:
    expected = tmp_path / f"qt{version}.dll"
    expected.touch()
    monkeypatch.setattr(qt_agent, attribute, expected)

    assert qt_agent.agent_dll_for(version, verify=False) == expected


def test_agent_dll_for_rejects_an_unknown_qt_version() -> None:
    with pytest.raises(ValueError, match="unknown Qt version: '7'"):
        qt_agent.agent_dll_for("7")


def test_agent_dll_for_reports_a_missing_bundled_dll(tmp_path, monkeypatch) -> None:
    missing = Path(tmp_path / "missing.dll")
    monkeypatch.setattr(qt_agent, "QT5_AGENT_DLL", missing)

    with pytest.raises(FileNotFoundError, match="Qt 5 agent DLL missing"):
        qt_agent.agent_dll_for("5")


def test_manifest_rejects_a_non_object_top_level_value(tmp_path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(qt_agent, "AGENT_MANIFEST", manifest)

    with pytest.raises(qt_agent.AgentIntegrityError, match="invalid top-level value"):
        qt_agent._load_manifest()


def test_manifest_rejects_a_dll_without_an_entry(tmp_path, monkeypatch) -> None:
    dll = tmp_path / "unregistered.dll"
    dll.write_bytes(b"stub")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(qt_agent, "AGENT_MANIFEST", manifest)

    with pytest.raises(qt_agent.AgentIntegrityError, match="no integrity record"):
        qt_agent.verify_agent_dll(dll)


@pytest.mark.parametrize(
    "entry",
    [
        '{"sha256": "not-a-sha256", "size": 4}',
        '{"sha256": "' + "a" * 64 + '", "size": true}',
    ],
    ids=["malformed-hash", "boolean-size"],
)
def test_manifest_rejects_incomplete_integrity_records(tmp_path, monkeypatch, entry) -> None:
    dll = tmp_path / "agent.dll"
    dll.write_bytes(b"stub")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(f'{{"agent.dll": {entry}}}', encoding="utf-8")
    monkeypatch.setattr(qt_agent, "AGENT_MANIFEST", manifest)

    with pytest.raises(qt_agent.AgentIntegrityError, match="incomplete integrity record"):
        qt_agent.verify_agent_dll(dll)


def test_manifest_reports_a_dll_that_cannot_be_read(tmp_path, monkeypatch) -> None:
    dll = tmp_path / "agent.dll"
    dll.write_bytes(b"stub")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"agent.dll": {"sha256": "' + "a" * 64 + '", "size": 4}}', encoding="utf-8"
    )
    monkeypatch.setattr(qt_agent, "AGENT_MANIFEST", manifest)
    original_stat = Path.stat

    def fail_for_dll(path, *args, **kwargs):
        if path == dll:
            raise PermissionError("access denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_for_dll)
    with pytest.raises(qt_agent.AgentIntegrityError, match="cannot read agent DLL"):
        qt_agent.verify_agent_dll(dll)
