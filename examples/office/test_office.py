"""Examples: Excel and Word COM automation.

Requires Microsoft Office to be installed.
"""

import os
import tempfile

import pytest

try:
    import win32com.client  # noqa: F401

    HAS_OFFICE = True
except ImportError:
    HAS_OFFICE = False

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_office():
    if not HAS_OFFICE:
        pytest.skip("pywin32 not installed")


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------


def test_excel_write_and_read():
    from dolphin_desktop import ExcelApp

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        path = f.name

    try:
        with ExcelApp.open(path) as xl:
            ws = xl.active_workbook.sheet(1)

            # Write headers
            ws.cell(1, 1).value = "Name"
            ws.cell(1, 2).value = "Score"

            # Write data
            ws.cell(2, 1).value = "Alice"
            ws.cell(2, 2).value = 95

            ws.cell(3, 1).value = "Bob"
            ws.cell(3, 2).value = 87

            xl.active_workbook.save()

        # Re-open and verify
        with ExcelApp.open(path) as xl:
            ws = xl.active_workbook.sheet(1)
            assert ws.cell(1, 1).text == "Name"
            assert ws.cell(2, 1).text == "Alice"
            assert ws.cell(2, 2).value == 95

    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_excel_range():
    from dolphin_desktop import ExcelApp

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        path = f.name

    try:
        with ExcelApp.open(path) as xl:
            ws = xl.active_workbook.sheet(1)

            data = [["X", "Y", "Z"], [1, 2, 3], [4, 5, 6]]
            ws.range("A1:C3").value = data

            xl.active_workbook.save()

        with ExcelApp.open(path) as xl:
            ws = xl.active_workbook.sheet(1)
            result = ws.range("A1:C3").as_list()

            assert result[0][0] == "X"
            assert result[1][1] == 2
            assert result[2][2] == 6

    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------


def test_word_find_replace():
    from dolphin_desktop import WordApp

    template_content = "Dear {{NAME}},\n\nYour order {{ORDER_ID}} is ready."

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False, mode="w", encoding="utf-8") as f:
        # Write a plain text file — Word will open it
        f.write(template_content)
        path = f.name

    try:
        with WordApp.open(path) as wd:
            doc = wd.active_document
            doc.find_replace("{{NAME}}", "John Doe")
            doc.find_replace("{{ORDER_ID}}", "ORD-12345")

            text = doc.text
            assert "John Doe" in text
            assert "ORD-12345" in text

    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
