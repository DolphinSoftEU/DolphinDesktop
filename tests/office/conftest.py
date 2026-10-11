"""Generate harmless macro fixtures without changing Office security policies."""

from __future__ import annotations

import pytest


@pytest.fixture(params=["Excel", "Word"])
def office_macro_document(request, tmp_path):
    client = pytest.importorskip("win32com.client")
    kind = request.param
    try:
        app = client.DispatchEx(f"{kind}.Application")
    except Exception as exc:
        pytest.skip(f"{kind} COM unavailable: {exc}")

    document = None
    fixture_error = None
    try:
        app.Visible = False
        app.DisplayAlerts = 0
        app.AutomationSecurity = 3
        if kind == "Excel":
            document = app.Workbooks.Add()
            document.Sheets(1).Cells(1, 1).Value = "macro-not-run"
            module_name = "ThisWorkbook"
            macro = (
                "Private Sub Workbook_Open()\n"
                '    ThisWorkbook.Worksheets(1).Cells(1, 1).Value = "macro-ran"\n'
                "End Sub"
            )
        else:
            document = app.Documents.Add()
            document.Content.Text = "macro-not-run"
            module_name = "ThisDocument"
            macro = (
                'Private Sub Document_Open()\n    ThisDocument.Content.Text = "macro-ran"\nEnd Sub'
            )

        try:
            module = document.VBProject.VBComponents(module_name).CodeModule
        except Exception as exc:
            fixture_error = (
                f"{kind} fixture creation needs existing trusted VBA project access; "
                f"security settings were not changed: {exc}"
            )
        if fixture_error is None:
            module.AddFromString(macro)
            module = None
            path = tmp_path / ("benign.xlsm" if kind == "Excel" else "benign.docm")
            if kind == "Excel":
                document.SaveAs(str(path), 52)  # xlOpenXMLWorkbookMacroEnabled
            else:
                document.SaveAs2(str(path), 13)  # wdFormatXMLDocumentMacroEnabled
    finally:
        try:
            if document is not None:
                document.Close(0)
        finally:
            document = None
            app.Quit()
            app = None

    if fixture_error is not None:
        pytest.skip(fixture_error)
    return kind, path


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
