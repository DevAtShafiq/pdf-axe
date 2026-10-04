# -*- coding: utf-8 -*-
"""
QR Screen Capture — ported from D:\\business docs\\Business Automation\\appostille_checker\\qr_screen_opener.py.

Provides three things to the rest of the app:

1. ``QRDecoder`` — robust QR decoder using pyzbar with multiple PIL-based
   preprocessing variants (grayscale, auto-contrast, sharpen, scale-up).

2. Screen-capture helpers — ``get_virtual_screen_rect``, ``grab_region_mss``,
   ``clip_region``, plus a small preview helper.

3. Two overlay UIs the host app can launch:
   - ``OverlaySelector``: fullscreen translucent overlay that lets the user
     drag a rectangle around a QR code and receive its (left, top, w, h).
   - ``OneClickOverlay``: fullscreen overlay that captures a single click;
     the host then captures multiple crop sizes around that click and runs
     the decoder on each.

High-level helpers — ``run_drag_select(parent, on_decoded)`` and
``run_pick_mode(parent, on_decoded)`` — wrap the above so callers don't
have to assemble the screen-capture and decode steps themselves. They
fall back gracefully on platforms / Python builds without ``mss`` or
``pyzbar``: in that case ``run_*`` returns False and the host can show an
"Install dependencies" hint.

Dependencies (Windows / cross-platform):
    pip install mss Pillow pyzbar
"""

from __future__ import annotations

import ctypes
import re
import sys
import webbrowser
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import tkinter as tk

# --- Optional / platform deps --------------------------------------------------

try:
    import mss  # type: ignore
    _MSS_OK = True
except Exception:
    mss = None  # type: ignore
    _MSS_OK = False

try:
    from PIL import Image, ImageFilter, ImageOps  # type: ignore
    _PIL_OK = True
except Exception:
    Image = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageOps = None  # type: ignore
    _PIL_OK = False

try:
    from pyzbar import pyzbar  # type: ignore
    _PYZBAR_OK = True
except Exception:
    pyzbar = None  # type: ignore
    _PYZBAR_OK = False

# cv2 is no longer used — kept as False sentinel so old callers don't break.
_CV2_OK = False


def backend_status() -> dict[str, bool]:
    return {
        "mss": _MSS_OK,
        "cv2": _CV2_OK,
        "PIL": _PIL_OK,
        "pyzbar": _PYZBAR_OK,
    }


def is_available() -> bool:
    """Return True iff the minimum stack (mss + PIL + pyzbar) is present."""
    return _MSS_OK and _PIL_OK and _PYZBAR_OK


# --- URL helpers ---------------------------------------------------------------

_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def looks_like_url(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 8:
        return False
    return bool(_URL_RE.match(t))


def normalize_url(text: str) -> str:
    return (text or "").strip()


# --- Virtual screen geometry (Windows) ----------------------------------------

def get_virtual_screen_rect() -> Tuple[int, int, int, int]:
    """Return (left, top, width, height) for the full virtual desktop.

    On Windows uses GetSystemMetrics; elsewhere returns a 1920x1080 default.
    """
    if sys.platform == "win32":
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        SM_CXVIRTUALSCREEN = 78
        SM_CYVIRTUALSCREEN = 79
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            return (
                int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
                int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
                int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
                int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
            )
        except Exception:
            pass
    return 0, 0, 1920, 1080


def clip_region(
    cx: int, cy: int, half_w: int, half_h: int,
    vleft: int, vtop: int, vw: int, vh: int,
) -> Tuple[int, int, int, int]:
    left = max(vleft, cx - half_w)
    top = max(vtop, cy - half_h)
    right = min(vleft + vw, cx + half_w)
    bottom = min(vtop + vh, cy + half_h)
    return left, top, max(1, right - left), max(1, bottom - top)


# --- Screen capture ------------------------------------------------------------

def grab_region_mss(left: int, top: int, width: int, height: int) -> "Image.Image":
    """Capture a screen region and return it as a PIL RGB Image.

    Previously returned a BGR numpy array (required opencv-python + numpy).
    Now returns a PIL Image directly — no numpy or cv2 required.
    """
    if not (_MSS_OK and _PIL_OK):
        raise RuntimeError("mss and Pillow are required for screen capture.")
    with mss.mss() as sct:  # type: ignore[union-attr]
        bbox = {"left": left, "top": top, "width": width, "height": height}
        shot = sct.grab(bbox)
        # mss gives BGRA raw bytes; .rgb gives clean RGB bytes directly.
        return Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)


def pil_preview(img: "Image.Image", max_side: int = 0) -> "Image.Image":
    """Return a (possibly thumbnailed) copy of a PIL image for display."""
    if max_side and max_side > 0:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return img


# Legacy alias: old callers passed a BGR ndarray; now just pass a PIL image.
def bgr_to_pil(img, max_side: int = 0):
    """Compatibility shim — now accepts a PIL image and thumbnails if needed."""
    if img is None or not _PIL_OK:
        return None
    return pil_preview(img, max_side)


