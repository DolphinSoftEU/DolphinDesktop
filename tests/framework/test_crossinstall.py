"""Cross-install pytest-dolphinsoft + dolphin-desktop verification."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STUB_ROOT = REPO_ROOT / "tests" / "fixtures" / "pytest_dolphinsoft_stub"


# ``timeout=300`` overrides the repo-wide ``timeout = 30`` — the
# module-scoped fixture below builds two wheels, creates a fresh venv,
# and pip-installs both, which routinely takes 30-90s on cold caches.
pytestmark = [pytest.mark.slow, pytest.mark.timeout(300)]


# Helpers


def _build_wheel(project_dir: Path, out_dir: Path) -> Path:
    """Invoke ``python -m build --wheel`` and return the built path.

    The built wheel's DISTRIBUTION name may differ from the top-level
    PACKAGE name, so we snapshot the outdir contents around the build
    and return the freshly-added file instead of sorting by name.
    """
    before = set(out_dir.glob("*.whl"))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(out_dir),
            str(project_dir),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        env=_isolated_env(),
    )
    if result.returncode != 0:
        raise AssertionError(
            f"build failed for {project_dir.name}: stdout={result.stdout}\nstderr={result.stderr}"
        )
    after = set(out_dir.glob("*.whl"))
    new = after - before
    assert len(new) == 1, (
        f"expected exactly one new wheel in {out_dir} after building "
        f"{project_dir.name}, saw {sorted(w.name for w in new)}"
    )
    return next(iter(new))


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


#: Env vars scrubbed from every subprocess so the "fresh venv"
#: guarantee holds — the parent may inherit ``PYTHONPATH``,
#: ``PIP_INDEX_URL``, ``VIRTUAL_ENV`` etc. which would poison the
#: venv's site-packages resolution or change where pip fetches from.
_ENV_LEAKAGE_KEYS = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PYTHONNOUSERSITE",
        "VIRTUAL_ENV",
        "VIRTUAL_ENV_PROMPT",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "CONDA_PYTHON_EXE",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_TARGET",
        "PIP_USER",
        "PIP_PREFIX",
        "PIP_REQUIRE_VIRTUALENV",
        "PIP_CONFIG_FILE",
    }
)


def _isolated_env() -> dict:
    """Copy the parent env, stripping vars that would leak the outer
    interpreter's site-packages or pip config into the fresh venv."""
    return {k: v for k, v in os.environ.items() if k not in _ENV_LEAKAGE_KEYS}


def _run(cmd: list[str | Path], **kw: object) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [str(c) for c in cmd],
        capture_output=True,
        text=True,
        timeout=kw.pop("timeout", 120),
        env=kw.pop("env", None) or _isolated_env(),
        **kw,  # type: ignore[arg-type]
    )
    return proc


# Fixture — the fully-set-up cross-install venv


