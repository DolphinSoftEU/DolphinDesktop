"""PowerPoint COM facade with document-scoped ownership.

PowerPoint is a multi-use COM server: creating an Application can reuse the
operator's process. Never quit that process or close untracked presentations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar

from ._office import _require_win32com
from ._office_session import _OfficeSession, _start_office

# Office MsoTriState values: formatting reads return None for msoTriStateMixed.
_MSO_TRUE = -1
_MSO_FALSE = 0
_MSO_MIXED = -2

_PP_PLACEHOLDER_BODY = 2  # PpPlaceholderType.ppPlaceholderBody: the notes text
_PP_MOUSE_CLICK = 1  # PpMouseActivation.ppMouseClick
_XL_COLUMN_CLUSTERED = 51  # XlChartType default for add_chart()


def _tristate(value: Any) -> bool | None:
    state = int(value)
    if state == _MSO_MIXED:
        return None
    return state != _MSO_FALSE


def _mso(value: bool) -> int:
    return _MSO_TRUE if value else _MSO_FALSE


def _rgb_from_office(value: Any) -> tuple[int, int, int]:
    """Office packs colours as B*65536 + G*256 + R."""
    packed = int(value)
    return packed & 0xFF, (packed >> 8) & 0xFF, (packed >> 16) & 0xFF


def _rgb_to_office(rgb: tuple[int, int, int]) -> int:
    red, green, blue = rgb
    for channel in (red, green, blue):
        if not 0 <= int(channel) <= 255:
            raise ValueError(f"RGB channels must be 0..255, got {rgb!r}")
    return int(red) | (int(green) << 8) | (int(blue) << 16)


class _ComOwner:
    """Shared release bookkeeping: children are released before the parent."""

    def __init__(self, _com: Any) -> None:
        self._com = _com
        self._children: list[_ComOwner] = []

    def _adopt(self, child: _ComOwnerT) -> _ComOwnerT:
        self._children.append(child)
        return child

    def _release_com(self) -> None:
        for child in self._children:
            child._release_com()
        self._children.clear()
        self._com = None


_ComOwnerT = TypeVar("_ComOwnerT", bound=_ComOwner)


class PowerPointTextRange(_ComOwner):
    """A run of text with its font formatting.

    Formatting reads return ``None`` when the range mixes values (bold and
    plain words in one box, say); writes apply to every character in the
    range. Character positions are 1-based, as in PowerPoint itself.
    """

    @property
    def text(self) -> str:
        return str(self._com.Text)

    @text.setter
    def text(self, value: str) -> None:
        self._com.Text = value

    @property
    def length(self) -> int:
        """Number of characters in the range."""
        return int(self._com.Length)

    @property
    def bold(self) -> bool | None:
        return _tristate(self._com.Font.Bold)

    @bold.setter
    def bold(self, value: bool) -> None:
        self._com.Font.Bold = _mso(value)

    @property
    def italic(self) -> bool | None:
        return _tristate(self._com.Font.Italic)

    @italic.setter
    def italic(self, value: bool) -> None:
        self._com.Font.Italic = _mso(value)

    @property
    def underline(self) -> bool | None:
        return _tristate(self._com.Font.Underline)

    @underline.setter
    def underline(self, value: bool) -> None:
        self._com.Font.Underline = _mso(value)

    @property
    def font_name(self) -> str:
        """Font family; empty when the range mixes fonts."""
        return str(self._com.Font.Name or "")

    @font_name.setter
    def font_name(self, value: str) -> None:
        self._com.Font.Name = value

    @property
    def font_size(self) -> float:
        """Font size in points."""
        return float(self._com.Font.Size)

    @font_size.setter
    def font_size(self, value: float) -> None:
        self._com.Font.Size = float(value)

    @property
    def font_color(self) -> tuple[int, int, int]:
        """Font colour as an ``(red, green, blue)`` tuple."""
        return _rgb_from_office(self._com.Font.Color.RGB)

    @font_color.setter
    def font_color(self, value: tuple[int, int, int]) -> None:
        self._com.Font.Color.RGB = _rgb_to_office(value)

    @property
    def hyperlink(self) -> str:
        """Mouse-click hyperlink address of the range, or ``""`` when there is none."""
        return str(self._com.ActionSettings(_PP_MOUSE_CLICK).Hyperlink.Address or "")

    @hyperlink.setter
    def hyperlink(self, address: str) -> None:
        self._com.ActionSettings(_PP_MOUSE_CLICK).Hyperlink.Address = address

    def characters(self, start: int, length: int | None = None) -> PowerPointTextRange:
        """Sub-range starting at 1-based *start*; *length* defaults to the rest."""
        if start < 1:
            raise ValueError("start is 1-based and must be >= 1")
        if length is None:
            length = self.length - start + 1
        return self._adopt(PowerPointTextRange(self._com.Characters(start, length)))

    @property
    def paragraphs(self) -> list[str]:
        """Paragraph texts without their trailing paragraph marks."""
        count = int(self._com.Paragraphs().Count)
        return [str(self._com.Paragraphs(i).Text).rstrip("\r\n") for i in range(1, count + 1)]


class PowerPointTable(_ComOwner):
    """A table shape's grid; rows and columns are 1-based like PowerPoint."""

    @property
    def rows(self) -> int:
        return int(self._com.Rows.Count)

    @property
    def columns(self) -> int:
        return int(self._com.Columns.Count)

    def cell(self, row: int, column: int) -> PowerPointTextRange:
        """The text of one cell as a formattable range."""
        return self._adopt(
            PowerPointTextRange(self._com.Cell(row, column).Shape.TextFrame.TextRange)
        )

    def row_values(self, row: int) -> list[str]:
        return [self.cell(row, column).text for column in range(1, self.columns + 1)]

    @property
    def values(self) -> list[list[str]]:
        """All cell texts, row by row."""
        return [self.row_values(row) for row in range(1, self.rows + 1)]

    def set_values(self, rows: Sequence[Sequence[Any]], *, start_row: int = 1) -> None:
        """Write *rows* into the table from *start_row*; the grid must be large enough."""
        for offset, values in enumerate(rows):
            row = start_row + offset
            if row > self.rows or len(values) > self.columns:
                raise ValueError(
                    f"{len(rows)} row(s) of up to {max(len(v) for v in rows)} value(s) do "
                    f"not fit a {self.rows}x{self.columns} table from row {start_row}"
                )
            for column, value in enumerate(values, start=1):
                self.cell(row, column).text = str(value)

    def add_row(self, before: int | None = None) -> None:
        """Append a row, or insert one before the 1-based *before* row."""
        if before is None:
            self._com.Rows.Add()
        else:
            self._com.Rows.Add(before)

    def add_column(self, before: int | None = None) -> None:
        if before is None:
            self._com.Columns.Add()
        else:
            self._com.Columns.Add(before)

    def delete_row(self, row: int) -> None:
        self._com.Rows(row).Delete()

    def delete_column(self, column: int) -> None:
        self._com.Columns(column).Delete()


