"""The desktop examples require Windows."""

import sys

import pytest


@pytest.fixture(autouse=True)
def require_windows():
    if sys.platform != "win32":
        pytest.skip("Desktop examples require Windows")
