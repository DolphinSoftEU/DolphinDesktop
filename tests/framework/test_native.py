"""Tests for :mod:`dolphin_desktop._native`.

``load_trusted_dll`` is the single chokepoint every native library load in
dolphin goes through (KAN-473): it refuses anything but an absolute path to
an existing file, and loads it with a restricted search order so a planted
DLL on the working directory or ``PATH`` can never be picked up. These tests
never load a real DLL — ``ctypes.WinDLL`` is monkeypatched so both the
success and failure paths of the actual load are exercised without touching
the OS loader.
"""

from __future__ import annotations

import ctypes
from unittest.mock import Mock

import pytest

from dolphin_desktop._native import (
    TRUSTED_LOAD_FLAGS,
    NativeLibraryError,
    existing_candidates,
    load_trusted_dll,
    program_files_dirs,
    system32_dir,
)

# --------------------------------------------------------------------------- #
# system32_dir                                                                #
# --------------------------------------------------------------------------- #


def test_system32_dir_uses_systemroot_when_set(monkeypatch) -> None:
    monkeypatch.setenv("SystemRoot", r"C:\CustomWindows")
    monkeypatch.delenv("WINDIR", raising=False)
    assert system32_dir() == r"C:\CustomWindows\System32"


def test_system32_dir_falls_back_to_windir(monkeypatch) -> None:
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.setenv("WINDIR", r"D:\Win")
    assert system32_dir() == r"D:\Win\System32"


def test_system32_dir_defaults_when_neither_env_var_is_set(monkeypatch) -> None:
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.delenv("WINDIR", raising=False)
    assert system32_dir() == r"C:\Windows\System32"


# --------------------------------------------------------------------------- #
# program_files_dirs                                                          #
# --------------------------------------------------------------------------- #


def test_program_files_dirs_collects_distinct_roots_in_order(monkeypatch) -> None:
    monkeypatch.setenv("ProgramW6432", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    # ProgramW6432 and ProgramFiles are the same value here, so the second
    # must be deduplicated rather than appended twice.
    assert program_files_dirs() == [r"C:\Program Files", r"C:\Program Files (x86)"]


def test_program_files_dirs_skips_unset_variables(monkeypatch) -> None:
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    assert program_files_dirs() == [r"C:\Program Files"]


def test_program_files_dirs_empty_when_nothing_set(monkeypatch) -> None:
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    assert program_files_dirs() == []


# --------------------------------------------------------------------------- #
# existing_candidates                                                         #
# --------------------------------------------------------------------------- #


def test_existing_candidates_skips_falsy_directories(tmp_path) -> None:
    (tmp_path / "found.dll").write_bytes(b"MZ")
    # An empty directory entry (e.g. an unset env var upstream) must be
    # skipped rather than raise inside os.path.join.
    found = existing_candidates(["", str(tmp_path)], ["found.dll"])
    assert found == [str(tmp_path / "found.dll")]


def test_existing_candidates_preserves_directory_then_name_order(tmp_path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "x.dll").write_bytes(b"MZ")
    (dir_b / "x.dll").write_bytes(b"MZ")
    (dir_b / "y.dll").write_bytes(b"MZ")
    found = existing_candidates([str(dir_a), str(dir_b)], ["x.dll", "y.dll"])
    assert found == [str(dir_a / "x.dll"), str(dir_b / "x.dll"), str(dir_b / "y.dll")]


# --------------------------------------------------------------------------- #
# load_trusted_dll                                                            #
# --------------------------------------------------------------------------- #


def test_load_trusted_dll_refuses_an_empty_path() -> None:
    with pytest.raises(NativeLibraryError, match="no path given"):
        load_trusted_dll("", what="test DLL")


def test_load_trusted_dll_refuses_a_non_string_path() -> None:
    with pytest.raises(NativeLibraryError, match="no path given"):
        load_trusted_dll(None, what="test DLL")  # type: ignore[arg-type]


def test_load_trusted_dll_succeeds_with_the_restricted_search_flags(monkeypatch, tmp_path) -> None:
    dll_path = tmp_path / "trusted.dll"
    dll_path.write_bytes(b"MZ")
    fake_handle = Mock()
    win_dll = Mock(return_value=fake_handle)
    monkeypatch.setattr(ctypes, "WinDLL", win_dll)

    result = load_trusted_dll(str(dll_path), what="trusted DLL")

    assert result is fake_handle
    win_dll.assert_called_once()
    args, kwargs = win_dll.call_args
    assert args[0] == str(dll_path)
    assert kwargs["winmode"] == TRUSTED_LOAD_FLAGS


def test_load_trusted_dll_wraps_a_loader_failure(monkeypatch, tmp_path) -> None:
    dll_path = tmp_path / "broken.dll"
    dll_path.write_bytes(b"MZ")
    monkeypatch.setattr(
        ctypes, "WinDLL", Mock(side_effect=OSError("The specified module could not be found"))
    )

    with pytest.raises(NativeLibraryError, match="LoadLibraryEx"):
        load_trusted_dll(str(dll_path), what="broken DLL")
