"""KAN-580: smoke the exact artifact before upload and isolate package indexes."""

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".github/scripts"
preview = runpy.run_path(str(SCRIPTS / "preview_version.py"))
smoke = runpy.run_path(str(SCRIPTS / "smoke_wheel.py"))


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )


def assert_local_smoke_gate(jobs):
    local = jobs["smoke-local"]
    assert local["needs"] == "build"
    assert local["runs-on"] == "windows-latest"
    assert "if" not in local and "continue-on-error" not in local
    for step in local["steps"]:
        assert "if" not in step and "continue-on-error" not in step
    commands = [step.get("run", "") for step in local["steps"]]
    verification = commands.index("python .github/scripts/distribution_manifest.py verify")
    smoke_index = next(i for i, command in enumerate(commands) if "smoke_wheel.py" in command)
    assert verification < smoke_index
    assert "--wheel-directory release-artifact/dist" in commands[smoke_index]
    assert '--expected-version "${{ needs.build.outputs.version }}"' in commands[smoke_index]
    assert "||" not in commands[smoke_index]
    for target in ("pypi", "testpypi"):
        publish = jobs[f"publish-{target}"]
        assert set(publish["needs"]) == {"build", "smoke-local"}
        assert publish["if"] == f"needs.build.outputs.target == '{target}'"
        assert "continue-on-error" not in publish


def test_publication_requires_successful_local_wheel_smoke():
    assert_local_smoke_gate(workflow("release.yml")["jobs"])


@pytest.mark.parametrize("bypass", ["needs", "skip", "ignore", "step-ignore", "wrong-wheel"])
def test_local_smoke_gate_rejects_bypasses(bypass):
    jobs = deepcopy(workflow("release.yml")["jobs"])
    local = jobs["smoke-local"]
    step = local["steps"][-1]
    if bypass == "needs":
        jobs["publish-testpypi"]["needs"] = ["build"]
    elif bypass == "skip":
        local["if"] = "false"
    elif bypass == "ignore":
        local["continue-on-error"] = "true"
    elif bypass == "step-ignore":
        step["continue-on-error"] = "true"
    else:
        step["run"] = step["run"].replace("release-artifact/dist", "dist")
    with pytest.raises(AssertionError):
        assert_local_smoke_gate(jobs)


def test_smoke_dependency_exports_keep_lockfile_hashes():
    for name, job_id in (
        ("ci.yml", "package"),
        ("release.yml", "smoke-local"),
        ("release.yml", "smoke-testpypi"),
    ):
        steps = workflow(name)["jobs"][job_id]["steps"]
        export = next(step["run"] for step in steps if "uv export" in step.get("run", ""))
        assert "--locked --no-default-groups --no-emit-project" in export
        assert "--no-hashes" not in export
        assert "release-requirements.txt" in export
        command = next(step["run"] for step in steps if "smoke_wheel.py" in step.get("run", ""))
        assert '--requirements "${{ runner.temp }}/release-requirements.txt"' in command


@pytest.mark.parametrize("target,index", [("testpypi", "test.pypi.org"), ("pypi", "pypi.org")])
def test_index_download_is_separate_and_hash_checked_before_install(target, index):
    jobs = workflow("release.yml")["jobs"]
    steps = jobs[f"smoke-{target}"]["steps"]
    commands = [step.get("run", "") for step in steps]
    download = next(i for i, command in enumerate(commands) if "pip download" in command)
    assert "--no-deps --only-binary=:all:" in commands[download]
    assert f"--index-url https://{index}/simple/" in commands[download]
    assert "--extra-index-url" not in "\n".join(commands)
    verification = next(i for i, command in enumerate(commands) if "Get-FileHash" in command)
    assert "$builtHash -ne $downloadedHash" in commands[verification]
    assert "exit 1" in commands[verification]
    installation = next(i for i, command in enumerate(commands) if "smoke_wheel.py" in command)
    assert download < verification < installation
    assert f"--wheel-directory {target}-download" in commands[installation]
    for job in (jobs["publish-testpypi"], jobs["publish-pypi"]):
        publish = job["steps"][-1]
        assert publish["with"].get("skip-existing", "false") == "false"


