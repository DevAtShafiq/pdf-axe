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

It prints exactly ONE JSON line on stdout and exits:

    {"ok": true,  "text": "...", "is_url": true, "url": "https://...",
     "rect": [left, top, width, height], "mode": "click" | "drag"}
    {"ok": false, "reason": "cancelled" | "not_found" | "unavailable" | "error",
     "detail": "..."}

On a miss the overlay stays open (with a hint) so the user can retry.

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


def _monitors() -> list:
    try:
        import mss
        with _mss_new() as s:
            return [(m["left"], m["top"], m["width"], m["height"]) for m in s.monitors[1:]]
    except Exception:
        return [_virtual_rect()]


def _cursor_pos() -> Optional[tuple]:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    return None


def grab_virtual_screen():
    """Screenshot of the whole virtual desktop → (PIL image, (left, top))."""
    import mss
    from PIL import Image
    with _mss_new() as sct:
        mon = sct.monitors[0]
        shot = sct.grab(mon)
        return Image.frombytes("RGB", (shot.width, shot.height), shot.rgb), (mon["left"], mon["top"])


def _brand_mark_path() -> str:
    base = getattr(sys, "_MEIPASS", _HERE)
    for root in (base, _HERE):
        p = os.path.join(root, "ui", "assets", "brand", "office-axe-mark.png")
        if os.path.isfile(p):
            return p
    return ""


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
PILL_BG = "#16181d"
PILL_BORDER = "#2c313b"
PILL_TEXT = "#f3f4f6"
PILL_MUTED = "#9aa3b2"
KBD_BORDER = "#3b414d"
ACCENT = "#2f86ff"
GREEN = "#22c55e"
AMBER = "#f5a524"
KEY = "#fe00fe"           # transparent-colour key of the pill / frame windows

MSG_IDLE = [("title", "Click a QR code to open it"), ("sep", ""), ("muted", "Drag to select an area"),
            ("sep", ""), ("kbd", "Esc"), ("muted", "to cancel")]
MSG_SCAN = [("dot:" + ACCENT, ""), ("title", "Scanning…")]
MSG_MISS = [("dot:" + AMBER, ""), ("title", "No QR code here"), ("sep", ""),
            ("muted", "click right on the code or drag a box around it")]


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


