"""Real PowerPoint COM tests: slides, text, formatting, pictures, persistence.

The unit tests in ``tests/framework/test_powerpoint_com.py`` and
``test_powerpoint_formatting.py`` pin the COM call contract against mocks;
this module checks that an installed PowerPoint actually answers those calls
the way the facade assumes. Skips cleanly when PowerPoint is not registered.
"""

from __future__ import annotations

import sys
import winreg

import pytest

from dolphin_desktop import PowerPointApp

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

_IMAGE = "POWERPNT.EXE"
RED = (255, 0, 0)
NAVY = (0, 32, 96)


def _powerpoint_registered() -> bool:
    """True when the PowerPoint.Application ProgID exists; starts no process."""
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "PowerPoint.Application"):
            return True
    except OSError:
        return False


def _powerpoint_pids() -> list[int]:
    from dolphin_desktop._office_session import _office_pids

    return _office_pids(_IMAGE)


def _wait_until_no_process(timeout: float = 30.0) -> bool:
    """A shared-mode instance exits on its own shortly after its last document closes."""
    import time as _time

    deadline = _time.monotonic() + timeout
    while _powerpoint_pids():
        if _time.monotonic() >= deadline:
            return False
        _time.sleep(1)
    return True


@pytest.fixture(autouse=True)
def _require_powerpoint():
    if not _powerpoint_registered():
        pytest.skip("PowerPoint.Application is not registered on this machine")


@pytest.fixture
def png(tmp_path):
    """A small solid PNG written with Pillow (a hard dependency of the library)."""
    from PIL import Image

    path = tmp_path / "logo.png"
    Image.new("RGB", (120, 80), (30, 144, 255)).save(path)
    return path


# ---------------------------------------------------------------------------
# Process ownership
# ---------------------------------------------------------------------------


def test_exclusive_session_owns_and_quits_its_own_process():
    """exclusive=True starts a fresh POWERPNT.EXE and Quit() really ends it."""
    if not _wait_until_no_process():
        pytest.skip("an operator PowerPoint is running; exclusive mode never claims it")

    app = PowerPointApp.create(visible=False, exclusive=True)
    try:
        assert app.owns_process
        pid = app.process_id
        assert pid is not None
        assert pid in _powerpoint_pids()
        assert app.active_presentation.slide_count == 0
    finally:
        app.quit()

    assert not app.owns_process
    assert pid not in _powerpoint_pids()


# ---------------------------------------------------------------------------
# Slides, text and persistence
# ---------------------------------------------------------------------------


def test_create_edit_save_reopen_and_export_pdf(tmp_path):
    """The whole facade chain against a live presentation."""
    pptx = tmp_path / "live.pptx"
    pdf = tmp_path / "live.pdf"

    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        assert deck.slide_count == 0
        width, height = deck.slide_size
        assert width > height > 0  # default 16:9 template

        slide = deck.add_slide()  # layout 12 = blank
        assert deck.slide_count == 1
        assert slide.index == 1
        assert slide.shape_count == 0

        box = slide.add_textbox("hello from dolphin", left=50, top=60, width=400, height=80)
        assert slide.shape_count == 1
        assert box.text == "hello from dolphin"
        assert box.has_text

        # Lookup by 1-based index and by the name PowerPoint assigned.
        assert slide.shape(1).text == "hello from dolphin"
        assert slide.shape(box.name).text == "hello from dolphin"
        assert slide.shape_names == [box.name]
        assert [s.name for s in slide.shapes] == [box.name]

        box.text = "edited"
        assert slide.shape(1).text == "edited"

        deck.save_as(pptx)
        assert deck.name == "live.pptx"
        deck.export_pdf(pdf)
        deck.save()  # plain Save works once the file has a path

    assert pptx.is_file() and pptx.stat().st_size > 0
    assert pdf.is_file()
    assert pdf.read_bytes().startswith(b"%PDF")

    # Reopen: the saved text survived and the exported PDF did not rename the deck.
    with PowerPointApp.open(pptx, visible=False) as app:
        deck = app.active_presentation
        assert deck.name == "live.pptx"
        assert deck.slide_count == 1
        assert deck.slide(1).shape(1).text == "edited"

        # Opening the same file twice is rejected rather than stealing ownership.
        with pytest.raises(RuntimeError, match="already open"):
            PowerPointApp.open(pptx, visible=False)


