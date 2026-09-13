"""Focused cross-install contract for the two pytest distributions."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
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


def test_project_metadata_docs_and_extras_stay_consistent(tmp_path: Path) -> None:
    """DESKTOP-197: package metadata and documented isolated extra installs agree."""
    project = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["requires-python"] == ">=3.11,<3.14"

    documented = (_REPO_ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
    declared_extras = set(project["optional-dependencies"])
    extra_section = documented.split("## Optional Extras", 1)[1].split("## Stacks", 1)[0]
    documented_extras = set(re.findall(r"^\| `([^`]+)` \|", extra_section, re.MULTILINE))
    assert documented_extras == declared_extras

    for relative_path in (
        "README.md",
        "docs/faq.md",
        "docs/getting-started.md",
        "docs/installation.md",
        "docs/index.md",
        "docs/ci/index.md",
        "docs/ci/jenkins.md",
    ):
        text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "3.11" in text and "3.13" in text
        assert "3.11+" not in text and "3.11 or newer" not in text

    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = _build_wheel(_REPO_ROOT, wheels)
    for extra in sorted(declared_extras):
        venv = tmp_path / f"venv-{extra}"
        created = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            capture_output=True,
            text=True,
            timeout=60,
            env=_env(),
        )
        assert created.returncode == 0, created.stderr
        install = subprocess.run(
            [
                str(_venv_python(venv)),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--no-deps",
                f"{wheel}[{extra}]",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=_env(),
        )
        assert install.returncode == 0, f"extra {extra!r} failed: {install.stderr}"


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

    markers = subprocess.run(
        [str(python), "-m", "pytest", "--markers", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=_env(),
    )
    assert markers.returncode == 0, markers.stderr
    assert "dolphinsoft_stub" in markers.stdout

    imports = subprocess.run(
        [
            str(python),
            "-c",
            "import dolphin_desktop, dolphinsoft; "
            "print(dolphin_desktop.__file__); print(dolphinsoft.__file__)",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=_env(),
    )
    assert imports.returncode == 0, imports.stderr
    desktop_path, dolphinsoft_path = (
        Path(line).resolve() for line in imports.stdout.splitlines() if line.strip()
    )
    assert desktop_path.is_file()
    assert dolphinsoft_path.is_file()
    assert desktop_path.parent != dolphinsoft_path.parent
