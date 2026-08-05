"""Smoke tests — verify the package can be imported without errors."""

import dolphin_desktop as dolphin


def test_public_api_accessible() -> None:
    assert hasattr(dolphin, "Desktop")
    assert hasattr(dolphin, "Application")
    assert hasattr(dolphin, "Window")
    assert hasattr(dolphin, "Locator")
    assert hasattr(dolphin, "Keyboard")
    assert hasattr(dolphin, "Mouse")
    assert hasattr(dolphin, "Clipboard")
    assert hasattr(dolphin, "SapGui")
    assert hasattr(dolphin, "SapSession")


def test_exceptions_accessible() -> None:
    from dolphin_desktop import (
        ApplicationError,
        DolphinError,
        ElementNotFoundError,
        WaitTimeoutError,
        WindowNotFoundError,
    )

    assert issubclass(ElementNotFoundError, DolphinError)
    assert issubclass(WaitTimeoutError, DolphinError)
    assert issubclass(ApplicationError, DolphinError)
    assert issubclass(WindowNotFoundError, DolphinError)


def test_version_present() -> None:
    from importlib.metadata import version

    ver = version("dolphin-desktop")
    assert ver  # non-empty string
