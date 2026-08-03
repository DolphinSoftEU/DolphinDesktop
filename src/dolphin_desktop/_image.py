"""Image-based automation — screen capture, template matching, and OCR."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

# Optional dependency sentinels
try:
    import cv2 as _cv2  # type: ignore[import-untyped]

    _CV2_ERROR: Exception | None = None
except ImportError as _e:
    _cv2 = None  # type: ignore[assignment]
    _CV2_ERROR = _e

try:
    import pytesseract as _pytesseract  # type: ignore[import-untyped]

    _TESS_ERROR: Exception | None = None
except ImportError as _e:
    _pytesseract = None  # type: ignore[assignment]
    _TESS_ERROR = _e


# Upper bound on the local maxima ``find_all`` runs suppression over.
_MAX_CANDIDATES = 10_000


def _require_cv2():  # type: ignore[return]
    """Return cv2 or raise a clear RuntimeError."""
    if _CV2_ERROR is not None:
        raise RuntimeError(
            "opencv-python is required for image matching. "
            "Install it with:  uv add opencv-python  or  pip install opencv-python"
        ) from _CV2_ERROR
    return _cv2


def _require_tesseract():  # type: ignore[return]
    """Return pytesseract or raise a clear RuntimeError."""
    if _TESS_ERROR is not None:
        raise RuntimeError(
            "pytesseract is required for OCR features. "
            "Install it with:  uv add pytesseract  or  pip install pytesseract  "
            "(also ensure Tesseract-OCR is installed on your system: "
            "https://github.com/UB-Mannheim/tesseract/wiki)"
        ) from _TESS_ERROR
    return _pytesseract


def _grab(region: tuple[int, int, int, int] | None = None):
    """Grab a PIL screenshot of *region* (or the whole virtual desktop if None).

    ``all_screens=True`` is mandatory for a multi-monitor setup: without it
    Pillow captures the primary monitor only, so a template on a secondary
    display is never found and *region* is cropped against the wrong
    framebuffer instead of against absolute screen coordinates.
    """
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=region, all_screens=True)


def _virtual_origin() -> tuple[int, int]:
    """Return the screen coordinate of the virtual desktop's top-left pixel."""
    try:
        import win32api  # type: ignore[import-untyped]
        import win32con  # type: ignore[import-untyped]

        return (
            win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN),
            win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN),
        )
    except Exception:
        return (0, 0)


def _grab_origin(region: tuple[int, int, int, int] | None) -> tuple[int, int]:
    """Return the screen coordinate that pixel (0, 0) of ``_grab(region)`` has.

    Pillow's win32 grab crops an explicit *bbox* against the virtual-screen
    origin, so a region capture is already in absolute coordinates.  With
    ``bbox=None`` it hands back the whole virtual desktop instead, whose first
    pixel is ``(SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN)`` — non-zero as soon as a
    monitor sits left of or above the primary one.
    """
    return (region[0], region[1]) if region else _virtual_origin()


def _pil_to_cv(pil_img):  # type: ignore[return]
    """Convert a PIL Image to a cv2/numpy BGR image."""
    import numpy as np  # type: ignore[import-untyped]

    rgb = pil_img.convert("RGB")
    arr = np.array(rgb)
    cv2 = _require_cv2()
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def _is_low_variance_image(img: Any) -> bool:
    """Return True for effectively solid-colour images.

    OpenCV's TM_CCOEFF_NORMED is undefined for zero-variance inputs and can
    report false positives for solid templates. Use a different method there.
    """
    import numpy as np  # type: ignore[import-untyped]

    return bool(np.std(img) < 1e-6)


# _ImageRect / _ImageElement — pywinauto-compatible proxy for image matches


