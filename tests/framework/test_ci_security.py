"""KAN-474: enforce CI permissions and the release security dependency graph."""

from __future__ import annotations

import os
import re
import runpy
import shutil
import subprocess
import sys
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE_SCRIPT = ROOT / ".github" / "scripts" / "validate_release.py"
release_target = runpy.run_path(str(RELEASE_SCRIPT))["release_target"]


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
            assert permissions == {"id-token": "write", "contents": "read"}
            assert job["environment"] in {"pypi", "testpypi"}
        elif name == "docs.yml" and job_id == "deploy":
            assert permissions == {"contents": "write"}
            assert job["if"] == ("github.event_name == 'push' && github.ref == 'refs/heads/master'")
        elif name == "release.yml" and job_id == "build":
            assert permissions == {"contents": "read", "actions": "read", "pull-requests": "read"}
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
    assert_security_gate(load(name), release=name == "release.yml")


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_minimum_installations_block_distribution(name: str) -> None:
    jobs = load(name)["jobs"]
    job = jobs["minimum-dependencies"]
    assert job["runs-on"] == "windows-latest"
    assert job["strategy"]["matrix"] == {
        "python-version": ["3.11", "3.12", "3.13"],
        "profile": ["base", "all-extras"],
    }
    assert "if" not in job and "continue-on-error" not in job
    for step in job["steps"]:
        assert "continue-on-error" not in step
        if "run" in step:
            assert "if" not in step
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "uv sync --locked --only-group build --only-group security" in commands
    assert "check_minimum_dependencies.py" in commands
    assert "--all-extras" in commands
    upload = next(s for s in job["steps"] if s.get("name", "").startswith("Upload"))
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "artifacts/minimum/"
    consumer = jobs["build" if name == "release.yml" else "package"]
    assert "minimum-dependencies" in consumer["needs"]


def assert_security_gate(workflow: dict, *, release: bool) -> None:
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
        command = step.get("run", "")
        assert "||" not in command, "Security checks must not mask shell failures"
        assert not re.search(r"\bexit\s+0\b|\bset\s+\+e\b|\$LASTEXITCODE\s*=", command)
    if release:
        jobs = workflow["jobs"]
        assert set(jobs["build"]["needs"]) == {"security", "minimum-dependencies"}
        assert "continue-on-error" not in jobs["build"]
        assert "if" not in jobs["build"]
        assert "ref" not in jobs["build"]["steps"][0]["with"]
        for job_id in ("publish-pypi", "publish-testpypi"):
            assert set(jobs[job_id]["needs"]) == {"build", "smoke-local"}
            target = "pypi" if job_id == "publish-pypi" else "testpypi"
            assert jobs[job_id]["if"] == f"needs.build.outputs.target == '{target}'"
            verification = next(s for s in jobs[job_id]["steps"] if "run" in s)
            assert verification["run"] == "python .github/scripts/distribution_manifest.py verify"
            assert verification["env"]["MANIFEST_SHA256"] == (
                "${{ needs.build.outputs.manifest_sha256 }}"
            )
            download = next(
                s
                for s in jobs[job_id]["steps"]
                if s.get("uses", "").startswith("actions/download-artifact@")
            )
            assert download["with"]["artifact-ids"] == "${{ needs.build.outputs.artifact_id }}"