@pytest.mark.parametrize("target", ["testpypi", "pypi"])
@pytest.mark.parametrize("substituted", [False, True])
def test_actual_workflow_rejects_same_version_with_different_bytes(tmp_path, target, substituted):
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("Index verification requires PowerShell")
    built = tmp_path / "release-artifact/dist"
    downloaded = tmp_path / f"{target}-download"
    built.mkdir(parents=True)
    downloaded.mkdir()
    name = "dolphin_desktop-0.2.0-py3-none-any.whl"
    (built / name).write_bytes(b"CI wheel")
    (downloaded / name).write_bytes(b"substitute" if substituted else b"CI wheel")
    step = next(
        step
        for step in workflow("release.yml")["jobs"][f"smoke-{target}"]["steps"]
        if "Get-FileHash" in step.get("run", "")
    )
    script = tmp_path / "verify.ps1"
    script.write_text("$ErrorActionPreference = 'Stop'\n" + step["run"], encoding="utf-8")
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (1 if substituted else 0), result.stdout + result.stderr


@pytest.mark.parametrize("prefix", ["PIP_", "pip_"])
def test_testpypi_download_ignores_inherited_indexes_and_config(tmp_path, prefix):
    powershell = shutil.which("pwsh")
    if powershell is None:
        pytest.skip("The TestPyPI download step requires PowerShell")
    configuration = tmp_path / "pip.ini"
    configuration.write_text(
        "[global]\nextra-index-url = https://config.invalid/simple/\n"
        "find-links = https://config.invalid/wheels/\n",
        encoding="utf-8",
    )
    log = tmp_path / "pip-invocations.jsonl"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "with open(os.environ['TEST_PIP_LOG'], 'a', encoding='utf-8') as output:\n"
        "    output.write(json.dumps({'args': sys.argv[1:], 'env': {\n"
        "        key.upper(): value for key, value in os.environ.items()\n"
        "        if key.upper().startswith('PIP_')\n"
        "        or key.upper() == 'PYTHONPATH'\n"
        "    }}) + '\\n')\n",
        encoding="utf-8",
    )
    step = next(
        step
        for step in workflow("release.yml")["jobs"]["smoke-testpypi"]["steps"]
        if step.get("name") == "Download dolphin-desktop from TestPyPI"
    )
    # Run the actual workflow script, substituting a recorder for Python so no
    # network access is needed. All pip calls must see the sanitized environment.
    python_path = str(Path(sys.executable)).replace("'", "''")
    probe_path = str(probe).replace("'", "''")
    script = tmp_path / "download.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"function python {{ & '{python_path}' '{probe_path}' @args }}\n"
        + step["run"].replace("${{ needs.build.outputs.version }}", "0.2.0.dev7"),
        encoding="utf-8",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PIP_") and key.upper() != "PYTHONPATH"
    }
    environment.update(
        {
            f"{prefix}EXTRA_INDEX_URL": "https://extra.invalid/simple/",
            f"{prefix}FIND_LINKS": "https://links.invalid/wheels/",
            f"{prefix}INDEX_URL": "https://primary.invalid/simple/",
            f"{prefix}CONFIG_FILE": str(configuration),
            "PYTHONPATH": str(tmp_path / "untrusted-python"),
            "TEST_PIP_LOG": str(log),
        }
    )
    subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(script)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    pip_calls = [call for call in calls if call["args"][:2] == ["-m", "pip"]]
    assert {call["args"][2] for call in pip_calls} == {"--version", "index", "download"}
    for call in pip_calls:
        assert call["env"] == {"PIP_CONFIG_FILE": os.devnull}
        if call["args"][2] in {"index", "download"}:
            index = call["args"].index("--index-url")
            assert call["args"][index + 1] == "https://test.pypi.org/simple/"


def test_preview_identity_uses_originating_ci_attempt():
    package = workflow("ci.yml")["jobs"]["package"]["steps"]
    prepare = next(step for step in package if step.get("id") == "version")
    assert prepare["env"] == {
        "CI_RUN_ID": "${{ github.run_id }}",
        "CI_RUN_ATTEMPT": "${{ github.run_attempt }}",
    }
    names = [step.get("name") for step in package]
    assert (
        names.index("Export locked runtime dependencies")
        < package.index(prepare)
        < names.index("Build wheel and sdist")
    )
    release = workflow("release.yml")["jobs"]["build"]
    expected = next(step for step in release["steps"] if step.get("id") == "version")
    assert expected["env"] == {
        "CI_RUN_ID": "${{ steps.provenance.outputs.ci_run_id }}",
        "CI_RUN_ATTEMPT": "${{ steps.provenance.outputs.ci_run_attempt }}",
    }
    assert release["outputs"]["version"] == "${{ steps.version.outputs.version }}"


