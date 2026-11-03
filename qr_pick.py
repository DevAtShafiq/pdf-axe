# -*- coding: utf-8 -*-
"""
qr_pick.py — "click a QR code on screen" picker (Office Axe).

The app's QR button runs this module as a short-lived HELPER PROCESS (Tk must
not run inside the pywebview process):

    dev:     python qr_pick.py
    frozen:  OfficeAxe.exe --qr-pick      (main_webview.main() dispatches here)

The helper covers the whole virtual desktop (every monitor, the app window
included) with a nearly transparent overlay:

* click          → grab the screen and decode growing crops around the point
* click + drag   → decode the dragged rectangle
* right-click / Esc → cancel

There is no instruction bar. Each click shows a small × button right at the
click point (its ring spins while scanning, turns amber on a miss, green when
a code is found); clicking the × cancels and closes the picker.

It prints exactly ONE JSON line on stdout and exits:

    {"ok": true,  "text": "...", "is_url": true, "url": "https://...",
     "rect": [left, top, width, height], "mode": "click" | "drag"}
    {"ok": false, "reason": "cancelled" | "not_found" | "unavailable" | "error",
     "detail": "..."}

On a miss the overlay stays open ("No QR code here" beside the ×) so the
user can retry.

The parent side (``run_helper`` / ``finalize``) is used by sfm_bridge: it runs
the helper, parses the line and, for http(s) links, opens the browser
immediately — like the old tkinter app did.

Test hooks (environment variables, used for automated verification only):
    OFFICEAXE_QR_PICK_AUTOCLICK="x,y"        simulate a click at screen x,y
    OFFICEAXE_QR_PICK_AUTOCLICK_DELAY=ms     delay before that click (600)
    OFFICEAXE_QR_PICK_TIMEOUT=seconds        auto-cancel after N s (600)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Half-extents (px) of the square crops tried around a click: 250 → 1000 px.
CROP_HALFS = (125, 175, 250, 350, 500)
# Crops that also get the slow preprocessing variants when the fast pass misses.
HARD_HALFS = (175, 350)
DRAG_THRESHOLD = 6          # px of movement that turns a click into a drag
MIN_REGION = 8              # px — smaller drags are treated as a click

ENV_AUTOCLICK = "OFFICEAXE_QR_PICK_AUTOCLICK"
ENV_AUTOCLICK_DELAY = "OFFICEAXE_QR_PICK_AUTOCLICK_DELAY"
ENV_TIMEOUT = "OFFICEAXE_QR_PICK_TIMEOUT"
DEFAULT_TIMEOUT_S = 600

_OPEN_SCHEMES = ("http://", "https://")


# =============================================================================
# Pure helpers (no screen, no Tk) — unit-tested
# =============================================================================

def normalize_url(text: str) -> str:
    """Return an openable http(s) URL for *text*, or "" when it is not one.

    ``www.example.com/x`` becomes ``https://www.example.com/x`` (like the old
    app). Only http/https with a host and no whitespace are accepted, so
    ``javascript:``, ``file:``, ``data:`` etc. are never opened.
    """
    t = (text or "").strip()
    if not t:
        return ""
    low = t.lower()
    if low.startswith("www."):
        t = "https://" + t
        low = t.lower()
    if not low.startswith(_OPEN_SCHEMES):
        return ""
    try:
        import qr_scan
        return t if qr_scan.is_safe_to_open(t) else ""
    except Exception:
        from urllib.parse import urlsplit
        try:
            p = urlsplit(t)
        except ValueError:
            return ""
        ok = p.scheme.lower() in ("http", "https") and bool(p.hostname) \
            and not any(ch in t for ch in "\r\n\t ")
        return t if ok else ""


def parse_result(stdout_text: str, stderr_text: str = "", returncode: Optional[int] = 0) -> dict:
    """Parse the helper's output: the last stdout line that is a JSON object
    with an ``ok`` key. Anything else becomes ``{"ok": false, "reason": "error"}``."""
    for line in reversed((stdout_text or "").splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "ok" in obj:
            obj["ok"] = bool(obj.get("ok"))
            if not obj["ok"]:
                obj.setdefault("reason", "error")
                obj.setdefault("detail", "")
            else:
                obj["text"] = str(obj.get("text") or "")
            return obj
    tail = (stderr_text or "").strip().splitlines()[-3:]
    detail = " | ".join(tail) if tail else f"QR picker exited (code {returncode}) without a result"
    return {"ok": False, "reason": "error", "detail": detail}


def _nearest(hits: list, px: float, py: float) -> dict:
    """The hit whose bbox contains (px, py), else the one whose centre is closest."""
    def score(h):
        x, y, w, hh = h["bbox"]
        inside = x <= px <= x + w and y <= py <= y + hh
        cx, cy = x + w / 2.0, y + hh / 2.0
        return (0 if inside else 1, (cx - px) ** 2 + (cy - py) ** 2)
    return min(hits, key=score)


def _decode(img, hard):
    import qr_scan
    return qr_scan.decode_image(img, hard=hard)


def pick_decode(img, x: float, y: float, halfs=CROP_HALFS, hard_halfs=HARD_HALFS) -> Optional[dict]:
    """Decode the QR code around point (x, y) of a screenshot *img* (PIL).

    Same idea as the old ``run_pick_mode``: square crops of growing size
    centred on the click, a fast pass on every size first and then the
    preprocessing variants on a couple of sizes. The first hit wins (the code
    nearest to the point if a crop holds several).

    Returns {text, bbox:[x,y,w,h] (img coords), strategy, half} or None.
    """
    W, H = img.size
    x, y = int(round(x)), int(round(y))
    if not (0 <= x < W and 0 <= y < H):
        return None
    passes = [(h, False) for h in halfs] + [(h, True) for h in hard_halfs]
    for half, hard in passes:
        x1, y1 = max(0, x - half), max(0, y - half)
        x2, y2 = min(W, x + half), min(H, y + half)
        if x2 - x1 < 16 or y2 - y1 < 16:
            continue
        hits = _decode(img.crop((x1, y1, x2, y2)), hard)
        if hits:
            h = _nearest(hits, x - x1, y - y1)
            bx, by, bw, bh = h["bbox"]
            return {"text": h["text"], "bbox": [bx + x1, by + y1, bw, bh],
                    "strategy": h.get("strategy", ""), "half": half}
    return None


def region_decode(img, left: float, top: float, width: float, height: float) -> Optional[dict]:
    """Decode the QR code inside a rectangle of *img* (the code nearest to the
    rectangle's centre if it holds several). Returns like ``pick_decode``."""
    W, H = img.size
    x1, y1 = max(0, int(left)), max(0, int(top))
    x2, y2 = min(W, int(left + width)), min(H, int(top + height))
    if x2 - x1 < MIN_REGION or y2 - y1 < MIN_REGION:
        return None
    hits = _decode(img.crop((x1, y1, x2, y2)), None)
    if not hits:
        return None
    h = _nearest(hits, (x2 - x1) / 2.0, (y2 - y1) / 2.0)
    bx, by, bw, bh = h["bbox"]
    return {"text": h["text"], "bbox": [bx + x1, by + y1, bw, bh],
            "strategy": h.get("strategy", ""), "half": 0}


def success_payload(hit: dict, origin=(0, 0), mode: str = "click") -> dict:
    """Helper output for a decoded hit (bbox in screenshot coords → screen rect)."""
    ox, oy = origin
    x, y, w, h = hit["bbox"]
    url = normalize_url(hit["text"])
    return {"ok": True, "text": hit["text"], "is_url": bool(url), "url": url,
            "rect": [int(x + ox), int(y + oy), int(w), int(h)], "mode": mode}


# =============================================================================
# Parent side — used by sfm_bridge
# =============================================================================

def helper_command() -> list:
    """argv that starts the picker helper (frozen EXE or dev interpreter)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--qr-pick"]
    return [sys.executable, os.path.join(_HERE, "qr_pick.py")]


def run_helper(timeout: Optional[float] = None, env: Optional[dict] = None) -> dict:
    """Run the picker helper and return its parsed result (blocks until the
    user picks / cancels). Never raises."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    run_env = dict(os.environ if env is None else env)
    run_env["PYTHONIOENCODING"] = "utf-8"
    try:
        cp = subprocess.run(
            helper_command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=_HERE, env=run_env, creationflags=flags,
            timeout=timeout or (DEFAULT_TIMEOUT_S + 60),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "cancelled", "detail": "timeout"}
    except Exception as exc:
        return {"ok": False, "reason": "error", "detail": str(exc)}
    out = cp.stdout.decode("utf-8", "replace") if isinstance(cp.stdout, bytes) else (cp.stdout or "")
    err = cp.stderr.decode("utf-8", "replace") if isinstance(cp.stderr, bytes) else (cp.stderr or "")
    return parse_result(out, err, cp.returncode)


def finalize(res: dict, opener: Optional[Callable[[str], object]] = None) -> dict:
    """Add URL details to a helper result and open http(s) links right away.

    Adds ``url``, ``is_url``, ``opened`` (+ ``host``/``domain``/``label``/
    ``warnings`` from qr_scan.classify_payload). Only http/https URLs are
    handed to the browser.
    """
    out = dict(res or {})
    if not out.get("ok"):
        out.setdefault("reason", "error")
        out.setdefault("detail", "")
        return out
    text = str(out.get("text") or "")
    url = normalize_url(text)
    out["url"] = url
    out["is_url"] = bool(url)
    out["opened"] = False
    try:
        import qr_scan
        info = qr_scan.classify_payload(url or text)
        for k in ("type", "label", "host", "domain", "scheme", "warnings", "notes"):
            if k in info:
                out[k] = info[k]
    except Exception:
        pass
    if url:
        if opener is None:
            import webbrowser
            opener = webbrowser.open_new_tab
        try:
            opener(url)
            out["opened"] = True
        except Exception as exc:
            out["open_error"] = str(exc)
    return out


# =============================================================================
# Helper process — the overlay
# =============================================================================

def _emit(obj: dict) -> None:
    """Print the one JSON result line (works in a windowed frozen build too)."""
    line = json.dumps(obj, ensure_ascii=True) + "\n"
    stream = sys.stdout
    if stream is not None:
        try:
            stream.write(line)
            stream.flush()
            return
        except Exception:
            pass
    if sys.platform == "win32":     # no sys.stdout (windowed EXE): write the handle
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.GetStdHandle(-11)
            data = line.encode("utf-8")
            n = ctypes.c_ulong(0)
            k32.WriteFile(h, data, len(data), ctypes.byref(n), None)
        except Exception:
            pass


def _set_dpi_aware() -> None:
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _mss_new():
    import mss
    cls = getattr(mss, "MSS", None) or mss.mss      # mss.mss is deprecated in mss 10
    return cls()


def _virtual_rect() -> tuple:
    if sys.platform == "win32":
        try:
            import ctypes
            u = ctypes.windll.user32
            return (int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77)),
                    int(u.GetSystemMetrics(78)), int(u.GetSystemMetrics(79)))
        except Exception:
            pass
    import mss
    with _mss_new() as s:
        m = s.monitors[0]
        return m["left"], m["top"], m["width"], m["height"]


