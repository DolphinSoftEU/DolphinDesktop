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


def test_table_set_values_makes_no_change_when_a_later_row_does_not_fit():
    # The valid first row must not be written before the invalid second row is
    # rejected: validation happens before any cell is touched.
    com, cells = _table_com([["", ""], ["", ""]])
    with pytest.raises(ValueError, match="do not fit"):
        PowerPointTable(com).set_values([["A", "B"], ["C", "D", "E"]])
    com.Cell.assert_not_called()
    assert cells == {}


def test_table_set_values_rejects_start_row_below_one():
    com, _ = _table_com([["", ""]])
    with pytest.raises(ValueError, match="start_row must be >= 1"):
        PowerPointTable(com).set_values([["a", "b"]], start_row=0)
    com.Cell.assert_not_called()


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


def test_add_table_rejects_bad_values_before_creating_a_shape():
    slide_com = MagicMock()
    slide = ppt.PowerPointSlide(slide_com)
    with pytest.raises(ValueError, match="do not fit"):
        slide.add_table(2, 2, values=[["a", "b", "c"]])
    # The data is validated up front, so no empty table is left on the slide.
    slide_com.Shapes.AddTable.assert_not_called()


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


def test_add_chart_rejects_a_non_numeric_value_before_creating_a_shape():
    slide_com = MagicMock()
    slide_com.Parent.Windows.Count = 1
    slide = ppt.PowerPointSlide(slide_com)
    # The first series is valid; the second holds a non-number. Every value is
    # converted up front, so no shape (not even a partial chart) is created.
    with pytest.raises(ValueError, match=r"series 'B' value 'abc' is not a number"):
        slide.add_chart(["Q1"], {"A": [1], "B": ["abc"]})
    slide_com.Shapes.AddChart2.assert_not_called()


def test_add_chart_rolls_back_the_shape_when_building_fails():
    slide_com = MagicMock()
    slide_com.Parent.Windows.Count = 1
    shape_com = slide_com.Shapes.AddChart2.return_value
    collection = shape_com.Chart.SeriesCollection.return_value
    collection.Count = 0
    collection.NewSeries.side_effect = RuntimeError("COM refused NewSeries")
    slide = ppt.PowerPointSlide(slide_com)
    with pytest.raises(RuntimeError, match="COM refused NewSeries"):
        slide.add_chart(["Q1"], {"A": [1]})
    shape_com.Delete.assert_called_once_with()  # no half-built chart left behind
    assert slide._children == []  # and nothing tracked for it


def test_add_chart_keeps_the_original_error_when_rollback_also_fails():
    slide_com = MagicMock()
    slide_com.Parent.Windows.Count = 1
    shape_com = slide_com.Shapes.AddChart2.return_value
    shape_com.Chart.SeriesCollection.return_value.Count = 0
    shape_com.Chart.SeriesCollection.return_value.NewSeries.side_effect = RuntimeError("build")
    shape_com.Delete.side_effect = RuntimeError("delete")
    with pytest.raises(RuntimeError, match="build") as info:
        ppt.PowerPointSlide(slide_com).add_chart(["Q1"], {"A": [1]})
    assert any("partial chart also failed" in note for note in info.value.__notes__)


def test_add_chart_without_title_disables_the_default_title():
    # AddChart2 leaves a single-series chart with a default title; title=None
    # must turn it off explicitly, not leave whatever AddChart2 produced.
    slide_com = MagicMock()
    slide_com.Parent.Windows.Count = 1
    chart = slide_com.Shapes.AddChart2.return_value.Chart
    chart.SeriesCollection.return_value.Count = 0
    chart.HasTitle = MSO_TRUE  # the default AddChart2 would leave behind
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


PP_ACTION_NONE = 0
PP_ACTION_HYPERLINK = 7


class _Hyperlink:
    def __init__(self, log: list[tuple[str, object]], *, reject: bool = False) -> None:
        self._log = log
        self.reject = reject
        self._address: str | None = None

    @property
    def Address(self) -> str | None:  # noqa: N802 - COM casing
        return self._address

    @Address.setter
    def Address(self, value: str) -> None:  # noqa: N802
        if self.reject:
            raise RuntimeError("PowerPoint rejected the address")
        self._log.append(("Address", value))
        self._address = value

    def Delete(self) -> None:  # noqa: N802
        self._log.append(("Delete", None))
        self._address = None


class _ActionSetting:
    """A ppMouseClick ActionSetting that records writes in order."""

    def __init__(self, *, reject_address: bool = False, fail_action_off: bool = False) -> None:
        self.log: list[tuple[str, object]] = []
        self.Hyperlink = _Hyperlink(self.log, reject=reject_address)
        self._action = PP_ACTION_NONE
        self._fail_action_off = fail_action_off

    @property
    def Action(self) -> int:  # noqa: N802
        return self._action

    @Action.setter
    def Action(self, value: int) -> None:  # noqa: N802
        if value == PP_ACTION_NONE and self._fail_action_off:
            raise RuntimeError("PowerPoint refused to change the action")
        self.log.append(("Action", value))
        self._action = value


