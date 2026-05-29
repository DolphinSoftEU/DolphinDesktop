"""Tests for auto-waiting and timeout configuration."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

import dolphin_desktop
import dolphin_desktop._config as _cfg
from dolphin_desktop import ElementNotFoundError
from dolphin_desktop._locator import Locator, _ResolvedLocator
from dolphin_desktop._window import Window


def _window(spec: MagicMock | None = None) -> Window:
    """Return a Window backed by *spec* (or a fresh MagicMock if omitted)."""
    return Window(spec if spec is not None else MagicMock())


def _failing_spec() -> MagicMock:
    """Return a mock spec whose child_window().wait() always raises."""
    spec = MagicMock()
    child = MagicMock()
    child.wait.side_effect = Exception("element not visible")
    spec.child_window.return_value = child
    spec.children.return_value = []
    return spec


def _succeeding_spec() -> MagicMock:
    """Return a mock spec whose child_window().wait() always succeeds."""
    spec = MagicMock()
    child = MagicMock()
    spec.child_window.return_value = child
    spec.children.return_value = []
    return spec


# ---------------------------------------------------------------------------
# dolphin_desktop.config() code configuration
# ---------------------------------------------------------------------------


class TestConfigCode:
    def test_config_sets_timeout(self):
        original = _cfg.get_timeout()
        try:
            dolphin_desktop.config(timeout=3.0)
            assert _cfg.get_timeout() == pytest.approx(3.0)
        finally:
            _cfg._defaults["timeout"] = original

    def test_config_sets_poll_interval(self):
        original = _cfg.get_poll_interval()
        try:
            dolphin_desktop.config(poll_interval=0.25)
            assert _cfg.get_poll_interval() == pytest.approx(0.25)
        finally:
            _cfg._defaults["poll_interval"] = original

    def test_config_noop_when_no_args(self):
        before_timeout = _cfg.get_timeout()
        before_poll = _cfg.get_poll_interval()
        dolphin_desktop.config()
        assert _cfg.get_timeout() == pytest.approx(before_timeout)
        assert _cfg.get_poll_interval() == pytest.approx(before_poll)

    def test_config_accepts_int(self):
        original = _cfg.get_timeout()
        try:
            dolphin_desktop.config(timeout=5)
            assert _cfg.get_timeout() == 5.0
            assert isinstance(_cfg.get_timeout(), float)
        finally:
            _cfg._defaults["timeout"] = original

    def test_get_timeout_returns_float(self):
        assert isinstance(_cfg.get_timeout(), float)

    def test_get_poll_interval_returns_float(self):
        assert isinstance(_cfg.get_poll_interval(), float)

    def test_poll_interval_default_is_100ms(self):
        assert _cfg.get_poll_interval() == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# DOLPHIN_TIMEOUT environment variable configuration
# ---------------------------------------------------------------------------


class TestConfigEnvVar:
    def test_env_var_sets_timeout_on_module_load(self, monkeypatch):
        """DOLPHIN_TIMEOUT is read at _config import time; reload confirms the mapping."""
        from importlib import reload

        original = dict(_cfg._defaults)
        monkeypatch.setenv("DOLPHIN_TIMEOUT", "7.5")
        try:
            reload(_cfg)
            assert _cfg._defaults["timeout"] == pytest.approx(7.5)
        finally:
            _cfg._defaults.update(original)

    def test_env_var_absent_gives_default(self, monkeypatch):
        from importlib import reload

        original = dict(_cfg._defaults)
        monkeypatch.delenv("DOLPHIN_TIMEOUT", raising=False)
        try:
            reload(_cfg)
            assert _cfg._defaults["timeout"] == pytest.approx(10.0)
        finally:
            _cfg._defaults.update(original)

    def test_env_var_float_string(self, monkeypatch):
        from importlib import reload

        original = dict(_cfg._defaults)
        monkeypatch.setenv("DOLPHIN_TIMEOUT", "2.5")
        try:
            reload(_cfg)
            assert _cfg._defaults["timeout"] == pytest.approx(2.5)
        finally:
            _cfg._defaults.update(original)


# ---------------------------------------------------------------------------
# @pytest.mark.dolphin(timeout=X) marker configuration
# ---------------------------------------------------------------------------


@pytest.mark.dolphin(timeout=1.5)
def test_marker_overrides_timeout_during_test():
    """The @pytest.mark.dolphin(timeout=X) marker sets the active timeout."""
    assert _cfg.get_timeout() == pytest.approx(1.5)


@pytest.mark.dolphin(timeout=0.5)
def test_marker_uses_fractional_timeout():
    assert _cfg.get_timeout() == pytest.approx(0.5)


def test_marker_timeout_is_restored_after_marked_test():
    """After a marked test, the global timeout is back to its pre-test value."""
    # If the fixture rolls back correctly, the current timeout must differ from
    # every marker value used above (1.5, 0.5).
    current = _cfg.get_timeout()
    assert current != pytest.approx(1.5)
    assert current != pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Locator timeout propagation
# ---------------------------------------------------------------------------


class TestLocatorTimeout:
    def test_new_locator_uses_global_timeout(self):
        original = _cfg.get_timeout()
        try:
            _cfg._defaults["timeout"] = 8.0
            loc = Locator(_window())
            assert loc._timeout == pytest.approx(8.0)
        finally:
            _cfg._defaults["timeout"] = original

    def test_timeout_clone_has_new_value(self):
        loc = Locator(_window())
        clone = loc.timeout(2.0)
        assert clone._timeout == pytest.approx(2.0)

    def test_timeout_clone_does_not_mutate_original(self):
        loc = Locator(_window())
        original_t = loc._timeout
        loc.timeout(99.0)
        assert loc._timeout == pytest.approx(original_t)

    def test_resolved_locator_timeout_clone_is_resolved_locator(self):
        rl = _ResolvedLocator(MagicMock())
        clone = rl.timeout(3.0)
        assert isinstance(clone, _ResolvedLocator)
        assert clone._timeout == pytest.approx(3.0)

    def test_resolved_locator_timeout_does_not_mutate_original(self):
        rl = _ResolvedLocator(MagicMock())
        t0 = rl._timeout
        rl.timeout(77.0)
        assert rl._timeout == pytest.approx(t0)


# ---------------------------------------------------------------------------
# Auto-wait without explicit wait_for
# ---------------------------------------------------------------------------


class TestAutoWaitRetry:
    def test_click_succeeds_when_element_appears_after_delay(self):
        """click() waits automatically without an explicit wait_for().

        pywinauto's wait() is mocked to block until an event fires, simulating
        a dialog that appears asynchronously after ~300 ms.
        """
        ready = threading.Event()
        spec = MagicMock()
        child = MagicMock()

        def delayed_wait(state, timeout):
            if not ready.wait(timeout=timeout):
                raise RuntimeError("element did not appear in time")

        child.wait.side_effect = delayed_wait
        spec.child_window.return_value = child
        spec.children.return_value = []

        threading.Timer(0.3, ready.set).start()

        loc = _window(spec).get_by_role("Button").timeout(2.0)
        loc.click()  # no explicit wait_for; auto-wait handles the delay

        child.click_input.assert_called_once()

    def test_wait_passes_timeout_to_pywinauto(self):
        """Locator calls pywinauto's wait() with its own _timeout value."""
        spec = _succeeding_spec()
        loc = _window(spec).get_by_role("Button").timeout(4.2)
        loc.click()
        spec.child_window.return_value.wait.assert_called_once_with(
            "exists visible", timeout=pytest.approx(4.2)
        )

    def test_chained_actions_without_explicit_wait(self):
        """Chain of actions uses auto-wait on every step."""
        spec = _succeeding_spec()
        win = _window(spec)
        win.get_by_role("Edit").timeout(1.0).type_text("hello")
        win.get_by_role("Button").timeout(1.0).click()

        child = spec.child_window.return_value
        assert child.type_keys.called
        assert child.click_input.called


# ---------------------------------------------------------------------------
# ElementNotFoundError message format
# ---------------------------------------------------------------------------


class TestElementNotFoundError:
    def test_raises_element_not_found_error(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError):
            loc.click()

    def test_error_message_includes_waited_seconds(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc.click()
        assert "0.05s" in str(exc_info.value)

    def test_error_message_includes_last_seen_tree(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc.click()
        assert "last seen tree" in str(exc_info.value).lower()

    def test_error_message_includes_criteria(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc.click()
        assert "Button" in str(exc_info.value)

    def test_element_not_found_is_dolphin_error(self):
        from dolphin_desktop import DolphinError

        assert issubclass(ElementNotFoundError, DolphinError)

    def test_double_click_raises_on_missing_element(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError):
            loc.double_click()

    def test_type_text_raises_on_missing_element(self):
        loc = _window(_failing_spec()).get_by_role("Edit").timeout(0.05)
        with pytest.raises(ElementNotFoundError):
            loc.type_text("hello")

    def test_timeout_value_in_error_matches_locator_timeout(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(1.23)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc.click()
        assert "1.23s" in str(exc_info.value)
