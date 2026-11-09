"""CLI backend selection through the production plugin in a fresh process."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dolphin_desktop import _backend, pytest_plugin

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


def _run_pytest(
    tmp_path: Path, *args: str, bootstrap: bool = False
) -> subprocess.CompletedProcess[str]:
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
            *([] if bootstrap else ["-p", "dolphin_desktop.pytest_plugin"]),
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


def _write_bootstrap(tmp_path: Path) -> Path:
    test_directory = tmp_path / "tests" / "framework"
    test_directory.mkdir(parents=True)
    repo_tests = Path(__file__).resolve().parents[1]
    for name in ("conftest.py", "_preflight.py", "__init__.py"):
        shutil.copy2(repo_tests / name, tmp_path / "tests" / name)
    return test_directory


@pytest.mark.component
@pytest.mark.parametrize(
    "registration", ["entry_point", "conftest", "nested_conftest", "bootstrap"]
)
def test_external_backend_reaches_fixtures_and_provider_action(tmp_path, registration):
    (tmp_path / "external_provider.py").write_text(_PROVIDER, encoding="utf-8")
    test_directory = tmp_path
    if registration == "nested_conftest":
        test_directory = tmp_path / "tests" / "framework"
        test_directory.mkdir(parents=True)
    if registration == "bootstrap":
        test_directory = _write_bootstrap(tmp_path)
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
        (test_directory / "conftest.py").write_text(
            "from dolphin_desktop import register_backend\n"
            "from external_provider import ExternalBackend\n"
            "register_backend(ExternalBackend)\n",
            encoding="utf-8",
        )
    (test_directory / "test_dispatch.py").write_text(
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
    result = _run_pytest(
        tmp_path, "--dolphin-backend=test_ext", bootstrap=registration == "bootstrap"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.mark.component
@pytest.mark.parametrize("bootstrap", [False, True])
def test_unknown_backend_is_usage_error_before_test_execution(tmp_path, bootstrap):
    test_directory = _write_bootstrap(tmp_path) if bootstrap else tmp_path
    (test_directory / "test_unreachable.py").write_text(
        'def test_unreachable():\n    raise AssertionError("test must not run")\n', encoding="utf-8"
    )
    result = _run_pytest(tmp_path, "--dolphin-backend=nonexistent_backend", bootstrap=bootstrap)
    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, output
    assert "--dolphin-backend" in output
    assert "Unknown backend 'nonexistent_backend'" in output
    assert "Available backends:" in output
    assert "uia" in output and "win32" in output
    assert "test must not run" not in output


@pytest.mark.component
@pytest.mark.parametrize("bootstrap", [False, True])
def test_auto_cli_uses_concrete_backend_for_native_launch_and_connect(tmp_path, bootstrap):
    test_directory = _write_bootstrap(tmp_path) if bootstrap else tmp_path
    (test_directory / "test_auto.py").write_text(
        """
from unittest.mock import Mock, call
from dolphin_desktop import resolve_backend, _desktop, _runner

def test_auto(desktop, dolphin_backend, monkeypatch):
    assert dolphin_backend == 'auto'
    expected = resolve_backend('auto').id
    process_app = Mock(process=321)
    native_app = Mock(return_value=process_app)
    wrapper = Mock(return_value='wrapped')
    monkeypatch.setattr(_desktop, '_PyWinApp', native_app)
    monkeypatch.setattr(_desktop, 'Application', wrapper)
    monkeypatch.setattr(_desktop, '_process_image_path', lambda pid: 'probe.exe')
    monkeypatch.setattr(_runner, 'launch_cmd_on_desktop', Mock(return_value=(321, 987)))
    monkeypatch.setattr(desktop, '_resolved_hidden', False)
    assert desktop.launch('probe.exe', startup_delay=0) == 'wrapped'
    assert desktop.connect(process=321) == 'wrapped'
    assert native_app.call_args_list == [call(backend=expected), call(backend=expected)]
    assert all(args.kwargs['backend'] == expected for args in wrapper.call_args_list)
""",
        encoding="utf-8",
    )
    result = _run_pytest(tmp_path, "--dolphin-backend=auto", bootstrap=bootstrap)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


@pytest.fixture
def backend_config():
    config = SimpleNamespace(backend_id="uia")
    config.getoption = lambda name, default=None: (
        config.backend_id if name == "--dolphin-backend" else default
    )
    return config


@pytest.mark.parametrize("backend_id", ["uia", "win32", "qt", "image", "cdp", "auto", "test_ext"])
def test_collection_accepts_registry_ids_without_constructing_backends(
    monkeypatch, backend_config, backend_id
):
    class CannotConstruct:
        def __init__(self):
            raise AssertionError("CLI validation must not construct backends")

    monkeypatch.setattr(_backend, "_REGISTRY", {backend_id: CannotConstruct})
    monkeypatch.setattr(_backend, "_load_plugins", lambda: None)
    backend_config.backend_id = backend_id
    pytest_plugin.pytest_collection_modifyitems(backend_config)
    assert backend_config.backend_id == backend_id


def test_collection_rejects_unknown_backend(backend_config):
    backend_config.backend_id = "nonexistent_backend"
    with pytest.raises(pytest.UsageError) as error:
        pytest_plugin.pytest_collection_modifyitems(backend_config)

    message = str(error.value)
    assert "--dolphin-backend: Unknown backend 'nonexistent_backend'" in message
    assert "Available backends:" in message
    assert "uia" in message and "win32" in message
    assert isinstance(error.value.__cause__, ValueError)