class PowerPointChart(_ComOwner):
    """Read-mostly view of a chart shape: type, title, categories and series."""

    @property
    def chart_type(self) -> int:
        """The XlChartType value (51 is a clustered column chart)."""
        return int(self._com.ChartType)

    @property
    def has_title(self) -> bool:
        return bool(self._com.HasTitle)

    @property
    def title(self) -> str:
        return str(self._com.ChartTitle.Text) if self.has_title else ""

    @title.setter
    def title(self, value: str) -> None:
        self._com.HasTitle = _MSO_TRUE
        self._com.ChartTitle.Text = value

    @property
    def series_count(self) -> int:
        return int(self._com.SeriesCollection().Count)

    @property
    def series_names(self) -> list[str]:
        return [str(self._com.SeriesCollection(i).Name) for i in range(1, self.series_count + 1)]

    @property
    def categories(self) -> list[str]:
        """Category labels of the first series."""
        if self.series_count == 0:
            return []
        return [str(value) for value in self._com.SeriesCollection(1).XValues]

    def series_values(self, name_or_index: str | int) -> list[float]:
        """Numeric values of one series, by 1-based index or by name."""
        return [float(value) for value in self._com.SeriesCollection(name_or_index).Values]


class PowerPointShape(_ComOwner):
    """A named PowerPoint shape: text, formatting, geometry, table or chart."""

    @property
    def name(self) -> str:
        """Shape name used by :meth:`PowerPointSlide.shape`."""
        return str(self._com.Name)

    @property
    def has_text(self) -> bool:
        """Whether the shape carries a text frame (pictures and lines do not)."""
        return bool(self._com.HasTextFrame)

    def _frame(self) -> Any:
        if not self._com.HasTextFrame:
            raise ValueError(f"Shape {self.name!r} has no text frame")
        return self._com.TextFrame

    @property
    def text_range(self) -> PowerPointTextRange:
        """The whole text of the shape as a formattable range."""
        return self._adopt(PowerPointTextRange(self._frame().TextRange))

    def characters(self, start: int, length: int | None = None) -> PowerPointTextRange:
        """A 1-based sub-range of the shape text; see :meth:`PowerPointTextRange.characters`."""
        return self.text_range.characters(start, length)

    @property
    def text(self) -> str:
        """Read shape text; raise ValueError for shapes without a text frame."""
        return str(self._frame().TextRange.Text)

    @text.setter
    def text(self, value: str) -> None:
        self._frame().TextRange.Text = value

    @property
    def paragraphs(self) -> list[str]:
        return self.text_range.paragraphs

    @property
    def bold(self) -> bool | None:
        return self.text_range.bold

    @bold.setter
    def bold(self, value: bool) -> None:
        self.text_range.bold = value

    @property
    def italic(self) -> bool | None:
        return self.text_range.italic

    @italic.setter
    def italic(self, value: bool) -> None:
        self.text_range.italic = value

    @property
    def underline(self) -> bool | None:
        return self.text_range.underline

    @underline.setter
    def underline(self, value: bool) -> None:
        self.text_range.underline = value

    @property
    def font_name(self) -> str:
        return self.text_range.font_name

    @font_name.setter
    def font_name(self, value: str) -> None:
        self.text_range.font_name = value

    @property
    def font_size(self) -> float:
        return self.text_range.font_size

    @font_size.setter
    def font_size(self, value: float) -> None:
        self.text_range.font_size = value

    @property
    def font_color(self) -> tuple[int, int, int]:
        return self.text_range.font_color

    @font_color.setter
    def font_color(self, value: tuple[int, int, int]) -> None:
        self.text_range.font_color = value

    @property
    def hyperlink(self) -> str:
        return self.text_range.hyperlink

    @hyperlink.setter
    def hyperlink(self, address: str) -> None:
        self.text_range.hyperlink = address

    # Tables and charts.

    @property
    def has_table(self) -> bool:
        return bool(self._com.HasTable)

    @property
    def table(self) -> PowerPointTable:
        if not self._com.HasTable:
            raise ValueError(f"Shape {self.name!r} is not a table")
        return self._adopt(PowerPointTable(self._com.Table))

    @property
    def has_chart(self) -> bool:
        return bool(self._com.HasChart)

    @property
    def chart(self) -> PowerPointChart:
        if not self._com.HasChart:
            raise ValueError(f"Shape {self.name!r} is not a chart")
        return self._adopt(PowerPointChart(self._com.Chart))

    # Geometry, in points.

    @property
    def left(self) -> float:
        return float(self._com.Left)

    @left.setter
    def left(self, value: float) -> None:
        self._com.Left = float(value)

    @property
    def top(self) -> float:
        return float(self._com.Top)

    @top.setter
    def top(self, value: float) -> None:
        self._com.Top = float(value)

    @property
    def width(self) -> float:
        return float(self._com.Width)

    @width.setter
    def width(self, value: float) -> None:
        self._com.Width = float(value)

    @property
    def height(self) -> float:
        return float(self._com.Height)

    @height.setter
    def height(self, value: float) -> None:
        self._com.Height = float(value)

    def delete(self) -> None:
        """Remove this shape from its slide."""
        self._com.Delete()


