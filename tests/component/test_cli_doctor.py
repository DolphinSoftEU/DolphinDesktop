"""Black-box checks for the installed ``dolphin doctor`` command."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(60)]


def test_doctor_command_executes_successfully_and_returns_zero(tmp_path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from dolphin_desktop._cli import main; main()", "doctor"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "dolphin  " in result.stdout
    assert "Python   " in result.stdout
