"""Shared fixtures for the dolphin test suite."""

import pytest

from dolphin_desktop import Desktop


@pytest.fixture(scope="session")
def desktop() -> Desktop:
    return Desktop(backend="uia")