def test_textbox_geometry_matches_what_was_requested():
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        box = slide.add_textbox("fixed", left=72, top=144, width=288, height=36)
        assert (box.left, box.top, box.width) == (72.0, 144.0, 288.0)
        assert box.height > 0  # PowerPoint auto-fits text-box height

        box.left = 100
        box.top = 200
        box.width = 300
        assert (box.left, box.top, box.width) == (100.0, 200.0, 300.0)


def test_multi_paragraph_text_round_trips():
    """CR separates paragraphs in PowerPoint; the facade hides the marks."""
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        box = slide.add_textbox("first line\rsecond line\rthird line")
        assert box.paragraphs == ["first line", "second line", "third line"]
        assert "second line" in box.text
        assert box.text_range.length == len("first line\rsecond line\rthird line")


def test_slides_can_be_reordered_and_deleted():
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        for label in ("one", "two", "three"):
            deck.add_slide().add_textbox(label)
        assert [s.shape(1).text for s in deck.slides] == ["one", "two", "three"]

        deck.move_slide(3, 1)
        assert [s.shape(1).text for s in deck.slides] == ["three", "one", "two"]

        deck.slide(2).delete()
        assert deck.slide_count == 2
        assert [s.shape(1).text for s in deck.slides] == ["three", "two"]
        assert [s.index for s in deck.slides] == [1, 2]


def test_shape_can_be_deleted_from_slide():
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        keep = slide.add_textbox("keep")
        slide.add_textbox("drop")
        assert slide.shape_count == 2
        slide.shape(2).delete()
        assert slide.shape_count == 1
        assert slide.shape_names == [keep.name]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_bold_italic_underline_on_a_whole_textbox():
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        box = slide.add_textbox("Quarterly results")

        assert box.bold is False
        assert box.italic is False
        assert box.underline is False

        box.bold = True
        box.italic = True
        box.underline = True
        assert box.bold is True
        assert box.italic is True
        assert box.underline is True

        # A fresh lookup of the same shape sees the same formatting.
        again = slide.shape(1)
        assert (again.bold, again.italic, again.underline) == (True, True, True)

        box.bold = False
        assert box.bold is False
        assert again.italic is True


def test_font_name_size_and_color_round_trip():
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        box = slide.add_textbox("Styled")

        box.font_name = "Arial"
        box.font_size = 32
        box.font_color = RED
        assert box.font_name == "Arial"
        assert box.font_size == 32.0
        assert box.font_color == RED

        box.font_name = "Courier New"
        box.font_size = 11.5
        box.font_color = NAVY
        assert slide.shape(1).font_name == "Courier New"
        assert slide.shape(1).font_size == 11.5
        assert slide.shape(1).font_color == NAVY


def test_partial_formatting_reports_mixed_on_the_whole_and_exact_on_the_run():
    """Bold a single word: the box is 'mixed', the word is bold, the rest is not."""
    text = "Revenue up 12% this quarter"
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        box = slide.add_textbox(text)

        start = text.index("12%") + 1  # PowerPoint positions are 1-based
        pct = box.characters(start, 3)
        assert pct.text == "12%"
        pct.bold = True
        pct.italic = True
        pct.font_color = RED

        assert box.bold is None
        assert box.italic is None
        assert pct.bold is True
        assert pct.italic is True
        assert pct.font_color == RED

        before = box.characters(1, start - 1)
        after = box.characters(start + 3)
        assert before.text == "Revenue up "
        assert after.text == " this quarter"
        assert before.bold is False
        assert after.bold is False
        assert after.italic is False
        assert before.font_color != RED

        # The run can be found again from a fresh shape lookup.
        assert slide.shape(1).characters(start, 3).bold is True


def test_formatting_survives_save_and_reopen(tmp_path):
    pptx = tmp_path / "styled.pptx"
    text = "Plain BOLD italic"
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        box = deck.add_slide().add_textbox(text)
        box.font_name = "Arial"
        box.font_size = 20
        box.characters(7, 4).bold = True  # "BOLD"
        box.characters(12, 6).italic = True  # "italic"
        box.characters(12, 6).font_color = NAVY
        deck.save_as(pptx)

    with PowerPointApp.open(pptx, visible=False) as app:
        box = app.active_presentation.slide(1).shape(1)
        assert box.text == text
        assert box.font_name == "Arial"
        assert box.font_size == 20.0
        assert box.bold is None
        assert box.italic is None
        assert box.characters(7, 4).bold is True
        assert box.characters(7, 4).italic is False
        assert box.characters(12, 6).italic is True
        assert box.characters(12, 6).bold is False
        assert box.characters(12, 6).font_color == NAVY
        assert box.characters(1, 5).bold is False