class PowerPointSlide(_ComOwner):
    """A slide with shape lookup, text boxes, pictures, tables, charts and notes."""

    @property
    def index(self) -> int:
        """Current 1-based slide index."""
        return int(self._com.SlideIndex)

    @property
    def shape_count(self) -> int:
        """Number of shapes on the slide."""
        return int(self._com.Shapes.Count)

    def _track(self, com_shape: Any) -> PowerPointShape:
        return self._adopt(PowerPointShape(com_shape))

    def shape(self, name_or_index: str | int) -> PowerPointShape:
        """Return a shape by name or 1-based index."""
        return self._track(self._com.Shapes.Item(name_or_index))

    @property
    def shapes(self) -> list[PowerPointShape]:
        """All shapes on the slide, in z-order."""
        return [self._track(self._com.Shapes.Item(i)) for i in range(1, self.shape_count + 1)]

    @property
    def shape_names(self) -> list[str]:
        return [str(self._com.Shapes.Item(i).Name) for i in range(1, self.shape_count + 1)]

    # Layout placeholders and notes.

    @property
    def has_title(self) -> bool:
        """Whether the slide layout provides a title placeholder."""
        return bool(self._com.Shapes.HasTitle)

    @property
    def title(self) -> str:
        """Text of the layout's title placeholder; ValueError when the layout has none."""
        if not self.has_title:
            raise ValueError("slide layout has no title placeholder")
        return str(self._com.Shapes.Title.TextFrame.TextRange.Text)

    @title.setter
    def title(self, value: str) -> None:
        if not self.has_title:
            raise ValueError("slide layout has no title placeholder")
        self._com.Shapes.Title.TextFrame.TextRange.Text = value

    def _notes_placeholder(self) -> Any | None:
        shapes = self._com.NotesPage.Shapes
        for i in range(1, int(shapes.Count) + 1):
            shape = shapes.Item(i)
            try:
                placeholder_type = int(shape.PlaceholderFormat.Type)
            except Exception:
                continue  # not a placeholder
            if placeholder_type == _PP_PLACEHOLDER_BODY:
                return shape
        return None

    @property
    def notes(self) -> str:
        """Speaker notes text, ``""`` when the notes page has no body placeholder."""
        placeholder = self._notes_placeholder()
        if placeholder is None:
            return ""
        return str(placeholder.TextFrame.TextRange.Text)

    @notes.setter
    def notes(self, value: str) -> None:
        placeholder = self._notes_placeholder()
        if placeholder is None:
            raise RuntimeError("slide notes page has no body placeholder to write into")
        placeholder.TextFrame.TextRange.Text = value

    # Shape creation.

    def add_textbox(
        self,
        text: str,
        *,
        left: float = 36,
        top: float = 36,
        width: float = 600,
        height: float = 100,
    ) -> PowerPointShape:
        """Add horizontal text; position and size are in points."""
        shape = self._track(self._com.Shapes.AddTextbox(1, left, top, width, height))
        shape.text = text
        return shape

    def add_picture(
        self,
        path: str | Path,
        *,
        left: float = 36,
        top: float = 36,
        width: float | None = None,
        height: float | None = None,
    ) -> PowerPointShape:
        """Embed an image file; omitted width/height keep the picture's own size."""
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(str(resolved))
        return self._track(
            self._com.Shapes.AddPicture(
                str(resolved),
                _MSO_FALSE,  # LinkToFile: embed, never link to a path
                _MSO_TRUE,  # SaveWithDocument
                left,
                top,
                -1 if width is None else width,
                -1 if height is None else height,
            )
        )

    def add_table(
        self,
        rows: int,
        columns: int,
        *,
        left: float = 36,
        top: float = 36,
        width: float = 600,
        height: float = 200,
        values: Sequence[Sequence[Any]] | None = None,
    ) -> PowerPointShape:
        """Add a *rows* x *columns* table, optionally filled from *values* row by row."""
        if rows < 1 or columns < 1:
            raise ValueError("a table needs at least one row and one column")
        shape = self._track(self._com.Shapes.AddTable(rows, columns, left, top, width, height))
        if values is not None:
            shape.table.set_values(values)
        return shape

    def add_chart(
        self,
        categories: Sequence[Any],
        series: Mapping[str, Sequence[float]],
        *,
        chart_type: int = _XL_COLUMN_CLUSTERED,
        left: float = 36,
        top: float = 36,
        width: float = 600,
        height: float = 300,
        title: str | None = None,
    ) -> PowerPointShape:
        """Add a chart fed from *categories* and named *series*.

        Series are written as literal arrays on the chart itself, so the
        embedded Excel workbook is never opened and Excel is not required.
        PowerPoint refuses to insert charts into a presentation that has no
        document window, so the presentation must come from
        ``create(visible=True)`` / ``open(visible=True)`` or be the operator's.
        """
        if not categories or not series:
            raise ValueError("a chart needs at least one category and one series")
        for name, values in series.items():
            if len(values) != len(categories):
                raise ValueError(
                    f"series {name!r} has {len(values)} value(s) for {len(categories)} categories"
                )
        if not self._presentation_has_window():
            raise RuntimeError(
                "PowerPoint cannot insert a chart into a presentation without a "
                "document window; create or open it with visible=True"
            )
        shape_com = self._com.Shapes.AddChart2(-1, chart_type, left, top, width, height, True)
        chart = shape_com.Chart
        # AddChart2 applies a style that may change the type (a line chart
        # comes back as line-with-markers); pin the requested type.
        chart.ChartType = chart_type
        collection = chart.SeriesCollection()
        for index in range(int(collection.Count), 0, -1):
            collection(index).Delete()  # the template's sample series
        labels = tuple(categories)
        for name, values in series.items():
            new_series = collection.NewSeries()
            new_series.Name = name
            new_series.XValues = labels
            new_series.Values = tuple(float(value) for value in values)
        shape = self._track(shape_com)
        if title is not None:
            shape.chart.title = title
        return shape

    def _presentation_has_window(self) -> bool:
        try:
            return int(self._com.Parent.Windows.Count) > 0
        except Exception:
            return False

    def delete(self) -> None:
        """Delete this slide from its presentation."""
        self._com.Delete()


