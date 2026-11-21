"""Excel and Word COM examples using real, temporary Office documents."""

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def office_file(tmp_path):
    """Create valid input files; the public wrappers open existing documents."""
    client = pytest.importorskip("win32com.client")
    registry = pytest.importorskip("winreg")

    def create(kind):
        prog_id = {"excel": "Excel.Application", "word": "Word.Application"}[kind]
        try:
            with registry.OpenKey(registry.HKEY_CLASSES_ROOT, prog_id):
                pass
        except FileNotFoundError:
            pytest.skip(f"{prog_id} is not installed")
        path = tmp_path / ("example.xlsx" if kind == "excel" else "example.docx")
        app = client.DispatchEx(prog_id)
        document = None
        try:
            app.Visible = False
            if kind == "excel":
                document = app.Workbooks.Add()
                document.SaveAs(str(path), FileFormat=51)
            else:
                document = app.Documents.Add()
                document.Content.Text = "Dear {{NAME}}, your order {{ORDER_ID}} is ready."
                document.SaveAs2(str(path), FileFormat=16)
        finally:
            try:
                if document is not None:
                    document.Close(SaveChanges=False)
            finally:
                app.Quit()
        return path

    return create


def test_excel_write_and_read(office_file):
    from dolphin_desktop import ExcelApp

    path = office_file("excel")
    with ExcelApp.open(path, visible=False) as xl:
        sheet = xl.active_workbook.sheet(1)
        sheet.cell(1, 1).value = "Name"
        sheet.cell(1, 2).value = "Score"
        sheet.cell(2, 1).value = "Alice"
        sheet.cell(2, 2).value = 95
        xl.active_workbook.save()
    with ExcelApp.open(path, visible=False) as xl:
        sheet = xl.active_workbook.sheet(1)
        assert sheet.cell(1, 1).text == "Name"
        assert sheet.cell(2, 1).text == "Alice"
        assert sheet.cell(2, 2).value == 95


def test_excel_range(office_file):
    from dolphin_desktop import ExcelApp

    path = office_file("excel")
    with ExcelApp.open(path, visible=False) as xl:
        xl.active_workbook.sheet(1).range("A1:C3").value = [["X", "Y", "Z"], [1, 2, 3], [4, 5, 6]]
        xl.active_workbook.save()
    with ExcelApp.open(path, visible=False) as xl:
        result = xl.active_workbook.sheet(1).range("A1:C3").as_list()
        assert result == [["X", "Y", "Z"], [1, 2, 3], [4, 5, 6]]


def test_word_find_replace(office_file):
    from dolphin_desktop import WordApp

    path = office_file("word")
    with WordApp.open(path, visible=False) as word:
        document = word.active_document
        document.find_replace("{{NAME}}", "John Doe")
        document.find_replace("{{ORDER_ID}}", "ORD-12345")
        assert "John Doe" in document.text
        assert "ORD-12345" in document.text
        document.save()
    with WordApp.open(path, visible=False) as word:
        assert "John Doe" in word.active_document.text
