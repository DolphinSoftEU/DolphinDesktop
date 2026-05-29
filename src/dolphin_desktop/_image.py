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
    """Grab a PIL screenshot of *region* (or full screen if None)."""
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=region)


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


# ---------------------------------------------------------------------------
# _ImageRect / _ImageElement — pywinauto-compatible proxy for image matches
# ---------------------------------------------------------------------------


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

    def type_keys(self, text: str, **_kw: Any) -> None:
        import pywinauto.mouse as _m  # type: ignore[import-untyped]
        from pywinauto.keyboard import send_keys  # type: ignore[import-untyped]

        _m.click(coords=(self._cx, self._cy))
        send_keys(text)

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


# ---------------------------------------------------------------------------
# ImageLocator
# ---------------------------------------------------------------------------


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
        """Load the template image as a cv2 array."""
        cv2 = _require_cv2()
        tmpl = cv2.imread(str(self._template_path))
        if tmpl is None:
            raise FileNotFoundError(f"Template image not found: {self._template_path}")
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
        rx, ry = (effective_region[0], effective_region[1]) if effective_region else (0, 0)

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
        """Return all match centres above threshold."""
        cv2 = _require_cv2()
        import numpy as np  # type: ignore[import-untyped]

        effective = region if region is not None else self._region
        tmpl = self._load_template()
        screen = _pil_to_cv(_grab(effective))
        rx, ry = (effective[0], effective[1]) if effective else (0, 0)

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
            result = cv2.matchTemplate(screen, tmpl, cv2.TM_SQDIFF_NORMED)
            locations = np.where((1.0 - result) >= self._threshold)
        else:
            result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
            locations = np.where(result >= self._threshold)
        for pt in zip(locations[1], locations[0], strict=False):
            cx = pt[0] + tw // 2 + rx
            cy = pt[1] + th // 2 + ry
            points.append((cx, cy))
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
        """Poll every 0.5 s until the template appears; raise on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pt = self.find(region)
            if pt is not None:
                return pt
            time.sleep(0.5)
        raise RuntimeError(f"Template {self._template_path} not found within {timeout}s")

    def exists(
        self,
        timeout: float = 0.0,
        region: tuple[int, int, int, int] | None = None,
    ) -> bool:
        """Return True if the template is found within *timeout* seconds."""
        if timeout <= 0:
            return self.find(region) is not None
        try:
            self.wait_for(timeout=timeout, region=region)
            return True
        except RuntimeError:
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
    ) -> tuple[int, int] | None:
        """Return the centre (x, y) of the first bounding box containing *text*, or None."""
        tess = _require_tesseract()
        img = _grab(region)
        data = tess.image_to_data(img, output_type=tess.Output.DICT)

        for i, word in enumerate(data["text"]):
            if text.lower() in str(word).lower():
                x = data["left"][i]
                y = data["top"][i]
                w = data["width"][i]
                h = data["height"][i]
                cx = x + w // 2
                cy = y + h // 2
                if region:
                    cx += region[0]
                    cy += region[1]
                return cx, cy
        return None
