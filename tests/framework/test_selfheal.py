"""Tests for :mod:`dolphin_desktop._selfheal`."""

from __future__ import annotations

import errno
import math
import multiprocessing
import os
from pathlib import Path

import pytest


def test_windows_retry_retries_only_transient_contention(monkeypatch) -> None:
    from dolphin_desktop import _selfheal

    monkeypatch.setattr(_selfheal.os, "name", "nt")
    monkeypatch.setattr(_selfheal.time, "sleep", lambda _: None)
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(errno.EACCES, "temporarily locked")
        return "ok"

    assert _selfheal._retry_windows_contention(operation) == "ok"
    assert attempts == 2


def test_windows_access_denied_does_not_retry_as_contention(monkeypatch) -> None:
    from dolphin_desktop import _selfheal

    monkeypatch.setattr(_selfheal.os, "name", "nt")
    error = OSError(errno.EACCES, "Access is denied", None, 5)
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise error

    assert not _selfheal._is_windows_contention(error)
    with pytest.raises(OSError, match="Access is denied"):
        _selfheal._retry_windows_contention(operation)
    assert attempts == 1


def test_windows_retry_propagates_persistent_errors_without_retry(monkeypatch) -> None:
    from dolphin_desktop import _selfheal

    monkeypatch.setattr(_selfheal.os, "name", "nt")
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise OSError(errno.EINVAL, "persistent failure")

    with pytest.raises(OSError, match="persistent failure"):
        _selfheal._retry_windows_contention(operation)
    assert attempts == 1


def test_windows_retry_stops_after_its_bounded_budget(monkeypatch) -> None:
    from dolphin_desktop import _selfheal

    monkeypatch.setattr(_selfheal.os, "name", "nt")
    monkeypatch.setattr(_selfheal, "_WINDOWS_RETRY_TIMEOUT", 0.0)
    error = OSError(errno.EACCES, "temporarily locked")
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise error

    with pytest.raises(OSError, match="temporarily locked"):
        _selfheal._retry_windows_contention(operation)
    assert attempts == 1


def _record_selfheal_events(
    path: str,
    worker_id: int,
    count: int,
    ready: multiprocessing.Queue,
    start: multiprocessing.Event,
) -> None:
    os.environ["DOLPHIN_SELFHEAL_FILE"] = path
    from dolphin_desktop._selfheal import record_fallback

    ready.put(worker_id)
    assert start.wait(10)
    for sequence in range(count):
        record_fallback(
            {"auto_id": f"primary-{worker_id}-{sequence}"},
            {"auto_id": f"fallback-{worker_id}-{sequence}"},
            test_name=f"worker-{worker_id}-{sequence}",
        )


def _assert_complete_records(
    records: list[dict[str, object]],
    worker_count: int,
    records_per_worker: int,
) -> None:
    expected = {
        f"primary-{worker_id}-{sequence}": {
            "test": f"worker-{worker_id}-{sequence}",
            "primary": {"auto_id": f"primary-{worker_id}-{sequence}"},
            "fallback": {"auto_id": f"fallback-{worker_id}-{sequence}"},
        }
        for worker_id in range(worker_count)
        for sequence in range(records_per_worker)
    }
    assert len(records) == len(expected)
    actual = {record["primary"]["auto_id"]: record for record in records}
    assert set(actual) == set(expected)

    for primary_auto_id, record in actual.items():
        assert set(record) == {"ts", "test", "primary", "fallback"}
        timestamp = record["ts"]
        assert isinstance(timestamp, (int, float))
        assert not isinstance(timestamp, bool)
        assert math.isfinite(timestamp)
        expected_record = expected[primary_auto_id]
        assert record["test"] == expected_record["test"]
        assert record["primary"] == expected_record["primary"]
        assert record["fallback"] == expected_record["fallback"]


def test_selfheal_records_are_complete_across_processes(tmp_path: Path) -> None:
    from dolphin_desktop._selfheal import selfheal_stats

    path = tmp_path / "selfheal.jsonl"
    worker_count = 4
    records_per_worker = 25
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    processes = [
        context.Process(
            target=_record_selfheal_events,
            args=(str(path), worker_id, records_per_worker, ready, start),
        )
        for worker_id in range(worker_count)
    ]

    try:
        for process in processes:
            process.start()
        assert {ready.get(timeout=10) for _ in processes} == set(range(worker_count))
        start.set()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0

        records = selfheal_stats(n=worker_count * records_per_worker, file=path)
        _assert_complete_records(records, worker_count, records_per_worker)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)


def test_selfheal_stats_returns_only_the_requested_recent_entries(tmp_path: Path) -> None:
    from dolphin_desktop._selfheal import selfheal_stats

    journal = tmp_path / "events.jsonl"
    journal.write_text('{"test":"first"}\n{"test":"second"}\n', encoding="utf-8")
    assert selfheal_stats(1, file=journal) == [{"test": "second"}]


def test_selfheal_stats_missing_file_does_not_create_lock_sidecar(
    tmp_path: Path,
) -> None:
    from dolphin_desktop._selfheal import selfheal_stats

    journal = tmp_path / "missing.jsonl"
    assert selfheal_stats(file=journal) == []
    assert not journal.exists()
    assert not journal.with_name(journal.name + ".lock").exists()


def test_selfheal_records_redacted_jsonl(tmp_path: Path, monkeypatch) -> None:
    from dolphin_desktop._selfheal import record_fallback, selfheal_stats

    path = tmp_path / "selfheal.jsonl"
    monkeypatch.setenv("DOLPHIN_SELFHEAL_FILE", str(path))
    record_fallback({"title": "token=secret"}, {"title": "Save"}, test_name="unit")
    event = selfheal_stats(file=path)[0]
    assert event["test"] == "unit"
    assert "secret" not in event["primary"]["title"]