def grab_virtual_screen():
    """Screenshot of the whole virtual desktop → (PIL image, (left, top))."""
    import mss
    from PIL import Image
    with _mss_new() as sct:
        mon = sct.monitors[0]
        shot = sct.grab(mon)
        return Image.frombytes("RGB", (shot.width, shot.height), shot.rgb), (mon["left"], mon["top"])


def _check_stack() -> str:
    """'' when the screen + decoder stack is usable, else a short reason."""
    missing = []
    try:
        import mss  # noqa: F401
    except Exception:
        missing.append("mss")
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        missing.append("Pillow")
    try:
        import qr_scan
        if not qr_scan.decoder_available():
            missing.append("pyzbar")
    except Exception:
        missing.append("pyzbar")
    try:
        import tkinter  # noqa: F401
    except Exception:
        missing.append("tkinter")
    return ", ".join(missing)


# ── Overlay colours (dark, neutral; accent = Office Axe blue) ────────────────
TINT = "#0b0d12"
TINT_ALPHA = 0.15
CHIP_BG = "#16181d"
CHIP_BG_HOVER = "#2a1517"
CHIP_BORDER = "#3b414d"
CHIP_TEXT = "#f3f4f6"
CHIP_MUTED = "#c9ced8"
ACCENT = "#2f86ff"
GREEN = "#22c55e"
AMBER = "#f5a524"
RED = "#f0656b"
KEY = "#fe00fe"           # transparent-colour key of the chip / frame windows

