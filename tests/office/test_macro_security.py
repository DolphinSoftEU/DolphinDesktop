"""Real Office tests using macros that only change a document marker."""

import pytest

from dolphin_desktop import ExcelApp, WordApp

pytestmark = pytest.mark.integration


def _marker(app):
    if isinstance(app, ExcelApp):
        return app.active_workbook.sheet(1).cell(1, 1).value
    return app.active_document.text.strip()


def test_default_open_blocks_benign_auto_open_macro(office_macro_document):
    kind, path = office_macro_document
    wrapper = ExcelApp if kind == "Excel" else WordApp
    with wrapper.open(path, visible=False) as app:
        assert _marker(app) == "macro-not-run"


def test_explicit_opt_in_runs_benign_macro_if_office_policy_allows(office_macro_document):
    kind, path = office_macro_document
    wrapper = ExcelApp if kind == "Excel" else WordApp
    with wrapper.open(path, visible=False, allow_macros=True) as app:
        marker = _marker(app)
        if marker == "macro-not-run":
            pytest.skip(f"{kind} did not execute the macro despite opt-in; check Office policies")
        assert marker == "macro-ran"


def test_default_open_keeps_cached_external_link_value(excel_link_document):
    with ExcelApp.open(excel_link_document, visible=False) as app:
        assert app.active_workbook.sheet(1).cell(1, 1).value == "cached-value"
