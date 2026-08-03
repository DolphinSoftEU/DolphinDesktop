"""Environment variables must pass the same validation ``config()`` enforces.

``DOLPHIN_TIMEOUT=-1`` used to reach the locator and make every lookup fail
instantly; ``DOLPHIN_VIDEO=keepAll`` used to discard every recording because
the mode comparison downstream is case-sensitive.
"""

from __future__ import annotations

import importlib

import pytest

from dolphin_desktop import _config
from dolphin_desktop._config import (
    MAX_VIDEO_FPS,
    VALID_TRACE_MODES,
    _env_choice,
    _env_number,
    _video_modes,
)


class TestNumericEnvRanges:
    def test_negative_timeout_is_refused(self, monkeypatch):
        monkeypatch.setenv("DOLPHIN_TIMEOUT", "-1")
        with pytest.warns(UserWarning):
            assert _env_number("DOLPHIN_TIMEOUT", 10.0, float, minimum=0) == 10.0

    def test_zero_timeout_is_still_accepted(self, monkeypatch):
        monkeypatch.setenv("DOLPHIN_TIMEOUT", "0")
        assert _env_number("DOLPHIN_TIMEOUT", 10.0, float, minimum=0) == 0.0

    def test_fps_above_the_ceiling_is_refused(self, monkeypatch):
        monkeypatch.setenv("DOLPHIN_VIDEO_FPS", "500")
        with pytest.warns(UserWarning):
            value = _env_number("DOLPHIN_VIDEO_FPS", 10, int, minimum=1, maximum=MAX_VIDEO_FPS)
        assert value == 10

    def test_negative_retry_is_refused(self, monkeypatch):
        monkeypatch.setenv("DOLPHIN_RETRY", "-3")
        with pytest.warns(UserWarning):
            assert _env_number("DOLPHIN_RETRY", 0, int, minimum=0) == 0

    def test_a_valid_value_passes_through(self, monkeypatch):
        monkeypatch.setenv("DOLPHIN_VIDEO_FPS", "24")
        assert _env_number("DOLPHIN_VIDEO_FPS", 10, int, minimum=1, maximum=30) == 24


class TestEnumEnvValues:
    def test_video_mode_is_matched_case_insensitively(self, monkeypatch):
        monkeypatch.setenv("DOLPHIN_VIDEO", "keepAll")
        assert _env_choice("DOLPHIN_VIDEO", "keepfailedonly", _video_modes()) == "keepall"

    def test_an_unknown_video_mode_falls_back(self, monkeypatch):
        monkeypatch.setenv("DOLPHIN_VIDEO", "keep-everything")
        with pytest.warns(UserWarning):
            value = _env_choice("DOLPHIN_VIDEO", "keepfailedonly", _video_modes())
        assert value == "keepfailedonly"

    def test_an_unknown_trace_mode_falls_back(self, monkeypatch):
        monkeypatch.setenv("DOLPHIN_TRACE", "sometimes")
        with pytest.warns(UserWarning):
            assert _env_choice("DOLPHIN_TRACE", "on-failure", VALID_TRACE_MODES) == ("on-failure")

    def test_surrounding_whitespace_is_tolerated(self, monkeypatch):
        monkeypatch.setenv("DOLPHIN_TRACE", " Always ")
        assert _env_choice("DOLPHIN_TRACE", "on-failure", VALID_TRACE_MODES) == "always"


class TestDefaultsAreBuiltThroughTheValidators:
    def test_a_hostile_environment_cannot_poison_the_defaults(self, monkeypatch):
        monkeypatch.setenv("DOLPHIN_TIMEOUT", "-1")
        monkeypatch.setenv("DOLPHIN_VIDEO", "keepAll")
        monkeypatch.setenv("DOLPHIN_VIDEO_FPS", "500")
        try:
            with pytest.warns(UserWarning):
                reloaded = importlib.reload(_config)
            assert reloaded.get_timeout() == 10.0
            assert reloaded.get_video_mode() == "keepall"
            assert reloaded.get_video_fps() == 10
        finally:
            for name in ("DOLPHIN_TIMEOUT", "DOLPHIN_VIDEO", "DOLPHIN_VIDEO_FPS"):
                monkeypatch.delenv(name, raising=False)
            importlib.reload(_config)
