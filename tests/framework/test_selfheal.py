"""Tests for :mod:`dolphin_desktop._selfheal`."""

from __future__ import annotations

from pathlib import Path


def test_selfheal_stats_returns_only_the_requested_recent_entries(tmp_path: Path) -> None:
    from dolphin_desktop._selfheal import selfheal_stats

    journal = tmp_path / "events.jsonl"
    journal.write_text('{"test":"first"}\n{"test":"second"}\n', encoding="utf-8")
    assert selfheal_stats(1, file=journal) == [{"test": "second"}]


def test_selfheal_records_redacted_jsonl(tmp_path: Path, monkeypatch) -> None:
    from dolphin_desktop._selfheal import record_fallback, selfheal_stats

    path = tmp_path / "selfheal.jsonl"
    monkeypatch.setenv("DOLPHIN_SELFHEAL_FILE", str(path))
    record_fallback({"title": "token=secret"}, {"title": "Save"}, test_name="unit")
    event = selfheal_stats(file=path)[0]
    assert event["test"] == "unit"
    assert "secret" not in event["primary"]["title"]
