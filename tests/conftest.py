"""Shared fixtures for the dolphin test suite."""

from pathlib import Path

import pytest

pytest_plugins = ("dolphin_desktop.pytest_plugin",)

from dolphin_desktop import Desktop  # noqa: E402

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
    return Desktop(backend="uia")