@pytest.fixture(scope="module")
def crossinstall_venv(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Build both wheels, create a fresh venv, install both, and return the venv metadata.

    Module-scoped: the whole setup — two wheel builds, a fresh venv and two
    pip invocations — runs 30-90s depending on cache warmth (hence the
    module-level ``timeout(300)``), so it is paid once per file instead of
    once per test.
    """
    work = tmp_path_factory.mktemp("crossinstall")
    wheels_dir = work / "wheels"
    wheels_dir.mkdir()

    stub_wheel = _build_wheel(STUB_ROOT, wheels_dir)
    dd_wheel = _build_wheel(REPO_ROOT, wheels_dir)

    venv_dir = work / ".venv"
    _run([sys.executable, "-m", "venv", str(venv_dir)], timeout=60)
    py = _venv_python(venv_dir)
    assert py.exists(), f"venv python missing at {py}"

    # Fresh pip to avoid resolver quirks.
    _run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], timeout=120)

    # Install BOTH distributions in one pip invocation so pip's file
    # conflict detection sees both wheels simultaneously.
    install = _run(
        [py, "-m", "pip", "install", "--quiet", str(stub_wheel), str(dd_wheel)],
        timeout=180,
    )
    assert install.returncode == 0, (
        f"pip install both wheels exited {install.returncode}. "
        f"No-file-conflict criterion failed.\n"
        f"stdout:\n{install.stdout}\nstderr:\n{install.stderr}"
    )

    return {
        "py": py,
        "stub_wheel": stub_wheel,
        "dd_wheel": dd_wheel,
        "workdir": work,
    }


# Test 1 — no file conflict between the two wheels


def test_no_top_level_dolphin_files_shared_between_wheels(crossinstall_venv):
    """Zip inspection: stub owns ``dolphinsoft/`` paths, desktop wheel owns
    ``dolphin_desktop/`` paths;
    overlap would trigger pip's "would overwrite" error.
    """
    import zipfile

    def top_paths(whl: Path) -> set[str]:
        with zipfile.ZipFile(whl) as z:
            return {n.split("/", 1)[0] for n in z.namelist() if n}

    stub_tops = top_paths(crossinstall_venv["stub_wheel"])
    dd_tops = top_paths(crossinstall_venv["dd_wheel"])
    # dist-info directories are per-distribution and don't collide.
    stub_pkg = {t for t in stub_tops if not t.endswith(".dist-info")}
    dd_pkg = {t for t in dd_tops if not t.endswith(".dist-info")}
    assert stub_pkg == {"dolphinsoft"}, f"stub wheel top-level != dolphinsoft: {stub_pkg}"
    assert dd_pkg == {"dolphin_desktop"}, (
        f"dolphin-desktop wheel top-level != dolphin_desktop: {dd_pkg}"
    )
    overlap = stub_pkg & dd_pkg
    assert not overlap, f"Wheels share top-level namespace: {overlap}. 'last install wins' hazard."


# Test 2 — both imports resolve to their own distribution


def test_import_dolphinsoft_resolves_to_pytest_dolphinsoft_stub(crossinstall_venv):
    """``import dolphinsoft`` MUST land on the stub package, not on dolphin-desktop."""
    py = crossinstall_venv["py"]
    script = textwrap.dedent(
        """
        import dolphinsoft, dolphin_desktop, json, sys
        info = {
            "dolphinsoft_file": dolphinsoft.__file__,
            "dolphin_desktop_file": dolphin_desktop.__file__,
            "has_step": hasattr(dolphinsoft, "step"),
            "python": sys.executable,
        }
        print("CROSSINSTALL_JSON:" + json.dumps(info))
        """
    )
    r = _run([py, "-c", script])
    assert r.returncode == 0, (
        f"cross-install import check failed rc={r.returncode}\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    m = re.search(r"CROSSINSTALL_JSON:(.+)", r.stdout)
    assert m, f"stdout didn't contain CROSSINSTALL_JSON payload: {r.stdout!r}"
    info = json.loads(m.group(1))
    assert "site-packages" in info["dolphinsoft_file"].replace("\\", "/"), (
        f"``import dolphinsoft`` resolved outside the fresh venv's "
        f"site-packages: {info['dolphinsoft_file']!r}. "
        f"pytest-dolphinsoft stub was not the resolved distribution — "
        f"the venv is leaky."
    )
    # Segment-boundary check: 'dolphinsoft' must be its own path
    # segment, not a prefix of another package.
    dp = info["dolphinsoft_file"].replace("\\", "/")
    assert re.search(r"/dolphinsoft/__init__\.py$", dp), (
        f"``import dolphinsoft`` did not resolve to a package rooted "
        f"at ``dolphinsoft/__init__.py``: {dp!r}"
    )
    assert info["has_step"], "dolphinsoft.step is missing — public step() API broken"
    dd = info["dolphin_desktop_file"].replace("\\", "/")
    assert re.search(r"/dolphin_desktop/__init__\.py$", dd), (
        f"``import dolphin_desktop`` did not resolve to the desktop package: {dd!r}"
    )


# Test 3 — pytest11 plugin auto-registration for both


def test_pytest11_auto_loads_both_plugins(crossinstall_venv):
    """Both ``dolphinsoft`` (stub) and ``dolphin-desktop`` must appear in
    ``pytest --trace-config``.
    """
    py = crossinstall_venv["py"]
    # Run pytest in a scratch dir with an empty conftest so trace-config
    # output isn't influenced by any leftover local config.
    scratch = crossinstall_venv["workdir"] / "scratch"
    scratch.mkdir(exist_ok=True)
    (scratch / "conftest.py").write_text("", encoding="utf-8")
    r = _run(
        [py, "-m", "pytest", "--trace-config", "-p", "no:cacheprovider", "--collect-only", "-q"],
        cwd=scratch,
        timeout=120,
    )
    out = r.stdout + "\n" + r.stderr
    # Loosely match: pytest's trace-config prints one line per plugin
    # discovered. Look for the two distributions we care about.
    assert re.search(r"\bdolphin-desktop\b|\bdolphin_desktop\b", out), (
        f"pytest --trace-config did not mention dolphin-desktop. "
        f"pytest11 plugin auto-registration is broken.\n"
        f"---output---\n{out}"
    )
    # The stub's plugin module is dolphinsoft._plugin, but pytest
    # reports it by entry-point name = 'dolphinsoft' or by the module
    # path.
    assert re.search(r"dolphinsoft\._plugin|PLUGIN.*dolphinsoft\b", out), (
        f"pytest --trace-config did not mention the pytest-dolphinsoft "
        f"stub plugin.\n---output---\n{out}"
    )


def test_pytest11_exposes_dolphin_desktop_options(crossinstall_venv):
    """A clean pytest process exposes the installed plugin's public options."""
    py = crossinstall_venv["py"]
    scratch = crossinstall_venv["workdir"] / "scratch"
    r = _run([py, "-m", "pytest", "--help", "-p", "no:cacheprovider"], cwd=scratch)
    out = r.stdout + "\n" + r.stderr
    assert r.returncode == 0, f"pytest --help failed:\n{out}"
    for option in ("--dolphin-backend", "--dolphin-timeout", "--dolphin-retry"):
        assert option in out, f"pytest --help did not expose {option}:\n{out}"


# Test 4 — dolphinsoft.step emits stdout-prefix events


def test_step_emits_stdout_prefix_events(crossinstall_venv):
    """A ``dolphinsoft.step("…")`` block must print ``DOLPHINSOFT_EVENT:``
    lines for ``step_start`` +
    ``step_finish``.
    """
    py = crossinstall_venv["py"]
    scratch = crossinstall_venv["workdir"] / "smoke"
    scratch.mkdir(exist_ok=True)
    (scratch / "conftest.py").write_text("", encoding="utf-8")
    (scratch / "test_step.py").write_text(
        textwrap.dedent(
            """
            import dolphinsoft
            def test_login_flow():
                with dolphinsoft.step("Fill login form"):
                    assert 1 + 1 == 2
            """
        ),
        encoding="utf-8",
    )
    r = _run(
        [py, "-m", "pytest", "-s", "-p", "no:cacheprovider", "test_step.py"],
        cwd=scratch,
        timeout=60,
    )
    assert r.returncode == 0, (
        f"pytest smoke run failed rc={r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    # pytest prints the test-file path inline BEFORE the first stdout
    # line the test emits, so ``line.startswith("DOLPHINSOFT_EVENT:")``
    # would miss the first event (its line is prefixed by
    # ``"test_step.py "``). Search anywhere in each line and pull the
    # JSON payload after the marker.
    events = [
        json.loads(m.group(1))
        for line in r.stdout.splitlines()
        for m in [re.search(r"DOLPHINSOFT_EVENT:\s*(\{.*\})", line)]
        if m
    ]
    kinds = [e.get("kind") for e in events]
    assert "step_start" in kinds, f"No step_start event emitted. Full stdout:\n{r.stdout}"
    assert "step_finish" in kinds, f"No step_finish event emitted. Full stdout:\n{r.stdout}"
    # Order sanity — start precedes finish for the same step name.
    starts = [i for i, e in enumerate(events) if e.get("kind") == "step_start"]
    finishes = [i for i, e in enumerate(events) if e.get("kind") == "step_finish"]
    assert starts and finishes and max(starts) < min(finishes), (
        f"step_start must precede step_finish. Events: {events}"
    )
    # Named payload matches what the test wrote.
    step_names = {e.get("name") for e in events if e.get("kind") == "step_start"}
    assert "Fill login form" in step_names, (
        f"step_start payload missing test's step name. Events: {events}"
    )
