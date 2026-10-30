"""More real PowerPoint scenarios (DESKTOP-240): persistence of formatting in
tables and paragraphs, charts across slide reordering, z-order, PDF page count
with mixed content, geometry persistence and emptying a deck.

Same preconditions as ``test_powerpoint_live.py``: skips when PowerPoint is not
registered; chart tests need a visible exclusive process.
"""

from __future__ import annotations

import sys
import winreg

import pytest

from dolphin_desktop import PowerPointApp

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

_IMAGE = "POWERPNT.EXE"
RED = (255, 0, 0)
GREEN = (0, 128, 0)


def _powerpoint_registered() -> bool:
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "PowerPoint.Application"):
            return True
    except OSError:
        return False


def _pids() -> list[int]:
    from dolphin_desktop._office_session import _office_pids

    return _office_pids(_IMAGE)


@pytest.fixture(autouse=True)
def _require_powerpoint():
    if not _powerpoint_registered():
        pytest.skip("PowerPoint.Application is not registered on this machine")


@pytest.fixture
def visible_exclusive_app():
    if _pids():
        pytest.skip("an operator PowerPoint is running; exclusive mode never claims it")
    with PowerPointApp.create(visible=True, exclusive=True) as app:
        yield app


@pytest.fixture
def png(tmp_path):
    from PIL import Image

    path = tmp_path / "logo.png"
    Image.new("RGB", (64, 32), (200, 30, 30)).save(path)
    return path


# ---------------------------------------------------------------------------
# Formatting persistence
# ---------------------------------------------------------------------------


def test_table_cell_formatting_and_hyperlink_survive_reopen(tmp_path):
    pptx = tmp_path / "table-format.pptx"
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        table = deck.add_slide().add_table(2, 2, values=[["Head", "Link"], ["1", "2"]]).table
        head = table.cell(1, 1)
        head.bold = True
        head.font_color = RED
        head.font_size = 18
        link = table.cell(1, 2)
        link.hyperlink = "https://example.test/table"
        deck.save_as(pptx)

    with PowerPointApp.open(pptx, visible=False) as app:
        table = app.active_presentation.slide(1).shape(1).table
        assert table.values == [["Head", "Link"], ["1", "2"]]
        assert table.cell(1, 1).bold is True
        assert table.cell(1, 1).font_color == RED
        assert table.cell(1, 1).font_size == 18.0
        assert table.cell(2, 1).bold is False
        assert table.cell(1, 2).hyperlink == "https://example.test/table"


def test_per_paragraph_formatting_survives_reopen(tmp_path):
    pptx = tmp_path / "paragraphs.pptx"
    text = "Title line\rbody one\rbody two"
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        box = deck.add_slide().add_textbox(text)
        box.characters(1, len("Title line")).bold = True
        box.characters(1, len("Title line")).font_size = 30
        body_start = len("Title line") + 2
        box.characters(body_start).font_size = 14
        box.characters(body_start).font_color = GREEN
        deck.save_as(pptx)

    with PowerPointApp.open(pptx, visible=False) as app:
        box = app.active_presentation.slide(1).shape(1)
        assert box.paragraphs == ["Title line", "body one", "body two"]
        assert box.characters(1, 10).bold is True
        assert box.characters(1, 10).font_size == 30.0
        assert box.characters(body_start).bold is False
        assert box.characters(body_start).font_size == 14.0
        assert box.characters(body_start).font_color == GREEN
        # mixed formatting across the whole box reads as None
        assert box.bold is None


def test_textbox_geometry_and_notes_survive_reopen(tmp_path):
    pptx = tmp_path / "geometry.pptx"
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        slide = deck.add_slide()
        box = slide.add_textbox("moved", left=10, top=20, width=300, height=40)
        box.left = 123
        box.top = 45
        box.width = 250
        slide.notes = "first note\rsecond note"
        deck.save_as(pptx)

    with PowerPointApp.open(pptx, visible=False) as app:
        slide = app.active_presentation.slide(1)
        box = slide.shape(1)
        assert (box.left, box.top, box.width) == (123.0, 45.0, 250.0)
        assert slide.notes.split("\r") == ["first note", "second note"]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def test_pie_chart_with_three_categories(visible_exclusive_app):
    slide = visible_exclusive_app.active_presentation.add_slide()
    shape = slide.add_chart(
        ["North", "South", "West"], {"Share": [50, 30, 20]}, chart_type=5, title="Share by region"
    )
    chart = shape.chart
    assert chart.chart_type == 5  # xlPie
    assert chart.series_count == 1
    assert chart.series_names == ["Share"]
    assert chart.categories == ["North", "South", "West"]
    assert sum(chart.series_values(1)) == 100.0
    assert chart.title == "Share by region"


def test_chart_follows_its_slide_when_slides_are_reordered(visible_exclusive_app):
    deck = visible_exclusive_app.active_presentation
    deck.add_slide().add_textbox("text slide")
    deck.add_slide().add_chart(["a", "b"], {"s": [1, 2]}, title="moved chart")
    assert deck.slide(2).shape(1).has_chart

    deck.move_slide(2, 1)
    assert deck.slide(1).shape(1).has_chart
    assert deck.slide(1).shape(1).chart.title == "moved chart"
    assert deck.slide(2).shape(1).text == "text slide"
    assert not deck.slide(2).shape(1).has_chart


