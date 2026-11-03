# -*- coding: utf-8 -*-
"""
screen_pick.py — "drag a box" screenshot picker (Office Axe).

Runs as a short-lived HELPER PROCESS (Tk must not run inside the pywebview
process), exactly like qr_pick.py:

    dev:     python screen_pick.py --out "C:\\...\\Screenshot 2026-01-01 10-00-00.png"
    frozen:  OfficeAxe.exe --screen-pick --out "..."   (main_webview dispatches here)

The helper grabs the whole virtual desktop first (so the picture is frozen —
menus and tooltips stay put), shows it full-screen and dimmed, and lets the
user drag a rectangle. Releasing the mouse (or Enter) saves that part as PNG
to --out (never overwriting an existing file); Esc / right-click cancels.

It prints exactly ONE JSON line on stdout:

    {"ok": true, "path": "...", "rect": [l, t, w, h], "width": w, "height": h}
    {"ok": false, "reason": "cancelled" | "unavailable" | "error", "detail": "..."}

Test hooks (automated verification only):
    OFFICEAXE_SCREEN_PICK_AUTODRAG="x1,y1,x2,y2"   select that box (virtual-desktop coords)
    OFFICEAXE_SCREEN_PICK_TIMEOUT=seconds          auto-cancel after N s (300)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

MIN_SIZE = 4
ENV_AUTODRAG = "OFFICEAXE_SCREEN_PICK_AUTODRAG"
ENV_TIMEOUT = "OFFICEAXE_SCREEN_PICK_TIMEOUT"
DEFAULT_TIMEOUT_S = 300


# ── pure helpers ─────────────────────────────────────────────────────────────

def normalize_rect(x1: float, y1: float, x2: float, y2: float, bound_w: int, bound_h: int):
    """Two drag corners → (left, top, width, height) clamped to the image, or None."""
    l, r = sorted((int(round(x1)), int(round(x2))))
    t, b = sorted((int(round(y1)), int(round(y2))))
    l, t = max(0, l), max(0, t)
    r, b = min(bound_w, r), min(bound_h, b)
    if r - l < MIN_SIZE or b - t < MIN_SIZE:
        return None
    return l, t, r - l, b - t


def save_crop(img, rect, out_path: str) -> dict:
    """Crop *img* to *rect* and write a PNG to *out_path* (must not exist)."""
    l, t, w, h = rect
    crop = img.crop((l, t, l + w, t + h))
    with open(out_path, "xb") as fh:
        crop.save(fh, format="PNG")
    return {"ok": True, "path": out_path, "rect": [l, t, w, h], "width": w, "height": h}


def helper_command(out_path: str) -> list:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--screen-pick", "--out", out_path]
    return [sys.executable, os.path.join(_HERE, "screen_pick.py"), "--out", out_path]


def run_helper(out_path: str, timeout: Optional[float] = None, env: Optional[dict] = None) -> dict:
    """Run the picker helper (blocks until the user picks / cancels). Never raises."""
    import qr_pick
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    run_env = dict(os.environ if env is None else env)
    run_env["PYTHONIOENCODING"] = "utf-8"
    try:
        cp = subprocess.run(
            helper_command(out_path), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=_HERE, env=run_env, creationflags=flags,
            timeout=timeout or (DEFAULT_TIMEOUT_S + 60),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "cancelled", "detail": "timeout"}
    except Exception as exc:
        return {"ok": False, "reason": "error", "detail": str(exc)}
    out = cp.stdout.decode("utf-8", "replace") if isinstance(cp.stdout, bytes) else (cp.stdout or "")
    err = cp.stderr.decode("utf-8", "replace") if isinstance(cp.stderr, bytes) else (cp.stderr or "")
    res = qr_pick.parse_result(out, err, cp.returncode)
    res.pop("text", None)
    return res


# ── the overlay (helper process) ─────────────────────────────────────────────

class RegionOverlay:
    def __init__(self, img, origin, out_path: str) -> None:
        import tkinter as tk
        from PIL import Image, ImageEnhance, ImageTk
        self.tk = tk
        self.img = img
        self.out_path = out_path
        self.result: Optional[dict] = None
        self.start = None
        self.rect = None
        left, top = origin
        w, h = img.size

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(f"{w}x{h}+{left}+{top}")
        self.root.configure(cursor="crosshair", bg="black")

        dim = ImageEnhance.Brightness(img).enhance(0.45)
        self.ph_dim = ImageTk.PhotoImage(dim, master=self.root)
        self.ph_full = ImageTk.PhotoImage(img, master=self.root)

        self.cv = tk.Canvas(self.root, width=w, height=h, highlightthickness=0, bd=0,
                            cursor="crosshair", bg="black")
        self.cv.pack(fill="both", expand=True)
        self.cv.create_image(0, 0, image=self.ph_dim, anchor="nw")
        # The bright selection = a child canvas showing the undimmed picture.
        self.sel = tk.Canvas(self.root, highlightthickness=1, highlightbackground="#4f8cff",
                             bd=0, cursor="crosshair", bg="black")
        self.sel_img = self.sel.create_image(0, 0, image=self.ph_full, anchor="nw")
        self.hint = self.cv.create_text(
            w // 2, 36, text="Drag to select an area  ·  Esc to cancel",
            fill="white", font=("Segoe UI", 13, "bold"))
        self.size_lbl = self.cv.create_text(0, 0, text="", fill="white", anchor="sw",
                                            font=("Segoe UI", 10, "bold"), state="hidden")

        for widget in (self.cv, self.sel):
            widget.bind("<ButtonPress-1>", self._press)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<ButtonRelease-1>", self._release)
            widget.bind("<Button-3>", lambda e: self.cancel())
        self.root.bind("<Escape>", lambda e: self.cancel())
        self.root.bind("<Return>", lambda e: self._commit())

    def _xy(self, e):
        return (e.x_root - self.root.winfo_rootx(), e.y_root - self.root.winfo_rooty())

    def _press(self, e) -> None:
        self.start = self._xy(e)
        self.cv.itemconfigure(self.hint, state="hidden")

    def _drag(self, e) -> None:
        if not self.start:
            return
        x, y = self._xy(e)
        self.show_rect(normalize_rect(self.start[0], self.start[1], x, y, *self.img.size))

    def show_rect(self, r) -> None:
        self.rect = r
        if not r:
            self.sel.place_forget()
            self.cv.itemconfigure(self.size_lbl, state="hidden")
            return
        l, t, w, h = r
        self.sel.place(x=l, y=t, width=w, height=h)
        self.sel.coords(self.sel_img, -l, -t)
        self.cv.coords(self.size_lbl, l, max(14, t - 4))
        self.cv.itemconfigure(self.size_lbl, text=f"{w} × {h}", state="normal")

    def _release(self, e) -> None:
        if not self.start:
            return
        x, y = self._xy(e)
        self.show_rect(normalize_rect(self.start[0], self.start[1], x, y, *self.img.size))
        self.start = None
        self._commit()

    def _commit(self) -> None:
        if not self.rect:
            self.cv.itemconfigure(self.hint, state="normal")
            return
        try:
            self.finish(save_crop(self.img, self.rect, self.out_path))
        except FileExistsError:
            self.finish({"ok": False, "reason": "error", "detail": "File already exists"})
        except Exception as exc:
            self.finish({"ok": False, "reason": "error", "detail": str(exc)})

    def cancel(self) -> None:
        self.finish({"ok": False, "reason": "cancelled", "detail": ""})

    def finish(self, res: dict) -> None:
        if self.result is None:
            self.result = res
            self.root.after(0, self.root.destroy)

    def run(self) -> dict:
        self.root.deiconify()
        self.root.lift()
        self.root.after(50, lambda: (self.root.focus_force(), self.cv.focus_set()))
        auto = os.environ.get(ENV_AUTODRAG, "").strip()
        if auto:
            try:
                x1, y1, x2, y2 = (float(v) for v in auto.split(","))
                ox, oy = self.root.winfo_rootx(), self.root.winfo_rooty()

                def _auto():
                    self.show_rect(normalize_rect(x1 - ox, y1 - oy, x2 - ox, y2 - oy, *self.img.size))
                    self._commit()
                self.root.after(500, _auto)
            except ValueError:
                pass
        try:
            limit = float(os.environ.get(ENV_TIMEOUT, "") or DEFAULT_TIMEOUT_S)
        except ValueError:
            limit = DEFAULT_TIMEOUT_S
        self.root.after(int(limit * 1000), self.cancel)
        self.root.mainloop()
        return self.result or {"ok": False, "reason": "cancelled", "detail": ""}


def _arg(argv, name: str) -> str:
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError):
        return ""


def main(argv=None) -> int:
    import qr_pick
    argv = list(sys.argv[1:] if argv is None else argv)
    out = _arg(argv, "--out")
    if not out or not os.path.isabs(out):
        qr_pick._emit({"ok": False, "reason": "error", "detail": "Missing --out path"})
        return 2
    qr_pick._set_dpi_aware()
    try:
        import mss  # noqa: F401
        import tkinter  # noqa: F401
        from PIL import ImageTk  # noqa: F401
    except Exception as exc:
        qr_pick._emit({"ok": False, "reason": "unavailable", "detail": str(exc)})
        return 2
    try:
        img, origin = qr_pick.grab_virtual_screen()
        res = RegionOverlay(img, origin, out).run()
    except Exception as exc:
        res = {"ok": False, "reason": "error", "detail": str(exc)}
    qr_pick._emit(res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