def test_paragraph_level_formatting_via_character_ranges():
    """Format the second paragraph only; paragraphs stay readable as a list."""
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        box = slide.add_textbox("Heading\rbody text")
        heading = box.characters(1, len("Heading"))
        body = box.characters(len("Heading") + 2)  # skip the paragraph mark
        heading.bold = True
        heading.font_size = 28
        body.font_size = 14
        body.italic = True

        assert box.paragraphs == ["Heading", "body text"]
        assert heading.bold is True and heading.font_size == 28.0
        assert body.italic is True and body.font_size == 14.0
        assert body.bold is False
        assert box.bold is None


# ---------------------------------------------------------------------------
# Pictures and non-text shapes
# ---------------------------------------------------------------------------


def test_add_picture_embeds_image_with_native_or_forced_size(png):
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()

        native = slide.add_picture(png, left=10, top=20)
        assert not native.has_text
        assert (native.left, native.top) == (10.0, 20.0)
        # 120x80 px at 96 dpi -> 90x60 pt; keep the aspect ratio check loose
        # so a different DPI assumption in Office does not fail the test.
        assert native.width > 0 and native.height > 0
        assert abs(native.width / native.height - 1.5) < 0.05

        forced = slide.add_picture(png, left=200, top=20, width=240, height=100)
        assert (forced.width, forced.height) == (240.0, 100.0)

        assert slide.shape_count == 2
        with pytest.raises(ValueError, match="no text frame"):
            _ = native.text


def test_picture_survives_save_and_reopen_without_the_source_file(png, tmp_path):
    pptx = tmp_path / "with_picture.pptx"
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        deck.add_slide().add_picture(png, left=0, top=0, width=100, height=50)
        deck.save_as(pptx)

    png.unlink()  # embedded, not linked: the deck must not need it any more

    with PowerPointApp.open(pptx, visible=False) as app:
        shape = app.active_presentation.slide(1).shape(1)
        assert not shape.has_text
        assert (shape.width, shape.height) == (100.0, 50.0)


def test_shape_without_text_frame_rejects_text_access():
    """A line has no text frame; the facade says so instead of crashing."""
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        slide._com.Shapes.AddLine(10, 10, 200, 10)
        line = slide.shape(1)
        assert not line.has_text
        with pytest.raises(ValueError, match="no text frame"):
            _ = line.text
        with pytest.raises(ValueError, match="no text frame"):
            line.text = "x"
        with pytest.raises(ValueError, match="no text frame"):
            line.bold = True


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_open_with_default_security_restores_automation_security(tmp_path):
    """open() leaves AutomationSecurity exactly as PowerPoint had it."""
    pptx = tmp_path / "plain.pptx"
    with PowerPointApp.create(visible=False) as app:
        app.active_presentation.add_slide()
        app.active_presentation.save_as(pptx)

    with PowerPointApp.open(pptx, visible=False) as app:
        # 3 (ForceDisable) only applies while Presentations.Open runs.
        assert int(app._com.AutomationSecurity) != 3


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def test_table_round_trip_with_values_and_cell_formatting():
    rows = [["Region", "Revenue"], ["EMEA", 120], ["APAC", 95]]
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        shape = slide.add_table(3, 2, left=40, top=40, width=400, height=120, values=rows)

        assert shape.has_table
        assert not shape.has_chart
        table = shape.table
        assert (table.rows, table.columns) == (3, 2)
        assert table.values == [["Region", "Revenue"], ["EMEA", "120"], ["APAC", "95"]]
        assert table.row_values(3) == ["APAC", "95"]
        assert table.cell(2, 1).text == "EMEA"

        header = table.cell(1, 1)
        header.bold = True
        header.font_color = RED
        assert table.cell(1, 1).bold is True
        assert table.cell(1, 1).font_color == RED
        assert table.cell(2, 1).bold is False

        table.cell(3, 2).text = "101"
        assert slide.shape(1).table.values[2] == ["APAC", "101"]


