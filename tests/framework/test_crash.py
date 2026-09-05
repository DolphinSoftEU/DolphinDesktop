"""Tests for :mod:`dolphin_desktop._crash`."""

from __future__ import annotations


def test_crash_pid_selection_combines_owned_and_attached_processes(monkeypatch) -> None:
    from dolphin_desktop import _application
    from dolphin_desktop._crash import _target_pids

    monkeypatch.setattr(_application, "_live_pids", {1})
    monkeypatch.setattr(_application, "_session_pids", {2})
    monkeypatch.setattr(_application, "_attached_pids", {3})
    assert _target_pids(None) == {1, 2, 3}
    assert _target_pids(42) == {42}


def test_crash_stack_formatter_handles_missing_and_real_exceptions() -> None:
    from dolphin_desktop._crash import _fmt_stack

    assert isinstance(_fmt_stack(None), str)
    assert "ValueError: bad" in _fmt_stack(ValueError("bad"))
