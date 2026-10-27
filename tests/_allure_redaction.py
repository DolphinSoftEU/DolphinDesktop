"""Run real Allure reporters without inheriting the source repo's pytest configuration."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree


def inspect_dolphin_artifacts(directory: Path, canary: str) -> dict[str, Any]:
    leaks = []
    runs = []
    html = []
    for path in directory.rglob("*"):
        # pytest's execution cache deliberately retains original node IDs
        # for selection/--lf. It is not a Dolphin report or publishable output.
        if ".pytest_cache" in path.relative_to(directory).parts:
            continue
        if canary in str(path.relative_to(directory)):
            leaks.append(str(path.relative_to(directory)))
        if not path.is_file():
            continue
        if canary.encode() in path.read_bytes():
            leaks.append(str(path.relative_to(directory)))
        if path.suffix == ".db":
            with closing(sqlite3.connect(path)) as connection:
                runs.extend(connection.execute("SELECT test_nodeid, status FROM runs").fetchall())
        if path.suffix == ".html":
            html.append(path.read_text(encoding="utf-8"))
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if canary in name or canary.encode() in archive.read(name):
                        leaks.append(f"{path.name}:{name}")
    return {"leaks": leaks, "runs": runs, "html": html}


def terminal_session(*, enabled: bool = True, workers: bool = False) -> tuple[Any, Any, list, list]:
    writes: list = []
    cleanups: list = []
    writer = SimpleNamespace(write=lambda text, **kwargs: writes.append((text, kwargs)))
    reporter = SimpleNamespace(_tw=writer)
    config = SimpleNamespace(
        cwd_relative_nodeid=lambda nodeid: nodeid,
        getoption=lambda name, default=None: 2 if workers and name == "numprocesses" else default,
        pluginmanager=SimpleNamespace(getplugin=lambda name: reporter if enabled else None),
        add_cleanup=cleanups.append,
    )
    return SimpleNamespace(config=config), writer, writes, cleanups


def run_in_thread(callback: Any) -> Any:
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(callback).result(timeout=10)


def typed_parameter_cases() -> list[tuple[Any, set[str]]]:
    """Typed inputs for the shared collector, including nested enum payloads."""

    class Credential(Enum):
        code = ("739184",)

    return [
        (Path("739184"), {"739184"}),
        (
            uuid.UUID("73918452-7361-4739-8184-527361739184"),
            {"73918452-7361-4739-8184-527361739184"},
        ),
        (Decimal("739184.01"), {"739184.01"}),
        (Credential.code, {"Credential.code", "code", "739184"}),
        ({"nested": [Path("739184"), Decimal("739184.01")]}, {"739184", "739184.01"}),
    ]


def junit_session(directory: Path) -> Any:
    """Minimal session for the JUnit publication boundary, without starting pytest."""
    return SimpleNamespace(
        config=SimpleNamespace(
            rootpath=directory,
            getoption=lambda name, default=None: str(directory / "junit.xml"),
        )
    )


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
    pytest_args: tuple[str, ...] = (),
    invocation_subdir: str | None = None,
) -> dict[str, Any]:
    """Return the terminal output and on-disk JSON/attachments from a child pytest run."""
    invocation_dir = directory / invocation_subdir if invocation_subdir else directory
    invocation_dir.mkdir(parents=True, exist_ok=True)
    child = invocation_dir / "test_allure_case.py"
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
            "--junitxml",
            str(directory / "junit.xml"),
            "-o",
            "junit_logging=all",
            *pytest_args,
        ],
        cwd=invocation_dir,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        timeout=45,
        check=False,
    )
    files = {path.name: path.read_bytes() for path in report_dir.iterdir()}
    terminal = result.stdout + result.stderr
    (directory / "terminal.txt").write_text(terminal, encoding="utf-8")
    return {
        "exit_code": result.returncode,
        "terminal": terminal,
        "canary": canary,
        "junit": (directory / "junit.xml").read_text(encoding="utf-8"),
        "testcases": [
            dict(element.attrib)
            for element in ElementTree.parse(directory / "junit.xml").iter("testcase")
        ],
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