# --- Decoder -------------------------------------------------------------------

@dataclass
class DecodeResult:
    text: str
    strategy: str
    is_url: bool


class QRDecoder:
    """pyzbar-based QR decoder with multiple PIL preprocessing variants.

    Replaces the previous OpenCV QRCodeDetector implementation.
    pyzbar handles rotated, partial, and difficult codes well on its own;
    the preprocessing variants (grayscale, auto-contrast, sharpen, scale-up)
    further boost reliability without requiring opencv-python or numpy.
    """

    def __init__(self) -> None:
        if not _PYZBAR_OK:
            raise RuntimeError("pyzbar is required for QRDecoder.")

    @staticmethod
    def validate_url(text: str) -> bool:
        return looks_like_url(text)

    def try_decode_qr(self, img: "Image.Image") -> Optional[DecodeResult]:
        for label, variant in self._preprocess_variants(img):
            r = self._try_pyzbar(variant, label)
            if r:
                return r
        return None

    def _preprocess_variants(self, img: "Image.Image"):
        """Yield (label, PIL.Image) tuples — lightest/fastest first."""
        out = []

        # 1. Original colour
        out.append(("original", img))

        # 2. Grayscale
        gray = img.convert("L")
        out.append(("gray", gray.convert("RGB")))

        # 3. Auto-contrast (approximates Otsu thresholding)
        ac = ImageOps.autocontrast(gray)
        out.append(("autocontrast", ac.convert("RGB")))

        # 4. Sharpen
        sharp = img.filter(ImageFilter.SHARPEN)
        out.append(("sharpen", sharp))

        # 5. Scale-up for small captures
        w, h = img.size
        if max(h, w) < 800:
            up = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
            out.append(("scale2x", up))
        if max(h, w) < 1200:
            up = img.resize((w * 3, h * 3), Image.Resampling.LANCZOS)
            out.append(("scale3x", up))

        return out

    def _try_pyzbar(self, img: "Image.Image", strategy: str) -> Optional[DecodeResult]:
        if not _PYZBAR_OK:
            return None
        try:
            for c in pyzbar.decode(img):
                try:
                    txt = c.data.decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if txt:
                    return DecodeResult(
                        text=txt,
                        strategy=strategy,
                        is_url=self.validate_url(txt),
                    )
        except Exception:
            pass
        return None


# --- Overlays ------------------------------------------------------------------

class OverlaySelector:
    """Fullscreen translucent overlay; user drags a rectangle, the supplied
    ``on_complete(left, top, width, height)`` callback fires with absolute
    virtual-screen coords. ``on_cancel`` fires on Esc / zero-area release."""

    def __init__(
        self,
        on_complete: Callable[[int, int, int, int], None],
        on_cancel: Optional[Callable[[], None]] = None,
        semi_opaque: float = 0.2,
    ) -> None:
        self._on_complete = on_complete
        self._on_cancel = on_cancel or (lambda: None)
        self._alpha = semi_opaque
        self._top: Optional[tk.Toplevel] = None
        self._canvas: Optional[tk.Canvas] = None
        self._start: Optional[Tuple[int, int]] = None
        self._rect_id: Optional[int] = None

    def start(self, parent: tk.Misc) -> None:
        vx, vy, vw, vh = get_virtual_screen_rect()
        self._top = tk.Toplevel(parent)
        self._top.overrideredirect(True)
        self._top.attributes("-topmost", True)
        try:
            self._top.attributes("-alpha", self._alpha)
        except tk.TclError:
            pass
        self._top.geometry(f"{vw}x{vh}+{vx}+{vy}")
        self._top.configure(bg="black")
        self._top.focus_force()
        self._canvas = tk.Canvas(self._top, highlightthickness=0, bg="black")
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.config(cursor="crosshair")
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._top.bind("<Escape>", self._on_escape)
        self._canvas.bind("<Escape>", self._on_escape)

    def close(self) -> None:
        if self._top is not None:
            try:
                self._top.destroy()
            except Exception:
                pass
        self._top = None
        self._canvas = None

    def _on_press(self, e: tk.Event) -> None:
        self._start = (e.x, e.y)
        if self._rect_id and self._canvas:
            self._canvas.delete(self._rect_id)
        self._rect_id = None

    def _on_drag(self, e: tk.Event) -> None:
        if not self._start or not self._canvas:
            return
        x0, y0 = self._start
        x1, y1 = e.x, e.y
        if self._rect_id:
            self._canvas.coords(self._rect_id, x0, y0, x1, y1)
        else:
            self._rect_id = self._canvas.create_rectangle(
                x0, y0, x1, y1, outline="#00ff88", width=2
            )

    def _on_release(self, e: tk.Event) -> None:
        if not self._start:
            self.close()
            self._on_cancel()
            return
        x0, y0 = self._start
        x1, y1 = e.x, e.y
        left = min(x0, x1); top = min(y0, y1)
        right = max(x0, x1); bottom = max(y0, y1)
        self.close()
        if right - left < 5 or bottom - top < 5:
            self._on_cancel()
            return
        vx, vy, _, _ = get_virtual_screen_rect()
        self._on_complete(vx + left, vy + top, right - left, bottom - top)

    def _on_escape(self, _e: Optional[tk.Event] = None) -> None:
        self.close()
        self._on_cancel()


