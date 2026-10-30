"""Regenerate the benign macro documents used by ``tests/office/test_macro_security.py``.

The committed ``benign.xlsm`` / ``benign.docm`` / ``benign.pptm`` each carry one
macro that only rewrites a marker inside the document itself (``macro-not-run``
-> ``macro-ran``); Excel and Word run theirs on open, PowerPoint's has to be
asked for by name. Nothing else: no file, registry, network or shell access.

Office refuses programmatic access to a document's VBA project unless the
operator trusts "access to the VBA project object model" (Trust Center ->
Macro Settings). That is why the test suite ships the generated files instead
of building them at test time: the tests then run on any machine with Office,
and the trust setting never has to be enabled where tests run.

Run this script only on a machine where that setting is enabled on purpose::

    uv run python tests/fixtures/office/make_macro_fixtures.py

It overwrites the two files next to it and prints their SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER_BEFORE = "macro-not-run"
MARKER_AFTER = "macro-ran"

EXCEL_MACRO = (
    "Private Sub Workbook_Open()\n"
    f'    ThisWorkbook.Worksheets(1).Cells(1, 1).Value = "{MARKER_AFTER}"\n'
    "End Sub"
)
WORD_MACRO = (
    f'Private Sub Document_Open()\n    ThisDocument.Content.Text = "{MARKER_AFTER}"\nEnd Sub'
)
# PowerPoint has no open-time macro event, so this one is a plain public Sub.
# The negative test asks PowerPoint to run it by name; with macros disabled the
# request is refused and the marker stays untouched.
POWERPOINT_MODULE = "DolphinMacro"
POWERPOINT_MACRO = (
    "Public Sub MarkRan()\n"
    "    ActivePresentation.Slides(1).Shapes(1).TextFrame.TextRange.Text = "
    f'"{MARKER_AFTER}"\n'
    "End Sub"
)


_VBEXT_CT_DOCUMENT = 100  # vbext_ComponentType.vbext_ct_Document


def _document_module(project, preferred_name: str | None):
    """The document-level VBA module, whatever the Office UI language calls it.

    English Office names it ThisWorkbook / ThisDocument; localized builds use
    e.g. "Ten_skoroszyt". Excel reports the name through ``Workbook.CodeName``;
    otherwise take the only document-type component that is not a worksheet.
    """
    components = project.VBComponents
    if preferred_name:
        return components(preferred_name).CodeModule
    for index in range(1, int(components.Count) + 1):
        component = components(index)
        if int(component.Type) == _VBEXT_CT_DOCUMENT:
            return component.CodeModule
    raise RuntimeError("no document-level VBA module found")


def _build(kind: str) -> Path:
    import win32com.client as client

    app = client.DispatchEx(f"{kind}.Application")
    document = None
    try:
        if kind != "PowerPoint":
            app.Visible = False  # PowerPoint refuses Visible=False; it gets no window instead
        app.DisplayAlerts = 0
        app.AutomationSecurity = 3  # never run anything while authoring
        if kind == "PowerPoint":
            document = app.Presentations.Add(0)  # no window
            slide = document.Slides.Add(1, 12)  # blank layout
            box = slide.Shapes.AddTextbox(1, 36, 36, 600, 100)
            box.TextFrame.TextRange.Text = MARKER_BEFORE
            module = document.VBProject.VBComponents.Add(1)  # vbext_ct_StdModule
            module.Name = POWERPOINT_MODULE
            module.CodeModule.AddFromString(POWERPOINT_MACRO)
            path = HERE / "benign.pptm"
            document.SaveAs(str(path), 25)  # ppSaveAsOpenXMLPresentationMacroEnabled
        elif kind == "Excel":
            document = app.Workbooks.Add()
            document.Sheets(1).Cells(1, 1).Value = MARKER_BEFORE
            module = _document_module(document.VBProject, str(document.CodeName))
            module.AddFromString(EXCEL_MACRO)
            path = HERE / "benign.xlsm"
            document.SaveAs(str(path), 52)  # xlOpenXMLWorkbookMacroEnabled
        else:
            document = app.Documents.Add()
            document.Content.Text = MARKER_BEFORE
            module = _document_module(document.VBProject, None)
            module.AddFromString(WORD_MACRO)
            path = HERE / "benign.docm"
            document.SaveAs2(str(path), 13)  # wdFormatXMLDocumentMacroEnabled
        module = None
        return path
    finally:
        try:
            if document is not None:
                if kind == "PowerPoint":
                    document.Close()
                else:
                    document.Close(0)
        finally:
            app.Quit()


def main() -> int:
    for kind in ("Excel", "Word", "PowerPoint"):
        try:
            path = _build(kind)
        except Exception as exc:
            print(f"{kind}: FAILED: {exc}", file=sys.stderr)
            print(
                "Enable Trust Center -> Macro Settings -> 'Trust access to the VBA "
                "project object model' for this Office application and retry.",
                file=sys.stderr,
            )
            return 1
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{path.name}: {path.stat().st_size} bytes sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
