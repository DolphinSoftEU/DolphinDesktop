"""Tests for :mod:`dolphin_desktop._telemetry`."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock


def test_telemetry_strips_user_source_but_keeps_dolphin_frames() -> None:
    from dolphin_desktop._telemetry import _strip_user_source

    event = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "module": "project.test",
                                "context_line": "secret",
                                "vars": {"x": "secret"},
                            },
                            {"module": "dolphin_desktop._locator", "context_line": "safe"},
                        ]
                    }
                }
            ]
        }
    }
    frames = _strip_user_source(event)["exception"]["values"][0]["stacktrace"]["frames"]
    assert "context_line" not in frames[0] and frames[1]["context_line"] == "safe"


def test_raised_in_dolphin_rejects_an_empty_frame_list() -> None:
    from dolphin_desktop._telemetry import _raised_in_dolphin

    event = {"exception": {"values": [{"stacktrace": {"frames": []}}]}}

    assert _raised_in_dolphin(event) is False


def test_telemetry_initialization_is_idempotent(monkeypatch) -> None:
    import dolphin_desktop._telemetry as telemetry

    sentry = SimpleNamespace(init=Mock())
    monkeypatch.setitem(sys.modules, "sentry_sdk", sentry)
    monkeypatch.setattr(telemetry, "_initialized", False)
    monkeypatch.setattr(telemetry, "_DOLPHIN_DSN", "https://example.invalid/1")
    monkeypatch.setenv("DOLPHIN_TELEMETRY", "on")
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    telemetry.init()
    telemetry.init()

    sentry.init.assert_called_once()
    assert telemetry.is_enabled() is True
