"""Exercise real PowerShell cleanup and error propagation in both build scripts."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(os.name != "nt", reason="Windows path semantics"),
]
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "scenario",
    [
        "trailing",
        "slashes",
        "case",
        "extended",
        "dots",
        "unc",
        "wrong-parent",
        "wrong-name",
        "outside-junction",
        "junction",
        "cleanup-only-error",
        "primary-error",
        "primary-guard-error",
        "verification-error",
        "build-and-log-error",
    ],
)
def test_cleanup_paths_and_original_error_priority(tmp_path, scenario):
    pwsh = shutil.which("pwsh")
    assert pwsh, "PowerShell 7 is required by the Windows native build"
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(ROOT / "tests/framework/fixtures/qt_build_cleanup.ps1"),
            "-Repository",
            str(ROOT),
            "-Sandbox",
            str(tmp_path),
            "-Scenario",
            scenario,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    if scenario in {"primary-error", "verification-error", "build-and-log-error"}:
        assert "Original error preserved:" in result.stdout
        assert "controlled cleanup failure" in result.stdout
    if scenario == "primary-guard-error":
        assert "Original error preserved: original build failure" in result.stdout
        assert "Refusing to remove unexpected temporary directory" in result.stdout
    if scenario == "build-and-log-error":
        assert "controlled diagnostics failure" in result.stdout