class OneClickOverlay:
    """Fullscreen overlay that fires ``on_click(absX, absY)`` once and dismisses."""

    def __init__(
        self,
        on_click: Callable[[int, int], None],
        on_cancel: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_click = on_click
        self._on_cancel = on_cancel or (lambda: None)
        self._top: Optional[tk.Toplevel] = None

    def start(self, parent: tk.Misc) -> None:
        vx, vy, vw, vh = get_virtual_screen_rect()
        self._top = tk.Toplevel(parent)
        self._top.overrideredirect(True)
        self._top.attributes("-topmost", True)
        try:
            self._top.attributes("-alpha", 0.12)
        except tk.TclError:
            pass
        self._top.geometry(f"{vw}x{vh}+{vx}+{vy}")
        self._top.configure(bg="#101010")
        f = tk.Frame(self._top, bg="#101010", cursor="crosshair")
        f.pack(fill=tk.BOTH, expand=True)
        f.focus_force()
        f.bind("<Button-1>", self._click)
        self._top.bind("<Escape>", self._esc)
        f.bind("<Escape>", self._esc)

    def close(self) -> None:
        if self._top is not None:
            try:
                self._top.destroy()
            except Exception:
                pass
        self._top = None

    def _click(self, e: tk.Event) -> None:
        vx, vy, _, _ = get_virtual_screen_rect()
        self.close()
        self._on_click(vx + e.x, vy + e.y)

    def _esc(self, _e: Optional[tk.Event] = None) -> None:
        self.close()
        self._on_cancel()


# --- High-level helpers --------------------------------------------------------

# Crop sizes (half-extent) tried around a one-click point.
FALLBACK_CROP_HALFS = (125, 175, 250)  # 250x250, 350x350, 500x500


def open_payload(text: str) -> bool:
    """Open a decoded QR payload in the default browser if it's a URL.
    Returns True if the URL was handed off to the browser."""
    t = normalize_url(text)
    if not t:
        return False
    if looks_like_url(t):
        try:
            webbrowser.open_new_tab(t)
            return True
        except Exception:
            return False
    if t.lower().startswith("www."):
        try:
            webbrowser.open_new_tab("https://" + t)
            return True
        except Exception:
            return False
    return False


def run_drag_select(
    parent: tk.Misc,
    on_decoded: Callable[[Optional[DecodeResult]], None],
    on_dismissed: Optional[Callable[[], None]] = None,
) -> bool:
    """Show the drag-select overlay, capture the drawn rectangle, decode any
    QR code in it, and call ``on_decoded(result_or_None)`` from the Tk thread.

    Returns False immediately (without opening overlay) if the QR stack isn't
    installed; the caller should show a hint in that case.
    """
    if not is_available():
        return False
    decoder = QRDecoder()

    def _complete(left: int, top: int, w: int, h: int) -> None:
        try:
            img = grab_region_mss(left, top, w, h)
            res = decoder.try_decode_qr(img)
        except Exception:
            res = None
        on_decoded(res)

    def _cancel() -> None:
        if on_dismissed:
            on_dismissed()

    sel = OverlaySelector(on_complete=_complete, on_cancel=_cancel)
    sel.start(parent)
    return True


def run_pick_mode(
    parent: tk.Misc,
    on_decoded: Callable[[Optional[DecodeResult]], None],
    on_dismissed: Optional[Callable[[], None]] = None,
) -> bool:
    """Show the one-click overlay; on click, capture multiple crop sizes
    around the click and try the decoder on each. Calls ``on_decoded`` with
    the first hit, or ``None`` if nothing was found at any crop size."""
    if not is_available():
        return False
    decoder = QRDecoder()

    def _click(abs_x: int, abs_y: int) -> None:
        vleft, vtop, vw, vh = get_virtual_screen_rect()
        best: Optional[DecodeResult] = None
        for half in FALLBACK_CROP_HALFS:
            left, top, w, h = clip_region(abs_x, abs_y, half, half, vleft, vtop, vw, vh)
            try:
                img = grab_region_mss(left, top, w, h)
            except Exception:
                continue
            res = decoder.try_decode_qr(img)
            if res:
                best = res
                break
        on_decoded(best)

    def _cancel() -> None:
        if on_dismissed:
            on_dismissed()

    ov = OneClickOverlay(on_click=_click, on_cancel=_cancel)
    ov.start(parent)
    return True
