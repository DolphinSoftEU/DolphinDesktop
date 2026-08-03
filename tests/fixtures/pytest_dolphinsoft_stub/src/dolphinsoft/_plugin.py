"""Stub pytest11 plugin — flips ``dolphinsoft._event_mode_active``."""

from __future__ import annotations

import dolphinsoft


def pytest_configure(config):
    dolphinsoft._event_mode_active = True