def test_preview_versions_are_unique_across_ci_runs_and_retries():
    versions = {
        preview["artifact_version"]("0.2.0.dev7", str(run), str(attempt))
        for run in (1, 10, 100, 12345678901)
        for attempt in (1, 2, 10, 999999)
    }
    assert len(versions) == 16
    assert all(version.startswith("0.2.0.dev") and version[10:].isdigit() for version in versions)


@pytest.mark.parametrize("run,attempt", [("0", "1"), ("1", "0"), ("x", "1"), ("1", "1000000")])
def test_preview_rejects_ambiguous_ci_identifiers(run, attempt):
    with pytest.raises(ValueError):
        preview["artifact_version"]("0.2.0.dev7", run, attempt)


@pytest.mark.parametrize("version", ["0.2.0", "0.2.0rc1"])
def test_tagged_release_versions_are_preserved(version):
    assert preview["artifact_version"](version, "42", "2") == version


def test_preview_updates_both_versions_without_touching_lock(tmp_path):
    (tmp_path / "src/dolphin_desktop").mkdir(parents=True)
    project = tmp_path / "pyproject.toml"
    package = tmp_path / "src/dolphin_desktop/__init__.py"
    project.write_text('[project]\nversion = "0.2.0.dev7"\n', encoding="utf-8")
    package.write_text('__version__ = "0.2.0.dev7"\n', encoding="utf-8")
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"locked dependencies")
    output = tmp_path / "output"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "preview_version.py"), "prepare"],
        cwd=tmp_path,
        env={**os.environ, "CI_RUN_ID": "42", "CI_RUN_ATTEMPT": "2", "GITHUB_OUTPUT": str(output)},
        check=True,
    )
    assert 'version = "0.2.0.dev42000002"' in project.read_text(encoding="utf-8")
    assert package.read_text(encoding="utf-8") == '__version__ = "0.2.0.dev42000002"\n'
    assert output.read_text(encoding="utf-8") == "version=0.2.0.dev42000002\n"
    assert lock.read_bytes() == b"locked dependencies"


@pytest.mark.parametrize("fail_at", [None, 0, 1, 2, 3, 4, 5])
def test_smoke_isolates_indexes_and_stops_at_first_failure(tmp_path, monkeypatch, fail_at):
    wheel = tmp_path / "fixture.whl"
    wheel.touch()
    requirements = tmp_path / "requirements.txt"
    requirements.touch()
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://untrusted.invalid/simple/")
    monkeypatch.setenv("PIP_FIND_LINKS", "https://untrusted.invalid/wheels/")
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    monkeypatch.setattr(smoke["venv"].EnvBuilder, "create", lambda *args: None)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["check"] is True
        assert kwargs["cwd"] != ROOT
        environment = kwargs["env"]
        assert "PIP_EXTRA_INDEX_URL" not in environment and "PIP_FIND_LINKS" not in environment
        assert "PYTHONPATH" not in environment
        assert environment["PIP_CONFIG_FILE"] == os.devnull
        if len(calls) - 1 == fail_at:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(smoke["subprocess"], "run", run)
    if fail_at is not None:
        with pytest.raises(subprocess.CalledProcessError):
            smoke["smoke"](tmp_path, requirements, "0.2.0.dev7")
        assert len(calls) == fail_at + 1
    else:
        smoke["smoke"](tmp_path, requirements, "0.2.0.dev7")
        assert len(calls) == 6
        assert calls[0][4:] == [
            "--index-url",
            "https://pypi.org/simple/",
            "--require-hashes",
            "--only-binary=:all:",
            "--no-deps",
            "-r",
            str(requirements),
        ]
        assert calls[1][4:] == ["--no-deps", "--no-index", str(wheel)]
        assert calls[2][1:] == ["-m", "pip", "check"]
        assert calls[3][1] == "-I" and calls[3][-1] == "0.2.0.dev7"
        assert calls[4][1:] == ["--help"]
        assert calls[5][1:] == ["info", "backends"]


@pytest.mark.parametrize("count", [0, 2])
def test_smoke_rejects_missing_or_ambiguous_wheel(tmp_path, count):
    for index in range(count):
        (tmp_path / f"fixture{index}.whl").touch()
    with pytest.raises(ValueError, match="exactly one wheel"):
        smoke["smoke"](tmp_path, tmp_path / "requirements.txt", "0.2.0.dev7")
