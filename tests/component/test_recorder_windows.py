"""Installation and cleanup of real low-level recorder hooks."""

import pytest
from tests.component._recorder_probe import check_hook_lifecycle

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(35)]


def test_real_recorder_hook_installation_and_cleanup():
    check_hook_lifecycle()
