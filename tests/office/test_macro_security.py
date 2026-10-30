"""Real Office tests using macros that only change a document marker."""

import pytest

from dolphin_desktop import ExcelApp, PowerPointApp, WordApp

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


# Resolving an OLE link starts an out-of-process Office server; on a cold or
# slow machine a single activation can take close to a minute.
_LINK_TIMEOUT = pytest.mark.timeout(300)


@_LINK_TIMEOUT
def test_default_open_keeps_cached_external_link_value(excel_link_document):
    with ExcelApp.open(excel_link_document, visible=False) as app:
        assert app.active_workbook.sheet(1).cell(1, 1).value == "cached-value"


@_LINK_TIMEOUT
def test_word_default_open_keeps_cached_ole_link_value(word_link_document):
    with WordApp.open(word_link_document, visible=False) as app:
        assert app.active_document.text.strip() == "cached-value"
        # Explicitly updating the same field proves the source is available
        # and has changed, rather than treating a broken link as a security pass.
        app.active_document._com.Fields.Update()
        assert app.active_document.text.strip() == "changed-source"


def test_powerpoint_default_open_keeps_pptm_macros_disabled(powerpoint_macro_document):
    """A .pptm opens with macros disabled: PowerPoint refuses to run the macro by name.

    PowerPoint has no open-time macro event, so there is no "did it run on
    open" marker to read. Instead the test asks for the macro explicitly through
    ``Application.Run``; with macros disabled PowerPoint rejects the request and
    the slide text is unchanged. Macros are never enabled anywhere in this test.
    """
    path, module, macro = powerpoint_macro_document
    with PowerPointApp.open(path, visible=False) as app:
        deck = app.active_presentation
        assert deck.slide(1).shape(1).text == "macro-not-run"
        with pytest.raises(Exception, match=r"(?i)macro|makro|disabled|wyłączon"):
            app._com.Run(f"{path.name}!{module}.{macro}")
        assert deck.slide(1).shape(1).text == "macro-not-run"
        assert int(app._com.AutomationSecurity) != 3, "ForceDisable applies only while opening"