class _ImageRect:
    """Rect-like object returned by _ImageElement.rectangle()."""

    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _ImageElement:
    """Minimal pywinauto-compatible element backed by a screen coordinate.

    Returned by the image failover in ``Locator._resolve()`` when all UIA/Win32
    selectors fail but an ``image_fallback`` ImageLocator finds the element on-screen.
    Supports the subset of the pywinauto wrapper interface that ``Locator`` uses.
    """

    def __init__(self, cx: int, cy: int, tw: int = 1, th: int = 1) -> None:
        self._cx = cx
        self._cy = cy
        self._tw = max(1, tw)
        self._th = max(1, th)
        self._focused = False

    # --- mouse ---

    def click_input(self) -> None:
        import pywinauto.mouse as _m  # type: ignore[import-untyped]

        _m.click(coords=(self._cx, self._cy))

    def double_click_input(self) -> None:
        import pywinauto.mouse as _m  # type: ignore[import-untyped]

        _m.double_click(coords=(self._cx, self._cy))

    def right_click_input(self) -> None:
        import pywinauto.mouse as _m  # type: ignore[import-untyped]

        _m.right_click(coords=(self._cx, self._cy))

    def move_mouse_input(self, coords: tuple[int, int] | None = None) -> None:
        import pywinauto.mouse as _m  # type: ignore[import-untyped]

        _m.move(coords=coords or (self._cx, self._cy))

    # --- keyboard ---

    def type_keys(self, text: str, **kwargs: Any) -> None:
        """Type a pywinauto key sequence at the matched coordinate.

        Mirrors ``HwndWrapper.type_keys``: *text* is a key sequence, not
        literal text, and callers escape literal text themselves. The
        ``with_spaces`` / ``pause`` kwargs ``Locator.type_text`` passes must
        reach ``send_keys`` — dropping ``with_spaces`` swallows every space.

        The focusing click is skipped once :meth:`set_focus` has run: the
        select-all that ``Locator.set_text`` sends between the two would be
        collapsed by a second click, inserting the new text instead of
        replacing it.
        """
        from pywinauto.keyboard import send_keys  # type: ignore[import-untyped]

        accepted = ("pause", "with_spaces", "with_tabs", "with_newlines", "vk_packet")
        if not self._focused:
            self.set_focus()
        send_keys(text, **{k: v for k, v in kwargs.items() if k in accepted})

    def set_edit_text(self, text: str) -> None:
        raise NotImplementedError("Image-matched elements do not support set_edit_text")

    # --- text / value ---

    def window_text(self) -> str:
        return ""

    def get_value(self) -> str:
        raise AttributeError("Image-matched elements do not support get_value")

    # --- focus / state ---

    def set_focus(self) -> None:
        import pywinauto.mouse as _m  # type: ignore[import-untyped]

        _m.click(coords=(self._cx, self._cy))
        self._focused = True

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True

    def scroll_into_view(self) -> None:
        pass

    # --- geometry ---

    def rectangle(self) -> _ImageRect:
        left = self._cx - self._tw // 2
        top = self._cy - self._th // 2
        return _ImageRect(left, top, left + self._tw, top + self._th)

    def capture_as_image(self):  # type: ignore[return]
        """Capture the element region as a PIL Image."""
        rect = self.rectangle()
        return _grab((rect.left, rect.top, rect.right, rect.bottom))

    # --- pywinauto wait protocol ---

    def wait(self, state: str = "visible", timeout: float = 0.0) -> _ImageElement:
        return self


# ImageLocator