CHIP_SIZE = 26            # px at 96 dpi — diameter of the × button
MISS_TEXT = "No QR code here"


class _Frame:
    """A rectangle outline drawn in its own click-through, topmost window
    (the tinted overlay is almost transparent, so lines drawn on it would be too)."""

    def __init__(self, tk, master, color: str, width: int = 2):
        self.tk = tk
        self.color = color
        self.width = width
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.win.configure(bg=KEY)
        self.cv = tk.Canvas(self.win, bg=KEY, highlightthickness=0, bd=0, cursor="crosshair")
        self.cv.pack(fill="both", expand=True)
        self.shown = False

    def show(self, l: int, t: int, w: int, h: int, pad: int = 0) -> None:
        bw = self.width
        l, t, w, h = l - pad - bw, t - pad - bw, w + 2 * (pad + bw), h + 2 * (pad + bw)
        w, h = max(w, 2 * bw + 1), max(h, 2 * bw + 1)
        self.win.geometry(f"{w}x{h}+{l}+{t}")
        self.cv.delete("all")
        o = bw / 2.0
        self.cv.create_rectangle(o, o, w - o - 1, h - o - 1, outline=self.color, width=bw)
        if not self.shown:
            self.win.deiconify()
            self.shown = True
        self.win.lift()

    def hide(self) -> None:
        if self.shown:
            self.win.withdraw()
            self.shown = False


