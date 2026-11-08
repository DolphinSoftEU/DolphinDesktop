"""CLI backend selection through the production plugin in a fresh process."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dolphin_desktop import _backend

_PROVIDER = """
from dolphin_desktop import Backend, Capability

class ExternalBackend(Backend):
    id = 'test_ext'
    platform = 'any'

    def capabilities(self):
        return frozenset({Capability.CLICK})

    def find_element(self, parent, criteria):
        return criteria['name']

    def click(self, element, *, button='left'):
        element.append(('external-click', button))

    def type_text(self, element, text):
        element.append(text)

    def get_tree(self, root, *, depth=None):
        return {'name': 'external-root'}

    def screenshot(self, element=None):
        return None
"""


def _run_pytest(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(Path(__file__).resolve().parents[2] / "src")]
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "dolphin_desktop.pytest_plugin",
            "--dolphin-trace=off",
            "--dolphin-video=off",
            *args,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.component
@pytest.mark.parametrize("registration", ["entry_point", "conftest"])
def test_external_backend_reaches_fixtures_and_provider_action(tmp_path, registration):
    (tmp_path / "external_provider.py").write_text(_PROVIDER, encoding="utf-8")
    if registration == "entry_point":
        # Real importlib.metadata discovery of an installed distribution layout.
        dist_info = tmp_path / "external_provider-1.0.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: external-provider\nVersion: 1.0\n",
            encoding="utf-8",
        )
        (dist_info / "entry_points.txt").write_text(
            "[dolphin_desktop.backends]\ntest_ext = external_provider:ExternalBackend\n",
            encoding="utf-8",
        )
    else:
        (tmp_path / "conftest.py").write_text(
            "from dolphin_desktop import register_backend\n"
            "from external_provider import ExternalBackend\n"
            "register_backend(ExternalBackend)\n",
            encoding="utf-8",
        )
    (tmp_path / "test_dispatch.py").write_text(
        """
from dolphin_desktop import Capability, resolve_backend

def test_dispatch(desktop, dolphin_backend):
    assert dolphin_backend == 'test_ext'
    assert desktop.backend_supports(Capability.CLICK)
    assert not desktop.backend_supports(Capability.SET_VALUE)
    desktop.require_capability(Capability.CLICK)
    provider = resolve_backend(dolphin_backend)
    assert type(provider).__module__ == 'external_provider'
    actions = []
    provider.click(actions, button='right')
    assert actions == [('external-click', 'right')]
""",
        encoding="utf-8",
    )
    result = _run_pytest(tmp_path, "--dolphin-backend=test_ext")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.component
def test_unknown_backend_is_usage_error_before_test_execution(tmp_path):
    (tmp_path / "test_unreachable.py").write_text(
        'raise AssertionError("test must not be collected")\n', encoding="utf-8"
    )
    result = _run_pytest(tmp_path, "--dolphin-backend=nonexistent_backend")
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, output
    assert "--dolphin-backend" in output
    assert "Unknown backend 'nonexistent_backend'" in output
    assert "Available backends:" in output
    assert "uia" in output and "win32" in output
    assert "test must not be collected" not in output


@pytest.mark.parametrize("backend_id", ["uia", "win32", "qt", "image", "cdp", "auto"])
def test_cli_accepts_registry_ids_without_constructing_backends(monkeypatch, backend_id):
    class CannotConstruct:
        def __init__(self):
            raise AssertionError("CLI validation must not construct backends")

    monkeypatch.setattr(_backend, "_REGISTRY", {backend_id: CannotConstruct})
    monkeypatch.setattr(_backend, "_load_plugins", lambda: None)
    _backend._validate_backend_id(backend_id)
