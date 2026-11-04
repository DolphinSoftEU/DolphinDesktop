"""Real native focus classification and low-level hook regression for KAN-576."""

import pytest
from tests.component._recorder_window import check_real_hook_lifecycle, record_native_transitions

from dolphin_desktop._recorder import _PASSWORD_PLACEHOLDER

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(20)]


def test_real_hook_installation_and_cleanup():
    check_real_hook_lifecycle()


def test_real_hook_delayed_password_text_transitions():
    actions, code = record_native_transitions()
    assert [(action.kind, action.data) for action in actions] == [
        ("type_text", _PASSWORD_PLACEHOLDER),
        ("type_text", "tttt"),
        ("type_text", _PASSWORD_PLACEHOLDER),
        ("type_text", _PASSWORD_PLACEHOLDER),
    ]
    assert "tttt" in code
    for secret in ("qqqq", "ssss", "kkkk"):
        assert secret not in code