@pytest.mark.parametrize(
    "bypass",
    [
        "job-ignore",
        "step-ignore",
        "shell-ignore",
        "exit-zero",
        "build-needs",
        "publish-needs",
        "publish-always",
        "build-always",
    ],
)
def test_security_policy_rejects_gate_bypasses(bypass: str) -> None:
    workflow = deepcopy(load("release.yml"))
    jobs = workflow["jobs"]
    scan = next(
        step for step in jobs["security"]["steps"] if step.get("name") == "Static analysis (SAST)"
    )
    if bypass == "job-ignore":
        jobs["security"]["continue-on-error"] = "true"
    elif bypass == "step-ignore":
        scan["continue-on-error"] = "true"
    elif bypass == "shell-ignore":
        scan["run"] += " || true"
    elif bypass == "exit-zero":
        scan["run"] += "\nexit 0"
    elif bypass == "build-needs":
        jobs["build"]["needs"] = []
    elif bypass == "publish-needs":
        jobs["publish-pypi"]["needs"] = "security"
    elif bypass == "publish-always":
        jobs["publish-pypi"]["if"] = "always()"
    elif bypass == "build-always":
        jobs["build"]["if"] = "always()"
    with pytest.raises(AssertionError):
        assert_security_gate(workflow, release=True)


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
    # No registry packages, build backend, shared cache or interpreter downloads.
    # A path dependency makes the metadata change resolvable even while offline.
    dependency = tmp_path / "dependency"
    dependency.mkdir()
    (dependency / "pyproject.toml").write_text(
        '[project]\nname = "lock-fixture-dependency"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    project = tmp_path / "pyproject.toml"
    metadata = (
        '[project]\nname = "lock-fixture"\nversion = "1.0.0"\n'
        'requires-python = ">=3.11"\ndependencies = []\n'
        '[tool.uv.sources]\nlock-fixture-dependency = { path = "dependency" }\n'
    )
    project.write_text(metadata, encoding="utf-8")
    command = [
        uv,
        "--no-config",
        "--cache-dir",
        str(tmp_path / "empty-cache"),
        "lock",
        "--offline",
        "--python",
        sys.executable,
    ]

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*command, *arguments], cwd=tmp_path, capture_output=True, text=True, check=False
        )

    generated = run()
    assert generated.returncode == 0, generated.stderr
    valid = run("--check")
    assert valid.returncode == 0, valid.stderr
    lock = tmp_path / "uv.lock"
    original = lock.read_bytes()
    project.write_text(
        metadata.replace("dependencies = []", 'dependencies = ["lock-fixture-dependency==1.0.0"]'),
        encoding="utf-8",
    )
    result = run("--check")
    assert result.returncode == 1, result.stderr
    assert re.search(r"lockfile.*needs to be updated.*--check", result.stderr), result.stderr
    assert lock.read_bytes() == original
    # Updating the lock succeeds in the same offline environment: the failure
    # above cannot be attributed to unavailable packages or a broken fixture.
    updated = run()
    assert updated.returncode == 0, updated.stderr
    assert lock.read_bytes() != original
    assert run("--check").returncode == 0


def test_locked_qt5_runtime_has_a_windows_wheel() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    runtimes = [package for package in lock["package"] if package["name"] == "pyqt5-qt5"]
    windows_runtime = next(package for package in runtimes if package["version"] == "5.15.2")
    assert any("win_amd64.whl" in wheel["url"] for wheel in windows_runtime["wheels"])
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "PyQt5-Qt5==5.15.2; sys_platform == 'win32'" in project["dependency-groups"]["qt-tests"]


@pytest.mark.parametrize(
    ("version", "event", "ref", "requested", "expected"),
    [
        ("0.2.0", "workflow_dispatch", "refs/tags/v0.2.0", "pypi", "pypi"),
        ("0.2.0", "push", "refs/tags/v0.2.0", "", "pypi"),
        ("0.2.0rc1", "push", "refs/tags/v0.2.0-rc.1", "", "testpypi"),
        ("0.2.0.dev7", "workflow_dispatch", "refs/heads/testpypi-preview", "testpypi", "testpypi"),
        ("0.2.0rc1", "workflow_dispatch", "refs/tags/v0.2.0-rc.1", "testpypi", "testpypi"),
        ("0.2.0", "workflow_dispatch", "refs/tags/v0.2.0", "testpypi", "testpypi"),
    ],
)
def test_approved_release_refs(version, event, ref, requested, expected) -> None:
    assert release_target(version, event, ref, requested) == expected


