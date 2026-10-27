"""Contract tests for PowerPoint tables, charts, notes, layout titles and hyperlinks."""

from unittest.mock import MagicMock, call

import pytest

import dolphin_desktop
from dolphin_desktop import PowerPointChart, PowerPointTable
from dolphin_desktop import _powerpoint as ppt

MSO_TRUE, MSO_FALSE = -1, 0


def test_table_and_chart_facades_are_exported():
    assert {"PowerPointTable", "PowerPointChart"} <= set(dolphin_desktop.__all__)
    assert dolphin_desktop.PowerPointTable is PowerPointTable
    assert dolphin_desktop.PowerPointChart is PowerPointChart


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def _table_com(values):
    com = MagicMock()
    com.Rows.Count = len(values)
    com.Columns.Count = len(values[0])
    cells = {}

    def cell(row, column):
        key = (row, column)
        if key not in cells:
            cell_com = MagicMock()
            cell_com.Shape.TextFrame.TextRange.Text = values[row - 1][column - 1]
            cells[key] = cell_com
        return cells[key]

    com.Cell.side_effect = cell
    return com, cells


def test_table_reads_values_row_by_row_with_one_based_cells():
    com, _ = _table_com([["Region", "Revenue"], ["EMEA", "120"]])
    table = PowerPointTable(com)
    assert (table.rows, table.columns) == (2, 2)
    assert table.cell(2, 1).text == "EMEA"
    assert table.row_values(1) == ["Region", "Revenue"]
    assert table.values == [["Region", "Revenue"], ["EMEA", "120"]]


def test_table_set_values_writes_strings_from_start_row():
    com, cells = _table_com([["", ""], ["", ""], ["", ""]])
    PowerPointTable(com).set_values([["APAC", 95]], start_row=3)
    assert cells[(3, 1)].Shape.TextFrame.TextRange.Text == "APAC"
    assert cells[(3, 2)].Shape.TextFrame.TextRange.Text == "95"


def test_table_set_values_rejects_data_that_does_not_fit():
    com, _ = _table_com([["", ""]])
    with pytest.raises(ValueError, match="do not fit"):
        PowerPointTable(com).set_values([["a", "b"], ["c", "d"]])
    with pytest.raises(ValueError, match="do not fit"):
        PowerPointTable(com).set_values([["a", "b", "c"]])


def test_table_row_and_column_mutations_call_powerpoint_one_based():
    com, _ = _table_com([["x"]])
    table = PowerPointTable(com)
    table.add_row()
    table.add_row(before=1)
    table.add_column()
    table.add_column(before=2)
    table.delete_row(3)
    table.delete_column(1)
    assert com.Rows.Add.call_args_list == [call(), call(1)]
    assert com.Columns.Add.call_args_list == [call(), call(2)]
    com.Rows.assert_called_with(3)
    com.Rows.return_value.Delete.assert_called_once_with()
    com.Columns.assert_called_with(1)
    com.Columns.return_value.Delete.assert_called_once_with()


def test_shape_table_access_requires_a_table_shape():
    shape = ppt.PowerPointShape(MagicMock(HasTable=MSO_FALSE, Name="caption"))
    assert not shape.has_table
    with pytest.raises(ValueError, match="not a table"):
        _ = shape.table
    table_shape = ppt.PowerPointShape(MagicMock(HasTable=MSO_TRUE))
    assert table_shape.has_table
    assert isinstance(table_shape.table, PowerPointTable)


def test_add_table_validates_size_and_fills_values(monkeypatch):
    slide_com = MagicMock()
    slide = ppt.PowerPointSlide(slide_com)
    with pytest.raises(ValueError, match="at least one row"):
        slide.add_table(0, 2)
    slide_com.Shapes.AddTable.assert_not_called()

    filled = []
    monkeypatch.setattr(
        ppt.PowerPointTable, "set_values", lambda self, values: filled.append(values)
    )
    slide.add_table(2, 2, left=1, top=2, width=3, height=4, values=[["a", "b"]])
    slide_com.Shapes.AddTable.assert_called_once_with(2, 2, 1, 2, 3, 4)
    assert filled == [[["a", "b"]]]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def _chart_com(series):
    com = MagicMock()
    com.ChartType = 51
    com.HasTitle = MSO_FALSE
    collection = MagicMock(Count=len(series))

    def series_collection(*args):
        if not args:
            return collection
        key = args[0]
        name = key if isinstance(key, str) else list(series)[key - 1]
        values, categories = series[name]
        return MagicMock(Name=name, Values=tuple(values), XValues=tuple(categories))

    com.SeriesCollection.side_effect = series_collection
    return com