class PickOverlay:
    def __init__(self) -> None:
        import tkinter as tk
        import tkinter.font as tkfont
        self.tk = tk
        self.result: Optional[dict] = None
        self.busy = False
        self.press = None
        self.dragging = False
        self._msg_job = None
        self._mon = None

        self.vx, self.vy, self.vw, self.vh = _virtual_rect()
        self.monitors = _monitors() or [(self.vx, self.vy, self.vw, self.vh)]

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
        self.f_title = tkfont.Font(family="Segoe UI Semibold", size=10)
        self.f_text = tkfont.Font(family="Segoe UI", size=10)
        self.f_kbd = tkfont.Font(family="Segoe UI", size=8)

        # instruction pill (separate window so it stays fully opaque)
        self.pill = tk.Toplevel(self.root)
        self.pill.withdraw()
        self.pill.overrideredirect(True)
        self.pill.attributes("-topmost", True)
        try:
            self.pill.attributes("-transparentcolor", KEY)
            self.pill.attributes("-alpha", 0.97)
        except tk.TclError:
            pass
        self.pill.configure(bg=KEY)
        self.pcv = tk.Canvas(self.pill, bg=KEY, highlightthickness=0, bd=0, cursor="crosshair")
        self.pcv.pack(fill="both", expand=True)
        self.mark = self._load_mark(int(round(20 * self.scale)))

        self.sel = _Frame(tk, self.root, ACCENT, max(2, int(round(2 * self.scale))))
        self.hit = _Frame(tk, self.root, GREEN, max(3, int(round(3 * self.scale))))

        for w in (self.root, self.pill):
            w.bind("<Escape>", lambda e: self.cancel())
            w.bind("<Button-3>", lambda e: self.cancel())
        self.cv.bind("<ButtonPress-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.cv.bind("<Motion>", self._on_move)

    # ── pill ────────────────────────────────────────────────────────────────
    def _load_mark(self, size: int):
        p = _brand_mark_path()
        if not p:
            return None
        try:
            from PIL import Image, ImageTk
            im = Image.open(p).convert("RGBA")
            im.thumbnail((size, size), Image.Resampling.LANCZOS)
            bg = Image.new("RGBA", im.size, PILL_BG)
            bg.alpha_composite(im)
            return ImageTk.PhotoImage(bg.convert("RGB"), master=self.root)
        except Exception:
            return None

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.pcv.create_polygon(pts, smooth=True, **kw)

    def set_pill(self, parts, accent: Optional[str] = None) -> None:
        s = self.scale
        pad_x, h, gap = int(16 * s), int(42 * s), int(9 * s)
        c = self.pcv
        c.delete("all")
        # measure
        items = []
        if self.mark is not None:
            items.append(("img", self.mark.width()))
        for kind, text in parts:
            if kind == "title":
                items.append((kind, self.f_title.measure(text), text))
            elif kind == "muted":
                items.append((kind, self.f_text.measure(text), text))
            elif kind == "kbd":
                items.append((kind, self.f_kbd.measure(text) + int(12 * s), text))
            elif kind == "sep":
                items.append((kind, int(4 * s), ""))
            elif kind.startswith("dot:"):
                items.append((kind, int(8 * s), ""))
        w = pad_x * 2 + sum(it[1] for it in items) + gap * (len(items) - 1)
        self.pill.geometry(f"{w}x{h}")
        c.configure(width=w, height=h)
        self._round_rect(1, 1, w - 2, h - 2, int(12 * s), fill=PILL_BG, outline=accent or PILL_BORDER,
                         width=1)
        x, cy = pad_x, h / 2.0
        for it in items:
            kind, iw = it[0], it[1]
            if kind == "img":
                c.create_image(x, cy, image=self.mark, anchor="w")
            elif kind == "title":
                c.create_text(x, cy, text=it[2], font=self.f_title, fill=PILL_TEXT, anchor="w")
            elif kind == "muted":
                c.create_text(x, cy, text=it[2], font=self.f_text, fill=PILL_MUTED, anchor="w")
            elif kind == "kbd":
                kh = int(18 * s)
                c.create_rectangle(x, cy - kh / 2, x + iw, cy + kh / 2, outline=KBD_BORDER, width=1)
                c.create_text(x + iw / 2.0, cy, text=it[2], font=self.f_kbd, fill=PILL_TEXT)
            elif kind == "sep":
                r = 1.5 * s
                c.create_oval(x + iw / 2 - r, cy - r, x + iw / 2 + r, cy + r, fill=PILL_MUTED, outline="")
            elif kind.startswith("dot:"):
                col = kind[4:]
                c.create_oval(x, cy - iw / 2, x + iw, cy + iw / 2, fill=col, outline="")
            x += iw + gap
        self._place_pill(force=True)

    def _monitor_at(self, px: int, py: int) -> tuple:
        for m in self.monitors:
            if m[0] <= px < m[0] + m[2] and m[1] <= py < m[1] + m[3]:
                return m
        return self.monitors[0]

    def _place_pill(self, force: bool = False, at: Optional[tuple] = None) -> None:
        pos = at or _cursor_pos() or (self.vx + self.vw // 2, self.vy + self.vh // 2)
        mon = self._monitor_at(*pos)
        if mon == self._mon and not force:
            return
        self._mon = mon
        self.pill.update_idletasks()
        w = self.pill.winfo_reqwidth()
        x = mon[0] + (mon[2] - w) // 2
        y = mon[1] + int(28 * self.scale)
        self.pill.geometry(f"+{x}+{y}")
        self.pill.lift()

    def flash_message(self, parts, ms: int, accent: Optional[str] = None) -> None:
        if self._msg_job is not None:
            self.root.after_cancel(self._msg_job)
        self.set_pill(parts, accent)
        self._msg_job = self.root.after(ms, self._back_to_idle)

    def _back_to_idle(self) -> None:
        self._msg_job = None
        if not self.busy:
            self.set_pill(MSG_IDLE)

    # ── mouse ───────────────────────────────────────────────────────────────
    def _on_move(self, e) -> None:
        self._place_pill(at=(e.x_root, e.y_root))

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
                self.decode_region(l, t, w, h)
                return
            self.sel.hide()
        self.pick_at(e.x_root, e.y_root)

    # ── decoding ────────────────────────────────────────────────────────────
    def _windows(self) -> list:
        return [self.root, self.pill, self.sel.win, self.hit.win]

    def _grab(self):
        """Screenshot with every overlay window made fully transparent first,
        so the tint / pill / outline never end up in the decoded pixels."""
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
        self._run_decode("click", lambda img, o: pick_decode(img, sx - o[0], sy - o[1]))

    def decode_region(self, l: int, t: int, w: int, h: int) -> None:
        self._run_decode("drag", lambda img, o: region_decode(img, l - o[0], t - o[1], w, h))

    def _run_decode(self, mode: str, fn) -> None:
        if self.busy:
            return
        self.busy = True
        if self._msg_job is not None:
            self.root.after_cancel(self._msg_job)
            self._msg_job = None
        self.set_pill(MSG_SCAN, ACCENT)
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

    def _poll(self, box: dict, origin, mode: str) -> None:
        if not box.get("done"):
            self.root.after(40, lambda: self._poll(box, origin, mode))
            return
        self.sel.hide()
        hit = box.get("hit")
        if box.get("error"):
            self.busy = False
            self.flash_message([("dot:" + AMBER, ""), ("title", "Could not decode"), ("sep", ""),
                                ("muted", box["error"][:80])], 2500, AMBER)
            return
        if not hit:
            self.busy = False
            self.flash_message(MSG_MISS, 2000, AMBER)
            return
        payload = success_payload(hit, origin, mode)
        l, t, w, h = payload["rect"]
        self.hit.show(l, t, w, h, pad=int(6 * self.scale))
        self.set_pill([("dot:" + GREEN, ""),
                       ("title", "QR code found"), ("sep", ""),
                       ("muted", "Opening link…" if payload["is_url"] else "Showing the text…")],
                      GREEN)
        self.root.after(420, lambda: self.finish(payload))

    # ── lifecycle ───────────────────────────────────────────────────────────
    def cancel(self) -> None:
        if self.result is None:
            self.finish({"ok": False, "reason": "cancelled", "detail": ""})

    def finish(self, result: dict) -> None:
        if self.result is not None:
            return
        self.result = result
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
        self.set_pill(MSG_IDLE)
        self.pill.deiconify()
        self.pill.lift()
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
