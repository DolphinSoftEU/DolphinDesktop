"""Office fixtures: shipped macro documents and generated harmless link documents."""

from __future__ import annotations

from pathlib import Path

import pytest

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "office"

# Generated once by tests/fixtures/office/make_macro_fixtures.py on a machine
# where "trust access to the VBA project object model" was enabled on purpose.
# Each file carries a single macro that rewrites a marker inside the document
# itself when it opens; nothing outside the document is touched.
_MACRO_FILES = {"Excel": "benign.xlsm", "Word": "benign.docm"}


@pytest.fixture(params=["Excel", "Word"])
def office_macro_document(request, tmp_path):
    """A copy of the benign macro document plus an identical control copy.

    Office refuses to add macros programmatically unless the operator trusts
    the VBA project object model, so the documents are shipped pre-built
    rather than generated here; the tests therefore run with Office's default
    security settings and never change them.
    """
    pytest.importorskip("win32com.client")
    kind = request.param
    source = _FIXTURES / _MACRO_FILES[kind]
    if not source.is_file():
        pytest.skip(f"{source.name} missing; regenerate with make_macro_fixtures.py")
    path = tmp_path / source.name
    path.write_bytes(source.read_bytes())
    control_path = path.with_name(f"control{path.suffix}")
    control_path.write_bytes(path.read_bytes())
    return kind, path, control_path


@pytest.fixture
def excel_link_document(tmp_path):
    """Cache a local external link, then change the source while the target is closed."""
    client = pytest.importorskip("win32com.client")
    try:
        app = client.DispatchEx("Excel.Application")
    except Exception as exc:
        pytest.skip(f"Excel COM unavailable: {exc}")
    try:
        app.Visible = False
        app.DisplayAlerts = False
        app.AutomationSecurity = 3
        source_path = tmp_path / "source.xlsx"
        target_path = tmp_path / "linked.xlsx"
        source = app.Workbooks.Add()
        source.Sheets(1).Cells(1, 1).Value = "cached-value"
        source.SaveAs(str(source_path), 51)  # xlOpenXMLWorkbook
        target = app.Workbooks.Add()
        sheet_name = str(source.Sheets(1).Name).replace("'", "''")
        target.Sheets(1).Cells(1, 1).Formula = f"='[source.xlsx]{sheet_name}'!$A$1"
        app.Calculate()
        assert target.Sheets(1).Cells(1, 1).Value == "cached-value"
        target.SaveAs(str(target_path), 51)
        target.Close(False)
        source.Sheets(1).Cells(1, 1).Value = "changed-source"
        source.Save()
        source.Close(False)
        source = None
        target = None
    finally:
        app.Quit()
    return target_path


@pytest.fixture
def word_link_document(tmp_path):
    """Build a cached OLE LINK field to a harmless local Word document."""
    client = pytest.importorskip("win32com.client")
    try:
        app = client.DispatchEx("Word.Application")
    except Exception as exc:
        pytest.skip(f"Word COM unavailable: {exc}")
    previous_links = None
    try:
        app.Visible = False
        app.DisplayAlerts = 0
        app.AutomationSecurity = 3
        previous_links = app.Options.UpdateLinksAtOpen
        app.Options.UpdateLinksAtOpen = False
        source_path = tmp_path / "source.docx"
        target_path = tmp_path / "linked.docx"
        source = app.Documents.Add()
        source.Content.Text = "cached-value"
        source.SaveAs2(str(source_path), 12)  # wdFormatXMLDocument
        source.Close(0)
        source = None
        target = app.Documents.Add()
        # The \a switch marks this OLE link for automatic updates; \t renders
        # the cached result as text without activating an OLE object. The field
        # is inserted as wdFieldEmpty (-1) with the full LINK code: asking for
        # wdFieldLink (56) makes Word build the OLE object inside Fields.Add,
        # which on some machines takes a minute or never returns. Update()
        # below resolves the link once, on our terms.
        link_path = str(source_path).replace("\\", "\\\\")
        field = target.Fields.Add(
            target.Range(0, 0), -1, f'LINK Word.Document.12 "{link_path}" "" \\a \\t', False
        )
        field.Update()
        assert field.Result.Text.strip() == "cached-value"
        target.SaveAs2(str(target_path), 12)
        target.Close(0)
        field = None
        target = None
        source = app.Documents.Open(str(source_path))
        source.Content.Text = "changed-source"
        source.Save()
        source.Close(0)
        source = None
    finally:
        try:
            if previous_links is not None:
                app.Options.UpdateLinksAtOpen = previous_links
        finally:
            app.Quit()
    return target_path
