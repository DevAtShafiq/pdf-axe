# -*- coding: utf-8 -*-
"""
record_demo.py — film the real Office Axe desktop app for the product video.

    python promotion/record_demo.py                  # full take → promotion/work/run-*/
    python promotion/record_demo.py --shots 1,2      # only some shots (rehearsal)
    python promotion/record_demo.py --no-capture     # rehearse without FFmpeg

What it does
1. Generates fresh fictional demo files (make_demo_data.py) in a new run folder.
2. Backs up the app's shared settings file (restored at the end, always).
3. Starts a *separate* Office Axe instance (`main_webview.py`) with
   - a demo test-mode subscription file inside the run folder,
   - Chromium remote debugging on a private port — used only to locate
     controls and wait for the UI; every click and keystroke is real OS input.
4. Places the window at exactly 1920×1080 and films it with FFmpeg gdigrab.
5. Performs the storyboard shots and logs every step to events.json:
   {t, type, ...} with rectangles in video coordinates (for zooms/callouts).

Never point it at real folders: it only opens its own demo-data folder.
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

W, H, FPS = 1920, 1080, 30
CDP_PORT = 9360
SETTINGS = os.path.join(ROOT, "sfm_settings.json")


def demo_data_root() -> str:
    """A new, short folder for this take's demo files (never an existing one)."""
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
        self.origin = (0, 0)       # video (0,0) on screen
        self.client = (0, 0)       # web page (0,0) on screen

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
        env = dict(os.environ)
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={CDP_PORT}"
        env["PYTHONUTF8"] = "1"
        log = open(os.path.join(self.run, "app.log"), "w", encoding="utf-8")
        self.app = subprocess.Popen([sys.executable, os.path.join(ROOT, "main_webview.py")],
                                    cwd=ROOT, env=env, stdout=log, stderr=log)
        self.hwnd = D.find_window("Office Axe", self.app.pid, timeout=40)
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        for _ in range(60):
            try:
                br = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
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
        """Place the window and make sure it stays put: the app maximises
        itself once its page has loaded (sometimes a few seconds late), so
        keep re-placing until the size holds for 3 s."""
        for attempt in range(8):
            self._place_once()
            time.sleep(3.0)
            l, t, r, b = D.frame_bounds(self.hwnd)
            if (l, t, r - l, b - t) == (self.origin[0], self.origin[1], W, H):
                return
            print(f"  window moved to {(l, t, r - l, b - t)}, placing again", flush=True)
        raise RuntimeError("the app window keeps moving")

    def ensure_placed(self):
        """If anything moved the window, put it straight back (and note it)."""
        l, t, r, b = D.frame_bounds(self.hwnd)
        if (l, t, r - l, b - t) == (self.origin[0], self.origin[1], W, H):
            return
        print(f"  window moved to {(l, t, r - l, b - t)} — putting it back", flush=True)
        D.place_window(self.hwnd, self.origin[0], self.origin[1], W, H, topmost=True)
        self.client = D.client_origin(self.hwnd)
        self.ev("window_restored", was=[l, t, r - l, b - t])
        time.sleep(0.4)
        l, t, r, b = D.frame_bounds(self.hwnd)
        if (l, t, r - l, b - t) != (self.origin[0], self.origin[1], W, H):
            raise RuntimeError(f"the app window moved to {(l, t, r - l, b - t)} during filming")

    def _place_once(self):
        sw, sh = D.user32.GetSystemMetrics(0), D.user32.GetSystemMetrics(1)
        x, y = max(0, (sw - W) // 2), max(0, (sh - H) // 2)
        l, t, r, b = D.place_window(self.hwnd, x, y, W, H, topmost=True)
        if (r - l, b - t) != (W, H):
            raise RuntimeError(f"window is {(r - l, b - t)}, wanted {(W, H)}")
        self.origin = (l, t)
        self.client = D.client_origin(self.hwnd)
        D.focus(self.hwnd)
        time.sleep(0.5)

    def stage(self, folder):
        """Light theme, clean list, the demo folder open — before filming."""
        p = self.page
        p.evaluate("() => { try { App.applyTheme('light'); } catch (e) {} }")
        p.evaluate("() => { if (window.Subscription) { const g = document.getElementById('sub-gate'); if (g) g.classList.add('hidden'); } }")
        p.evaluate("() => App.switchPanel('workspace')")
        p.evaluate("() => FileTree.setSort && FileTree.setSort('name', true)")
        p.evaluate("f => FileTree.loadFolder(f)", folder)
        time.sleep(1.5)

    # ── capture ──────────────────────────────────────────────────────────────
    def start_capture(self):
        out = os.path.join(self.run, "raw.mp4")
        if self.capture:
            cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "gdigrab", "-framerate", str(FPS), "-draw_mouse", "1",
                   "-offset_x", str(self.origin[0]), "-offset_y", str(self.origin[1]),
                   "-video_size", f"{W}x{H}", "-i", "desktop",
                   "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", out]
            self.ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=open(os.path.join(self.run, "ffmpeg.log"), "w"))
            time.sleep(0.6)            # gdigrab opens the desktop and starts grabbing
        self.t0 = time.time()
        self.ev("start", width=W, height=H, fps=FPS)
        time.sleep(0.8)
        self.sync_flash()

    def sync_flash(self):
        """A small magenta square for 0.4 s in the page's top-left corner. The
        editor finds it in the video to line the event log up with the frames
        (it happens before shot 1, so it is never in the final cut)."""
        dx, dy = self.client[0] - self.origin[0], self.client[1] - self.origin[1]
        self.page.evaluate("""() => { const d = document.createElement('div'); d.id = 'oa-sync';
            d.style.cssText = 'position:fixed;left:0;top:0;width:48px;height:48px;background:#ff00ff;z-index:2147483647';
            document.body.appendChild(d); }""")
        self.ev("sync", x=dx + 24, y=dy + 24)
        time.sleep(0.4)
        self.page.evaluate("() => document.getElementById('oa-sync')?.remove()")
        time.sleep(0.8)

    def stop_capture(self):
        self.ev("end")
        if self.ff:
            time.sleep(0.4)
            try:
                self.ff.communicate(b"q", timeout=30)
            except Exception:
                self.ff.kill()

    # ── helpers ──────────────────────────────────────────────────────────────
    def now(self):
        return round(time.time() - self.t0, 3) if self.t0 else 0.0

    def ev(self, type_, **kw):
        e = {"t": self.now(), "type": type_, **kw}
        self.events.append(e)
        print(f"  {e['t']:7.2f}s  {type_}  {kw.get('name', kw.get('text', ''))}", flush=True)
        return e

    def rect(self, selector, nth=0):
        """Element rectangle in video coordinates [x, y, w, h] (or None)."""
        r = self.page.evaluate("""([s, n]) => { const els = document.querySelectorAll(s); const el = els[n];
            if (!el) return null; const b = el.getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; }""",
                               [selector, nth])
        if not r:
            return None
        dx, dy = self.client[0] - self.origin[0], self.client[1] - self.origin[1]
        return [round(r[0] + dx), round(r[1] + dy), round(r[2]), round(r[3])]

    def rect_union(self, selector):
        r = self.page.evaluate("""s => { const b = [...document.querySelectorAll(s)].map(e => e.getBoundingClientRect());
            if (!b.length) return null; const l = Math.min(...b.map(x => x.left)), t = Math.min(...b.map(x => x.top));
            return [l, t, Math.max(...b.map(x => x.right)) - l, Math.max(...b.map(x => x.bottom)) - t]; }""", selector)
        if not r:
            return None
        dx, dy = self.client[0] - self.origin[0], self.client[1] - self.origin[1]
        return [round(r[0] + dx), round(r[1] + dy), round(r[2]), round(r[3])]

    def rect_text(self, selector, text):
        r = self.page.evaluate("""([s, t]) => { const el = [...document.querySelectorAll(s)]
              .find(e => e.offsetParent !== null && e.textContent.trim().toLowerCase().includes(t.toLowerCase()));
            if (!el) return null; el.scrollIntoView({block: 'nearest'}); const b = el.getBoundingClientRect();
            return [b.left, b.top, b.width, b.height]; }""", [selector, text])
        if not r:
            raise RuntimeError(f"not found: {selector} containing {text!r}")
        dx, dy = self.client[0] - self.origin[0], self.client[1] - self.origin[1]
        return [round(r[0] + dx), round(r[1] + dy), round(r[2]), round(r[3])]

    def screen(self, r, fx=0.5, fy=0.5):
        return self.origin[0] + r[0] + r[2] * fx, self.origin[1] + r[1] + r[3] * fy

    def click_rect(self, r, name="", right=False, double=False, fx=0.5, fy=0.5):
        self.ensure_placed()
        x, y = self.screen(r, fx, fy)
        D.move(x, y)
        time.sleep(0.12)
        self.ev("click", name=name, x=round(x - self.origin[0]), y=round(y - self.origin[1]),
                rect=r, right=right)
        D.click(right=right, double=double)

    def click_sel(self, selector, name="", nth=0, **kw):
        self.page.wait_for_selector(selector, state="visible")
        r = self.rect(selector, nth)
        self.click_rect(r, name or selector, **kw)
        return r

    def click_text(self, selector, text, **kw):
        r = self.rect_text(selector, text)
        self.click_rect(r, text, **kw)
        return r

    def press(self, k, *mods, label=None):
        self.ev("key", text=label or "+".join([m.capitalize() for m in mods] + [k.capitalize() if len(k) > 1 else k.upper()]))
        D.key(k, *mods)

    def wait(self, s):
        time.sleep(s)

    def row(self, name):
        """A file-list row by its name."""
        return self.rect_text("#filelist-list .file-item", name)

    # ── shots ────────────────────────────────────────────────────────────────
    def shot1_folders(self, data):
        p = self.page
        self.ev("section", n=1, name="folders")
        self.wait(1.0)
        self.click_sel("#btn-new-folder-menu", "New folder menu")
        self.wait(0.6)
        self.click_text("#ctx-menu .ctx-item", "from a list")
        p.wait_for_selector("#nfl-text", state="visible")
        self.wait(0.4)
        self.ev("focus", name="dialog", rect=self.rect(".modal-overlay .modal"))
        self.wait(0.3)
        self.click_sel("#nfl-text", "names box")
        self.ev("typing", name="names", rect=self.rect("#nfl-text"))
        D.type_text("\n".join(data["students"]), cps=22)
        self.wait(0.4)
        self.ev("focus", name="preview", rect=self.rect("#nfl-list"))
        self.wait(1.8)
        btn = p.evaluate("""() => { const b = [...document.querySelectorAll('.modal-overlay .modal-footer .btn-primary')].pop();
                                   return b ? b.textContent.trim() : ''; }""")
        self.click_text(".modal-overlay .modal-footer .btn-primary", btn.split()[0] if btn else "Create")
        p.wait_for_selector("#nfl-text", state="detached")
        self.wait(0.6)
        r = self.rect("#filelist-list")
        self.ev("focus", name="created", rect=r)
        self.wait(2.0)

    def shot2_preview_rename(self, data):
        p = self.page
        self.ev("section", n=2, name="preview")
        self.wait(0.8)
        self.click_rect(self.row("Documents received"), "Documents received", double=True)
        self.wait(1.2)
        self.click_rect(self.row("Study plan.docx"), "Study plan.docx")
        self.wait(0.6)
        self.ev("focus", name="docx preview", rect=self.rect("#pane-preview"))
        self.wait(2.2)
        self.click_rect(self.row("scan_0012.pdf"), "scan_0012.pdf")
        self.wait(0.6)
        self.ev("focus", name="pdf preview", rect=self.rect("#pane-preview"))
        self.wait(1.8)
        self.press("f2")
        self.wait(0.6)
        self.press("a", "ctrl")
        D.type_text("bachelor cert", cps=9)
        self.wait(1.0)
        dd = p.evaluate("""() => { const d = [...document.querySelectorAll('.rt-dropdown, .inline-rename-suggestions')]
                                    .find(e => e.offsetParent !== null); if (!d) return null;
                                    const b = d.getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; }""")
        if dd:
            dx, dy = self.client[0] - self.origin[0], self.client[1] - self.origin[1]
            self.ev("focus", name="suggestions", rect=[round(dd[0] + dx), round(dd[1] + dy), round(dd[2]), round(dd[3])])
        self.wait(1.4)
        self.click_text(".rt-dropdown .rt-item", "학사 학위증명서 (아포스티유 포함)")
        self.wait(0.5)
        if p.evaluate("!!document.querySelector('input.inline-rename:focus')"):
            self.press("enter")
        self.wait(1.8)

    def shot3_convert(self, data):
        p = self.page
        self.ev("section", n=3, name="convert")
        self.wait(0.8)
        self.click_rect(self.row("Study plan.docx"), "Study plan.docx")
        self.wait(0.3)
        r = self.row("Recommendation letter.docx")
        x, y = self.screen(r)
        D.move(x, y)
        self.ev("click", name="ctrl+click Recommendation letter.docx", x=round(x - self.origin[0]), y=round(y - self.origin[1]), rect=r)
        D._send(D._key(D.VK["ctrl"]))
        D.click()
        D._send(D._key(D.VK["ctrl"], flags=D.KEY_UP))
        self.ev("key", text="Ctrl + click")
        self.wait(0.8)
        self.click_rect(r, "context menu", right=True)
        self.wait(0.7)
        self.click_text("#ctx-menu .ctx-item", "to PDF")
        p.wait_for_selector(".task-card", timeout=15000)
        self.wait(0.3)
        self.ev("focus", name="task card", rect=self.rect(".task-card"))
        p.wait_for_selector(".task-card.is-done, .task-card.is-failed", timeout=120000)
        self.wait(0.2)
        self.ev("focus", name="task done", rect=self.rect(".task-card"))
        self.wait(2.2)

    def shot4_arrange(self, data):
        p = self.page
        self.ev("section", n=4, name="arrange")
        self.wait(0.8)
        self.click_rect(self.row("Transcript.pdf"), "Transcript.pdf")
        self.wait(1.2)
        self.click_sel("#qa-pdf-arrange", "Arrange")
        p.wait_for_selector(".pt-overlay .pt-tile .pt-thumb img, .pt-overlay .pt-tile .pt-thumb canvas", timeout=20000)
        self.wait(1.2)
        self.ev("focus", name="page grid", rect=self.rect_union(".pt-overlay .pt-tile"))
        tile = lambda i: self.rect(".pt-overlay .pt-tile .pt-thumb", i)
        self.click_rect(tile(5), "blank page")
        self.wait(0.5)
        self.press("delete", label="Del")
        self.wait(1.3)
        self.press("z", "ctrl")
        self.wait(1.4)
        self.click_rect(tile(5), "blank page again")
        self.press("delete", label="Del")
        self.wait(1.0)
        self.click_rect(tile(2), "sideways page")
        self.wait(0.5)
        self.press("l", label="L  rotate left")
        self.wait(1.3)
        a, b = tile(4), tile(0)
        ax, ay = self.screen(a)
        bx, by = self.screen(b, 0.15, 0.5)
        self.ev("drag", name="cover letter to front", rect=a, to=[round(bx - self.origin[0]), round(by - self.origin[1])])
        D.drag(ax, ay, bx, by, duration=1.2)
        self.wait(1.4)
        self.ev("focus", name="arranged", rect=self.rect_union(".pt-overlay .pt-tile"))
        self.click_sel("#pt-a-save", "Save")
        p.wait_for_selector(".pt-overlay", state="detached", timeout=30000)
        self.wait(2.5)

    # ── run ──────────────────────────────────────────────────────────────────
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
        if self.app and self.app.poll() is None:
            self.app.terminate()
            try:
                self.app.wait(10)
            except Exception:
                self.app.kill()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="1,2,3,4")
    ap.add_argument("--no-capture", action="store_true")
    ap.add_argument("--run", default="")
    ap.add_argument("--data-root", default="", help="where the fictional demo files go "
                    "(default: a new 'Documents\Office Axe Demo N' folder, so the paths on screen stay short)")
    a = ap.parse_args(argv)
    run = os.path.abspath(a.run or os.path.join(HERE, "work", time.strftime("run-%Y%m%d-%H%M%S")))
    os.makedirs(run, exist_ok=True)
    data = make_demo_data.build(a.data_root or demo_data_root())
    backup = os.path.join(run, "sfm_settings.backup.json")
    if os.path.exists(SETTINGS):
        shutil.copy2(SETTINGS, backup)
    rec = Recorder(run, capture=not a.no_capture)
    ok = False
    try:
        rec.prepare_subscription()
        rec.launch()
        rec.place()
        rec.stage(data["app"])
        time.sleep(2.5)                # let the window settle before filming
        rec.ensure_placed()
        rec.start_capture()
        shots = {"1": rec.shot1_folders, "2": rec.shot2_preview_rename,
                 "3": rec.shot3_convert, "4": rec.shot4_arrange}
        for s in a.shots.split(","):
            shots[s.strip()](data)
        rec.wait(0.8)
        ok = True
    except Exception:
        try:
            rec.page.screenshot(path=os.path.join(run, "failure.png"))
            print("rows:", rec.page.evaluate("[...document.querySelectorAll('#filelist-list .file-item')].map(e => e.textContent.trim().slice(0, 40))"))
        except Exception:
            pass
        raise
    finally:
        rec.stop_capture()
        with open(os.path.join(run, "events.json"), "w", encoding="utf-8") as fh:
            json.dump({"ok": ok, "events": rec.events, "data": data}, fh, ensure_ascii=False, indent=1)
        rec.close()
        if os.path.exists(backup):
            shutil.copy2(backup, SETTINGS)       # put the owner's settings back
    print(run)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
