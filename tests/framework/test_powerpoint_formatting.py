"""Contract tests for PowerPoint text formatting, geometry, pictures and ordering."""

from unittest.mock import MagicMock

import pytest

import dolphin_desktop
from dolphin_desktop import PowerPointTextRange
from dolphin_desktop import _powerpoint as ppt

MSO_TRUE, MSO_FALSE, MSO_MIXED = -1, 0, -2


def test_text_range_is_exported():
    assert "PowerPointTextRange" in dolphin_desktop.__all__
    assert dolphin_desktop.PowerPointTextRange is PowerPointTextRange


@pytest.mark.parametrize(
    ("office_value", "expected"),
    [(MSO_TRUE, True), (MSO_FALSE, False), (MSO_MIXED, None), (1, True)],
)
def test_tristate_reads_map_to_bool_or_none_for_mixed(office_value, expected):
    com = MagicMock()
    com.Font.Bold = office_value
    com.Font.Italic = office_value
    com.Font.Underline = office_value
    rng = PowerPointTextRange(com)
    assert rng.bold is expected
    assert rng.italic is expected
    assert rng.underline is expected


def test_tristate_writes_use_mso_constants_not_python_bools():
    com = MagicMock()
    rng = PowerPointTextRange(com)
    rng.bold = True
    rng.italic = False
    rng.underline = True
    assert com.Font.Bold == MSO_TRUE
    assert com.Font.Italic == MSO_FALSE
    assert com.Font.Underline == MSO_TRUE


def test_font_color_round_trips_office_bgr_packing():
    com = MagicMock()
    com.Font.Color.RGB = 0x0000FF  # red in Office packing is the low byte
    rng = PowerPointTextRange(com)
    assert rng.font_color == (255, 0, 0)
    rng.font_color = (10, 20, 30)
    assert com.Font.Color.RGB == 10 | (20 << 8) | (30 << 16)


def test_font_color_rejects_out_of_range_channels():
    rng = PowerPointTextRange(MagicMock())
    with pytest.raises(ValueError, match=r"0..255"):
        rng.font_color = (256, 0, 0)


def test_characters_is_one_based_and_defaults_to_the_rest_of_the_text():
    com = MagicMock(Length=11)
    rng = PowerPointTextRange(com)
    rng.characters(7)
    com.Characters.assert_called_once_with(7, 5)
    rng.characters(1, 3)
    com.Characters.assert_called_with(1, 3)
    with pytest.raises(ValueError, match="1-based"):
        rng.characters(0)


def test_paragraphs_strip_paragraph_marks():
    com = MagicMock()
    com.Paragraphs.return_value.Count = 2
    com.Paragraphs.side_effect = lambda *args: (
        MagicMock(Count=2) if not args else MagicMock(Text={1: "first\r", 2: "second"}[args[0]])
    )
    assert PowerPointTextRange(com).paragraphs == ["first", "second"]


def test_shape_formatting_delegates_to_the_whole_text_range():
    com = MagicMock(HasTextFrame=MSO_TRUE, Name="caption")
    font = com.TextFrame.TextRange.Font
    font.Bold = MSO_MIXED
    font.Size = 18.0
    font.Name = "Arial"
    shape = ppt.PowerPointShape(com)
    assert shape.has_text
    assert shape.bold is None
    assert shape.font_size == 18.0
    assert shape.font_name == "Arial"
    shape.bold = True
    shape.font_size = 24
    shape.font_name = "Calibri"
    assert font.Bold == MSO_TRUE
    assert font.Size == 24.0
    assert font.Name == "Calibri"


def test_shape_without_text_frame_rejects_formatting_and_reports_has_text_false():
    shape = ppt.PowerPointShape(MagicMock(HasTextFrame=MSO_FALSE, Name="picture"))
    assert not shape.has_text
    with pytest.raises(ValueError, match="no text frame"):
        _ = shape.bold
    with pytest.raises(ValueError, match="no text frame"):
        shape.italic = True
    with pytest.raises(ValueError, match="no text frame"):
        _ = shape.text_range


def test_shape_geometry_reads_and_writes_points_as_floats():
    com = MagicMock(Left=10, Top=20, Width=300, Height=40)
    shape = ppt.PowerPointShape(com)
    assert (shape.left, shape.top, shape.width, shape.height) == (10.0, 20.0, 300.0, 40.0)
    shape.left = 15
    shape.width = 250
    assert com.Left == 15.0
    assert com.Width == 250.0


def test_add_picture_embeds_rather_than_links_and_keeps_native_size(tmp_path):
    image = tmp_path / "logo.png"
    image.write_bytes(b"\x89PNG")
    com = MagicMock()
    slide = ppt.PowerPointSlide(com)
    slide.add_picture(image, left=5, top=6)
    com.Shapes.AddPicture.assert_called_once_with(
        str(image.resolve()), MSO_FALSE, MSO_TRUE, 5, 6, -1, -1
    )
    slide.add_picture(image, width=100, height=50)
    assert com.Shapes.AddPicture.call_args.args[5:] == (100, 50)


def test_add_picture_rejects_a_missing_file_before_touching_com(tmp_path):
    com = MagicMock()
    with pytest.raises(FileNotFoundError):
        ppt.PowerPointSlide(com).add_picture(tmp_path / "missing.png")
    com.Shapes.AddPicture.assert_not_called()


def test_shapes_and_shape_names_enumerate_in_order():
    com = MagicMock()
    com.Shapes.Count = 2
    com.Shapes.Item.side_effect = lambda i: MagicMock(Name=f"shape{i}")
    slide = ppt.PowerPointSlide(com)
    assert slide.shape_names == ["shape1", "shape2"]
    assert [s.name for s in slide.shapes] == ["shape1", "shape2"]


def test_move_slide_slide_size_and_slides_enumeration():
    com = MagicMock()
    com.Slides.Count = 3
    com.PageSetup.SlideWidth = 960
    com.PageSetup.SlideHeight = 540
    deck = ppt.PowerPointPresentation(com)
    deck.move_slide(3, 1)
    com.Slides.Item.assert_called_with(3)
    com.Slides.Item.return_value.MoveTo.assert_called_once_with(1)
    assert deck.slide_size == (960.0, 540.0)
    assert len(deck.slides) == 3


def test_closing_a_presentation_releases_nested_ranges_too():
    com = MagicMock()
    deck = ppt.PowerPointPresentation(com)
    slide = deck.slide(1)
    shape = slide.shape(1)
    whole = shape.text_range
    part = whole.characters(1, 2)
    deck.close()
    assert deck._com is None
    assert slide._com is None
    assert shape._com is None
    assert whole._com is None
    assert part._com is None
