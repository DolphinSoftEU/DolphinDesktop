"""Unit tests for wheel metadata selection and the minimum-install smoke guard."""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/check_minimum_dependencies.py"
module = runpy.run_path(str(SCRIPT))
wheel_requirements = module["wheel_requirements"]


@pytest.fixture
def wheel(tmp_path):
    def make(requirements, extras=("docs", "pytest", "vision", "all")):
        metadata = "Metadata-Version: 2.3\nName: Dolphin_Desktop\nVersion: 1.0\n"
        metadata += "".join(f"Provides-Extra: {extra}\n" for extra in extras)
        metadata += "".join(f"Requires-Dist: {requirement}\n" for requirement in requirements)
        path = tmp_path / "dolphin_desktop-1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("dolphin_desktop-1.0.dist-info/METADATA", metadata)
        return path

    return make


@pytest.mark.parametrize(
    ("options", "expected", "selected"),
    [
        ({}, ["shared>=1"], set()),
        ({"extra": "vision"}, ["numpy>=1.26", "shared>=1"], {"vision"}),
        ({"extra": "docs"}, ["shared>=1", "shared>=3"], {"docs"}),
        ({"extra": "pytest"}, ["pytest>=9.0.3", "shared>=1"], {"pytest"}),
        (
            {"extra": "all"},
            ["numpy>=1.26", "pytest>=9.0.3", "shared>=1"],
            {"all", "vision", "pytest"},
        ),
        (
            {"all_extras": True},
            ["numpy>=1.26", "pytest>=9.0.3", "shared>=1", "shared>=3"],
            {"all", "vision", "pytest", "docs"},
        ),
    ],
)
def test_profiles_do_not_pull_requirements_from_unselected_extras(
    wheel, options, expected, selected
):
    path = wheel(
        [
            "shared>=1",
            "shared>=1",
            'shared>=3; extra == "docs"',
            'numpy>=1.26; extra == "vision"',
            'pytest>=9.0.3; extra == "pytest"',
            'dolphin-desktop[vision,pytest]; extra == "all"',
        ]
    )
    assert wheel_requirements(path, **options) == (expected, selected)


@pytest.mark.parametrize("system", ["Windows", "Linux"])
@pytest.mark.parametrize("version", ["3.11", "3.13"])
@pytest.mark.parametrize("extra", [None, "vision"])
def test_environment_and_extra_markers_are_evaluated_together(wheel, system, version, extra):
    path = wheel(
        [
            'comtypes>=1.4.8; platform_system == "Windows"',
            'numpy>=2.1; python_version >= "3.13" and extra == "vision"',
            'numpy>=1.26; python_version < "3.13" and extra == "vision"',
        ]
    )
    expected = ["comtypes>=1.4.8"] if system == "Windows" else []
    if extra:
        expected.append("numpy>=2.1" if version == "3.13" else "numpy>=1.26")
    requirements, selected = wheel_requirements(
        path, extra=extra, environment={"platform_system": system, "python_version": version}
    )
    assert requirements == expected
    assert selected == ({extra} if extra else set())
    assert all(";" not in requirement for requirement in requirements)


def test_nested_self_references_terminate_without_pypi_product_requirement(wheel):
    path = wheel(
        [
            'DOLPHIN.DESKTOP[vision]; extra == "all"',
            'dolphin_desktop[all,pytest]; extra == "vision"',
            'pytest>=9.0.3; extra == "pytest"',
        ]
    )
    assert wheel_requirements(path, extra="all") == (["pytest>=9.0.3"], {"all", "vision", "pytest"})


@pytest.mark.parametrize("options", [{"extra": "typo"}, {"extra": "docs", "all_extras": True}])
def test_invalid_profile_fails_instead_of_silently_testing_base(wheel, options):
    with pytest.raises(ValueError):
        wheel_requirements(wheel([]), **options)


def test_unknown_self_referenced_extra_is_rejected(wheel):
    with pytest.raises(ValueError, match="Unknown extras"):
        wheel_requirements(wheel(['dolphin-desktop[typo]; extra == "all"']), extra="all")


