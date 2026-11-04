"""Contract tests for the thin PowerPointShape delegations Codecov flagged on PR #57.

Every accessor here forwards to the shape's whole text range or to the COM
shape; the tests pin that forwarding (and the two defensive `except` branches)
without a real PowerPoint.
"""

from unittest.mock import MagicMock, PropertyMock

import pytest

from dolphin_desktop import _powerpoint as ppt

MSO_TRUE, MSO_FALSE, MSO_MIXED = -1, 0, -2


def _text_shape(**font):
    com = MagicMock(HasTextFrame=MSO_TRUE, Name="caption")
    rng = com.TextFrame.TextRange
    rng.Length = 11
    rng.Font.Italic = font.get("italic", MSO_FALSE)
    rng.Font.Underline = font.get("underline", MSO_TRUE)
    rng.Font.Color.RGB = font.get("rgb", 0x0000FF)  # red
    rng.ActionSettings.return_value.Hyperlink.Address = font.get("link", "https://example.test")
    return com, ppt.PowerPointShape(com)


def test_shape_characters_and_paragraphs_delegate_to_the_whole_range():
    com, shape = _text_shape()
    rng = com.TextFrame.TextRange
    rng.Paragraphs.side_effect = lambda *a: (
        MagicMock(Count=2) if not a else MagicMock(Text={1: "one\r", 2: "two"}[a[0]])
    )
    sub = shape.characters(3)
    rng.Characters.assert_called_once_with(3, 9)  # rest of an 11-character range
    assert sub._com is rng.Characters.return_value
    assert shape.paragraphs == ["one", "two"]


def test_shape_italic_underline_font_color_and_hyperlink_read_through():
    com, shape = _text_shape(italic=MSO_MIXED, underline=MSO_TRUE, rgb=0x0000FF)
    assert shape.italic is None
    assert shape.underline is True
    assert shape.font_color == (255, 0, 0)
    assert shape.hyperlink == "https://example.test"
    com.TextFrame.TextRange.ActionSettings.assert_called_with(1)  # ppMouseClick


def test_shape_underline_and_font_color_write_through():
    com, shape = _text_shape()
    font = com.TextFrame.TextRange.Font
    shape.underline = False
    assert font.Underline == MSO_FALSE
    shape.font_color = (1, 2, 3)
    assert font.Color.RGB == 1 | (2 << 8) | (3 << 16)
    with pytest.raises(ValueError, match=r"0\.\.255"):
        shape.font_color = (0, 0, 999)


def test_shape_top_height_setters_and_delete_touch_the_com_shape():
    com = MagicMock(Top=1, Height=2)
    shape = ppt.PowerPointShape(com)
    shape.top = 33
    shape.height = 44
    assert com.Top == 33.0
    assert com.Height == 44.0
    shape.delete()
    com.Delete.assert_called_once_with()


def test_notes_lookup_skips_shapes_that_are_not_placeholders():
    """A notes page carries non-placeholder shapes; asking them for a placeholder type raises."""
    picture = MagicMock()
    type(picture).PlaceholderFormat = PropertyMock(side_effect=Exception("not a placeholder"))
    body = MagicMock()
    body.PlaceholderFormat.Type = 2  # ppPlaceholderBody
    body.TextFrame.TextRange.Text = "speaker notes"
    com = MagicMock()
    com.NotesPage.Shapes.Count = 2
    com.NotesPage.Shapes.Item.side_effect = lambda i: [picture, body][i - 1]
    assert ppt.PowerPointSlide(com).notes == "speaker notes"


def test_presentation_without_a_reachable_window_count_is_treated_as_windowless():
    """If Parent.Windows cannot be read, add_chart refuses instead of guessing."""
    com = MagicMock()
    type(com.Parent).Windows = PropertyMock(side_effect=Exception("no parent"))
    slide = ppt.PowerPointSlide(com)
    assert slide._presentation_has_window() is False
    with pytest.raises(RuntimeError, match="visible=True"):
        slide.add_chart(["a"], {"s": [1]})
    com.Shapes.AddChart2.assert_not_called()
