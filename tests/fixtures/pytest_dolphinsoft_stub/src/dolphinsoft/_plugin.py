"""Minimal pytest11 plugin used only to prove side-by-side installation."""

from __future__ import annotations


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "dolphinsoft_stub: cross-install fixture")