def test_extra_names_are_normalized(wheel):
    path = wheel(['dependency[feature]>=1; extra == "my-extra"'], extras=("my-extra",))
    assert wheel_requirements(path, extra="My_Extra") == (["dependency[feature]>=1"], {"my-extra"})


@pytest.mark.parametrize("extra", [None, "pytest", "all"])
def test_check_smokes_cli_and_pytest_for_flattened_aggregate(wheel, tmp_path, monkeypatch, extra):
    # Hatch flattens `all` in real wheels; no self-reference remains to expand.
    path = wheel(
        [
            "pillow>=13.0.0",
            'pytest>=9.0.3; extra == "pytest" or extra == "all"',
            'numpy>=1.26; extra == "all"',
        ]
    )
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if "pytest" in command:
            smoke = Path(command[4]).read_text(encoding="utf-8")
            assert 'get_plugin("dolphin-desktop")' in smoke

    monkeypatch.setattr(module["subprocess"], "run", run)
    module["check"](path, tmp_path / "evidence", extra=extra)
    assert any(
        command[0].endswith(("/dolphin", "/dolphin.exe", "\\dolphin.exe"))
        and command[1:] == ("--help",)
        for command in commands
    )
    assert any(command[1:] == ("info", "backends") for command in commands)
    assert any("pytest" in command for command in commands) == (extra is not None)
    assert any("import numpy;" in argument for command in commands for argument in command) == (
        extra == "all"
    )


@pytest.mark.parametrize("failure", ["audit", "timeout"])
def test_failure_diagnostics_preserve_original_error_with_windows_bytes(
    wheel, tmp_path, monkeypatch, failure
):
    path = wheel(["pillow>=13.0.0"])
    output = tmp_path / "evidence"
    output.mkdir()
    (output / "result.json").write_text('{"status": "passed"}', encoding="utf-8")
    captured = []

    class LegacyConsole:
        encoding = "cp1252"

        def write(self, value):
            captured.append(value.encode(self.encoding).decode(self.encoding))

        def flush(self):
            pass

    console = LegacyConsole()
    monkeypatch.setattr(module["sys"], "stdout", console)
    original_error = None

    def run(command, **kwargs):
        nonlocal original_error
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        if "pip_audit" not in command:
            return
        # A native Windows tool can emit bytes that are invalid in UTF-8,
        # alongside valid UTF-8 that an older console cannot represent.
        kwargs["stdout"].buffer.write(b"audit failure: \x81\xff " + "\u2603".encode())
        kwargs["stdout"].flush()
        if failure == "audit":
            original_error = subprocess.CalledProcessError(1, command)
        else:
            original_error = subprocess.TimeoutExpired(command, 300)
        raise original_error

    monkeypatch.setattr(module["subprocess"], "run", run)
    expected = subprocess.CalledProcessError if failure == "audit" else subprocess.TimeoutExpired
    with pytest.raises(expected) as raised:
        module["check"](path, output)
    assert raised.value is original_error
    diagnostic = "".join(captured)
    assert "pip_audit" in diagnostic
    assert r"audit failure: \x81\xff \u2603" in diagnostic
    assert not (output / "result.json").exists()
    assert b"audit failure: \x81\xff" in (output / "commands.log").read_bytes()


@pytest.mark.parametrize("block_plugin", [False, True])
def test_smoke_requires_registered_distribution_entry_point(tmp_path, block_plugin):
    smoke = tmp_path / "test_smoke.py"
    smoke.write_text(module["PYTEST_SMOKE"], encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    command = [
        sys.executable,
        "-I",
        "-m",
        "pytest",
        str(smoke),
        "--noconftest",
        "--confcutdir",
        str(tmp_path),
        "-c",
        os.devnull,
        "-q",
    ]
    if block_plugin:
        command += ["-p", "no:dolphin-desktop"]
    result = subprocess.run(
        command, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=20
    )
    if block_plugin:
        assert result.returncode == 1, result.stdout + result.stderr
        assert "dolphin-desktop entry point was not loaded" in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