class _CancelChip:
    """Small round × button shown where the user clicked.

    Clicking it cancels the picker. Its ring tells what is happening: a
    spinning blue arc while scanning, amber (with a short "No QR code here"
    note beside it) after a miss, green when a code was found.
    """

    def __init__(self, tk, master, scale: float, font, on_cancel):
        self.tk = tk
        self.s = scale
        self.font = font
        self.on_cancel = on_cancel
        self.d = int(round(CHIP_SIZE * scale))           # button diameter
        self.ring = max(2, int(round(2 * scale)))
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        self.win.configure(bg=KEY)
        self.cv = tk.Canvas(self.win, bg=KEY, highlightthickness=0, bd=0, cursor="hand2")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<ButtonRelease-1>", self._on_click)
        self.cv.bind("<Enter>", lambda e: self._set_hover(True))
        self.cv.bind("<Leave>", lambda e: self._set_hover(False))
        self.shown = False
        self.hover = False
        self.mode = "idle"            # idle | scan | miss | found
        self.note = ""
        self.at = (0, 0)
        self._spin = 0
        self._spin_job = None

    # pad around the circle so the ring/spinner is not clipped
    def _pad(self) -> int:
        return self.ring + 2

    def show(self, x: int, y: int, mode: str = "scan", note: str = "") -> None:
        self.at = (int(x), int(y))
        self.mode = mode
        self.note = note
        self._draw()
        if not self.shown:
            self.win.deiconify()
            self.shown = True
        self.win.lift()
        self._animate(mode == "scan")

    def set_mode(self, mode: str, note: str = "") -> None:
        if not self.shown:
            return
        self.mode = mode
        self.note = note
        self._draw()
        self._animate(mode == "scan")

    def hide(self) -> None:
        self._animate(False)
        if self.shown:
            self.win.withdraw()
            self.shown = False

    def _set_hover(self, on: bool) -> None:
        self.hover = on
        if self.shown:
            self._draw()

    def _on_click(self, e) -> None:
        # Only the round button cancels, not the "No QR code here" note.
        p = self._pad()
        r = self.d / 2.0
        if (e.x - (p + r)) ** 2 + (e.y - (p + r)) ** 2 <= (r + p) ** 2:
            self.on_cancel()

    def _animate(self, on: bool) -> None:
        if self._spin_job is not None:
            try:
                self.win.after_cancel(self._spin_job)
            except Exception:
                pass
            self._spin_job = None
        if on:
            self._spin = (self._spin + 24) % 360
            self._draw()
            self._spin_job = self.win.after(40, lambda: self._animate(True))

    def _draw(self) -> None:
        s, d, p, rw = self.s, self.d, self._pad(), self.ring
        note_w = 0
        if self.note:
            note_w = self.font.measure(self.note) + int(22 * s)
        w = d + 2 * p + (int(6 * s) + note_w if note_w else 0)
        h = d + 2 * p
        # The centre of the × sits exactly on the click point.
        cx, cy = self.at
        self.win.geometry(f"{w}x{h}+{cx - p - d // 2}+{cy - p - d // 2}")
        c = self.cv
        c.delete("all")
        c.configure(width=w, height=h)

        if note_w:                      # note to the right of the button
            nx = d + 2 * p + int(6 * s)
            nh = int(22 * s)
            ny = (h - nh) / 2.0
            r = nh / 2.0
            c.create_oval(nx, ny, nx + nh, ny + nh, fill=CHIP_BG, outline="")
            c.create_oval(nx + note_w - nh, ny, nx + note_w, ny + nh, fill=CHIP_BG, outline="")
            c.create_rectangle(nx + r, ny, nx + note_w - r, ny + nh, fill=CHIP_BG, outline="")
            c.create_text(nx + note_w / 2.0, h / 2.0, text=self.note, font=self.font, fill=CHIP_MUTED)

        x1, y1, x2, y2 = p, p, p + d, p + d
        ring = {"scan": CHIP_BORDER, "miss": AMBER, "found": GREEN}.get(self.mode, CHIP_BORDER)
        if self.hover:
            ring = RED
        c.create_oval(x1, y1, x2, y2, fill=CHIP_BG_HOVER if self.hover else CHIP_BG,
                      outline=ring, width=rw)
        if self.mode == "scan" and not self.hover:
            c.create_arc(x1, y1, x2, y2, start=self._spin, extent=100, style="arc",
                         outline=ACCENT, width=rw)
        # the ×
        k = d * 0.2
        mx, my = p + d / 2.0, p + d / 2.0
        lw = max(1.5, 1.6 * s)
        col = CHIP_TEXT
        c.create_line(mx - k, my - k, mx + k, my + k, fill=col, width=lw, capstyle="round")
        c.create_line(mx - k, my + k, mx + k, my - k, fill=col, width=lw, capstyle="round")


