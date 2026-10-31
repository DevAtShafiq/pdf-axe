# -*- coding: utf-8 -*-
"""
record_demo.py — film the real Office Axe desktop app, one chapter per take.

    python promotion/record_demo.py --run promotion/work/full1            # every chapter
    python promotion/record_demo.py --run promotion/work/full1 --chapters 6,7
    python promotion/record_demo.py --run promotion/work/full1 --chapters 3 --no-capture   # rehearse

Each chapter (see chapters.py) becomes   <run>/ch<NN>/raw.mp4 + events.json.
Re-running a chapter replaces only that chapter's take (a new folder name is
used if one exists already, nothing is deleted) and the editor always picks
the newest successful take per chapter.

How it stays safe
- A *separate* Office Axe instance (`main_webview.py`) with a demo test-mode
  subscription file and Chromium remote debugging on a private port. The port
  is used to locate controls, wait for the UI and do invisible setup between
  takes; every visible click, drag and keystroke is real OS input, so the real
  cursor is filmed.
- Fictional demo files only (make_demo_data.py), written to a new
  `Documents\\Office Axe Demo N` folder so on-screen paths stay short.
- The app's shared `sfm_settings.json` is backed up and restored afterwards.
- FFmpeg gdigrab films the window, placed topmost at exactly W×H (16:9) above
  the taskbar.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import desktop as D  # noqa: E402
import make_demo_data  # noqa: E402

W, H, FPS = 2474, 1392, 30          # 16:9, the full screen width above the taskbar
CDP_PORT = 9360                     # first choice; a free port is picked at launch


def free_port(start=9360, end=9399):
    import socket
    for port in range(start, end):
        with socket.socket() as sk:
            try:
                sk.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free debugging port")
SETTINGS = os.path.join(ROOT, "sfm_settings.json")


def demo_data_root() -> str:
    """A new, short folder for this run's demo files (never an existing one)."""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    n = 1
    while os.path.exists(os.path.join(docs, f"Office Axe Demo {n}")):
        n += 1
    return os.path.join(docs, f"Office Axe Demo {n}")


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def new_dir(path: str) -> str:
    """*path*, or path-2, path-3 … if it exists (old takes are kept)."""
    p, n = path, 2
    while os.path.exists(p):
        p = f"{path}-{n}"
        n += 1
    os.makedirs(p)
    return p


