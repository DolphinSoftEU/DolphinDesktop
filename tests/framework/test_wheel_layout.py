"""Wheel-layout regression tests."""

from __future__ import annotations

import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"


# Fast — pyproject.toml build-config asserts (sub-second)


@pytest.fixture(scope="module")
def pyproject_text() -> str:
    return PYPROJECT.read_text(encoding="utf-8")


def test_hatch_wheel_packages_only_dolphin_desktop(pyproject_text: str):
    """The wheel-target package map must be exactly ``["src/dolphin_desktop"]``."""
    m = re.search(
        r"^\[tool\.hatch\.build\.targets\.wheel\]\s*\n"
        r"(?:^(?!\[).*\n)*?"
        r"^packages\s*=\s*(\[[^\]]*\])",
        pyproject_text,
        re.M,
    )
    assert m, (
        "[tool.hatch.build.targets.wheel].packages missing from "
        "pyproject.toml. An explicit ``packages`` line is required so "
        "the wheel manifest is deterministic."
    )
    packages_literal = m.group(1)
    # Every quoted string inside the array — TOML basic or literal.
    entries = re.findall(r'"([^"]+)"|\'([^\']+)\'', packages_literal)
    values = sorted({a or b for a, b in entries})
    assert values == ["src/dolphin_desktop"], (
        f"[tool.hatch.build.targets.wheel].packages must be exactly "
        f'["src/dolphin_desktop"]. Found: '
        f"{values}. Any extra path — especially ``src/dolphin`` — "
        f"resurrects the top-level ``dolphin/`` collision hazard."
    )


def test_hatch_sdist_contains_no_top_level_dolphin_files():
    """The built sdist must not contain a separate ``src/dolphin/`` tree."""
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--sdist",
                "--outdir",
                str(out_dir),
                str(REPO_ROOT),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            pytest.fail(
                f"python -m build --sdist exited {result.returncode}. "
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )

        archives = list(out_dir.glob("dolphin_desktop-*.tar.gz"))
        assert len(archives) == 1, f"Expected exactly one built sdist, found {archives}."
        with tarfile.open(archives[0], "r:gz") as archive:
            names = archive.getnames()

        forbidden = [name for name in names if re.search(r"(?:^|/)src/dolphin(?:/|$)", name)]
        assert not forbidden, (
            f"sdist {archives[0].name} contains a top-level ``dolphin/`` tree: "
            f"{forbidden!r}"
        )


# Slow — actually build the wheel and inspect it (opt-in)


@pytest.mark.slow
def test_built_wheel_contains_no_top_level_dolphin_files():
    """Build the wheel into a tempdir and assert the zip contains no entry whose top-level name is
    ``dolphin``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(out_dir),
                str(REPO_ROOT),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            pytest.fail(
                f"python -m build --wheel exited {result.returncode}. "
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        wheels = list(out_dir.glob("dolphin_desktop-*.whl"))
        assert len(wheels) == 1, f"Expected exactly one built wheel, found {wheels}."
        with zipfile.ZipFile(wheels[0]) as z:
            top_levels = sorted({n.split("/", 1)[0] for n in z.namelist() if n})
        allowed_pkg = {"dolphin_desktop"}
        dist_info = {t for t in top_levels if t.endswith(".dist-info")}
        unexpected = set(top_levels) - allowed_pkg - dist_info
        assert not unexpected, (
            f"Wheel {wheels[0].name} carries unexpected top-level "
            f"entries: {sorted(unexpected)}. Only ``dolphin_desktop/`` "
            f"plus the ``*.dist-info/`` metadata are permitted."
        )
        # Extra guard: check verbatim for both sister-namespace names.
        forbidden_top_levels = {"dolphin", "dolphinsoft"}
        for name in top_levels:
            assert name not in forbidden_top_levels, (
                f"Wheel {wheels[0].name} ships top-level ``{name}/`` — "
                f"that namespace is reserved for pytest-dolphinsoft "
                f"Wheel-layout contract violated."
            )