class ImageLocator:
    """Locate a template image on screen using OpenCV template matching.

    Parameters
    ----------
    template:
        Path to the template PNG/BMP/JPG image.
    threshold:
        Minimum match confidence (0-1, default 0.85).
    scales:
        List of scale factors to try during multi-scale matching
        (e.g. ``[0.8, 1.0, 1.2]``).  When ``None`` only scale 1.0 is used.
    region:
        Default bounding box ``(left, top, right, bottom)`` in screen
        coordinates to restrict the search area.  Each method's own *region*
        parameter overrides this default.
    """

    def __init__(
        self,
        template: str | Path,
        threshold: float = 0.85,
        *,
        scales: list[float] | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._template_path = Path(template)
        self._threshold = threshold
        self._scales: list[float] = scales if scales is not None else [1.0]
        self._region = region

    def _load_template(self):  # type: ignore[return]
        """Load the template image as a cv2 array.

        Decoded from bytes rather than via ``cv2.imread``, whose ANSI ``fopen``
        cannot open a path containing non-ASCII characters on Windows and
        returns ``None`` as if the file were missing.
        """
        # cv2 first: numpy also arrives with the ``vision`` extra, so on a
        # base install importing it ahead of the guard replaces the message
        # naming the extra with a bare ModuleNotFoundError.
        cv2 = _require_cv2()

        import numpy as np  # type: ignore[import-untyped]

        if not self._template_path.is_file():
            raise FileNotFoundError(f"Template image not found: {self._template_path}")
        tmpl = cv2.imdecode(np.fromfile(str(self._template_path), np.uint8), cv2.IMREAD_COLOR)
        if tmpl is None:
            raise ValueError(f"Template image could not be decoded: {self._template_path}")
        return tmpl

    def _match(
        self,
        screen,
        tmpl,
        scale: float,
    ) -> tuple[tuple[int, int] | None, int, int, float]:
        """Try matching *tmpl* at *scale* against *screen*.

        Returns ``(match_loc, tw, th, score)`` or ``(None, tw, th, score)``.
        *match_loc* is the top-left corner of the match in the (possibly cropped) screen.
        """
        cv2 = _require_cv2()
        if scale != 1.0:
            h, w = tmpl.shape[:2]
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            tmpl_s = cv2.resize(tmpl, (new_w, new_h))
        else:
            tmpl_s = tmpl

        th, tw = tmpl_s.shape[:2]
        if th > screen.shape[0] or tw > screen.shape[1]:
            return None, tw, th, 0.0

        if _is_low_variance_image(screen) or _is_low_variance_image(tmpl_s):
            result = cv2.matchTemplate(screen, tmpl_s, cv2.TM_SQDIFF_NORMED)
            min_val, _, min_loc, _ = cv2.minMaxLoc(result)
            score = 1.0 - min_val
            if score < self._threshold:
                return None, tw, th, score
            return min_loc, tw, th, score

        result = cv2.matchTemplate(screen, tmpl_s, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val < self._threshold:
            return None, tw, th, max_val
        return max_loc, tw, th, max_val

    def _find_impl(
        self,
        effective_region: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, int, int] | None:
        """Return ``(cx, cy, tw, th)`` of the best match or None."""
        tmpl = self._load_template()
        screen = _pil_to_cv(_grab(effective_region))
        rx, ry = _grab_origin(effective_region)

        best_val = -1.0
        best: tuple[int, int, int, int] | None = None

        for scale in self._scales:
            loc, tw, th, val = self._match(screen, tmpl, scale)
            if loc is not None and val > best_val:
                best_val = val
                cx = loc[0] + tw // 2 + rx
                cy = loc[1] + th // 2 + ry
                best = (cx, cy, tw, th)

        return best

    def find(
        self,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int] | None:
        """Return ``(cx, cy)`` of the best template match, or None."""
        effective = region if region is not None else self._region
        result = self._find_impl(effective)
        return (result[0], result[1]) if result is not None else None

    def find_with_size(
        self,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int, int, int] | None:
        """Return ``(cx, cy, template_w, template_h)`` or None.

        Used internally by the failover chain so that ``_ImageElement``
        has the correct bounding box.
        """
        effective = region if region is not None else self._region
        return self._find_impl(effective)

    def find_all(
        self,
        region: tuple[int, int, int, int] | None = None,
    ) -> list[tuple[int, int]]:
        """Return the centre of each distinct match above threshold.

        Overlapping matches are suppressed, so one on-screen occurrence yields
        exactly one centre and ``len(find_all())`` counts occurrences.
        """
        cv2 = _require_cv2()
        import numpy as np  # type: ignore[import-untyped]

        effective = region if region is not None else self._region
        tmpl = self._load_template()
        screen = _pil_to_cv(_grab(effective))
        rx, ry = _grab_origin(effective)

        points: list[tuple[int, int]] = []

        # For find_all use only scale 1.0 or the first scale (multi-scale deduplication
        # across scales is complex and rarely needed for enumeration).
        scale = self._scales[0]
        if scale != 1.0:
            h, w = tmpl.shape[:2]
            tmpl = cv2.resize(tmpl, (max(1, int(w * scale)), max(1, int(h * scale))))

        th, tw = tmpl.shape[:2]
        if th > screen.shape[0] or tw > screen.shape[1]:
            return points

        if _is_low_variance_image(screen) or _is_low_variance_image(tmpl):
            scores = 1.0 - cv2.matchTemplate(screen, tmpl, cv2.TM_SQDIFF_NORMED)
        else:
            scores = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)

        # Keep only template-sized local maxima, then drop any survivor whose
        # box still overlaps a stronger one — every pixel offset around a
        # single on-screen occurrence otherwise clears the threshold and would
        # be reported as its own match.
        peaks = cv2.dilate(scores, np.ones((th, tw), np.uint8))
        ys, xs = np.where((scores >= self._threshold) & (scores >= peaks))
        vals = scores[ys, xs]

        # A score surface that plateaus (a flat background under a low-variance
        # template) makes every pixel a local maximum, so the suppression below
        # would run over millions of candidates. Only the strongest can survive
        # it anyway.
        if vals.size > _MAX_CANDIDATES:
            top = np.argpartition(-vals, _MAX_CANDIDATES)[:_MAX_CANDIDATES]
            xs, ys, vals = xs[top], ys[top], vals[top]

        order = np.argsort(-vals)
        xs, ys = xs[order], ys[order]

        kept_x = np.empty(xs.size, dtype=np.int64)
        kept_y = np.empty(ys.size, dtype=np.int64)
        n = 0
        for x, y in zip(xs.tolist(), ys.tolist(), strict=False):
            if n and bool(np.any((np.abs(kept_x[:n] - x) < tw) & (np.abs(kept_y[:n] - y) < th))):
                continue
            kept_x[n] = x
            kept_y[n] = y
            n += 1
            points.append((x + tw // 2 + rx, y + th // 2 + ry))
        return points

    def click(self, region: tuple[int, int, int, int] | None = None) -> None:
        """Click the template match centre."""
        import pywinauto.mouse as _mouse  # type: ignore[import-untyped]

        pt = self.find(region)
        if pt is None:
            raise RuntimeError(
                f"Template {self._template_path} not found on screen (threshold={self._threshold})"
            )
        _mouse.click(coords=pt)

    def double_click(self, region: tuple[int, int, int, int] | None = None) -> None:
        """Double-click the template match centre."""
        import pywinauto.mouse as _mouse  # type: ignore[import-untyped]

        pt = self.find(region)
        if pt is None:
            raise RuntimeError(
                f"Template {self._template_path} not found on screen (threshold={self._threshold})"
            )
        _mouse.double_click(coords=pt)

    def wait_for(
        self,
        timeout: float = 10.0,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int]:
        """Poll every 0.5 s until the template appears; raise on timeout.

        The template is always searched for at least once, so ``timeout=0``
        means "look now" rather than "never look".

        Raises :class:`~dolphin_desktop.WaitTimeoutError` — same base
        exception every other ``wait_for_*`` in dolphin uses, so tests
        can catch it uniformly regardless of the backend that produced
        the timeout.
        """
        from ._exceptions import WaitTimeoutError as _WaitTimeoutError

        deadline = time.monotonic() + timeout
        while True:
            pt = self.find(region)
            if pt is not None:
                return pt
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        raise _WaitTimeoutError(
            f"Template {self._template_path} not found within {timeout}s",
            hint=(
                "verify the template file exists, the threshold is not too "
                "high (default 0.85), and DPI scaling matches the reference "
                "screenshot"
            ),
        )

    def exists(
        self,
        timeout: float = 0.0,
        region: tuple[int, int, int, int] | None = None,
    ) -> bool:
        """Return True if the template is found within *timeout* seconds."""
        from ._exceptions import WaitTimeoutError as _WaitTimeoutError

        if timeout <= 0:
            return self.find(region) is not None
        try:
            self.wait_for(timeout=timeout, region=region)
            return True
        except _WaitTimeoutError:
            return False

    def as_element(
        self,
        region: tuple[int, int, int, int] | None = None,
    ) -> _ImageElement | None:
        """Find the template and return an _ImageElement proxy, or None."""
        result = self.find_with_size(region)
        if result is None:
            return None
        cx, cy, tw, th = result
        return _ImageElement(cx, cy, tw, th)


class Screen:
    """Static helpers for full-screen capture, colour sampling, and OCR."""

    @staticmethod
    def screenshot(region: tuple[int, int, int, int] | None = None):
        """Return a PIL Image of the screen or *region*."""
        return _grab(region)

    @staticmethod
    def pixel_color(x: int, y: int) -> tuple[int, int, int]:
        """Return the (R, G, B) colour at screen coordinate (*x*, *y*)."""
        img = _grab((x, y, x + 1, y + 1))
        return img.getpixel((0, 0))[:3]  # type: ignore[return-value]

    @staticmethod
    def find_image(
        template: str | Path,
        threshold: float = 0.85,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int] | None:
        """Convenience wrapper: create an ImageLocator and call find()."""
        return ImageLocator(template, threshold).find(region)

    @staticmethod
    def text(region: tuple[int, int, int, int] | None = None) -> str:
        """Return OCR text of the screen or *region* via pytesseract."""
        tess = _require_tesseract()
        img = _grab(region)
        return tess.image_to_string(img)

    @staticmethod
    def find_text(
        text: str,
        region: tuple[int, int, int, int] | None = None,
        *,
        scales: tuple[int, ...] = (1, 2, 3),
    ) -> tuple[int, int] | None:
        """Return the centre (x, y) of the first bounding box containing *text*, or None.

        Small UI fonts (combo-list rows, grid cells) sit below Tesseract's
        reliable glyph size at 96 DPI, so on a miss the capture is retried
        upscaled — each entry in *scales* in order, coordinates mapped back
        to screen space. Pass ``scales=(1,)`` to disable the retries.
        """
        tess = _require_tesseract()
        img = _grab(region)
        ox, oy = _grab_origin(region)

        for scale in scales:
            if scale == 1:
                scaled = img
            else:
                from PIL.Image import Resampling

                scaled = img.resize(
                    (img.width * scale, img.height * scale),
                    Resampling.LANCZOS,
                )
            data = tess.image_to_data(scaled, output_type=tess.Output.DICT)
            for i, word in enumerate(data["text"]):
                if text.lower() in str(word).lower():
                    cx = (data["left"][i] + data["width"][i] // 2) // scale
                    cy = (data["top"][i] + data["height"][i] // 2) // scale
                    return cx + ox, cy + oy
        return None