class PowerPointPresentation(_ComOwner):
    """Presentation with slide access, persistence and PDF export."""

    def __init__(self, _com: Any) -> None:
        super().__init__(_com)
        self._closed = False

    @property
    def name(self) -> str:
        """Presentation file name."""
        return str(self._com.Name)

    @property
    def slide_count(self) -> int:
        """Number of slides."""
        return int(self._com.Slides.Count)

    @property
    def slide_size(self) -> tuple[float, float]:
        """Slide ``(width, height)`` in points."""
        setup = self._com.PageSetup
        return float(setup.SlideWidth), float(setup.SlideHeight)

    @property
    def has_window(self) -> bool:
        """Whether PowerPoint shows this presentation in a document window."""
        return int(self._com.Windows.Count) > 0

    def _track(self, com_slide: Any) -> PowerPointSlide:
        return self._adopt(PowerPointSlide(com_slide))

    def slide(self, index: int) -> PowerPointSlide:
        """Return a slide by 1-based index."""
        return self._track(self._com.Slides.Item(index))

    @property
    def slides(self) -> list[PowerPointSlide]:
        """All slides in presentation order."""
        return [self._track(self._com.Slides.Item(i)) for i in range(1, self.slide_count + 1)]

    def add_slide(self, *, layout: int = 12) -> PowerPointSlide:
        """Append a slide using a PpSlideLayout value (12 blank, 11 title only, 1 title)."""
        return self._track(self._com.Slides.Add(self.slide_count + 1, layout))

    def move_slide(self, index: int, new_index: int) -> None:
        """Move the slide at 1-based *index* so that it becomes *new_index*."""
        self._com.Slides.Item(index).MoveTo(new_index)

    def save(self) -> None:
        """Save changes to the existing presentation file."""
        self._com.Save()

    def save_as(self, path: str | Path) -> None:
        """Save to an absolute path; PowerPoint infers format from the extension."""
        self._com.SaveAs(str(Path(path).resolve()))

    def export_pdf(self, path: str | Path) -> None:
        """Export a PDF copy, retaining the presentation's existing file name."""
        # PrintRange is required even when exporting all slides. Pass Nothing
        # explicitly; positional arguments also work with late-bound Dispatch.
        self._com.ExportAsFixedFormat(str(Path(path).resolve()), 2, 1, 0, 1, 1, 0, None, 1)

    def close(self, *, save: bool = False) -> None:
        """Close this presentation, optionally saving; a failed save leaves it open."""
        if self._closed:
            return
        if save:
            self.save()
        self._com.Close()
        self._closed = True
        self._release_com()