@pytest.mark.parametrize(
    "version",
    ["0.2.0.dev7", "0.2.0a1", "0.2.0b1", "0.2.0rc1", "0.2.0.post1", "0.2.0+local", "1!0.2.0"],
)
def test_production_rejects_non_final_versions(version: str) -> None:
    with pytest.raises(ValueError, match=r"final X\.Y\.Z"):
        release_target(version, "workflow_dispatch", "refs/heads/master", "pypi")


@pytest.mark.parametrize(
    ("version", "event", "ref", "requested"),
    [
        ("0.2.0", "workflow_dispatch", "refs/heads/master", "pypi"),
        ("0.2.0", "workflow_dispatch", "refs/heads/testpypi-preview", "pypi"),
        ("0.2.0", "workflow_dispatch", "refs/heads/feature/fix", "pypi"),
        ("0.2.0", "workflow_dispatch", "refs/heads/v0.2.0", "pypi"),
        ("0.2.0", "workflow_dispatch", "refs/tags/preview", "pypi"),
        ("0.2.0", "workflow_dispatch", "refs/tags/v0.2.1", "pypi"),
        ("0.2.0rc1", "workflow_dispatch", "refs/tags/v0.2.0-rc.1", "pypi"),
        ("0.2.0rc2", "push", "refs/tags/v0.2.0-rc.1", ""),
        ("0.2.0", "push", "refs/heads/master", ""),
        ("0.2.0", "workflow_dispatch", "refs/heads/feature/fix", "testpypi"),
        ("0.2.0", "workflow_dispatch", "refs/heads/master", "unknown"),
        ("0.2.0", "pull_request", "refs/heads/master", "pypi"),
    ],
)
def test_unapproved_release_is_rejected(version, event, ref, requested) -> None:
    with pytest.raises(ValueError):
        release_target(version, event, ref, requested)


@pytest.mark.parametrize("approved_commit", [True, False])
def test_production_tag_requires_a_master_commit(tmp_path: Path, approved_commit: bool) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init")
    git("config", "user.name", "Release policy test")
    git("config", "user.email", "release-policy@example.invalid")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "release-fixture"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    git("add", "pyproject.toml")
    git("commit", "-m", "Approved release source")
    git("update-ref", "refs/remotes/origin/master", "HEAD")
    if not approved_commit:
        (tmp_path / "unmerged.txt").write_text("Unapproved source", encoding="utf-8")
        git("add", "unmerged.txt")
        git("commit", "-m", "Unmerged feature source")
    output = tmp_path / "outputs.txt"
    result = subprocess.run(
        [sys.executable, str(RELEASE_SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "EVENT_NAME": "workflow_dispatch",
            "RELEASE_REF": "refs/tags/v0.2.0",
            "DISPATCH_TARGET": "pypi",
            "GITHUB_OUTPUT": str(output),
        },
        check=False,
    )
    if approved_commit:
        assert result.returncode == 0, result.stderr
        assert output.read_text(encoding="utf-8") == "version=0.2.0\ntarget=pypi\n"
    else:
        assert result.returncode == 1
        assert "Release rejected" in result.stderr
        assert not output.exists()


def test_release_workflow_enforces_validation_before_building() -> None:
    steps = load("release.yml")["jobs"]["build"]["steps"]
    validation = next(step for step in steps if step.get("id") == "meta")
    assert validation["run"] == "python .github/scripts/validate_release.py"
    assert validation["env"]["RELEASE_REF"] == "${{ github.ref }}"
    assert validation["env"]["EVENT_NAME"] == "${{ github.event_name }}"
    assert validation["env"]["DISPATCH_TARGET"] == "${{ inputs.target }}"
    assert "if" not in validation and "continue-on-error" not in validation
    assert steps[0]["with"]["fetch-depth"] == "0"
    provenance_index = next(i for i, step in enumerate(steps) if step.get("id") == "provenance")
    download_index = next(
        i
        for i, step in enumerate(steps)
        if step.get("run") == "python .github/scripts/download_ci_artifact.py"
    )
    assert steps.index(validation) < provenance_index < download_index
    assert not any("python -m build" in step.get("run", "") for step in steps)
