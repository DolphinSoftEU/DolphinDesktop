"""KAN-586: reject broken release metadata before native installation tests."""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from packaging import markers

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
module = runpy.run_path(str(ROOT / ".github/scripts/check_wheel_platform.py"))
validate = module["validate_metadata"]
VALID = [
    'comtypes>=1.4.8; platform_system == "Windows"',
    'pywin32>=306; platform_system == "Windows"',
]


@pytest.fixture
def wheel(tmp_path):
    def create(requirements=VALID, *, name="dolphin-desktop", version="0.2.0"):
        path = tmp_path / "dolphin_desktop-0.2.0-py3-none-any.whl"
        metadata = f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n"
        metadata += "Provides-Extra: leaky\n"
        metadata += "".join(f"Requires-Dist: {value}\n" for value in requirements)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("dolphin_desktop-0.2.0.dist-info/METADATA", metadata)
        return path

    return create


@pytest.mark.parametrize(
    "marker", ['platform_system == "Windows"', 'sys_platform == "win32"', 'os_name == "nt"']
)
@pytest.mark.parametrize("host_os_name", ["nt", "posix"])
def test_built_wheel_markers_allow_only_windows_across_supported_pythons(
    wheel, marker, host_os_name, monkeypatch
):
    environment = markers.default_environment()
    environment["os_name"] = host_os_name
    monkeypatch.setattr(markers, "default_environment", lambda: environment.copy())
    path = wheel([f"{name}>=1; {marker}" for name in ("comtypes", "pywin32")])
    assert b"Requires-Dist:" in validate(path, "0.2.0")


@pytest.mark.parametrize("unconditional", [{"comtypes"}, {"pywin32"}, {"comtypes", "pywin32"}])
def test_old_020_wheel_with_unconditional_windows_dependencies_is_rejected(wheel, unconditional):
    requirements = [
        value.split(";")[0] if value.split(">=")[0] in unconditional else value for value in VALID
    ]
    with pytest.raises(ValueError, match="Missing platform marker"):
        validate(wheel(requirements), "0.2.0")


@pytest.mark.parametrize("missing", ["comtypes", "pywin32"])
def test_windows_must_still_receive_both_dependencies(wheel, missing):
    with pytest.raises(ValueError, match="Missing required Windows dependencies"):
        validate(wheel([value for value in VALID if not value.startswith(missing)]), "0.2.0")


def test_windows_markers_must_include_python_313(wheel):
    with pytest.raises(ValueError, match=r"Python 3\.13"):
        validate(wheel([value + ' and python_version < "3.13"' for value in VALID]), "0.2.0")


def test_optional_extra_cannot_reintroduce_non_windows_dependency(wheel):
    with pytest.raises(ValueError, match="Windows dependency active on Linux"):
        validate(wheel([*VALID, 'pywin32>=306; extra == "leaky"']), "0.2.0")


@pytest.mark.parametrize("identity", [{"version": "0.2.0.dev6"}, {"name": "other-product"}])
def test_development_installation_or_other_product_cannot_prove_release_metadata(wheel, identity):
    with pytest.raises(ValueError, match="METADATA does not match"):
        validate(wheel(**identity), "0.2.0")


def test_filename_version_must_match_requested_release(wheel):
    with pytest.raises(ValueError, match="filename does not match"):
        validate(wheel(), "0.2.0.dev6")


@pytest.mark.parametrize("fail_install", [False, True])
def test_smoke_uses_normal_isolated_pip_resolution_and_fails_closed(
    wheel, tmp_path, monkeypatch, fail_install
):
    output = tmp_path / "evidence"
    output.mkdir()
    (output / "result.json").write_text('{"status": "stale-success"}', encoding="utf-8")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://untrusted.invalid/simple")
    monkeypatch.setenv("PYTHONPATH", "untrusted-path")
    monkeypatch.setattr(
        module["venv"],
        "EnvBuilder",
        lambda **kw: SimpleNamespace(create=lambda path: path.mkdir(parents=True)),
    )
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert "PIP_EXTRA_INDEX_URL" not in kwargs["env"]
        assert "PYTHONPATH" not in kwargs["env"]
        assert kwargs["env"]["PIP_CONFIG_FILE"] == os.devnull
        if fail_install:
            raise subprocess.CalledProcessError(1, command)
        if "-c" in command:
            Path(command[-1]).write_text('{"version": "0.2.0"}', encoding="utf-8")

    monkeypatch.setattr(module["subprocess"], "run", run)
    if fail_install:
        with pytest.raises(subprocess.CalledProcessError):
            module["smoke"](wheel(), output, "0.2.0")
        assert not (output / "result.json").exists()
        assert len(commands) == 1
    else:
        path = wheel()
        module["smoke"](path, output, "0.2.0")
        install, consistency, imported = commands
        assert install[1:4] == ["-m", "pip", "install"]
        assert "--no-deps" not in install and "-r" not in install
        assert install[-1] == str(path.resolve())
        assert "--report" in install
        assert consistency[1:] == ["-m", "pip", "check"]
        assert imported[1:3] == ["-I", "-c"]
        assert json.loads((output / "result.json").read_text())["status"] == "passed"


def test_platform_matrix_installs_exact_publishable_artifact_and_gates_release():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    package = workflow["jobs"]["package"]
    job = workflow["jobs"]["package-platforms"]
    assert job["needs"] == "package"
    assert job["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "macos-latest", "windows-latest"],
        "python-version": ["3.11", "3.12", "3.13"],
    }
    assert "if" not in job and "continue-on-error" not in job
    steps = job["steps"]
    download = next(step for step in steps if "download-artifact@" in step.get("uses", ""))
    assert download["with"]["artifact-ids"] == "${{ needs.package.outputs.artifact_id }}"
    # download-artifact v4 otherwise nests ID-selected artifacts by name.
    assert download["with"]["merge-multiple"] == "true"
    assert download["with"]["path"] == "release-artifact"
    assert package["outputs"]["artifact_id"] == "${{ steps.upload.outputs.artifact-id }}"
    commands = [step.get("run", "") for step in steps]
    assert not any("-m build" in command for command in commands)
    verify = next(
        step for step in steps if "distribution_manifest.py verify" in step.get("run", "")
    )
    assert verify["env"]["MANIFEST_SHA256"] == "${{ needs.package.outputs.manifest_sha256 }}"
    smoke = next(step for step in steps if "check_wheel_platform.py" in step.get("run", ""))
    assert smoke["env"]["DOLPHIN_DESKTOP_VERSION"] == "${{ needs.package.outputs.version }}"
    assert steps.index(verify) < steps.index(smoke)
    for step in steps:
        assert "continue-on-error" not in step
        if "run" in step:
            assert "if" not in step
    required = runpy.run_path(str(ROOT / ".github/scripts/release_provenance.py"))["REQUIRED_JOBS"]
    assert {
        f"Wheel install / {system} / Python {python}"
        for system in job["strategy"]["matrix"]["os"]
        for python in job["strategy"]["matrix"]["python-version"]
    } <= required
