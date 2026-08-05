"""Clipboard — static helpers for reading/writing the Windows clipboard."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# Only one process may hold the clipboard at a time; the AUT finishing its own
# ^c write, clipboard history, and sync tools all take it briefly.
_OPEN_TIMEOUT = 2.0
_OPEN_RETRY_MAX = 0.1


@contextmanager
def _opened(timeout: float = _OPEN_TIMEOUT) -> Iterator[Any]:
    """Open the clipboard with retry/backoff and guarantee it is closed."""
    import win32clipboard  # type: ignore[import-untyped]

    from ._exceptions import WaitTimeoutError

    deadline = time.monotonic() + timeout
    delay = 0.01
    while True:
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception as exc:
            if time.monotonic() >= deadline:
                raise WaitTimeoutError(
                    f"Could not open the Windows clipboard within {timeout}s: {exc}",
                    hint=(
                        "another process is holding the clipboard — allow the "
                        "application to finish its copy, or close clipboard "
                        "history / sync tools (OneDrive, TeamViewer)"
                    ),
                ) from exc
            time.sleep(delay)
            delay = min(delay * 2, _OPEN_RETRY_MAX)
    try:
        yield win32clipboard
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def _dib_pixel_offset(data: bytes) -> int:
    """Return the offset of the pixel array within a CF_DIB blob.

    Pixels do not always start right after a 40-byte ``BITMAPINFOHEADER``:
    ``BI_BITFIELDS`` adds colour masks, ``<= 8bpp`` DIBs carry a palette, and
    V4/V5 headers are larger. A hardcoded offset shifts or corrupts the image.
    """
    header_size = int.from_bytes(data[0:4], "little")

    if header_size == 12:  # BITMAPCOREHEADER — RGBTRIPLE palette entries
        bit_count = int.from_bytes(data[10:12], "little")
        colors = 1 << bit_count if 0 < bit_count <= 8 else 0
        return header_size + colors * 3

    bit_count = int.from_bytes(data[14:16], "little")
    compression = int.from_bytes(data[16:20], "little")
    clr_used = int.from_bytes(data[32:36], "little")

    colors = clr_used or (1 << bit_count if 0 < bit_count <= 8 else 0)
    masks = 0
    if header_size <= 40:
        if compression == 3:  # BI_BITFIELDS
            masks = 12
        elif compression == 6:  # BI_ALPHABITFIELDS
            masks = 16
    return header_size + masks + colors * 4


class Clipboard:
    """Static helper for Windows clipboard operations.

    Usage::

        Clipboard.set_text("hello world")
        text = Clipboard.get_text()
        img  = Clipboard.get_image()   # PIL.Image or None
        Clipboard.clear()

    Every method retries opening the clipboard for up to two seconds and
    raises :class:`~dolphin_desktop.WaitTimeoutError` if another process
    never releases it.
    """

    @staticmethod
    def get_text() -> str:
        """Read CF_UNICODETEXT from the clipboard; returns empty string if none."""
        import win32con  # type: ignore[import-untyped]

        with _opened() as clip:
            try:
                return clip.GetClipboardData(win32con.CF_UNICODETEXT)
            except Exception:
                return ""

    @staticmethod
    def set_text(text: str) -> None:
        """Write *text* to the clipboard as CF_UNICODETEXT.

        ``EmptyClipboard`` is how a process takes clipboard ownership, so it
        cannot be deferred until after the write; the previous text is captured
        first and put back if the write fails, so a failure leaves the user's
        clipboard as it was instead of wiping it.
        """
        import win32con  # type: ignore[import-untyped]

        if not isinstance(text, str):
            raise TypeError(f"Clipboard.set_text() expects str, got {type(text).__name__}")

        with _opened() as clip:
            try:
                previous = clip.GetClipboardData(win32con.CF_UNICODETEXT)
            except Exception:
                previous = None
            clip.EmptyClipboard()
            try:
                clip.SetClipboardData(win32con.CF_UNICODETEXT, text)
            except Exception:
                if previous is not None:
                    try:
                        clip.SetClipboardData(win32con.CF_UNICODETEXT, previous)
                    except Exception:
                        pass
                raise

    @staticmethod
    def get_image():  # -> PIL.Image.Image | None
        """Read CF_DIB or CF_BITMAP from clipboard; returns None if not present."""
        import io

        import win32con  # type: ignore[import-untyped]
        from PIL import Image

        data: bytes | None = None
        with _opened() as clip:
            try:
                if clip.IsClipboardFormatAvailable(win32con.CF_DIB):
                    data = clip.GetClipboardData(win32con.CF_DIB)
                elif not clip.IsClipboardFormatAvailable(win32con.CF_BITMAP):
                    return None
            except Exception:
                return None

        if data is None:
            # Pillow opens and closes the clipboard itself, so it must run after
            # our own handle is released — nesting the two makes our ``finally``
            # close a clipboard Pillow has already closed.
            from PIL import ImageGrab

            try:
                return ImageGrab.grabclipboard()
            except Exception:
                return None

        try:
            # CF_DIB is a BITMAPINFOHEADER + pixel data — wrap in BMP file header
            bmp_header = (
                b"BM"
                + (len(data) + 14).to_bytes(4, "little")
                + b"\x00\x00\x00\x00"
                + (14 + _dib_pixel_offset(data)).to_bytes(4, "little")
            )
            return Image.open(io.BytesIO(bmp_header + data))
        except Exception:
            return None

    @staticmethod
    def clear() -> None:
        with _opened() as clip:
            clip.EmptyClipboard()