class PowerPointApp:
    """Microsoft PowerPoint COM automation, leaving the shared process running.

    Context exit closes only presentations opened or created by this wrapper,
    discarding changes unless explicitly saved. A connected wrapper owns none
    of the operator's existing presentations.
    """

    def __init__(self, _com: Any, *, _session: _OfficeSession | None = None) -> None:
        self._com = _com
        self._session = _session
        self._opened: list[PowerPointPresentation] = []
        self._borrowed: list[PowerPointPresentation] = []

    @property
    def owns_process(self) -> bool:
        """Whether an exclusive session owns a process whose exit is not yet confirmed."""
        return self._session is not None and not self._session.closed

    @property
    def process_id(self) -> int | None:
        """Verified exclusive process ID, or None for shared/connected sessions."""
        return self._session.pid if self._session is not None else None

    @classmethod
    def connect(cls) -> PowerPointApp:
        """Attach to running PowerPoint or raise RuntimeError."""
        try:
            return cls(_require_win32com().GetActiveObject("PowerPoint.Application"))
        except Exception as exc:
            raise RuntimeError("No running PowerPoint instance found.") from exc

    @classmethod
    def create(cls, *, visible: bool = True, exclusive: bool = False) -> PowerPointApp:
        """Create a presentation; exclusive=True owns a newly started UAT process."""
        com, session = _start_office(
            _require_win32com(),
            "PowerPoint.Application",
            "POWERPNT.EXE",
            exclusive=exclusive,
        )
        app = cls(com, _session=session)
        try:
            presentation = app._com.Presentations.Add(-1 if visible else 0)
            app._opened.append(PowerPointPresentation(presentation))
        except BaseException as exc:
            try:
                app.quit()
            except Exception as cleanup_error:
                exc.add_note(f"Office cleanup also failed: {cleanup_error}")
            raise
        return app

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        visible: bool = True,
        allow_macros: bool = False,
        exclusive: bool = False,
    ) -> PowerPointApp:
        """Open an editable presentation; VBA is disabled unless explicitly allowed.

        AutomationSecurity is restored even if opening fails. This is not a
        sandbox for linked content; Office policies still apply. An existing
        presentation at this path is rejected to preserve operator ownership.
        """
        if not isinstance(allow_macros, bool):
            raise TypeError("allow_macros must be a bool")
        resolved = Path(path).resolve()
        com, session = _start_office(
            _require_win32com(),
            "PowerPoint.Application",
            "POWERPNT.EXE",
            exclusive=exclusive,
        )
        app = cls(com, _session=session)
        try:
            for index in range(1, int(app._com.Presentations.Count) + 1):
                existing = app._com.Presentations.Item(index)
                if Path(str(existing.FullName)).resolve() == resolved:
                    raise RuntimeError(f"Presentation is already open: {resolved}")
            previous = app._com.AutomationSecurity
            try:
                app._com.AutomationSecurity = 1 if allow_macros else 3
                presentation = app._com.Presentations.Open(
                    str(resolved),
                    0,
                    0,
                    -1 if visible else 0,
                )
                app._opened.append(PowerPointPresentation(presentation))
            finally:
                app._com.AutomationSecurity = previous
        except BaseException:
            try:
                app.quit()
            except Exception:
                pass
            raise
        return app

    @property
    def active_presentation(self) -> PowerPointPresentation:
        """Most recently opened/created presentation, or PowerPoint's active one."""
        if self._opened:
            return self._opened[-1]
        presentation = PowerPointPresentation(self._com.ActivePresentation)
        self._borrowed.append(presentation)
        return presentation

    def quit(self, *, save_changes: bool = False) -> None:
        """Close owned presentations only; retain failures for retry and raise.

        Shared/connected processes are preserved. An exclusive process is quit
        only after all owned presentations close and no foreign ones remain.
        """
        failed: list[PowerPointPresentation] = []
        errors: list[str] = []
        for presentation in self._opened:
            try:
                presentation.close(save=save_changes)
            except Exception as exc:
                failed.append(presentation)
                errors.append(str(exc))
        self._opened = failed
        if errors:
            raise RuntimeError("Could not close PowerPoint presentations: " + "; ".join(errors))
        if self._session is None:
            self._release_com()
        elif self._session.running():
            if not self._session._quit_requested and self._com.Presentations.Count:
                raise RuntimeError("Unowned presentations prevent exclusive PowerPoint shutdown")
            self._session.quit(self._com, release=self._release_com)
        else:
            self._release_com()

    def _release_com(self) -> None:
        for presentation in self._borrowed:
            presentation._release_com()
        self._borrowed.clear()
        self._com = None

    def __enter__(self) -> PowerPointApp:
        return self

    def __exit__(self, *_: Any) -> None:
        self.quit()