class Recorder:
    def __init__(self, run_dir: str, capture: bool = True):
        self.run = run_dir
        self.capture = capture
        self.events: list = []
        self.t0 = None
        self.app = None
        self.ff = None
        self.page = None
        self.hwnd = None
        self.take = None
        self.origin = (0, 0)       # video (0,0) on screen
        self.client = (0, 0)       # web page (0,0) on screen
        import threading
        self._win_lock = threading.RLock()
        self._watching = False
        self._watcher = None

    # ── setup ────────────────────────────────────────────────────────────────
    def prepare_subscription(self):
        """A test-mode plan so the demo instance opens unlocked (no real payment)."""
        os.environ["OFFICEAXE_SUBSCRIPTION_FILE"] = os.path.join(self.run, "demo_subscription.json")
        import subscription as sub
        m = sub.SubscriptionManager(gateway=sub.PlaceholderGateway(sandbox=True))
        if not m.state()["access"]:
            r = m.start_checkout("yearly", "bkash",
                                 {"name": "Demo Office", "email": "demo@example.com", "phone": "01700000000"})
            m.confirm_checkout(r["checkout_id"])

    def launch(self):
        self.port = free_port()
        env = dict(os.environ)
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={self.port}"
        env["PYTHONUTF8"] = "1"
        log = open(os.path.join(self.run, "app.log"), "a", encoding="utf-8")
        self.app = subprocess.Popen([sys.executable, os.path.join(ROOT, "main_webview.py")],
                                    cwd=ROOT, env=env, stdout=log, stderr=log)
        self.hwnd = D.find_window("Office Axe", self.app.pid, timeout=40)
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        for _ in range(60):
            try:
                br = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
                pages = [p for c in br.contexts for p in c.pages if "index.html" in p.url]
                if pages:
                    self.page = pages[0]
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not self.page:
            raise RuntimeError("could not attach to the demo app")
        self.page.set_default_timeout(20000)
        self.page.wait_for_function("typeof FileTree !== 'undefined' && typeof Subscription !== 'undefined'")
        time.sleep(3)          # the window maximises itself once loaded; place it after that

    def place(self):
        """Place the window and make sure it stays put (the app maximises
        itself once loaded, sometimes a few seconds late)."""
        for _ in range(8):
            self._place_once()
            time.sleep(3.0)
            l, t, r, b = D.frame_bounds(self.hwnd)
            if (l, t, r - l, b - t) == (self.origin[0], self.origin[1], W, H):
                return
            print(f"  window moved to {(l, t, r - l, b - t)}, placing again", flush=True)
        raise RuntimeError("the app window keeps moving")

    def _placed(self):
        l, t, r, b = D.frame_bounds(self.hwnd)
        return (l, t, r - l, b - t) == (self.origin[0], self.origin[1], W, H), (l, t, r - l, b - t)

    def ensure_placed(self):
        """If anything moved / minimised the window, put it straight back.

        While filming the gap is logged as window_lost / window_back; the
        editor freezes the last good frame over it, so nothing that was
        behind the window is ever shown."""
        with self._win_lock:
            ok, was = self._placed()
            if ok:
                return
            print(f"  window moved to {was} — putting it back", flush=True)
            if self.t0 is not None:
                self.ev("window_lost", was=list(was))
            D.place_window(self.hwnd, self.origin[0], self.origin[1], W, H, topmost=True)
            self.client = D.client_origin(self.hwnd)
            D.focus(self.hwnd)
            time.sleep(0.3)
            if self.t0 is not None:
                self.ev("window_back")

    def _watch(self):
        """Background watchdog during a take (10× per second)."""
        while self._watching:
            try:
                if not self._placed()[0]:
                    self.ensure_placed()
            except Exception:
                pass
            time.sleep(0.1)

    def _place_once(self):
        sw = D.user32.GetSystemMetrics(0)
        x, y = max(0, (sw - W) // 2), 0
        l, t, r, b = D.place_window(self.hwnd, x, y, W, H, topmost=True)
        if (r - l, b - t) != (W, H):
            raise RuntimeError(f"window is {(r - l, b - t)}, wanted {(W, H)}")
        self.origin = (l, t)
        self.client = D.client_origin(self.hwnd)
        # keep every mouse path inside the window (never over the taskbar);
        # native helper windows (file dialog, QR / screenshot pickers) open inside it too
        D.BOUNDS = (l, t + 30, l + W, t + H - 8)
        D.focus(self.hwnd)
        time.sleep(0.5)

    def js(self, code, arg=None):
        """Invisible setup through the debugging port (never a visible action)."""
        return self.page.evaluate(code, arg) if arg is not None else self.page.evaluate(code)

    def close_overlays(self):
        """Close any dialog / editor left open, without saving (setup only)."""
        for _ in range(4):
            n = self.js("""() => { const ov = [...document.querySelectorAll('.modal-overlay, .st-overlay, .pt-overlay, .an-overlay')]
                          .filter(e => e.isConnected); return ov.length; }""")
            if not n:
                return
            D.key("esc")
            time.sleep(0.4)
            self.js("""() => { const b = [...document.querySelectorAll('.modal-overlay.dlg-top .modal-footer .btn')]
                        .find(x => /discard|don.t save|close|cancel|no/i.test(x.textContent)); if (b) b.click(); }""")
            time.sleep(0.3)

    def stage(self, folder, theme="light"):
        """Before each take: clean UI, the chapter's folder open, nothing selected."""
        p = self.page
        self.close_overlays()
        p.evaluate("t => { try { App.applyTheme(t); } catch (e) {} }", theme)
        p.evaluate("() => { const g = document.getElementById('sub-gate'); if (g) g.classList.add('hidden'); }")
        p.evaluate("() => App.switchPanel('workspace')")
        p.evaluate("() => { try { FileTree.setViewMode('list'); } catch (e) {} }")
        p.evaluate("() => FileTree.setSort && FileTree.setSort('name', true)")
        p.evaluate("() => { document.querySelectorAll('.task-card, .toast').forEach(e => e.remove()); }")
        p.evaluate("f => FileTree.loadFolder(f)", folder)
        time.sleep(1.2)
        p.evaluate("() => { try { FileTree.selectPaths([]); } catch (e) {} }")
        self.ensure_placed()
        D.move(self.origin[0] + W * 0.62, self.origin[1] + H * 0.55, duration=0.3)
        time.sleep(0.6)

    # ── capture (one take per chapter) ───────────────────────────────────────
    def start_take(self, name):
        self.ensure_placed()
        D.focus(self.hwnd)
        time.sleep(0.3)
        self.take = new_dir(os.path.join(self.run, name))
        self.events = []
        if self.capture:
            cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "gdigrab", "-framerate", str(FPS), "-draw_mouse", "1",
                   "-offset_x", str(self.origin[0]), "-offset_y", str(self.origin[1]),
                   "-video_size", f"{W}x{H}", "-i", "desktop",
                   "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p",
                   os.path.join(self.take, "raw.mp4")]
            self.ff = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                       stderr=open(os.path.join(self.take, "ffmpeg.log"), "w"))
            time.sleep(0.8)
        self.t0 = time.time()
        self.ev("start", width=W, height=H, fps=FPS)
        import threading
        self._watching = True
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._watcher.start()
        time.sleep(0.5)
        self.sync_flash()

    def sync_flash(self):
        """A small magenta square for 0.4 s in the page's top-left corner; the
        editor finds it to line the event log up with the frames (it comes
        before the chapter starts, so it is never in the final cut)."""
        dx, dy = self.client[0] - self.origin[0], self.client[1] - self.origin[1]
        self.js("""() => { const d = document.createElement('div'); d.id = 'oa-sync';
            d.style.cssText = 'position:fixed;left:0;top:0;width:48px;height:48px;background:#ff00ff;z-index:2147483647';
            document.body.appendChild(d); }""")
        self.ev("sync", x=dx + 24, y=dy + 24)
        time.sleep(0.4)
        self.js("() => document.getElementById('oa-sync')?.remove()")
        time.sleep(0.9)

    def end_take(self, ok, meta):
        self._watching = False
        if self._watcher:
            self._watcher.join(timeout=1)
        self.ev("end")
        if self.ff:
            time.sleep(0.4)
            try:
                self.ff.communicate(b"q", timeout=60)
            except Exception:
                self.ff.kill()
            self.ff = None
        with open(os.path.join(self.take, "events.json"), "w", encoding="utf-8") as fh:
            json.dump({"ok": ok, "events": self.events, **meta}, fh, ensure_ascii=False, indent=1)

    # ── helpers used by chapters.py ──────────────────────────────────────────
    def now(self):
        return round(time.time() - self.t0, 3) if self.t0 else 0.0

    def ev(self, type_, **kw):
        e = {"t": self.now(), "type": type_, **kw}
        self.events.append(e)
        print(f"  {e['t']:7.2f}s  {type_}  {kw.get('name', kw.get('text', ''))}", flush=True)
        return e

    def _video(self, r):
        dx, dy = self.client[0] - self.origin[0], self.client[1] - self.origin[1]
        return [round(r[0] + dx), round(r[1] + dy), round(r[2]), round(r[3])]

    def rect(self, selector, nth=0):
        """Element rectangle in video coordinates [x, y, w, h] (or None)."""
        r = self.js("""([s, n]) => { const el = [...document.querySelectorAll(s)].filter(e => e.offsetParent !== null || e.getClientRects().length)[n];
            if (!el) return null; const b = el.getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; }""",
                    [selector, nth])
        return self._video(r) if r else None

    def rect_union(self, selector):
        r = self.js("""s => { const b = [...document.querySelectorAll(s)].filter(e => e.getClientRects().length).map(e => e.getBoundingClientRect());
            if (!b.length) return null; const l = Math.min(...b.map(x => x.left)), t = Math.min(...b.map(x => x.top));
            return [l, t, Math.max(...b.map(x => x.right)) - l, Math.max(...b.map(x => x.bottom)) - t]; }""", selector)
        return self._video(r) if r else None

    def rect_text(self, selector, text, exact=False):
        r = self.js("""([s, t, ex]) => { const els = [...document.querySelectorAll(s)].filter(e => e.offsetParent !== null || e.getClientRects().length);
            const norm = x => x.textContent.replace(/\\s+/g, ' ').trim().toLowerCase();
            const el = els.find(e => ex ? norm(e) === t.toLowerCase() : norm(e).includes(t.toLowerCase()));
            if (!el) return null; el.scrollIntoView({block: 'nearest'}); const b = el.getBoundingClientRect();
            return [b.left, b.top, b.width, b.height]; }""", [selector, text, exact])
        if not r:
            raise RuntimeError(f"not found: {selector} containing {text!r}")
        return self._video(r)

    def screen(self, r, fx=0.5, fy=0.5):
        return self.origin[0] + r[0] + r[2] * fx, self.origin[1] + r[1] + r[3] * fy

    def click_rect(self, r, name="", right=False, double=False, fx=0.5, fy=0.5, sound=True):
        self.ensure_placed()
        x, y = self.screen(r, fx, fy)
        D.move(x, y)
        time.sleep(0.12)
        self.ev("click", name=name, x=round(x - self.origin[0]), y=round(y - self.origin[1]),
                rect=r, right=right, double=double, sound=sound)
        D.click(right=right, double=double)

    def click_sel(self, selector, name="", nth=0, **kw):
        self.page.wait_for_selector(selector, state="visible")
        r = self.rect(selector, nth)
        self.click_rect(r, name or selector, **kw)
        return r

    def click_text(self, selector, text, exact=False, **kw):
        r = self.rect_text(selector, text, exact)
        self.click_rect(r, text, **kw)
        return r

    def press(self, k, *mods, label=None, show=True):
        self.ev("key", text=label or "+".join([m.capitalize() for m in mods] + [k.capitalize() if len(k) > 1 else k.upper()]),
                show=show)
        D.key(k, *mods)

    def type(self, text, cps=13.0, name="", rect=None):
        """Type like a person; the editor adds soft key sounds for the span."""
        e = self.ev("typing", name=name, rect=rect, chars=len(text))
        D.type_text(text, cps=cps)
        e["dur"] = round(self.now() - e["t"], 3)

    def drag(self, r0, r1, name="", f0=(0.5, 0.5), f1=(0.5, 0.5), duration=1.1):
        x0, y0 = self.screen(r0, *f0)
        x1, y1 = self.screen(r1, *f1)
        self.ev("drag", name=name, rect=r0, frm=[round(x0 - self.origin[0]), round(y0 - self.origin[1])],
                to=[round(x1 - self.origin[0]), round(y1 - self.origin[1])])
        D.drag(x0, y0, x1, y1, duration=duration)

    def focus(self, name, rect, **kw):
        """Point the camera / a callout at a rectangle (see edit_demo.py)."""
        if rect:
            self.ev("focus", name=name, rect=rect, **kw)

    def wide(self, name=""):
        """Ask the editor's camera to show the whole window."""
        self.ev("wide", name=name)

    def success(self, name=""):
        """Something finished (a file was made): the editor plays a chime."""
        self.ev("success", name=name)

    def wait(self, s):
        time.sleep(s)

    def wait_for(self, js_condition, timeout=30):
        self.page.wait_for_function(js_condition, timeout=timeout * 1000)

    def row(self, name):
        """A file-list row by its (start of) name."""
        return self.rect_text("#filelist-list .file-item", name)

    def file_dialog(self, path, timeout=10):
        """Type a path into a native Windows Open dialog (real keyboard input)."""
        import win32gui
        end = time.time() + timeout
        hwnd = None
        while time.time() < end and not hwnd:
            def cb(h, _):
                nonlocal hwnd
                if win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == "#32770":
                    hwnd = h
            win32gui.EnumWindows(cb, None)
            time.sleep(0.2)
        if not hwnd:
            raise RuntimeError("file dialog did not open")
        time.sleep(0.6)
        self.ev("dialog", name="Windows file dialog")
        D.key("n", "alt")                # focus the File name box
        time.sleep(0.2)
        self.type(path, cps=40, name="file name")
        time.sleep(0.3)
        self.press("enter", show=False)
        time.sleep(0.8)

    def close(self):
        try:
            if self.hwnd:
                D.untopmost(self.hwnd)
        except Exception:
            pass
        try:
            if getattr(self, "_pw", None):
                self._pw.stop()
        except Exception:
            pass
        if self.app:
            # End the demo app and its WebView helper processes (a leftover
            # browser process would keep the debugging port and confuse the
            # next run). Only this run's own demo instance is touched.
            subprocess.run(["taskkill", "/PID", str(self.app.pid), "/T", "/F"],
                           capture_output=True)


