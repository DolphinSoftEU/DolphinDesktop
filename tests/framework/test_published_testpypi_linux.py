"""Regression guard for the Linux smoke of published TestPyPI wheels."""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit


def test_published_testpypi_linux_smoke() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / ".github" / "workflows" / "release.yml"
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    job = workflow["jobs"]["smoke-testpypi-linux"]

    assert job["runs-on"] == "ubuntu-24.04"
    assert job["needs"] == ["build", "publish-testpypi"]
    assert job["if"] == "needs.build.outputs.target == 'testpypi'"
    assert job["permissions"] == {"contents": "read"}
    assert "continue-on-error" not in job

    names = {step["name"]: step for step in job["steps"] if "name" in step}
    download = names["Download published wheel from TestPyPI"]
    verify = names["Verify published wheel matches approved CI wheel"]
    install = names["Install published wheel and verify Linux import and warning"]
    evidence = names["Upload Linux installation and import evidence"]

    assert "test.pypi.org/simple/" in download["run"]
    assert "--no-deps" in download["run"]
    assert "assert built_hash == published_hash" in verify["run"]
    assert ".github/scripts/check_wheel_platform.py" in install["run"]
    assert "--wheel-directory testpypi-download" in install["run"]
    assert evidence["if"] == "always()"
