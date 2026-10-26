"""Run real Allure reporters without inheriting the source repo's pytest configuration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

CHILD = """
import os
import sys
import allure
import pytest
from dolphin_desktop import Secret

canary = os.environ["KAN469_CANARY"]
phase = os.environ["KAN469_PHASE"]
if os.environ["KAN469_STYLE"] == "marked":
    Secret(canary)

def diagnostic():
    return "pin=" + canary if os.environ["KAN469_STYLE"] == "pin" else canary

@pytest.fixture
def scenario():
    allure.dynamic.parameter("pin", canary)
    allure.dynamic.label("pin", canary)
    print("setup stdout " + diagnostic())
    print("setup stderr " + diagnostic(), file=sys.stderr)
    with allure.step("setup operation " + diagnostic()):
        if phase == "setup":
            raise RuntimeError("setup operation " + diagnostic())
    yield
    print("teardown stdout " + diagnostic())
    print("teardown stderr " + diagnostic(), file=sys.stderr)
    with allure.step("teardown operation " + diagnostic()):
        if phase == "teardown":
            raise RuntimeError("teardown operation " + diagnostic())

@pytest.mark.xfail(phase == "xfail", reason="expected operation failure")
def test_operation(scenario):
    allure.dynamic.title("operation " + diagnostic())
    print("call stdout " + diagnostic())
    print("call stderr " + diagnostic(), file=sys.stderr)
    with allure.step("call operation " + diagnostic()):
        if phase in ("call", "xfail"):
            raise RuntimeError("call operation " + diagnostic())
    if phase == "success":
        try:
            with allure.step("handled operation"):
                raise RuntimeError("handled operation " + diagnostic())
        except RuntimeError as exc:
            assert exc.args == ("handled operation " + diagnostic(),)
"""


def run_allure_case(
    directory: Path,
    phase: str,
    style: str,
    order: str,
    *,
    source: str = CHILD,
    canary: str | None = None,
) -> dict[str, Any]:
    """Return the terminal output and on-disk JSON/attachments from a child pytest run."""
    child = directory / "test_allure_case.py"
    child.write_text(source, encoding="utf-8")
    if canary is None:
        canary = "KAN469_" + uuid.uuid4().hex
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    environment.update(KAN469_CANARY=canary, KAN469_PHASE=phase, KAN469_STYLE=style)
    plugins = []
    if order != "entry-point":
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        names = ["dolphin_desktop.pytest_plugin", "allure_pytest.plugin"]
        if order == "allure-first":
            names.reverse()
        plugins = [argument for name in names for argument in ("-p", name)]
    report_dir = directory / "allure"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *plugins,
            str(child),
            "-q",
            "-c",
            os.devnull,
            "--rootdir",
            str(directory),
            "--confcutdir",
            str(directory),
            "--dolphin-video=off",
            "--dolphin-trace=off",
            "--alluredir",
            str(report_dir),
        ],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        timeout=45,
        check=False,
    )
    files = {path.name: path.read_bytes() for path in report_dir.iterdir()}
    return {
        "exit_code": result.returncode,
        "terminal": result.stdout + result.stderr,
        "canary": canary,
        "leaks": [name for name, content in files.items() if canary.encode() in content],
        "documents": {
            name: json.loads(content) for name, content in files.items() if name.endswith(".json")
        },
        "attachments": {
            name: content.decode("utf-8")
            for name, content in files.items()
            if name.endswith(".txt")
        },
    }