def test_chart_reads_type_series_and_categories():
    com = _chart_com({"EMEA": ([100, 110], ["Q1", "Q2"]), "APAC": ([90, 95], ["Q1", "Q2"])})
    chart = PowerPointChart(com)
    assert chart.chart_type == 51
    assert not chart.has_title
    assert chart.title == ""
    assert chart.series_count == 2
    assert chart.series_names == ["EMEA", "APAC"]
    assert chart.categories == ["Q1", "Q2"]
    assert chart.series_values("APAC") == [90.0, 95.0]
    assert chart.series_values(1) == [100.0, 110.0]


def test_chart_title_setter_enables_the_title_first():
    com = _chart_com({})
    chart = PowerPointChart(com)
    assert chart.categories == []
    chart.title = "Trend"
    assert com.HasTitle == MSO_TRUE
    assert com.ChartTitle.Text == "Trend"


def test_shape_chart_access_requires_a_chart_shape():
    shape = ppt.PowerPointShape(MagicMock(HasChart=MSO_FALSE, Name="caption"))
    assert not shape.has_chart
    with pytest.raises(ValueError, match="not a chart"):
        _ = shape.chart


def test_add_chart_refuses_a_presentation_without_a_window():
    slide_com = MagicMock()
    slide_com.Parent.Windows.Count = 0
    with pytest.raises(RuntimeError, match="visible=True"):
        ppt.PowerPointSlide(slide_com).add_chart(["Q1"], {"a": [1]})
    slide_com.Shapes.AddChart2.assert_not_called()


def test_add_chart_validates_data_before_touching_com():
    slide_com = MagicMock()
    slide_com.Parent.Windows.Count = 1
    slide = ppt.PowerPointSlide(slide_com)
    with pytest.raises(ValueError, match="at least one category"):
        slide.add_chart([], {"a": []})
    with pytest.raises(ValueError, match="at least one category"):
        slide.add_chart(["Q1"], {})
    with pytest.raises(ValueError, match="2 value\\(s\\) for 1 categories"):
        slide.add_chart(["Q1"], {"a": [1, 2]})
    slide_com.Shapes.AddChart2.assert_not_called()


def test_add_chart_replaces_template_series_with_literal_arrays():
    slide_com = MagicMock()
    slide_com.Parent.Windows.Count = 1
    shape_com = slide_com.Shapes.AddChart2.return_value
    chart = shape_com.Chart
    collection = chart.SeriesCollection.return_value
    collection.Count = 3
    deleted = []
    collection.side_effect = lambda i: MagicMock(Delete=lambda: deleted.append(i))
    created = []

    def new_series():
        created.append(MagicMock())
        return created[-1]

    collection.NewSeries.side_effect = new_series
    shape_com.HasChart = MSO_TRUE

    slide = ppt.PowerPointSlide(slide_com)
    shape = slide.add_chart(
        ["Q1", "Q2"], {"EMEA": [1, 2], "APAC": [3, 4]}, chart_type=5, title="Mix", left=7, top=8
    )

    slide_com.Shapes.AddChart2.assert_called_once_with(-1, 5, 7, 8, 600, 300, True)
    assert chart.ChartType == 5
    assert deleted == [3, 2, 1]  # highest index first so positions stay valid
    assert [s.Name for s in created] == ["EMEA", "APAC"]
    assert created[0].XValues == ("Q1", "Q2")
    assert created[0].Values == (1.0, 2.0)
    assert created[1].Values == (3.0, 4.0)
    assert chart.HasTitle == MSO_TRUE
    assert chart.ChartTitle.Text == "Mix"
    chart.ChartData.Activate.assert_not_called()  # no embedded Excel round trip
    assert shape._com is shape_com


def test_add_chart_without_title_leaves_the_title_alone():
    slide_com = MagicMock()
    slide_com.Parent.Windows.Count = 1
    chart = slide_com.Shapes.AddChart2.return_value.Chart
    chart.SeriesCollection.return_value.Count = 0
    chart.HasTitle = MSO_FALSE
    ppt.PowerPointSlide(slide_com).add_chart(["Q1"], {"a": [1]})
    assert chart.HasTitle == MSO_FALSE


# ---------------------------------------------------------------------------
# Notes, titles, hyperlinks, windows
# ---------------------------------------------------------------------------