def _range_with(setting: _ActionSetting) -> tuple[MagicMock, ppt.PowerPointTextRange]:
    com = MagicMock()
    com.ActionSettings.return_value = setting
    return com, ppt.PowerPointTextRange(com)


def test_hyperlink_reads_empty_when_absent():
    com, rng = _range_with(_ActionSetting())
    assert rng.hyperlink == ""
    com.ActionSettings.assert_called_with(1)  # ppMouseClick


def test_hyperlink_activates_the_click_action_then_sets_the_address():
    setting = _ActionSetting()
    com, rng = _range_with(setting)
    rng.hyperlink = "https://example.test"
    com.ActionSettings.assert_called_with(1)
    # ppActionHyperlink first, then the address, as in Microsoft's example.
    # The reverse order resets an already-active link on real PowerPoint and
    # drops the address (verified live; see test_powerpoint_live.py).
    assert setting.log == [("Action", PP_ACTION_HYPERLINK), ("Address", "https://example.test")]
    assert rng.hyperlink == "https://example.test"
    assert setting.Action == PP_ACTION_HYPERLINK


def test_relinking_keeps_the_same_order_and_replaces_the_address():
    setting = _ActionSetting()
    _, rng = _range_with(setting)
    rng.hyperlink = "https://example.test/one"
    setting.log.clear()
    rng.hyperlink = "https://example.test/two"
    assert setting.log == [
        ("Action", PP_ACTION_HYPERLINK),
        ("Address", "https://example.test/two"),
    ]
    assert rng.hyperlink == "https://example.test/two"


def test_empty_hyperlink_turns_the_action_off_and_deletes_the_link():
    setting = _ActionSetting()
    _, rng = _range_with(setting)
    rng.hyperlink = "https://example.test"
    setting.log.clear()
    rng.hyperlink = ""
    assert setting.log == [("Action", PP_ACTION_NONE), ("Delete", None)]
    assert rng.hyperlink == ""
    assert setting.Action == PP_ACTION_NONE


def test_a_rejected_address_on_an_unlinked_range_turns_the_action_off_again():
    setting = _ActionSetting(reject_address=True)
    _, rng = _range_with(setting)
    with pytest.raises(RuntimeError, match="rejected the address"):
        rng.hyperlink = "not a link PowerPoint accepts"
    assert setting.log == [("Action", PP_ACTION_HYPERLINK), ("Action", PP_ACTION_NONE)]
    assert setting.Action == PP_ACTION_NONE  # never an active link without an address


def test_a_rejected_address_on_a_linked_range_keeps_the_existing_link():
    setting = _ActionSetting()
    _, rng = _range_with(setting)
    rng.hyperlink = "https://example.test/old"
    setting.Hyperlink.reject = True
    setting.log.clear()
    with pytest.raises(RuntimeError, match="rejected the address"):
        rng.hyperlink = "not a link PowerPoint accepts"
    assert setting.Action == PP_ACTION_HYPERLINK
    assert rng.hyperlink == "https://example.test/old"
    assert ("Action", PP_ACTION_NONE) not in setting.log


def test_a_failed_rollback_keeps_the_original_error_with_a_note():
    setting = _ActionSetting(reject_address=True, fail_action_off=True)
    _, rng = _range_with(setting)
    with pytest.raises(RuntimeError, match="rejected the address") as info:
        rng.hyperlink = "not a link PowerPoint accepts"
    assert any("action off again also failed" in note for note in info.value.__notes__)


@pytest.mark.parametrize("blank", [" ", "\t", "\r\n"])
def test_a_blank_hyperlink_is_refused_without_touching_com(blank):
    setting = _ActionSetting()
    _, rng = _range_with(setting)
    with pytest.raises(ValueError, match="use '' to remove"):
        rng.hyperlink = blank
    assert setting.log == []


def test_a_non_string_hyperlink_is_refused():
    setting = _ActionSetting()
    _, rng = _range_with(setting)
    with pytest.raises(TypeError, match="must be a str"):
        rng.hyperlink = None  # type: ignore[assignment]
    assert setting.log == []


def test_shape_hyperlink_delegates_to_its_text_range():
    setting = _ActionSetting()
    shape_com = MagicMock(HasTextFrame=MSO_TRUE)
    shape_com.TextFrame.TextRange.ActionSettings.return_value = setting
    shape = ppt.PowerPointShape(shape_com)
    shape.hyperlink = "https://example.test/x"
    shape_com.TextFrame.TextRange.ActionSettings.assert_called_with(1)
    assert setting.Action == PP_ACTION_HYPERLINK
    assert shape.hyperlink == "https://example.test/x"


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