class PickOverlay:
    def __init__(self) -> None:
        import tkinter as tk
        import tkinter.font as tkfont
        self.tk = tk
        self.result: Optional[dict] = None
        self.busy = False
        self.press = None
        self.dragging = False
        self._note_job = None

        self.vx, self.vy, self.vw, self.vh = _virtual_rect()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Office Axe — QR pick")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", TINT_ALPHA)
        except tk.TclError:
            pass
        self.root.configure(bg=TINT)
        self.root.geometry(f"{self.vw}x{self.vh}+{self.vx}+{self.vy}")
        self.cv = tk.Canvas(self.root, bg=TINT, highlightthickness=0, bd=0, cursor="crosshair")
        self.cv.pack(fill="both", expand=True)

        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.f_note = tkfont.Font(family="Segoe UI", size=9)

        self.sel = _Frame(tk, self.root, ACCENT, max(2, int(round(2 * self.scale))))
        self.hit = _Frame(tk, self.root, GREEN, max(3, int(round(3 * self.scale))))
        self.chip = _CancelChip(tk, self.root, self.scale, self.f_note, self.cancel)

        for w in (self.root, self.chip.win):
            w.bind("<Escape>", lambda e: self.cancel())
            w.bind("<Button-3>", lambda e: self.cancel())
        self.cv.bind("<ButtonPress-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)

    # ── mouse ───────────────────────────────────────────────────────────────
    def _on_press(self, e) -> None:
        if self.busy:
            return
        self.press = (e.x_root, e.y_root)
        self.dragging = False

    def _on_drag(self, e) -> None:
        if self.busy or self.press is None:
            return
        x0, y0 = self.press
        if not self.dragging and max(abs(e.x_root - x0), abs(e.y_root - y0)) < DRAG_THRESHOLD:
            return
        if not self.dragging:
            self.chip.hide()
        self.dragging = True
        l, t = min(x0, e.x_root), min(y0, e.y_root)
        self.sel.show(l, t, abs(e.x_root - x0), abs(e.y_root - y0))

    def _on_release(self, e) -> None:
        if self.busy or self.press is None:
            return
        x0, y0 = self.press
        self.press = None
        if self.dragging:
            self.dragging = False
            l, t = min(x0, e.x_root), min(y0, e.y_root)
            w, h = abs(e.x_root - x0), abs(e.y_root - y0)
            if w >= MIN_REGION and h >= MIN_REGION:
                self.decode_region(l, t, w, h, at=(e.x_root, e.y_root))
                return
            self.sel.hide()
        self.pick_at(e.x_root, e.y_root)

    # ── decoding ────────────────────────────────────────────────────────────
    def _windows(self) -> list:
        return [self.root, self.chip.win, self.sel.win, self.hit.win]

    def _grab(self):
        """Screenshot with every overlay window made fully transparent first,
        so the tint / × button / outline never end up in the decoded pixels."""
        alphas = []
        for w in self._windows():
            try:
                alphas.append((w, w.attributes("-alpha")))
                w.attributes("-alpha", 0.0)
            except Exception:
                pass
        try:
            self.root.update_idletasks()
            self.root.update()
            time.sleep(0.12)          # let the compositor present the change
            return grab_virtual_screen()
        finally:
            for w, a in alphas:
                try:
                    w.attributes("-alpha", a)
                except Exception:
                    pass
            self.root.update_idletasks()

    def pick_at(self, sx: int, sy: int) -> None:
        self._run_decode("click", (sx, sy), lambda img, o: pick_decode(img, sx - o[0], sy - o[1]))

    def decode_region(self, l: int, t: int, w: int, h: int, at: Optional[tuple] = None) -> None:
        self._run_decode("drag", at or (l + w, t + h),
                         lambda img, o: region_decode(img, l - o[0], t - o[1], w, h))

    def _run_decode(self, mode: str, at: tuple, fn) -> None:
        if self.busy:
            return
        self.busy = True
        if self._note_job is not None:
            self.root.after_cancel(self._note_job)
            self._note_job = None
        self.chip.show(at[0], at[1], "scan")
        try:
            img, origin = self._grab()
        except Exception as exc:
            self.finish({"ok": False, "reason": "error", "detail": f"Screen capture failed: {exc}"})
            return
        box: dict = {}

        def work():
            try:
                box["hit"] = fn(img, origin)
            except Exception as exc:     # pragma: no cover - defensive
                box["error"] = str(exc)
            box["done"] = True

        threading.Thread(target=work, daemon=True).start()
        self._poll(box, origin, mode)

    def _miss(self, note: str) -> None:
        """Back to picking; the × stays where it is with a short note."""
        self.busy = False
        self.chip.set_mode("miss", note)
        self._note_job = self.root.after(1800, self._clear_note)

    def _clear_note(self) -> None:
        self._note_job = None
        if not self.busy:
            self.chip.set_mode("miss", "")

    def _poll(self, box: dict, origin, mode: str) -> None:
        if not box.get("done"):
            self.root.after(40, lambda: self._poll(box, origin, mode))
            return
        self.sel.hide()
        hit = box.get("hit")
        if box.get("error"):
            self._miss("Could not decode")
            return
        if not hit:
            self._miss(MISS_TEXT)
            return
        payload = success_payload(hit, origin, mode)
        l, t, w, h = payload["rect"]
        self.hit.show(l, t, w, h, pad=int(6 * self.scale))
        self.chip.set_mode("found")
        self.root.after(420, lambda: self.finish(payload))

    # ── lifecycle ───────────────────────────────────────────────────────────
    def cancel(self) -> None:
        if self.result is None:
            self.finish({"ok": False, "reason": "cancelled", "detail": ""})

    def finish(self, result: dict) -> None:
        if self.result is not None:
            return
        self.result = result
        self.chip.hide()
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass

    def _poll_escape(self) -> None:
        """Esc works even if Windows refuses keyboard focus to the overlay."""
        if self.result is not None:
            return
        try:
            import ctypes
            if ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000:
                self.cancel()
                return
        except Exception:
            return
        self.root.after(60, self._poll_escape)

    def run(self) -> dict:
        self.root.deiconify()
        self.root.lift()
        self.root.after(50, self._focus)
        if sys.platform == "win32":
            self.root.after(250, self._poll_escape)

        auto = os.environ.get(ENV_AUTOCLICK, "").strip()
        if auto:
            try:
                ax, ay = (int(float(v)) for v in auto.split(","))
                delay = int(os.environ.get(ENV_AUTOCLICK_DELAY, "600") or 600)
                self.root.after(delay, lambda: self.pick_at(ax, ay))
            except ValueError:
                pass
        try:
            limit = float(os.environ.get(ENV_TIMEOUT, "") or DEFAULT_TIMEOUT_S)
        except ValueError:
            limit = DEFAULT_TIMEOUT_S
        self.root.after(int(limit * 1000),
                        lambda: self.finish({"ok": False, "reason": "cancelled", "detail": "timeout"}))
        self.root.mainloop()
        return self.result or {"ok": False, "reason": "cancelled", "detail": ""}

    def _focus(self) -> None:
        try:
            self.root.focus_force()
            self.cv.focus_set()
        except Exception:
            pass


def main(argv=None) -> int:
    """Helper-process entry point: show the overlay, print one JSON line."""
    _set_dpi_aware()
    missing = _check_stack()
    if missing:
        _emit({"ok": False, "reason": "unavailable", "detail": f"Missing: {missing}"})
        return 2
    try:
        res = PickOverlay().run()
    except Exception as exc:
        res = {"ok": False, "reason": "error", "detail": str(exc)}
    _emit(res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