def main(argv=None) -> int:
    import chapters
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--chapters", default="", help="e.g. 1,2,6 (default: all)")
    ap.add_argument("--no-capture", action="store_true")
    ap.add_argument("--data-root", default="")
    a = ap.parse_args(argv)
    run = os.path.abspath(a.run)
    os.makedirs(run, exist_ok=True)
    data = make_demo_data.build(a.data_root or demo_data_root())
    want = [int(x) for x in a.chapters.split(",") if x.strip()] or [c.n for c in chapters.CHAPTERS]
    # If an earlier run was cut off before it could put the owner's settings
    # back, do that first (the marker holds the backup's path, or "done").
    marker = os.path.join(HERE, "work", "settings_restore_pending.txt")
    if os.path.exists(marker):
        pending = open(marker, encoding="utf-8").read().strip()
        if pending and pending != "done" and os.path.exists(pending):
            shutil.copy2(pending, SETTINGS)
            print(f"restored settings left over from an interrupted run: {pending}")
    backup = os.path.join(run, f"sfm_settings.backup-{time.strftime('%H%M%S')}.json")
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, backup)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(backup)
        # While filming: no personal saved signatures on screen, and recordings /
        # screenshots go to the demo folder (so the owner's recordings list stays
        # private). The backup above is copied back at the end, always.
        with open(SETTINGS, encoding="utf-8") as fh:
            s = json.load(fh)
        s["annot_signatures"] = []
        s["rec_folder"] = os.path.join(data["root"], "Recordings")
        os.makedirs(s["rec_folder"], exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as fh:
            json.dump(s, fh, ensure_ascii=False, indent=2)
    rec = Recorder(run, capture=not a.no_capture)
    failed = []
    try:
        rec.prepare_subscription()
        rec.launch()
        rec.place()
        for ch in chapters.CHAPTERS:
            if ch.n not in want:
                continue
            ok = False
            for attempt in range(1, 3):              # one automatic retake
                print(f"\n=== chapter {ch.n}: {ch.title}" + (" (retake)" if attempt > 1 else ""), flush=True)
                if attempt > 1:
                    data = make_demo_data.build(demo_data_root())   # fresh files for the retake
                try:
                    folder = ch.setup(rec, data)
                    rec.stage(folder or data["inbox"])
                    rec.start_take(f"ch{ch.n:02d}")
                    rec.ev("section", n=ch.n, name=ch.key)
                    rec.wait(0.8)
                    ch.act(rec, data)
                    rec.wait(0.9)
                    ok = True
                except Exception as exc:
                    print(f"  !! chapter {ch.n} failed: {exc}", flush=True)
                    try:
                        rec.page.screenshot(path=os.path.join(rec.take or run, "failure.png"))
                    except Exception:
                        pass
                finally:
                    if rec.t0 is not None:
                        rec.end_take(ok, {"chapter": ch.n, "key": ch.key, "data": data})
                        rec.t0 = None
                if ok:
                    break
            if not ok:
                failed.append(ch.n)
    finally:
        rec.close()
        if os.path.exists(backup):
            shutil.copy2(backup, SETTINGS)       # put the owner's settings back
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write("done")
    print("\nfailed chapters:", failed or "none")
    print(run)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
