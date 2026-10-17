"""PowerPoint COM facade with document-scoped ownership.

PowerPoint is a multi-use COM server: creating an Application can reuse the
operator's process. Never quit that process or close untracked presentations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._office import _require_win32com


class PowerPointShape:
    """A named PowerPoint shape with optional editable text."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def name(self) -> str:
        """Shape name used by :meth:`PowerPointSlide.shape`."""
        return str(self._com.Name)

    @property
    def text(self) -> str:
        """Read shape text; raise ValueError for shapes without a text frame."""
        if not self._com.HasTextFrame:
            raise ValueError(f"Shape {self.name!r} has no text frame")
        return str(self._com.TextFrame.TextRange.Text)

    @text.setter
    def text(self, value: str) -> None:
        if not self._com.HasTextFrame:
            raise ValueError(f"Shape {self.name!r} has no text frame")
        self._com.TextFrame.TextRange.Text = value


class PowerPointSlide:
    """A slide with shape lookup and text-box creation."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def index(self) -> int:
        """Current 1-based slide index."""
        return int(self._com.SlideIndex)

    @property
    def shape_count(self) -> int:
        """Number of shapes on the slide."""
        return int(self._com.Shapes.Count)

    def shape(self, name_or_index: str | int) -> PowerPointShape:
        """Return a shape by name or 1-based index."""
        return PowerPointShape(self._com.Shapes.Item(name_or_index))

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
        shape = PowerPointShape(self._com.Shapes.AddTextbox(1, left, top, width, height))
        shape.text = text
        return shape

    def delete(self) -> None:
        """Delete this slide from its presentation."""
        self._com.Delete()


class PowerPointPresentation:
    """Presentation with slide access, persistence and PDF export."""

    def __init__(self, _com: Any) -> None:
        self._com = _com
        self._closed = False

    @property
    def name(self) -> str:
        """Presentation file name."""
        return str(self._com.Name)

    @property
    def slide_count(self) -> int:
        """Number of slides."""
        return int(self._com.Slides.Count)

    def slide(self, index: int) -> PowerPointSlide:
        """Return a slide by 1-based index."""
        return PowerPointSlide(self._com.Slides.Item(index))

    def add_slide(self, *, layout: int = 12) -> PowerPointSlide:
        """Append a slide using a PpSlideLayout value (12 means blank)."""
        return PowerPointSlide(self._com.Slides.Add(self.slide_count + 1, layout))

    def save(self) -> None:
        """Save changes to the existing presentation file."""
        self._com.Save()

    def save_as(self, path: str | Path) -> None:
        """Save to an absolute path; PowerPoint infers format from the extension."""
        self._com.SaveAs(str(Path(path).resolve()))

    def export_pdf(self, path: str | Path) -> None:
        """Export a PDF copy, retaining the presentation's existing file name."""
        self._com.SaveCopyAs(str(Path(path).resolve()), 32)  # ppSaveAsPDF

    def close(self, *, save: bool = False) -> None:
        """Close this presentation, optionally saving; a failed save leaves it open."""
        if self._closed:
            return
        if save:
            self.save()
        self._com.Close()
        self._closed = True


class PowerPointApp:
    """Microsoft PowerPoint COM automation, leaving the shared process running.

    Context exit closes only presentations opened or created by this wrapper,
    discarding changes unless explicitly saved. A connected wrapper owns none
    of the operator's existing presentations.
    """

    def __init__(self, _com: Any) -> None:
        self._com = _com
        self._opened: list[PowerPointPresentation] = []

    @classmethod
    def connect(cls) -> PowerPointApp:
        """Attach to running PowerPoint or raise RuntimeError."""
        try:
            return cls(_require_win32com().GetActiveObject("PowerPoint.Application"))
        except Exception as exc:
            raise RuntimeError("No running PowerPoint instance found.") from exc

    @classmethod
    def create(cls, *, visible: bool = True) -> PowerPointApp:
        """Create a blank presentation, starting PowerPoint if necessary."""
        app = cls(_require_win32com().Dispatch("PowerPoint.Application"))
        presentation = app._com.Presentations.Add(-1 if visible else 0)
        app._opened.append(PowerPointPresentation(presentation))
        return app

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        visible: bool = True,
        allow_macros: bool = False,
    ) -> PowerPointApp:
        """Open an editable presentation; VBA is disabled unless explicitly allowed.

        AutomationSecurity is restored even if opening fails. This is not a
        sandbox for linked content; Office policies still apply. An existing
        presentation at this path is rejected to preserve operator ownership.
        """
        if not isinstance(allow_macros, bool):
            raise TypeError("allow_macros must be a bool")
        resolved = Path(path).resolve()
        app = cls(_require_win32com().Dispatch("PowerPoint.Application"))
        for index in range(1, int(app._com.Presentations.Count) + 1):
            existing = app._com.Presentations.Item(index)
            if Path(str(existing.FullName)).resolve() == resolved:
                raise RuntimeError(f"Presentation is already open: {resolved}")
        previous = app._com.AutomationSecurity
        try:
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
        return PowerPointPresentation(self._com.ActivePresentation)

    def quit(self, *, save_changes: bool = False) -> None:
        """Close owned presentations only; retain failures for retry and raise.

        The shared PowerPoint process is never quit, even when started here.
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

    def __enter__(self) -> PowerPointApp:
        return self

    def __exit__(self, *_: Any) -> None:
        self.quit()
