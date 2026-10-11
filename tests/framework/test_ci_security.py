"""KAN-474: enforce CI permissions and the release security dependency graph."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    # BaseLoader keeps GitHub's `on` key a string (YAML 1.1 calls it a boolean).
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


@pytest.mark.parametrize("name", ["ci.yml", "docs.yml", "release.yml"])
def test_permissions_and_immutable_actions(name: str) -> None:
    workflow = load(name)
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in workflow["on"]
    for job_id, job in workflow["jobs"].items():
        permissions = job.get("permissions", workflow["permissions"])
        if name == "release.yml" and job_id.startswith("publish-"):
            assert permissions == {"id-token": "write"}
            assert job["environment"] in {"pypi", "testpypi"}
        elif name == "docs.yml" and job_id == "deploy":
            assert permissions == {"contents": "write"}
            assert job["if"] == ("github.event_name == 'push' && github.ref == 'refs/heads/master'")
        else:
            assert permissions == {"contents": "read"}
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", step["uses"])
            if step.get("uses", "").startswith("actions/checkout@"):
                expected = "true" if name == "docs.yml" and job_id == "deploy" else "false"
                assert step["with"]["persist-credentials"] == expected
            if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                assert re.fullmatch(r"\d+\.\d+\.\d+", step["with"]["version"])


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_security_checks_block_publication_for_the_same_revision(name: str) -> None:
    workflow = load(name)
    security = workflow["jobs"]["security"]
    assert set(security["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest"}
    assert "if" not in security
    assert "continue-on-error" not in security
    commands = "\n".join(step.get("run", "") for step in security["steps"])
    assert "uv sync --locked --only-group security" in commands
    assert "uv export --locked" in commands
    assert "--all-extras --all-groups" in commands
    assert "pip-audit" in commands and "--strict" in commands
    assert "bandit -r src/dolphin_desktop -ll" in commands
    assert "python .github/scripts/scan_secrets.py" in commands
    for step in security["steps"]:
        assert "if" not in step
        assert "continue-on-error" not in step
        assert "ref" not in step.get("with", {})
    if name == "release.yml":
        jobs = workflow["jobs"]
        assert jobs["build"]["needs"] == "security"
        assert "ref" not in jobs["build"]["steps"][0]["with"]
        for job_id in ("publish-pypi", "publish-testpypi"):
            assert jobs[job_id]["needs"] == "build"
            assert "always()" not in jobs[job_id]["if"]
            assert not any("run" in step for step in jobs[job_id]["steps"])
            assert jobs[job_id]["steps"][0]["with"]["name"] == "dolphin-desktop-dist"


def test_event_coverage() -> None:
    ci = load("ci.yml")
    assert set(ci["on"]) == {"push", "pull_request"}
    for event in ci["on"].values():
        assert set(event["branches"]) == {"master", "testpypi-preview"}
    release = load("release.yml")
    assert set(release["on"]) == {"push", "workflow_dispatch"}
    assert release["on"]["push"]["tags"] == ["v*.*.*"]


@pytest.mark.parametrize("name", ["ci.yml", "docs.yml", "release.yml"])
def test_dependency_commands_cannot_update_the_lock(name: str) -> None:
    for job in load(name)["jobs"].values():
        for step in job["steps"]:
            command = step.get("run", "")
            assert "uvx" not in command
            assert "--with " not in command
            assert "pip install --upgrade" not in command
            for line in command.splitlines():
                if "uv sync " in line or "uv export " in line:
                    assert "--locked" in line
                if "uv run " in line:
                    assert "--locked" in line or "--no-sync" in line
                if "python -m build" in line:
                    assert "--no-isolation" in line


def test_stale_lockfile_fails_without_being_rewritten(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv, "The CI policy tests require uv"
    for filename in ("pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / filename, tmp_path / filename)
    lock = tmp_path / "uv.lock"
    original = lock.read_bytes()
    project = tmp_path / "pyproject.toml"
    current_version = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
    project.write_text(
        project.read_text(encoding="utf-8").replace(
            f'version = "{current_version}"', 'version = "0.0.0"'
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [uv, "sync", "--locked", "--only-group", "security", "--offline"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "lockfile" in result.stderr.lower()
    assert lock.read_bytes() == original
