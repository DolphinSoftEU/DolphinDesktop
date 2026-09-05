"""Tests for :mod:`dolphin_desktop._telemetry`."""

from __future__ import annotations


def test_telemetry_before_send_drops_external_exception_and_keeps_package_error() -> None:
    from dolphin_desktop._exceptions import DolphinError
    from dolphin_desktop._telemetry import _before_send

    event = {"exception": {"values": [{"stacktrace": {"frames": []}}]}}
    assert _before_send(event, {"exc_info": (ValueError, ValueError("x"), None)}) is None
    assert _before_send(event, {"exc_info": (DolphinError, DolphinError("x"), None)}) is event


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
