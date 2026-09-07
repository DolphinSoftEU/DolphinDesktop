"""Top-level package name regression tests."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = REPO_ROOT / "src"
PYPROJECT = REPO_ROOT / "pyproject.toml"


# Source tree


def test_src_dolphin_directory_does_not_exist():
    """No ``src/dolphin/`` may exist — that name belongs to pytest-dolphinsoft."""
    forbidden = SRC_DIR / "dolphin"
    assert not forbidden.exists(), (
        f"{forbidden} exists. dolphin-desktop's "
        f"sole top-level package is dolphin_desktop; ``dolphin`` is "
        f"owned by pytest-dolphinsoft. Move contents into "
        f"src/dolphin_desktop/ instead."
    )


def test_src_only_contains_dolphin_desktop_package():
    """The ``src/`` layout ships exactly one Python package: dolphin_desktop."""
    package_dirs = [
        entry for entry in SRC_DIR.iterdir() if entry.is_dir() and (entry / "__init__.py").exists()
    ]
    package_names = sorted(p.name for p in package_dirs)
    assert package_names == ["dolphin_desktop"], (
        f"Expected sole top-level package 'dolphin_desktop', found "
        f"{package_names}. Any additional package would ship in the "
        f"wheel."
    )


def test_dolphin_desktop_package_has_init():
    """The package we ship must remain a regular package (with __init__.py)."""
    init = SRC_DIR / "dolphin_desktop" / "__init__.py"
    assert init.exists() and init.is_file(), (
        f"Package sentinel {init} missing. dolphin_desktop must be a "
        f"regular package with __init__.py."
    )


# pyproject.toml


@pytest.fixture(scope="module")
def pyproject_text() -> str:
    assert PYPROJECT.exists(), f"pyproject.toml missing at {PYPROJECT}"
    return PYPROJECT.read_text(encoding="utf-8")


def test_project_name_is_dolphin_desktop(pyproject_text: str):
    """Distribution name on PyPI stays ``dolphin-desktop``."""
    match = re.search(r'^name\s*=\s*"([^"]+)"', pyproject_text, re.M)
    assert match, "pyproject.toml has no [project] name"
    assert match.group(1) == "dolphin-desktop", (
        f"[project.name] must be 'dolphin-desktop'. Got {match.group(1)!r}."
    )


def test_windows_only_dependencies_are_limited_to_windows(pyproject_text: str):
    """Windows-only dependencies must not block non-Windows installs."""
    project = tomllib.loads(pyproject_text)["project"]
    dependencies = project["dependencies"]
    windows_only = {
        dep.split(";", 1)[0].split(">=", 1)[0].lower(): dep
        for dep in dependencies
        if ";" in dep and 'platform_system == "Windows"' in dep
    }

    assert windows_only == {
        "comtypes": 'comtypes>=1.4; platform_system == "Windows"',
        "pywin32": 'pywin32>=306; platform_system == "Windows"',
    }


def test_no_entry_point_targets_dolphin_top_level(pyproject_text: str):
    """Every entry point must resolve into ``dolphin_desktop.<module>``, never bare
    ``dolphin.<module>`` or ``dolphinsoft.<module>``.
    """
    # entry-point spec format: "name = module.path[:attr]"
    # TOML allows keys in three forms (bare, basic-string, literal-string)
    # and values in two string forms — the guard must handle every combination.
    lines = pyproject_text.splitlines()
    forbidden_heads = {"dolphin", "dolphinsoft"}
    heads: set[str] = set()
    targets: list[str] = []
    for i, line in enumerate(lines, start=1):
        # Only look at assignment lines of form ``key = "value"``, ``"key" = 'value'``, etc.
        m = re.match(
            r"""^\s*(?:[\w-]+|"[^"]+"|'[^']+')\s*=\s*"""
            r"""(?:"([^"]+)"|'([^']+)')\s*$""",
            line,
        )
        if not m:
            continue
        value = m.group(1) or m.group(2)
        # We only care about values that LOOK like a module target
        # (contain a dot or a colon, e.g. dolphin_desktop._cli:main).
        if ":" not in value and "." not in value:
            continue
        # Take the leftmost python-identifier segment.
        head = re.split(r"[.:]", value, maxsplit=1)[0]
        heads.add(head)
        if head == "dolphin_desktop":
            targets.append(value)
        if head in forbidden_heads:
            pytest.fail(
                f"pyproject.toml line {i}: entry-point target {value!r} "
                f"resolves into top-level ``{head}`` package, which is "
                f"reserved for pytest-dolphinsoft. Use "
                f"``dolphin_desktop.…`` instead."
            )
    assert forbidden_heads.isdisjoint(heads)
    # A scan that matched nothing would pass whatever the file said, so pin the
    # targets the distribution is known to register.
    assert "dolphin_desktop._cli:main" in targets
    assert "dolphin_desktop.pytest_plugin" in targets


