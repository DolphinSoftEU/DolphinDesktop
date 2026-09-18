"""Ambiguous-match handling: multiple matches must not read as "not found".

Regression guard for a live finding: two DataWindow panes shared the class
``pbdw`` in a PowerBuilder app — ``exists()`` reported False and actions
reported ElementNotFoundError, both hiding the real problem (criteria
matched more than one element).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pywinauto.findwindows import ElementAmbiguousError

from dolphin_desktop import AmbiguousMatchError
from dolphin_desktop._locator import Locator


class _FakeWindow:
    """Minimal Window stand-in exposing _get_spec()."""

    def __init__(self, spec):
        self._spec = spec

    def _get_spec(self):
        return self._spec


def _ambiguous_parent() -> _FakeWindow:
    ambiguous_spec = MagicMock()
    ambiguous_spec.wrapper_object.side_effect = ElementAmbiguousError(
        "2 elements match the criteria"
    )
    parent_spec = MagicMock()
    parent_spec.child_window.return_value = ambiguous_spec
    return _FakeWindow(parent_spec)


def test_resolve_raises_ambiguous_match_error():
    loc = Locator(_ambiguous_parent(), class_name="pbdw")
    with pytest.raises(AmbiguousMatchError, match="found_index"):
        loc._resolve()


def test_exists_reports_true_for_ambiguous_match():
    """Multiple matches means the element *does* exist — at least twice."""
    loc = Locator(_ambiguous_parent(), class_name="pbdw")
    assert loc.exists() is True


def test_ambiguity_beats_fallbacks():
    """Fallback selectors must not run — the primary criteria DID match."""
    parent = _ambiguous_parent()
    loc = Locator(parent, class_name="pbdw", fallback=[{"title": "other"}])
    with pytest.raises(AmbiguousMatchError):
        loc._resolve()
    # child_window was called once for the primary criteria only.
    assert parent._get_spec().child_window.call_count == 1


def test_ambiguous_fallback_raises_without_recording_self_healing():
    """A fallback ambiguity must not become not-found or telemetry."""
    parent_spec = MagicMock()
    parent_spec.child_window = MagicMock(
        side_effect=[
            RuntimeError("primary missing"),
            ElementAmbiguousError("2 fallback elements match the criteria"),
        ]
    )
    parent = _FakeWindow(parent_spec)
    loc = Locator(
        parent,
        auto_id="save_missing",
        fallback=[{"title": "Zapisz", "control_type": "Button"}],
    ).timeout(0)

    with patch("dolphin_desktop._selfheal.record_fallback") as record_fallback:
        with pytest.raises(AmbiguousMatchError, match="more than one"):
            loc._resolve()

    record_fallback.assert_not_called()
    assert parent_spec.child_window.call_count == 2


def test_ambiguous_match_error_is_distinct_dolphin_error():
    from dolphin_desktop import DolphinError, ElementNotFoundError

    assert issubclass(AmbiguousMatchError, DolphinError)
    assert not issubclass(AmbiguousMatchError, ElementNotFoundError)
