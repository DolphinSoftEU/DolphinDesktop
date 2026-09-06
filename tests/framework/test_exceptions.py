"""Tests for :mod:`dolphin_desktop._exceptions`."""

from __future__ import annotations


def test_exception_hierarchy_preserves_args_and_formats_optional_hints() -> None:
    from dolphin_desktop._exceptions import (
        AliasNotFoundError,
        AmbiguousMatchError,
        ApplicationError,
        DolphinError,
        ElementNotFoundError,
        UnsupportedCapabilityError,
        UnsupportedPatternError,
        WaitTimeoutError,
        WindowNotFoundError,
    )

    concrete = [
        ElementNotFoundError,
        AmbiguousMatchError,
        WaitTimeoutError,
        ApplicationError,
        WindowNotFoundError,
        AliasNotFoundError,
        UnsupportedCapabilityError,
        UnsupportedPatternError,
    ]
    assert all(issubclass(error, DolphinError) for error in concrete)
    plain = DolphinError("problem", 7)
    assert plain.args == ("problem", 7)
    assert str(plain) == "('problem', 7)"
    assert str(DolphinError("problem", hint="retry")) == "problem\n  hint: retry"
    assert str(DolphinError("problem", hint="")) == "problem"
