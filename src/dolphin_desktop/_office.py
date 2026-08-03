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


def _com_name(doc: Any) -> str:
    """Name of a workbook / document, for error messages."""
    try:
        return str(doc.Name)
    except Exception:
        return "<unknown>"


# Excel


class ExcelCell:
    """Wrapper around a single Excel cell."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def value(self) -> Any:
        return self._com.Value

    @value.setter
    def value(self, v: Any) -> None:
        self._com.Value = v

    @property
    def formula(self) -> str:
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
        return self._com.Name

    def activate(self) -> None:
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
        self._com.Save()

    def save_as(self, path: str | Path) -> None:
        """Save the workbook to a new *path*.

        Relative paths resolve against the process cwd — Excel would
        otherwise resolve them against its own ``CurDir``.
        """
        self._com.SaveAs(str(Path(path).resolve()))

    def close(self, *, save: bool = False) -> None:
        """Close the workbook, optionally saving changes."""
        # Positional args required — late-binding Dispatch ignores keyword names.
        # Signature: Close(SaveChanges, Filename, RouteWorkbook)
        self._com.Close(save)


class ExcelApp:
    """COM wrapper for Microsoft Excel."""

    def __init__(self, _com: Any, *, owned: bool = False) -> None:
        self._com = _com
        # True only for an instance this wrapper created — see open().
        self._owned = owned
        self._opened: list[Any] = []

    @classmethod
    def open(cls, path: str | Path, *, visible: bool = True) -> ExcelApp:
        """Open a workbook from *path* in a private Excel instance."""
        client = _require_win32com()
        # DispatchEx forces a fresh out-of-process server. A plain Dispatch
        # binds to the operator's already-running Excel through the running
        # object table, and quit() would then take their unsaved workbooks
        # down with it.
        xl = client.DispatchEx("Excel.Application")
        app = cls(xl, owned=True)
        try:
            xl.Visible = visible
            app._opened.append(xl.Workbooks.Open(str(Path(path).resolve())))
        except BaseException:
            # Nothing outside this call holds the private instance, so a
            # failing Open (missing, locked, corrupt, password-prompted)
            # would strand one visible excel.exe per call — a visible
            # Office instance with no document never self-reaps.
            try:
                app.quit()
            except Exception:
                pass
            raise
        return app

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
        return ExcelWorkbook(self._com.ActiveWorkbook)

    @property
    def active_sheet(self) -> ExcelSheet:
        return ExcelSheet(self._com.ActiveSheet)

    def quit(self, *, save_changes: bool = False) -> None:
        """Close the workbooks this wrapper opened and quit its own instance.

        Only workbooks opened through :meth:`open` are closed, and the
        Excel process is quit only when this wrapper started it. An
        instance reached through :meth:`connect` belongs to the operator
        and may hold unsaved work, so it is left running.

        A workbook whose ``Save()`` failed is left open and the instance
        is left running: closing or quitting would discard exactly the
        content the failed save was meant to keep. Calling ``quit()``
        again retries it. Idempotent otherwise.
        """
        unsaved: list[str] = []
        if self._opened:
            # Suppressed before the saves, not after them: Save() on a
            # read-only or never-saved workbook raises a modal Save-As
            # dialog that blocks until someone clicks it.
            self._com.DisplayAlerts = False
        kept: list[Any] = []
        for wb in self._opened:
            if save_changes:
                try:
                    wb.Save()
                except Exception as exc:
                    unsaved.append(f"{_com_name(wb)}: {exc}")
                    kept.append(wb)
                    continue
            try:
                wb.Close(False)
            except Exception:
                continue
        self._opened = kept
        if self._owned and not kept:
            self._com.DisplayAlerts = False
            self._com.Quit()
            # Quit() disconnects the proxy — a second call raises com_error.
            self._owned = False
        if unsaved:
            raise RuntimeError("quit(save_changes=True) could not save: " + "; ".join(unsaved))

    def __enter__(self) -> ExcelApp:
        return self

    def __exit__(self, *_: Any) -> None:
        self.quit(save_changes=False)


# Word

# wdSaveOptions
_WD_SAVE_CHANGES = -1
_WD_DO_NOT_SAVE_CHANGES = 0


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
        self._com.Save()

    def save_as(self, path: str | Path) -> None:
        """Save the document to a new *path*.

        Relative paths resolve against the process cwd — Word would
        otherwise resolve them against its own ``CurDir``.
        """
        self._com.SaveAs2(str(Path(path).resolve()))

    def close(self, *, save: bool = False) -> None:
        """Close the document, optionally saving."""
        # Positional args required — late-binding Dispatch ignores keyword names.
        # Signature: Close(SaveChanges, OriginalFormat, RouteDocument);
        # SaveChanges is wdSaveOptions: -1 wdSaveChanges, 0 wdDoNotSaveChanges.
        self._com.Close(_WD_SAVE_CHANGES if save else _WD_DO_NOT_SAVE_CHANGES)

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

    def __init__(self, _com: Any, *, owned: bool = False) -> None:
        self._com = _com
        # True only for an instance this wrapper created — see open().
        self._owned = owned
        self._opened: list[Any] = []

    @classmethod
    def open(cls, path: str | Path, *, visible: bool = True) -> WordApp:
        """Open a document from *path* in a private Word instance."""
        client = _require_win32com()
        # DispatchEx forces a fresh out-of-process server. A plain Dispatch
        # binds to the operator's already-running Word through the running
        # object table, and quit() would then take their unsaved documents
        # down with it.
        wd = client.DispatchEx("Word.Application")
        app = cls(wd, owned=True)
        try:
            wd.Visible = visible
            app._opened.append(wd.Documents.Open(str(Path(path).resolve())))
        except BaseException:
            # Nothing outside this call holds the private instance, so a
            # failing Open (missing, locked, corrupt, password-prompted)
            # would strand one visible winword.exe per call — a visible
            # Office instance with no document never self-reaps.
            try:
                app.quit()
            except Exception:
                pass
            raise
        return app

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
        return WordDocument(self._com.ActiveDocument)

    def quit(self, *, save_changes: bool = False) -> None:
        """Close the documents this wrapper opened and quit its own instance.

        Only documents opened through :meth:`open` are closed, and the
        Word process is quit only when this wrapper started it. An
        instance reached through :meth:`connect` belongs to the operator
        and may hold unsaved work, so it is left running.

        A document whose ``Save()`` failed is left open and the instance
        is left running: closing or quitting would discard exactly the
        content the failed save was meant to keep. Calling ``quit()``
        again retries it. Idempotent otherwise.
        """
        unsaved: list[str] = []
        if self._opened:
            # Suppressed before the saves, not after them: Save() on a
            # read-only or never-saved document raises a modal Save-As
            # dialog that blocks until someone clicks it.
            self._com.DisplayAlerts = False
        kept: list[Any] = []
        for doc in self._opened:
            if save_changes:
                try:
                    doc.Save()
                except Exception as exc:
                    unsaved.append(f"{_com_name(doc)}: {exc}")
                    kept.append(doc)
                    continue
            try:
                doc.Close(_WD_DO_NOT_SAVE_CHANGES)
            except Exception:
                continue
        self._opened = kept
        if self._owned and not kept:
            self._com.DisplayAlerts = False
            self._com.Quit()
            # Quit() disconnects the proxy — a second call raises com_error.
            self._owned = False
        if unsaved:
            raise RuntimeError("quit(save_changes=True) could not save: " + "; ".join(unsaved))

    def __enter__(self) -> WordApp:
        return self

    def __exit__(self, *_: Any) -> None:
        self.quit(save_changes=False)
