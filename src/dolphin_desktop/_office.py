"""Office COM wrappers — lazy win32com imports so the module loads without Office."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _require_win32com() -> Any:
    """Import win32com.client or raise a clear RuntimeError."""
    try:
        import win32com.client  # type: ignore[import-untyped]

        return win32com.client
    except ImportError as exc:
        raise RuntimeError(
            "win32com is not available. Install pywin32: pip install pywin32  or  uv add pywin32"
        ) from exc


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------


class ExcelCell:
    """Wrapper around a single Excel cell."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def value(self) -> Any:
        """Cell value (Python type)."""
        return self._com.Value

    @value.setter
    def value(self, v: Any) -> None:
        self._com.Value = v

    @property
    def formula(self) -> str:
        """Cell formula string."""
        return self._com.Formula

    @formula.setter
    def formula(self, v: str) -> None:
        self._com.Formula = v

    @property
    def text(self) -> str:
        """Formatted display text of the cell."""
        return self._com.Text

    def set_value(self, v: Any) -> ExcelCell:
        """Set cell value and return self for chaining."""
        self._com.Value = v
        return self


class ExcelRange:
    """Wrapper around an Excel range."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def value(self) -> Any:
        """Range values (tuple of tuples for multi-cell, scalar for single)."""
        return self._com.Value

    @value.setter
    def value(self, v: Any) -> None:
        self._com.Value = v

    def as_list(self) -> list[list]:
        """Return range values as a list of lists."""
        raw = self._com.Value
        if raw is None:
            return []
        # Single cell returns a scalar; multi-cell returns tuple of tuples
        if not isinstance(raw, tuple):
            return [[raw]]
        return [list(row) for row in raw]


class ExcelSheet:
    """Wrapper around an Excel worksheet."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def name(self) -> str:
        """Sheet name."""
        return self._com.Name

    def activate(self) -> None:
        """Make this sheet the active sheet."""
        self._com.Activate()

    def cell(self, row: int, col: int) -> ExcelCell:
        """Return the cell at 1-based *row* and *col*."""
        return ExcelCell(self._com.Cells(row, col))

    def range(self, address: str) -> ExcelRange:
        """Return the range identified by *address* (e.g. ``"A1:C3"``)."""
        return ExcelRange(self._com.Range(address))


class ExcelWorkbook:
    """Wrapper around an Excel workbook."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    def sheet(self, name_or_index: str | int) -> ExcelSheet:
        """Return a worksheet by name or 1-based index."""
        return ExcelSheet(self._com.Sheets(name_or_index))

    def save(self) -> None:
        """Save the workbook."""
        self._com.Save()

    def save_as(self, path: str | Path) -> None:
        """Save the workbook to a new *path*."""
        self._com.SaveAs(str(path))

    def close(self, *, save: bool = False) -> None:
        """Close the workbook, optionally saving changes."""
        self._com.Close(SaveChanges=save)


class ExcelApp:
    """COM wrapper for Microsoft Excel."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @classmethod
    def open(cls, path: str | Path, *, visible: bool = True) -> ExcelApp:
        """Open a workbook from *path* and return an ExcelApp."""
        client = _require_win32com()
        xl = client.Dispatch("Excel.Application")
        xl.Visible = visible
        xl.Workbooks.Open(str(Path(path).resolve()))
        return cls(xl)

    @classmethod
    def connect(cls) -> ExcelApp:
        """Connect to an already-running Excel instance."""
        client = _require_win32com()
        try:
            xl = client.GetActiveObject("Excel.Application")
        except Exception as exc:
            raise RuntimeError("No running Excel instance found.") from exc
        return cls(xl)

    @property
    def active_workbook(self) -> ExcelWorkbook:
        """The currently active workbook."""
        return ExcelWorkbook(self._com.ActiveWorkbook)

    @property
    def active_sheet(self) -> ExcelSheet:
        """The currently active worksheet."""
        return ExcelSheet(self._com.ActiveSheet)

    def quit(self, *, save_changes: bool = False) -> None:
        """Quit Excel, optionally saving all open workbooks."""
        self._com.DisplayAlerts = False
        if save_changes:
            for wb in self._com.Workbooks:
                wb.Save()
        self._com.Quit()

    def __enter__(self) -> ExcelApp:
        return self

    def __exit__(self, *_: Any) -> None:
        self.quit(save_changes=False)


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------


class WordDocument:
    """Wrapper around a Word document."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def text(self) -> str:
        """Full plain-text content of the document."""
        return self._com.Content.Text

    @property
    def name(self) -> str:
        """Document file name."""
        return self._com.Name

    def save(self) -> None:
        """Save the document."""
        self._com.Save()

    def save_as(self, path: str | Path) -> None:
        """Save the document to a new *path*."""
        self._com.SaveAs2(str(path))

    def close(self, *, save: bool = False) -> None:
        """Close the document, optionally saving."""
        self._com.Close(SaveChanges=save)

    def find_replace(self, find: str, replace: str) -> None:
        """Replace all occurrences of *find* with *replace*."""
        fr = self._com.Content.Find
        fr.ClearFormatting()
        fr.Replacement.ClearFormatting()
        # Positional args required — late-binding Dispatch ignores keyword names.
        # Signature: Execute(FindText, MatchCase, MatchWholeWord, MatchWildcards,
        #   MatchSoundsLike, MatchAllWordForms, Forward, Wrap, Format,
        #   ReplaceWith, Replace, ...)
        fr.Execute(find, False, False, False, False, False, True, 1, False, replace, 2)


class WordApp:
    """COM wrapper for Microsoft Word."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @classmethod
    def open(cls, path: str | Path, *, visible: bool = True) -> WordApp:
        """Open a document from *path* and return a WordApp."""
        client = _require_win32com()
        wd = client.Dispatch("Word.Application")
        wd.Visible = visible
        wd.Documents.Open(str(Path(path).resolve()))
        return cls(wd)

    @classmethod
    def connect(cls) -> WordApp:
        """Connect to an already-running Word instance."""
        client = _require_win32com()
        try:
            wd = client.GetActiveObject("Word.Application")
        except Exception as exc:
            raise RuntimeError("No running Word instance found.") from exc
        return cls(wd)

    @property
    def active_document(self) -> WordDocument:
        """The currently active document."""
        return WordDocument(self._com.ActiveDocument)

    def quit(self, *, save_changes: bool = False) -> None:
        """Quit Word, optionally saving all open documents."""
        self._com.DisplayAlerts = False
        if save_changes:
            for doc in self._com.Documents:
                doc.Save()
        self._com.Quit()

    def __enter__(self) -> WordApp:
        return self

    def __exit__(self, *_: Any) -> None:
        self.quit(save_changes=False)
