"""Shared fixtures for the dolphin test suite."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests._preflight import run_preflight

if TYPE_CHECKING:
    from dolphin_desktop import Desktop


def pytest_addoption(parser: pytest.Parser) -> None:
    """Declare Dolphin options without importing the production package."""
    group = parser.getgroup("dolphin", "Dolphin desktop testing")
    group.addoption(
        "--dolphin-backend",
        default="uia",
        choices=["uia", "win32"],
        help="pywinauto backend to use (default: uia)",
    )
    group.addoption(
        "--dolphin-timeout",
        type=float,
        default=None,
        help="Default element wait timeout in seconds (default: 10; also: DOLPHIN_TIMEOUT env var)",
    )
    group.addoption(
        "--dolphin-screenshot-on-fail",
        action="store_true",
        default=False,
        help="Capture a screenshot of the active window on test failure",
    )
    group.addoption(
        "--dolphin-headless",
        action="store_true",
        default=False,
        help="Run AUT processes on a hidden desktop",
    )
    group.addoption(
        "--dolphin-trace",
        default=None,
        choices=["off", "on-failure", "always"],
        metavar="{off,on-failure,always}",
        help="Trace capture mode",
    )
    group.addoption(
        "--dolphin-trace-dir",
        default="dolphin-traces",
        metavar="PATH",
        help="Directory for trace files",
    )
    group.addoption(
        "--dolphin-video",
        default=None,
        choices=["off", "keepfailedonly", "keepall"],
        metavar="{off,keepfailedonly,keepall}",
        help="Video recording mode",
    )
    group.addoption(
        "--dolphin-video-dir",
        default="dolphin-videos",
        metavar="PATH",
        help="Directory for video files",
    )
    group.addoption(
        "--dolphin-html",
        default=None,
        metavar="PATH",
        help="Write an HTML summary report to PATH",
    )
    group.addoption(
        "--dolphin-desktop-log-level",
        default=None,
        choices=["DEBUG", "INFO", "ERROR"],
        metavar="{DEBUG,INFO,ERROR}",
        help="Dolphin log verbosity",
    )
    group.addoption(
        "--dolphin-retry",
        type=int,
        default=None,
        metavar="N",
        help="Retry tests on transient Dolphin errors",
    )


def pytest_configure(config: pytest.Config) -> None:
    for marker in (
        "dolphin(timeout=..., video_mode=..., headless=...): per-test Dolphin config",
        "dolphin_headless: test requires headless desktop mode",
        "unit: hermetic unit test",
        "windows_component: controlled Windows component test",
        "component: controlled component test",
        "system: real AUT system test",
        "external: test requiring an external system",
    ):
        config.addinivalue_line("markers", marker)


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail configured jobs before nested conftests are collected."""
    run_preflight()


def _load_production_plugin(config: pytest.Config) -> None:
    """Register the production plugin after pytest-cov starts measurement."""
    if config.pluginmanager.get_plugin("dolphin_desktop.pytest_plugin") is not None:
        return
    from importlib import import_module

    plugin = import_module("dolphin_desktop.pytest_plugin")
    # pytest replays pytest_addoption/pytest_configure immediately when a
    # plugin is registered after those phases.  The test bootstrap already
    # declared the same options and markers without importing dolphin_desktop.
    addoption = plugin.pytest_addoption
    configure = plugin.pytest_configure
    plugin.pytest_addoption = lambda parser: None
    plugin.pytest_configure = lambda config: None
    try:
        config.pluginmanager.register(plugin)
    finally:
        plugin.pytest_addoption = addoption
        plugin.pytest_configure = configure


@pytest.hookimpl(tryfirst=True)
def pytest_collection(session: pytest.Session) -> None:
    """Load production fixtures after coverage starts, before collection."""
    _load_production_plugin(session.config)


# Categories are assigned by repository ownership, not by a broad
# "everything outside framework" rule.  Keeping the mapping here makes an
# unclassified new suite fail collection instead of silently disappearing
# from the required unit job.
_CATEGORY_BY_PATH = {
    "framework": "unit",
    "test_support": "unit",
    "component": "windows_component",
    "delphi": "system",
    "electron": "system",
    "oracle_forms": "system",
    "powerbuilder": "system",
    "qt": "system",
    "sap": "external",
    "steam": "external",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    tests_root = Path(__file__).parent
    for item in items:
        try:
            relative = Path(str(item.fspath)).relative_to(tests_root)
        except ValueError:
            continue  # a test collected from outside tests/ (examples/, docs/)
        if not relative.parts:
            raise pytest.UsageError(f"Could not classify test: {item.nodeid}")

        if relative.parts[0] == "mainframe":
            category = {
                "hllapi_mock": "unit",
                "mock_tn3270": "component",
                "pub400": "external",
                "tn5250_native": "external",
            }.get(relative.parts[1] if len(relative.parts) > 1 else "")
        else:
            category = _CATEGORY_BY_PATH.get(relative.parts[0])

        if category is None:
            raise pytest.UsageError(
                f"Unclassified test path {relative.as_posix()!r}; "
                "add it to the explicit test category map."
            )

        item.add_marker(getattr(pytest.mark, category))
        # Preserve the old selector during the migration, but derive it from
        # the explicit responsibility category. Unit and component tests stay
        # in the gate; only real-AUT and external tests are legacy
        # integration tests.
        if category in {"system", "external"}:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def desktop() -> Desktop:
    from dolphin_desktop import Desktop

    return Desktop(backend="uia")
