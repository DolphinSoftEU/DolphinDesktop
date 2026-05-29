"""Clipboard — static helpers for reading/writing the Windows clipboard."""

from __future__ import annotations


class Clipboard:
    """Static helper for Windows clipboard operations.

    Usage::

        Clipboard.set_text("hello world")
        text = Clipboard.get_text()
        img  = Clipboard.get_image()   # PIL.Image or None
        Clipboard.clear()
    """

    @staticmethod
    def get_text() -> str:
        """Read CF_UNICODETEXT from the clipboard; returns empty string if none."""
        import win32clipboard  # type: ignore[import-untyped]
        import win32con  # type: ignore[import-untyped]

        win32clipboard.OpenClipboard()
        try:
            try:
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            except Exception:
                return ""
        finally:
            win32clipboard.CloseClipboard()

    @staticmethod
    def set_text(text: str) -> None:
        """Write *text* to the clipboard as CF_UNICODETEXT."""
        import win32clipboard  # type: ignore[import-untyped]
        import win32con  # type: ignore[import-untyped]

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()

    @staticmethod
    def get_image():  # -> PIL.Image.Image | None
        """Read CF_DIB or CF_BITMAP from clipboard; returns None if not present."""
        import io

        import win32clipboard  # type: ignore[import-untyped]
        import win32con  # type: ignore[import-untyped]
        from PIL import Image, ImageGrab

        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
                data = win32clipboard.GetClipboardData(win32con.CF_DIB)
                # CF_DIB is a BITMAPINFOHEADER + pixel data — wrap in BMP file header
                bmp_header = (
                    b"BM"
                    + (len(data) + 14).to_bytes(4, "little")
                    + b"\x00\x00\x00\x00"
                    + (14 + 40).to_bytes(4, "little")
                )
                return Image.open(io.BytesIO(bmp_header + data))
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_BITMAP):
                # Fallback: use ImageGrab which handles the conversion
                return ImageGrab.grabclipboard()
            return None
        except Exception:
            return None
        finally:
            win32clipboard.CloseClipboard()

    @staticmethod
    def clear() -> None:
        """Empty the clipboard."""
        import win32clipboard  # type: ignore[import-untyped]

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
        finally:
            win32clipboard.CloseClipboard()