def _notes_page(shapes):
    com = MagicMock()
    com.NotesPage.Shapes.Count = len(shapes)
    com.NotesPage.Shapes.Item.side_effect = lambda i: shapes[i - 1]
    return com


def test_notes_use_the_body_placeholder_and_skip_non_placeholders():
    picture = MagicMock()
    type(picture).PlaceholderFormat = property(lambda self: (_ for _ in ()).throw(AttributeError()))
    slide_image = MagicMock()
    slide_image.PlaceholderFormat.Type = 12  # ppPlaceholderSlideImage
    body = MagicMock()
    body.PlaceholderFormat.Type = 2
    body.TextFrame.TextRange.Text = "remember the restatement"
    slide = ppt.PowerPointSlide(_notes_page([picture, slide_image, body]))
    assert slide.notes == "remember the restatement"
    slide.notes = "updated"
    assert body.TextFrame.TextRange.Text == "updated"


def test_notes_without_body_placeholder_read_empty_and_refuse_writes():
    slide_image = MagicMock()
    slide_image.PlaceholderFormat.Type = 12
    slide = ppt.PowerPointSlide(_notes_page([slide_image]))
    assert slide.notes == ""
    with pytest.raises(RuntimeError, match="no body placeholder"):
        slide.notes = "x"


def test_title_requires_a_layout_with_a_title_placeholder():
    com = MagicMock()
    com.Shapes.HasTitle = MSO_FALSE
    slide = ppt.PowerPointSlide(com)
    assert not slide.has_title
    with pytest.raises(ValueError, match="no title placeholder"):
        _ = slide.title
    with pytest.raises(ValueError, match="no title placeholder"):
        slide.title = "x"
    com.Shapes.HasTitle = MSO_TRUE
    com.Shapes.Title.TextFrame.TextRange.Text = "Q3"
    assert slide.title == "Q3"
    slide.title = "Q4"
    assert com.Shapes.Title.TextFrame.TextRange.Text == "Q4"


def test_hyperlink_uses_the_mouse_click_action_and_reads_empty_when_absent():
    com = MagicMock()
    com.ActionSettings.return_value.Hyperlink.Address = None
    rng = ppt.PowerPointTextRange(com)
    assert rng.hyperlink == ""
    rng.hyperlink = "https://example.test"
    com.ActionSettings.assert_called_with(1)
    assert com.ActionSettings.return_value.Hyperlink.Address == "https://example.test"
    shape = ppt.PowerPointShape(MagicMock(HasTextFrame=MSO_TRUE))
    shape.hyperlink = "https://example.test/x"
    shape._com.TextFrame.TextRange.ActionSettings.assert_called_with(1)


def test_has_window_reflects_document_window_count():
    com = MagicMock()
    com.Windows.Count = 0
    deck = ppt.PowerPointPresentation(com)
    assert not deck.has_window
    com.Windows.Count = 1
    assert deck.has_window


def test_closing_releases_tables_charts_and_cell_ranges():
    com = MagicMock()
    deck = ppt.PowerPointPresentation(com)
    shape = deck.slide(1).shape(1)
    table = shape.table
    cell = table.cell(1, 1)
    chart = shape.chart
    deck.close()
    assert shape._com is None
    assert table._com is None
    assert cell._com is None
    assert chart._com is None


# ---------------------------------------------------------------------------
# Controlled errors (DESKTOP-240 item 10)
# ---------------------------------------------------------------------------


def test_open_missing_file_raises_before_touching_com(monkeypatch, tmp_path):
    client = MagicMock()
    monkeypatch.setattr(ppt, "_require_win32com", lambda: client)
    with pytest.raises(FileNotFoundError):
        ppt.PowerPointApp.open(tmp_path / "missing.pptx")
    client.Dispatch.assert_not_called()


def test_missing_shape_and_slide_raise_lookup_error():
    slide_com = MagicMock(SlideIndex=2)
    slide_com.Shapes.Count = 1
    caption = MagicMock(Name="caption")

    def item(key):
        if key == 1:
            return caption
        raise Exception("not found")

    slide_com.Shapes.Item.side_effect = item
    slide = ppt.PowerPointSlide(slide_com)
    with pytest.raises(LookupError, match="No shape 'logo' on slide 2"):
        slide.shape("logo")

    deck_com = MagicMock(Name="deck.pptx")
    deck_com.Slides.Count = 1
    deck_com.Slides.Item.side_effect = Exception("not found")
    with pytest.raises(LookupError, match=r"No slide 7 in 'deck.pptx'"):
        ppt.PowerPointPresentation(deck_com).slide(7)