def test_pytest_plugin_entry_point_key_is_not_bare_dolphin(
    pyproject_text: str,
):
    """The ``[project.entry-points."pytest11"]`` group must not register a bare ``dolphin`` or
    ``dolphinsoft`` key.
    """
    lines = pyproject_text.splitlines()
    in_pytest11 = False
    # All three TOML spellings of the pytest11 table header must be recognised.
    pytest11_headers = (
        '[project.entry-points."pytest11"]',
        "[project.entry-points.'pytest11']",
        "[project.entry-points.pytest11]",
    )
    forbidden_keys = {"dolphin", "dolphinsoft"}
    registered: list[str] = []
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        # New section header ends the previous one.
        if stripped.startswith("["):
            in_pytest11 = stripped in pytest11_headers
            continue
        if not in_pytest11:
            continue
        # Extract the key — all three key forms and both value forms are accepted.
        m = re.match(
            r"""^\s*(?:([\w-]+)|"([^"]+)"|'([^']+)')\s*=\s*"""
            r"""(?:"[^"]+"|'[^']+')\s*$""",
            line,
        )
        if not m:
            continue
        key = m.group(1) or m.group(2) or m.group(3)
        registered.append(key)
        if key in forbidden_keys:
            pytest.fail(
                f"pyproject.toml line {i}: pytest11 entry-point key "
                f"is bare {key!r} — this can collide with a same-named "
                f"pytest-dolphinsoft entry. Use 'dolphin-desktop' "
                f"instead."
            )
    # The table must exist and register exactly the namespaced plugin key — a
    # scan that never found the header would otherwise pass silently.
    assert registered == ["dolphin-desktop"]


def test_backend_entry_point_group_uses_dolphin_desktop_prefix(
    pyproject_text: str,
):
    """The backend plug-in group name must be ``dolphin_desktop.backends``."""
    # Positive check: the dolphin_desktop.backends table header must
    # exist verbatim, in any of the three TOML quoting styles.
    assert re.search(
        r"""^\[project\.entry-points\.["']?dolphin_desktop\.backends["']?\]""",
        pyproject_text,
        re.M,
    ), (
        "Plugin entry-point group ``dolphin_desktop.backends`` header "
        "missing from pyproject.toml. Restore it to keep dolphin-desktop "
        "plug-ins namespaced away from pytest-dolphinsoft plug-ins."
    )
    # Negative check: neither ``dolphin.backends`` nor
    # ``dolphinsoft.backends`` may appear as a table header.
    for forbidden_head in ("dolphin", "dolphinsoft"):
        forbidden = re.search(
            r"""^\[project\.entry-points\.["']?""" + forbidden_head + r"""\.backends["']?\]""",
            pyproject_text,
            re.M,
        )
        assert not forbidden, (
            f"Plugin entry-point group is registered under bare "
            f"'{forbidden_head}.backends' — that namespace is reserved "
            f"for pytest-dolphinsoft."
        )


# Runtime import contract


def test_dolphin_desktop_importable():
    """Positive control: ``import dolphin_desktop`` works and returns the expected package."""
    import dolphin_desktop

    assert dolphin_desktop.__name__ == "dolphin_desktop"
    # And the canonical facade is reachable via the canonical import.
    from dolphin_desktop import Desktop  # noqa: F401


@pytest.mark.parametrize("sister_name", ["dolphin", "dolphinsoft"])
def test_dolphin_desktop_does_not_shadow_sister_import(sister_name):
    """Neither ``import dolphin`` nor ``import dolphinsoft`` may resolve to dolphin_desktop."""
    import importlib
    import sys

    # If the sister module was already imported (e.g., by
    # pytest-dolphinsoft under test), just verify it's not us.
    # Otherwise attempt a fresh import.
    if sister_name in sys.modules:
        mod = sys.modules[sister_name]
    else:
        try:
            mod = importlib.import_module(sister_name)
        except ImportError:
            # Perfectly fine — this environment doesn't have the
            # sister installed. The point is that WE do not provide it.
            return
    # If SOMETHING named ``sister_name`` is importable, verify it
    # isn't secretly dolphin_desktop under an alias.
    import dolphin_desktop

    assert mod is not dolphin_desktop, (
        f"``import {sister_name}`` resolved to dolphin_desktop "
        f"({dolphin_desktop.__file__!r}). This is forbidden — "
        f"the two must be independent packages."
    )
    assert mod.__name__ == sister_name, (
        f"``import {sister_name}`` returned module named "
        f"{mod.__name__!r}; expected an independent package."
    )
