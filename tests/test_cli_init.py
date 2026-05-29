"""Unit tests for `dolphin init` project scaffolding."""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock

import pytest

from dolphin_desktop._cli import _doctor_cmd, _scaffold

# ---------------------------------------------------------------------------
# minimal template
# ---------------------------------------------------------------------------


def test_minimal_creates_expected_files(tmp_path):
    target = tmp_path / "myproj"
    _scaffold(target, "myproj", "minimal", "3.11")

    assert (target / "pyproject.toml").is_file()
    assert (target / "conftest.py").is_file()
    assert (target / "tests" / "test_sample.py").is_file()
    # objects/ should NOT exist in minimal
    assert not (target / "objects").exists()


def test_minimal_pyproject_contains_name(tmp_path):
    target = tmp_path / "hello-tests"
    _scaffold(target, "hello-tests", "minimal", "3.12")

    content = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "hello-tests"' in content
    assert 'requires-python = ">=3.12"' in content


def test_minimal_pyproject_has_pytest_config(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "minimal", "3.11")

    content = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "testpaths" in content
    assert 'pythonpath = ["."]' in content


def test_minimal_test_file_imports_pytest(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "minimal", "3.11")

    content = (target / "tests" / "test_sample.py").read_text(encoding="utf-8")
    assert "import pytest" in content
    assert "launch" in content
    assert "notepad.exe" in content


# ---------------------------------------------------------------------------
# standard template
# ---------------------------------------------------------------------------


def test_standard_creates_objects_dir(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "standard", "3.11")

    assert (target / "objects").is_dir()
    assert (target / "objects" / "notepad_page.py").is_file()
    assert (target / "objects" / "__init__.py").is_file()
    assert (target / "tests" / "__init__.py").is_file()


def test_standard_notepad_page_has_expected_api(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "standard", "3.11")

    content = (target / "objects" / "notepad_page.py").read_text(encoding="utf-8")
    assert "class NotepadPage" in content
    assert "def type_text" in content
    assert "def read_text" in content
    assert "def clear" in content


def test_standard_test_uses_page_object(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "standard", "3.11")

    content = (target / "tests" / "test_sample.py").read_text(encoding="utf-8")
    assert "NotepadPage" in content
    assert "from objects.notepad_page import NotepadPage" in content


def test_standard_pyproject_has_ruff(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "standard", "3.11")

    content = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "ruff" in content


# ---------------------------------------------------------------------------
# enterprise template
# ---------------------------------------------------------------------------


def test_enterprise_creates_github_actions(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "enterprise", "3.11")

    ci = target / ".github" / "workflows" / "ci.yml"
    assert ci.is_file()
    content = ci.read_text(encoding="utf-8")
    assert "windows-latest" in content
    assert "pytest" in content


def test_enterprise_has_gitignore(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "enterprise", "3.11")

    gi = target / ".gitignore"
    assert gi.is_file()
    content = gi.read_text(encoding="utf-8")
    assert "__pycache__" in content
    assert "dolphin-traces" in content


def test_enterprise_has_pre_commit(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "enterprise", "3.11")

    pc = target / ".pre-commit-config.yaml"
    assert pc.is_file()
    content = pc.read_text(encoding="utf-8")
    assert "ruff" in content


def test_enterprise_pyproject_has_allure(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "enterprise", "3.11")

    content = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "allure-pytest" in content
    assert "allure-results" in content


def test_enterprise_allure_results_dir_exists(tmp_path):
    target = tmp_path / "proj"
    _scaffold(target, "proj", "enterprise", "3.11")

    assert (target / "allure-results").is_dir()


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------


def test_scaffold_raises_on_unknown_template(tmp_path):
    target = tmp_path / "proj"
    with pytest.raises(ValueError, match="Unknown template"):
        _scaffold(target, "proj", "bogus", "3.11")


def test_scaffold_nested_project_name(tmp_path):
    target = tmp_path / "my-cool-tests"
    _scaffold(target, "my-cool-tests", "minimal", "3.11")

    content = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "my-cool-tests"' in content


def test_scaffold_does_not_overwrite_existing(tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    # _scaffold should fail since mkdir(parents=True) is called on existing dir
    with pytest.raises(FileExistsError):
        _scaffold(target, "proj", "minimal", "3.11")


# ---------------------------------------------------------------------------
# doctor — encoding safety
# ---------------------------------------------------------------------------


def test_doctor_does_not_crash_on_ascii_only_stdout(monkeypatch):
    """doctor must not raise UnicodeEncodeError on consoles without Unicode support (cp1250)."""
    import argparse

    import pywinauto

    ascii_stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="replace")
    monkeypatch.setattr(sys, "stdout", ascii_stream)
    monkeypatch.setattr(pywinauto, "Desktop", MagicMock(return_value=MagicMock(windows=lambda: [])))

    # Should not raise even though status symbols are not encodable in ASCII.
    _doctor_cmd(argparse.Namespace())
