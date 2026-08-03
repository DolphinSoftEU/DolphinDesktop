"""Shared fixtures for the dolphin test suite."""

from pathlib import Path

import pytest

from dolphin_desktop import Desktop

# Everything outside tests/framework/ drives a real application — a Qt or
# Delphi binary, VS Code, a JVM, an SAP session, a terminal emulator — and
# cannot pass on a bare CI runner. Marking those suites here rather than
# file by file keeps the rule in one place and applies it to stacks added
# later: tests/framework/ is the headless suite, everything else is
# integration. CI runs `-m "not integration"`.
_HEADLESS_DIR = "framework"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    tests_root = Path(__file__).parent
    for item in items:
        try:
            relative = Path(str(item.fspath)).relative_to(tests_root)
        except ValueError:
            continue  # a test collected from outside tests/ (examples/, docs/)
        if relative.parts and relative.parts[0] != _HEADLESS_DIR:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def desktop() -> Desktop:
    return Desktop(backend="uia")