def test_table_rows_and_columns_can_be_added_and_deleted():
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        table = slide.add_table(2, 2, values=[["a", "b"], ["c", "d"]]).table

        table.add_row()
        assert table.rows == 3
        table.set_values([["e", "f"]], start_row=3)
        assert table.values == [["a", "b"], ["c", "d"], ["e", "f"]]

        table.add_row(before=1)
        assert table.rows == 4
        assert table.row_values(2) == ["a", "b"]

        table.add_column()
        assert table.columns == 3
        table.delete_row(1)
        table.delete_column(3)
        assert table.values == [["a", "b"], ["c", "d"], ["e", "f"]]


def test_table_values_survive_save_and_reopen(tmp_path):
    pptx = tmp_path / "table.pptx"
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        deck.add_slide().add_table(2, 3, values=[["x", "y", "z"], [1, 2, 3]])
        deck.save_as(pptx)

    with PowerPointApp.open(pptx, visible=False) as app:
        shape = app.active_presentation.slide(1).shape(1)
        assert shape.has_table
        assert shape.table.values == [["x", "y", "z"], ["1", "2", "3"]]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


@pytest.fixture
def visible_exclusive_app():
    """A visible deck (charts need a window) in an exclusive process (so it quits)."""
    if not _wait_until_no_process():
        pytest.skip("an operator PowerPoint is running; exclusive mode never claims it")
    with PowerPointApp.create(visible=True, exclusive=True) as app:
        yield app


def test_chart_on_a_hidden_presentation_fails_with_a_clear_message():
    """PowerPoint itself refuses; the facade names the fix instead of E_FAIL."""
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        with pytest.raises(RuntimeError, match="visible=True"):
            slide.add_chart(["Q1"], {"a": [1]})
        assert slide.shape_count == 0


def test_chart_exposes_categories_series_and_title(visible_exclusive_app):
    categories = ["Q1", "Q2", "Q3"]
    series = {"EMEA": [100, 110, 120], "APAC": [80, 85, 95]}
    slide = visible_exclusive_app.active_presentation.add_slide()
    shape = slide.add_chart(categories, series, title="Revenue by region")

    assert shape.has_chart
    assert not shape.has_table
    chart = shape.chart
    assert chart.chart_type == 51  # xlColumnClustered
    assert chart.has_title
    assert chart.title == "Revenue by region"
    assert chart.series_count == 2
    assert chart.series_names == ["EMEA", "APAC"]
    assert chart.categories == categories
    assert chart.series_values("EMEA") == [100.0, 110.0, 120.0]
    assert chart.series_values(2) == [80.0, 85.0, 95.0]

    chart.title = "Renamed"
    assert slide.shape(1).chart.title == "Renamed"


def test_chart_type_and_data_survive_save_and_reopen(tmp_path):
    if not _wait_until_no_process():
        pytest.skip("an operator PowerPoint is running; exclusive mode never claims it")
    pptx = tmp_path / "chart.pptx"
    with PowerPointApp.create(visible=True, exclusive=True) as app:
        deck = app.active_presentation
        deck.add_slide().add_chart(["Jan", "Feb"], {"Units": [5, 7]}, chart_type=4)  # xlLine
        deck.save_as(pptx)

    # Reading chart data does not need a window.
    with PowerPointApp.open(pptx, visible=False) as app:
        chart = app.active_presentation.slide(1).shape(1).chart
        assert chart is not None
        assert app.active_presentation.slide(1).shape(1).has_chart
        assert chart.chart_type == 4
        assert chart.series_names == ["Units"]
        assert chart.categories == ["Jan", "Feb"]
        assert chart.series_values("Units") == [5.0, 7.0]


# ---------------------------------------------------------------------------
# Notes, layout titles, hyperlinks, windows
# ---------------------------------------------------------------------------


def test_speaker_notes_round_trip_and_persist(tmp_path):
    pptx = tmp_path / "notes.pptx"
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        slide = deck.add_slide()
        assert slide.notes == ""
        slide.notes = "Mention the EMEA restatement."
        assert slide.notes == "Mention the EMEA restatement."
        assert deck.slide(1).notes == "Mention the EMEA restatement."
        deck.save_as(pptx)

    with PowerPointApp.open(pptx, visible=False) as app:
        assert app.active_presentation.slide(1).notes == "Mention the EMEA restatement."


