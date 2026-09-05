"""Tests for the Office COM wrapper objects."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dolphin_desktop import _office
from dolphin_desktop._office import (
    ExcelApp,
    ExcelCell,
    ExcelRange,
    ExcelSheet,
    ExcelWorkbook,
    WordApp,
    WordDocument,
)


def test_require_win32com_reports_a_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "win32com", None)

    with pytest.raises(RuntimeError, match="win32com is not available"):
        _office._require_win32com()


def test_excel_cell_and_range_preserve_com_values() -> None:
    com = MagicMock(Value="old", Formula="=A1", Text="formatted")
    cell = ExcelCell(com)

    assert (cell.value, cell.formula, cell.text) == ("old", "=A1", "formatted")
    cell.value = 10
    cell.formula = "=B2"
    assert cell.set_value(20) is cell
    assert com.Value == 20
    assert com.Formula == "=B2"

    empty = ExcelRange(SimpleNamespace(Value=None))
    scalar = ExcelRange(SimpleNamespace(Value="one"))
    matrix = ExcelRange(SimpleNamespace(Value=(("A", "B"), ("C", "D"))))
    assert empty.as_list() == []
    assert scalar.as_list() == [["one"]]
    assert matrix.as_list() == [["A", "B"], ["C", "D"]]

    range_com = SimpleNamespace(Value="before")
    value_range = ExcelRange(range_com)
    value_range.value = "after"
    assert value_range.value == "after"


def test_excel_sheet_and_workbook_wrap_nested_com_objects(tmp_path) -> None:
    sheet_com = MagicMock(Name="Summary")
    sheet_com.Cells.return_value = SimpleNamespace(Value=3, Formula="=1+2", Text="3")
    sheet_com.Range.return_value = SimpleNamespace(Value=((1, 2),))
    sheet = ExcelSheet(sheet_com)

    assert sheet.name == "Summary"
    sheet.activate()
    assert sheet.cell(2, 3).value == 3
    assert sheet.range("A1:B1").as_list() == [[1, 2]]
    sheet_com.Activate.assert_called_once_with()
    sheet_com.Cells.assert_called_once_with(2, 3)
    sheet_com.Range.assert_called_once_with("A1:B1")

    workbook_com = MagicMock()
    workbook = ExcelWorkbook(workbook_com)
    assert workbook.sheet("Summary")._com is workbook_com.Sheets.return_value
    workbook.save()
    workbook.save_as(tmp_path / "report.xlsx")
    workbook.close(save=True)

    workbook_com.Save.assert_called_once_with()
    workbook_com.SaveAs.assert_called_once_with(str((tmp_path / "report.xlsx").resolve()))
    workbook_com.Close.assert_called_once_with(True)


def test_excel_connect_and_active_properties_use_the_running_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    excel = MagicMock()
    client = MagicMock(GetActiveObject=MagicMock(return_value=excel))
    monkeypatch.setattr(_office, "_require_win32com", lambda: client)

    app = ExcelApp.connect()

    assert app._owned is False
    assert app.active_workbook._com is excel.ActiveWorkbook
    assert app.active_sheet._com is excel.ActiveSheet
    client.GetActiveObject.assert_called_once_with("Excel.Application")


def test_excel_connect_reports_when_no_instance_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.GetActiveObject.side_effect = OSError("not registered")
    monkeypatch.setattr(_office, "_require_win32com", lambda: client)

    with pytest.raises(RuntimeError, match="No running Excel instance found"):
        ExcelApp.connect()


def test_word_document_reads_saves_and_replaces_text() -> None:
    find = MagicMock()
    content = SimpleNamespace(Text="Hello world", Find=find)
    com = SimpleNamespace(Content=content, Name="letter.docx", Save=MagicMock())
    document = WordDocument(com)

    assert (document.text, document.name) == ("Hello world", "letter.docx")
    document.save()
    document.find_replace("world", "Dolphin")

    com.Save.assert_called_once_with()
    find.ClearFormatting.assert_called_once_with()
    find.Replacement.ClearFormatting.assert_called_once_with()
    find.Execute.assert_called_once_with(
        "world", False, False, False, False, False, True, 1, False, "Dolphin", 2
    )


def test_word_connect_and_active_document_use_the_running_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    word = MagicMock()
    client = MagicMock(GetActiveObject=MagicMock(return_value=word))
    monkeypatch.setattr(_office, "_require_win32com", lambda: client)

    app = WordApp.connect()

    assert app._owned is False
    assert app.active_document._com is word.ActiveDocument
    client.GetActiveObject.assert_called_once_with("Word.Application")


def test_word_connect_reports_when_no_instance_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.GetActiveObject.side_effect = OSError("not registered")
    monkeypatch.setattr(_office, "_require_win32com", lambda: client)

    with pytest.raises(RuntimeError, match="No running Word instance found"):
        WordApp.connect()


def test_word_save_as_resolves_relative_paths(tmp_path) -> None:
    com = MagicMock()
    document = WordDocument(com)

    document.save_as(tmp_path / "nested" / "letter.docx")

    com.SaveAs2.assert_called_once_with(str((tmp_path / "nested" / "letter.docx").resolve()))


def test_quit_ignores_a_close_failure_and_still_releases_owned_excel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_office, "_com_name", lambda doc: doc.Name)
    workbook = MagicMock(Name="broken.xlsx")
    workbook.Close.side_effect = OSError("already closed")
    excel = MagicMock()
    app = ExcelApp(excel, owned=True)
    app._opened.append(workbook)

    app.quit()

    assert app._opened == []
    excel.Quit.assert_called_once_with()


def test_office_cells_and_ranges_preserve_com_values() -> None:
    from dolphin_desktop._office import ExcelCell, ExcelRange

    cell_com = SimpleNamespace(Value=1, Formula="=1", Text="1")
    cell = ExcelCell(cell_com)
    assert cell.set_value(2) is cell and cell.value == 2
    cell.formula = "=2"
    assert (cell.formula, cell.text) == ("=2", "1")
    assert ExcelRange(SimpleNamespace(Value=(("A", "B"), ("C", "D")))).as_list() == [
        ["A", "B"],
        ["C", "D"],
    ]


def test_office_com_name_reads_name_or_falls_back() -> None:
    from dolphin_desktop._office import _com_name

    assert _com_name(SimpleNamespace(Name="Budget.xlsx")) == "Budget.xlsx"
    assert _com_name(object()) == "<unknown>"
