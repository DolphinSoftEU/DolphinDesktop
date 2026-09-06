"""Focused cross-install contract for the two pytest distributions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.windows_component, pytest.mark.slow, pytest.mark.timeout(300)]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STUB_ROOT = _REPO_ROOT / "tests" / "fixtures" / "pytest_dolphinsoft_stub"
_SCRUBBED_ENV = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PYTHONNOUSERSITE",
        "VIRTUAL_ENV",
        "VIRTUAL_ENV_PROMPT",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_TARGET",
        "PIP_USER",
        "PIP_PREFIX",
        "PIP_REQUIRE_VIRTUALENV",
        "PIP_CONFIG_FILE",
    }
)


def _env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in _SCRUBBED_ENV}


def _build_wheel(project: Path, output: Path) -> Path:
    before = set(output.glob("*.whl"))
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(output), str(project)],
        capture_output=True,
        text=True,
        timeout=180,
        env=_env(),
    )
    assert result.returncode == 0, f"wheel build failed:\n{result.stdout}\n{result.stderr}"
    new = set(output.glob("*.whl")) - before
    assert len(new) == 1
    return next(iter(new))


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def test_installed_plugins_expose_dolphin_options(tmp_path: Path) -> None:
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    stub = _build_wheel(_STUB_ROOT, wheels)
    desktop = _build_wheel(_REPO_ROOT, wheels)

    venv = tmp_path / ".venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        capture_output=True,
        text=True,
        timeout=60,
        env=_env(),
    )
    assert created.returncode == 0, created.stderr
    python = _venv_python(venv)
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", str(stub), str(desktop)],
        capture_output=True,
        text=True,
        timeout=180,
        env=_env(),
    )
    assert install.returncode == 0, install.stderr

    probe = subprocess.run(
        [str(python), "-m", "pytest", "--help", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=_env(),
    )
    assert probe.returncode == 0, probe.stderr
    for option in ("--dolphin-backend", "--dolphin-timeout", "--dolphin-retry"):
        assert option in probe.stdout
