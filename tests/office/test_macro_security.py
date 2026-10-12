"""Real Office tests using macros that only change a document marker."""

import pytest

from dolphin_desktop import ExcelApp, WordApp

pytestmark = pytest.mark.integration


def _marker(app):
    if isinstance(app, ExcelApp):
        return app.active_workbook.sheet(1).cell(1, 1).value
    return app.active_document.text.strip()


def test_default_open_blocks_macro_after_positive_control(office_macro_document):
    kind, path, control_path = office_macro_document
    wrapper = ExcelApp if kind == "Excel" else WordApp
    # Verify execution on a separate trusted copy first. A policy-blocked
    # positive control skips the entire test, including the negative case.
    with wrapper.open(control_path, visible=False, allow_macros=True) as app:
        marker = _marker(app)
        if marker == "macro-not-run":
            pytest.skip(f"{kind} did not execute the macro despite opt-in; check Office policies")
        assert marker == "macro-ran"
    with wrapper.open(path, visible=False, allow_macros=False) as app:
        assert _marker(app) == "macro-not-run"


def test_default_open_keeps_cached_external_link_value(excel_link_document):
    with ExcelApp.open(excel_link_document, visible=False) as app:
        assert app.active_workbook.sheet(1).cell(1, 1).value == "cached-value"


def test_word_default_open_keeps_cached_ole_link_value(word_link_document):
    with WordApp.open(word_link_document, visible=False) as app:
        assert app.active_document.text.strip() == "cached-value"
        # Explicitly updating the same field proves the source is available
        # and has changed, rather than treating a broken link as a security pass.
        app.active_document._com.Fields.Update()
        assert app.active_document.text.strip() == "changed-source"