def test_layout_title_placeholder_is_readable_and_writable():
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        blank = deck.add_slide()  # layout 12: no title placeholder
        assert not blank.has_title
        with pytest.raises(ValueError, match="no title placeholder"):
            _ = blank.title

        titled = deck.add_slide(layout=11)  # ppLayoutTitleOnly
        assert titled.has_title
        titled.title = "Q3 by region"
        assert titled.title == "Q3 by region"
        assert deck.slide(2).title == "Q3 by region"
        assert deck.slide(2).shape(1).bold in (True, False, None)  # the placeholder is a shape


def test_hyperlink_on_text_round_trips_and_persists(tmp_path):
    pptx = tmp_path / "links.pptx"
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        slide = deck.add_slide()
        box = slide.add_textbox("Full report")
        assert box.hyperlink == ""
        box.hyperlink = "https://example.test/report"
        assert box.hyperlink == "https://example.test/report"

        mixed = slide.add_textbox("See docs or source")
        mixed.characters(5, 4).hyperlink = "https://example.test/docs"
        assert mixed.characters(5, 4).hyperlink == "https://example.test/docs"
        assert mixed.characters(13, 6).hyperlink == ""
        deck.save_as(pptx)

    with PowerPointApp.open(pptx, visible=False) as app:
        slide = app.active_presentation.slide(1)
        assert slide.shape(1).hyperlink == "https://example.test/report"
        assert slide.shape(2).characters(5, 4).hyperlink == "https://example.test/docs"


def test_visible_presentation_gets_a_window_and_hidden_one_does_not():
    # Exclusive first: a shared hidden instance can still be shutting down
    # when the next check for foreign POWERPNT.EXE processes runs.
    if not _wait_until_no_process():
        pytest.skip("an operator PowerPoint is running; exclusive mode never claims it")
    with PowerPointApp.create(visible=True, exclusive=True) as app:
        assert app.active_presentation.has_window
    with PowerPointApp.create(visible=False) as app:
        assert not app.active_presentation.has_window


def test_connect_attaches_to_a_running_instance_without_closing_its_deck():
    """connect() reads the operator's active presentation and leaves it open."""
    client = pytest.importorskip("win32com.client")
    operator = client.Dispatch("PowerPoint.Application")
    deck_com = operator.Presentations.Add(-1)  # ActivePresentation needs a window
    try:
        deck_com.Slides.Add(1, 12).Shapes.AddTextbox(
            1, 10, 10, 200, 50
        ).TextFrame.TextRange.Text = "operator text"
        with PowerPointApp.connect() as app:
            assert not app.owns_process
            deck = app.active_presentation
            assert deck.slide_count == 1
            assert deck.slide(1).shape(1).text == "operator text"
        # The operator's presentation is still open and still readable.
        assert int(operator.Presentations.Count) == 1
        assert deck_com.Slides(1).Shapes(1).TextFrame.TextRange.Text == "operator text"
    finally:
        deck_com.Close()
        operator.Quit()


# ---------------------------------------------------------------------------
# PDF content
# ---------------------------------------------------------------------------


def test_exported_pdf_has_one_page_per_slide_with_the_slide_text(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    pdf = tmp_path / "deck.pdf"
    labels = ["alpha slide", "bravo slide", "charlie slide"]
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        for label in labels:
            deck.add_slide().add_textbox(label, left=50, top=50, width=500, height=60)
        deck.export_pdf(pdf)

    reader = pypdf.PdfReader(str(pdf))
    assert len(reader.pages) == len(labels)
    for page, label in zip(reader.pages, labels, strict=True):
        assert label in " ".join((page.extract_text() or "").split())


# ---------------------------------------------------------------------------
# Controlled errors (DESKTOP-240 item 10)
# ---------------------------------------------------------------------------


def test_opening_a_missing_file_fails_before_powerpoint_starts(tmp_path):
    before = _powerpoint_pids()
    with pytest.raises(FileNotFoundError):
        PowerPointApp.open(tmp_path / "does-not-exist.pptx", visible=False)
    assert _powerpoint_pids() == before


def test_missing_slide_and_shape_raise_lookup_errors():
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        slide = deck.add_slide()
        slide.add_textbox("only shape")
        with pytest.raises(LookupError, match="No slide 5"):
            deck.slide(5)
        with pytest.raises(LookupError, match="No shape 'logo'"):
            slide.shape("logo")
        with pytest.raises(LookupError, match="No shape 9"):
            slide.shape(9)
        assert slide.shape(1).text == "only shape"  # the deck is still usable