def test_three_series_keep_independent_values(visible_exclusive_app):
    slide = visible_exclusive_app.active_presentation.add_slide()
    series = {"A": [1, 2, 3], "B": [10, 20, 30], "C": [100, 200, 300]}
    chart = slide.add_chart(["x", "y", "z"], series).chart
    assert chart.series_names == ["A", "B", "C"]
    for index, (name, values) in enumerate(series.items(), start=1):
        assert chart.series_values(index) == [float(v) for v in values]
        assert chart.series_values(name) == [float(v) for v in values]


# ---------------------------------------------------------------------------
# Z-order, mixed content, emptying a deck
# ---------------------------------------------------------------------------


def test_shapes_keep_creation_order_and_shift_after_delete(png):
    with PowerPointApp.create(visible=False) as app:
        slide = app.active_presentation.add_slide()
        first = slide.add_textbox("first")
        picture = slide.add_picture(png)
        third = slide.add_textbox("third")
        assert slide.shape_names == [first.name, picture.name, third.name]
        assert [s.has_text for s in slide.shapes] == [True, False, True]

        slide.shape(1).delete()
        assert slide.shape_count == 2
        assert slide.shape_names == [picture.name, third.name]
        assert slide.shape(2).text == "third"


def test_pdf_has_one_page_per_slide_with_mixed_content(visible_exclusive_app, png, tmp_path):
    pypdf = pytest.importorskip("pypdf")
    deck = visible_exclusive_app.active_presentation
    deck.add_slide().add_textbox("page one text")
    deck.add_slide().add_table(2, 2, values=[["k", "v"], ["table", "text"]])
    deck.add_slide().add_chart(["q1", "q2"], {"units": [3, 4]}, title="chart page")
    deck.add_slide().add_picture(png)
    pdf = tmp_path / "mixed.pdf"
    deck.export_pdf(pdf)

    reader = pypdf.PdfReader(str(pdf))
    assert len(reader.pages) == 4
    assert "page one text" in " ".join((reader.pages[0].extract_text() or "").split())
    assert "table" in (reader.pages[1].extract_text() or "")
    assert "chart page" in (reader.pages[2].extract_text() or "")


def test_deck_can_be_emptied_and_refilled():
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        for label in ("one", "two", "three"):
            deck.add_slide().add_textbox(label)
        assert deck.slide_count == 3
        while deck.slide_count:
            deck.slide(deck.slide_count).delete()
        assert deck.slide_count == 0
        assert deck.slides == []
        deck.add_slide().add_textbox("again")
        assert deck.slide_count == 1
        assert deck.slide(1).shape(1).text == "again"


def test_slide_size_is_16_by_9_on_the_default_template():
    with PowerPointApp.create(visible=False) as app:
        width, height = app.active_presentation.slide_size
        assert abs(width / height - 16 / 9) < 0.01


def test_characters_rejects_a_zero_start_before_touching_com():
    with PowerPointApp.create(visible=False) as app:
        box = app.active_presentation.add_slide().add_textbox("abc")
        with pytest.raises(ValueError, match="1-based"):
            box.characters(0)
        assert box.text == "abc"


# ---------------------------------------------------------------------------
# Saving on close / quit, exclusive shutdown guard, table structure persistence
# ---------------------------------------------------------------------------


def test_close_with_save_writes_edits_to_the_file(tmp_path):
    pptx = tmp_path / "close-save.pptx"
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        box = deck.add_slide().add_textbox("before")
        deck.save_as(pptx)
        box.text = "after"
        deck.close(save=True)
        deck.close(save=True)  # idempotent once closed

    with PowerPointApp.open(pptx, visible=False) as app:
        assert app.active_presentation.slide(1).shape(1).text == "after"


def test_quit_with_save_changes_persists_edits(tmp_path):
    pptx = tmp_path / "quit-save.pptx"
    app = PowerPointApp.create(visible=False)
    deck = app.active_presentation
    deck.add_slide().add_textbox("draft")
    deck.save_as(pptx)
    deck.slide(1).shape(1).text = "final"
    app.quit(save_changes=True)

    with PowerPointApp.open(pptx, visible=False) as again:
        assert again.active_presentation.slide(1).shape(1).text == "final"


def test_exclusive_quit_is_blocked_by_a_foreign_presentation():
    """An exclusive process is never killed while someone else's deck is open in it."""
    if _pids():
        pytest.skip("an operator PowerPoint is running; exclusive mode never claims it")
    app = PowerPointApp.create(visible=True, exclusive=True)
    pid = app.process_id
    foreign = app._com.Presentations.Add(0)  # raw COM: not owned by the facade
    try:
        with pytest.raises(RuntimeError, match="Unowned presentations"):
            app.quit()
        assert pid in _pids(), "the process must survive the refused quit"
    finally:
        foreign.Close()
    app.quit()
    assert pid not in _pids()


def test_table_structure_changes_survive_reopen(tmp_path):
    pptx = tmp_path / "table-structure.pptx"
    with PowerPointApp.create(visible=False) as app:
        deck = app.active_presentation
        table = deck.add_slide().add_table(2, 3, values=[["a", "b", "c"]]).table
        table.set_values([["1", "2", "3"]], start_row=2)
        table.add_row()
        table.set_values([["x", "y", "z"]], start_row=3)
        table.delete_column(2)
        assert table.values == [["a", "c"], ["1", "3"], ["x", "z"]]
        deck.save_as(pptx)

    with PowerPointApp.open(pptx, visible=False) as app:
        table = app.active_presentation.slide(1).shape(1).table
        assert (table.rows, table.columns) == (3, 2)
        assert table.values == [["a", "c"], ["1", "3"], ["x", "z"]]
