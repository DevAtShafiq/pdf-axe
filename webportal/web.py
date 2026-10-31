# -*- coding: utf-8 -*-
"""
web.py — the Office Axe portal as a plain WSGI app (standard library only).

    Public      /                landing page, plans, download
                /download        the installer (file or redirect)
    Admin       /admin           keys overview, new key, search / filter
                /admin/keys/<id> one key: copy, devices, revoke, note
                /admin/activity  what happened, newest first
                /admin/import    bring in the desktop dashboard's keys
                /admin/settings  change password, app public key check
    App         /api/v1/activate | check | deactivate   (JSON, signed answers)
                /api/v1/revoked  withdrawn key ids
"""
from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import time
from datetime import datetime
from http.cookies import SimpleCookie
from typing import Optional
from urllib.parse import parse_qs, quote

CONTACT_EMAIL = os.environ.get("OFFICEAXE_CONTACT_EMAIL", "").strip()

from auth import FAILURE_WINDOW, MAX_FAILURES, AdminAccount, Sessions
from config import Config
from licensing import Licensing
from releases import ReleaseError, ReleaseManager, as_dt
from store import Store, iso

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
MAX_BODY = 256 * 1024

SECURITY_HEADERS = [
    ("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "same-origin"),
]

e = html.escape


# ── request / response ────────────────────────────────────────────────────────

class Request:
    def __init__(self, environ: dict, trust_proxy: bool = False):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        self.path = environ.get("PATH_INFO", "/") or "/"
        self.query = {k: v[0] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        cookie = SimpleCookie()
        try:
            cookie.load(environ.get("HTTP_COOKIE", ""))
        except Exception:
            pass
        self.cookies = {k: m.value for k, m in cookie.items()}
        ip = environ.get("REMOTE_ADDR", "")
        if trust_proxy and environ.get("HTTP_X_FORWARDED_FOR"):
            ip = environ["HTTP_X_FORWARDED_FOR"].split(",")[0].strip()
        self.ip = ip
        self._body: Optional[bytes] = None

    @property
    def body(self) -> bytes:
        if self._body is None:
            try:
                n = int(self.environ.get("CONTENT_LENGTH") or 0)
            except ValueError:
                n = 0
            self._body = self.environ["wsgi.input"].read(min(n, MAX_BODY)) if n > 0 else b""
        return self._body

    @property
    def form(self) -> dict:
        return {k: v[0] for k, v in parse_qs(self.body.decode("utf-8", "replace"), keep_blank_values=True).items()}

    def json(self) -> dict:
        try:
            data = json.loads(self.body.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    @property
    def is_local(self) -> bool:
        return self.environ.get("REMOTE_ADDR", "") in ("127.0.0.1", "::1") and \
            not self.environ.get("HTTP_X_FORWARDED_FOR")


class Response:
    def __init__(self, body=b"", status: str = "200 OK", ctype: str = "text/html; charset=utf-8",
                 headers: Optional[list] = None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status, self.headers = status, [("Content-Type", ctype)] + (headers or [])

    def __call__(self, start_response):
        start_response(self.status, self.headers + SECURITY_HEADERS +
                       [("Content-Length", str(len(self.body)))])
        return [self.body]


class FileResponse:
    """A large file, streamed in chunks, with HTTP Range support (video seeking)."""
    CHUNK = 256 * 1024

    def __init__(self, path: str, ctype: str, range_header: str = ""):
        self.path, self.ctype = path, ctype
        self.size = os.path.getsize(path)
        self.start, self.end, self.status = 0, self.size - 1, "200 OK"
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", (range_header or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else self.size - 1
            else:                                   # last N bytes
                start, end = max(0, self.size - int(m.group(2))), self.size - 1
            end = min(end, self.size - 1)
            if start > end or start >= self.size:
                self.status = "416 Range Not Satisfiable"
            else:
                self.start, self.end, self.status = start, end, "206 Partial Content"

    def __call__(self, start_response):
        if self.status.startswith("416"):
            start_response(self.status, [("Content-Range", f"bytes */{self.size}"), ("Content-Length", "0")]
                           + SECURITY_HEADERS)
            return [b""]
        length = self.end - self.start + 1
        headers = [("Content-Type", self.ctype), ("Content-Length", str(length)), ("Accept-Ranges", "bytes"),
                   ("Cache-Control", "public, max-age=86400")]
        if self.status.startswith("206"):
            headers.append(("Content-Range", f"bytes {self.start}-{self.end}/{self.size}"))
        start_response(self.status, headers + SECURITY_HEADERS)
        return self._iter(length)

    def _iter(self, length):
        with open(self.path, "rb") as fh:
            fh.seek(self.start)
            left = length
            while left > 0:
                chunk = fh.read(min(self.CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk


def redirect(location: str, headers: Optional[list] = None) -> Response:
    return Response(b"", "303 See Other", headers=[("Location", location)] + (headers or []))


def json_response(data: dict, status: str = "200 OK") -> Response:
    return Response(json.dumps(data, ensure_ascii=False), status, "application/json; charset=utf-8",
                    [("Cache-Control", "no-store")])


def not_found() -> Response:
    return Response(page("Not found", "<section class='narrow'><h1>Page not found</h1>"
                    "<p><a href='/'>Back to Office Axe</a></p></section>"), "404 Not Found")


# ── page helpers ──────────────────────────────────────────────────────────────

def fmt_date(s: str) -> str:
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%d %b %Y")
    except ValueError:
        return e(s)


def fmt_time(s: str) -> str:
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%d %b %Y, %H:%M UTC")
    except ValueError:
        return e(s)


ICONS = {
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "rename": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "pdf": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/><path d="M8 15h8M8 18h5"/>',
    "compress": '<path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7"/>',
    "merge": '<path d="M8 3v6a4 4 0 0 0 4 4h0a4 4 0 0 1 4 4v4M16 3v6a4 4 0 0 1-4 4"/>',
    "layers": '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 12 10 5 10-5M2 17l10 5 10-5"/>',
    "sign": '<path d="M3 17c3 0 4-6 7-6s1 6 4 6 3-3 4-3"/><path d="M3 21h18"/>',
    "brush": '<path d="M9.5 14.5 3 21M14 4l6 6-8 8-6-6z"/>',
    "id": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="11" r="2.5"/><path d="M5.5 17c.8-1.8 2-2.7 3.5-2.7s2.7.9 3.5 2.7M15 9h4M15 13h4"/>',
    "sparkles": '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "qr": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M17 20h4M20 17v4"/>',
    "lock": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "record": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5" fill="currentColor"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M14 9l2 2"/>',
    "download": '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/>',
    "play": '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z" fill="currentColor"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "windows": '<path d="M3 5.5 10.5 4.5v7H3zM12 4.3 21 3v8.5h-9zM3 13h7.5v7L3 19zM12 13h9v8l-9-1.2z"/>',
    "wifi-off": '<path d="M2 2l20 20M8.5 16.5a5 5 0 0 1 7 0M5 12.5a10 10 0 0 1 4.5-2.6M19 12.5a10 10 0 0 0-2.3-1.6M12 20h.01"/>',
    "monitor": '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
}


def icon(name: str, size: int = 22) -> str:
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS.get(name, "")}</svg>')


def page(title: str, body: str, admin: Optional[dict] = None, csrf: str = "", description: str = "",
         active: str = "") -> str:
    desc = description or ("Office Axe is a Windows app for office documents: convert to PDF, compress, merge, "
                           "arrange pages, sign, make folders in bulk, passport photos and more.")
    if admin:
        links = [("/admin", "Keys"), ("/admin/releases", "Releases"), ("/admin/guides", "Guides"),
                 ("/admin/activity", "Activity"), ("/admin/import", "Import"), ("/admin/settings", "Settings")]
        on = ' class="on"'
        items = "".join(f'<a href="{h}"{on if active == h else ""}>{t}</a>' for h, t in links)
        nav = f"""
        <nav class="admin-nav" id="site-nav">
          {items}
          <form method="post" action="/admin/logout" class="inline">
            <input type="hidden" name="csrf" value="{e(csrf)}"><button class="link">Log out</button>
          </form>
        </nav>"""
    else:
        links = [("/#features", "Features"), ("/#tour", "Tour"), ("/pricing/", "Pricing"), ("/document/", "Guides")]
        items = "".join(f'<a href="{h}">{t}</a>' for h, t in links)
        nav = f"""
        <nav class="pub-nav" id="site-nav">{items}
          <a class="btn primary sm" href="/download">{icon("download", 16)}Download</a>
        </nav>"""
    foot = "" if admin else f"""
<footer class="site-foot">
  <div class="foot-grid">
    <div>
      <a class="brand" href="/"><span class="logo">OA</span>Office Axe</a>
      <p class="muted small">A Windows app for everyday office documents — PDFs, Word, Excel, PowerPoint, photos and folders.</p>
    </div>
    <div><h4>Product</h4><a href="/#features">Features</a><a href="/#tour">Video tour</a><a href="/pricing/">Pricing</a><a href="/download">Download</a></div>
    <div><h4>Help</h4><a href="/document/">Guides</a><a href="/#activate">Activate a key</a><a href="/#faq">Questions</a></div>
    <div><h4>Pay with</h4><p class="muted small">bKash · Nagad · Visa / Mastercard</p>
      {f'<h4>Contact</h4><a href="mailto:{e(CONTACT_EMAIL)}">{e(CONTACT_EMAIL)}</a>' if CONTACT_EMAIL else ''}</div>
  </div>
  <div class="foot-bottom">© {datetime.now().year} Office Axe · Prices in Bangladeshi taka</div>
</footer>"""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · Office Axe</title>
<meta name="description" content="{e(desc)}">
<meta property="og:title" content="{e(title)} · Office Axe">
<meta property="og:description" content="{e(desc)}">
<meta property="og:image" content="/static/video/poster.jpg">
<meta name="theme-color" content="#2f6bff">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/portal.css?v=5">
<script src="/static/portal.js?v=5" defer></script>
</head><body class="{'admin' if admin else 'public'}">
<a class="skip" href="#main">Skip to content</a>
<header class="top">
  <a class="brand" href="{'/admin' if admin else '/'}"><span class="logo">OA</span>Office Axe{' <small>Admin</small>' if admin else ''}</a>
  <button class="nav-toggle" type="button" aria-controls="site-nav" aria-expanded="false" aria-label="Menu">{icon("menu", 22)}</button>
  {nav}
</header>
<main id="main">{body}</main>
{foot}
</body></html>"""


def pill(status: str) -> str:
    return f'<span class="pill {e(status)}">{e(status)}</span>'


# ── the app ───────────────────────────────────────────────────────────────────

class Portal:
    def __init__(self, config: Optional[Config] = None, store=None):
        self.cfg = config or Config()
        if store is not None:
            self.store = store
        elif self.cfg.store_kind == "firestore":
            from store_firestore import FirestoreStore
            self.store = FirestoreStore()
        else:
            self.store = Store(self.cfg.db_path)
        self.lic = Licensing(self.store, self.cfg.load_seed())
        # on the server the admin login lives in the database (no lasting files there)
        self.account = AdminAccount(self.cfg.admin_file, self.cfg.admin_user,
                                    store=self.store if hasattr(self.store, "get_meta") else None)
        self.sessions = Sessions(self.cfg.session_secret(), self.account)
        # release manager: Firestore on the server (shared with installed apps' Update Now),
        # a small JSON file on this PC
        if hasattr(self.store, "db") and not isinstance(self.store, Store):
            stamp = None
            try:
                from google.cloud import firestore as _fs   # the server stamps the time
                stamp = lambda: _fs.SERVER_TIMESTAMP
            except ImportError:
                pass
            self.releases = ReleaseManager(self.store.db, now=stamp)
        else:
            from minidb import MiniFirestore
            self.releases = ReleaseManager(MiniFirestore(os.path.join(self.cfg.data_dir, "releases.json")))

    # WSGI entry
    def __call__(self, environ, start_response):
        req = Request(environ, self.cfg.trust_proxy)
        try:
            res = self.route(req)
        except Exception as exc:   # never show a stack trace to visitors
            import logging
            logging.getLogger("portal").exception("request failed: %s", exc)
            res = Response(page("Error", "<section class='narrow'><h1>Something went wrong</h1>"
                                "<p>Please try again.</p></section>"), "500 Internal Server Error")
        return res(start_response)

    def route(self, req: Request) -> Response:
        p, m = req.path.rstrip("/") or "/", req.method
        if p == "/" and m == "GET":
            return self.landing()
        if p == "/download":
            return self.download()
        if p == "/document" and m == "GET":
            return self.document_page()
        if p == "/pricing" and m == "GET":
            return self.pricing_page()
        if p == "/login":                       # old release-manager addresses
            return redirect("/admin/login")
        if p == "/dashboard":
            return redirect("/admin/releases")
        if p == "/media/office-axe-demo.mp4" and m in ("GET", "HEAD"):
            if not os.path.isfile(self.cfg.video_file):
                return not_found()
            return FileResponse(self.cfg.video_file, "video/mp4", req.environ.get("HTTP_RANGE", ""))
        if p.startswith("/static/"):
            return self.static(p[len("/static/"):])
        if p == "/healthz":
            return json_response({"ok": True})
        if p.startswith("/api/v1/"):
            return self.api(req, p[len("/api/v1/"):])
        if p.startswith("/admin"):
            return self.admin(req, p, m)
        return not_found()

    # ── public ───────────────────────────────────────────────────────────────
    FEATURES = [
        ("folder", "One workspace", "Browse folders, preview PDFs, Word, Excel and photos, and search as you type."),
        ("list", "A folder for every student", "Paste a list of names and get a numbered folder for each one."),
        ("eye", "Instant preview", "See any document or image before you open it — pages, text and details."),
        ("rename", "Smart rename", "Rename one file or hundreds, with templates and suggestions."),
        ("undo", "Undo everything", "Cut, copy, paste and delete with Ctrl+Z and Ctrl+Y, like Windows Explorer."),
        ("pdf", "Convert to PDF", "Word, Excel, PowerPoint and photos to PDF — or PDF pages back to images."),
        ("compress", "Compress to a size", "Shrink a PDF to the size an upload form accepts, with an estimate first."),
        ("merge", "Merge, split, extract", "Combine PDFs and images, split by pages, or pull out the pages you need."),
        ("layers", "Arrange pages", "Rotate, reorder, delete and zoom into pages; read QR codes on them."),
        ("sign", "Fill, sign and add a photo", "Fill PDF forms, sign, add a passport photo and save a signed copy."),
        ("brush", "Mark up images", "Highlight, draw, add text and arrows on scans and screenshots."),
        ("id", "Passport and visa photos", "Crop to exact sizes like 35 × 45 mm with a face guide."),
        ("sparkles", "AI suit and tie", "Put a formal suit on a photo for applications (uses OpenAI when you choose it)."),
        ("qr", "Read QR codes", "From a file or anywhere on screen, with link safety checks."),
        ("lock", "ZIP and passwords", "Zip folders, unzip archives and lock PDFs or ZIPs with a password."),
        ("record", "Screen recorder", "Record the screen or take screenshots, with your microphone if you like."),
        ("key", "Buy one, get one", "One subscription works on 2 PCs — your office PC and one more; keys activate in seconds."),
    ]

    def landing(self) -> Response:
        try:
            cur = self.releases.current()
        except Exception:
            cur = {}
        ver = str(cur.get("version") or "")
        feats = "".join(f"""
          <article class="feat"><span class="feat-ic">{icon(k)}</span>
            <h3>{e(t)}</h3><p>{e(d)}</p></article>""" for k, t, d in self.FEATURES)
        faq = [
            ("Which computers does it run on?", "Windows 10 and Windows 11 PCs."),
            ("Does it need the internet?", "No — your files are handled on your own PC. A license key is checked online "
             "now and then; without internet Office Axe keeps working for 7 days after the last check."),
            ("How many PCs can I use?", "Buy one, get one: one subscription covers 2 PCs. A review or trial key may allow 1."),
            ("Are my documents uploaded anywhere?", "No. Office Axe works on the files on your PC. Only the optional "
             "“AI suit and tie” tool sends the photo you choose to OpenAI to create the new picture."),
            ("How do updates work?", "When a new version is published, the installed app shows “Update Now”."),
            ("How do I pay?", "With bKash, Nagad or a Visa / Mastercard card. Plans are prepaid and don't renew by themselves."),
        ]
        faq_html = "".join(f'<details class="faq-item"><summary>{e(q)}</summary><p>{e(a)}</p></details>' for q, a in faq)
        body = f"""
<section class="hero2">
  <div class="hero-copy">
    <p class="eyebrow">{icon("windows", 16)} Windows desktop app</p>
    <h1>Every office file job, <span class="hl">in one app</span>.</h1>
    <p class="lead">Convert Word, Excel, PowerPoint and photos to PDF, compress and merge PDFs, sign forms,
      make student folders in bulk and keep everything organised — with undo for every action.</p>
    <div class="cta">
      <a class="btn primary lg" href="/download">{icon("download", 18)}Download for Windows{f'<span class="ver">v{e(ver)}</span>' if ver else ''}</a>
      <a class="btn lg" href="#tour">{icon("play", 18)}Watch the tour</a>
    </div>
    <ul class="ticks">
      <li>{icon("check", 16)}Windows 10 &amp; 11</li><li>{icon("check", 16)}Works with your files offline</li>
      <li>{icon("check", 16)}Buy one, get one: 2 PCs per plan</li>
    </ul>
    {self._release_strip()}
  </div>
  <div class="hero-media"><img src="/static/img/workspace.jpg" alt="The Office Axe workspace" width="1200" height="567" fetchpriority="high"></div>
</section>

<section id="features" class="section">
  <div class="section-head"><p class="eyebrow">Features</p><h2>Everything an office does with documents</h2>
    <p class="muted">Seventeen tools in one window — each shown in the video tour below.</p></div>
  <div class="feat-grid">{feats}</div>
</section>

{self._tour_html()}
{self._slider_html()}

<section id="pricing" class="section alt">
  <div class="section-head"><p class="eyebrow">Pricing</p><h2>One plan, every tool — on 2 PCs</h2>
    <p class="muted">Buy one, get one. Prices in Bangladeshi taka. Prepaid — no automatic renewal.</p></div>
  {self._plans_html()}
  <p class="center"><a href="/pricing/">Compare plans and billing questions →</a></p>
</section>

<section id="activate" class="section">
  <div class="section-head"><p class="eyebrow">Getting started</p><h2>Install and activate in a minute</h2></div>
  <ol class="steps3">
    <li><span class="n">1</span><h3>Download</h3><p>Save the Windows installer and run it. No account is needed.</p></li>
    <li><span class="n">2</span><h3>Activate</h3><p>Open Office Axe, click <b>Activate with license key</b> and paste your key.</p></li>
    <li><span class="n">3</span><h3>Work</h3><p>Everything is ready. When an update is out, the app shows <b>Update Now</b>.</p></li>
  </ol>
</section>

<section id="faq" class="section alt">
  <div class="section-head"><p class="eyebrow">Questions</p><h2>Good to know</h2></div>
  <div class="faq">{faq_html}</div>
</section>

<section class="cta-band">
  <h2>Ready to try Office Axe?</h2>
  <a class="btn primary lg" href="/download">{icon("download", 18)}Download for Windows</a>
</section>"""
        return Response(page("Every office file job, in one app", body))

    PLAN_GROUPS = [
        ("Files and folders", ["File workspace with instant preview", "A folder for every name in a list",
                               "Smart rename", "Undo / redo for every action", "Folder sizes, type-to-find, open folders in place",
                               "“Office Axe” in the File Explorer right-click menu"]),
        ("PDF tools", ["Merge, split and extract pages", "Arrange pages: rotate, reorder, zoom",
                       "Fill forms, sign, add a photo", "Edit text and pictures in a PDF", "Protect a PDF with a password"]),
        ("Convert and compress", ["Word, Excel, PowerPoint and photos to PDF", "PDF pages to images",
                                  "Compress a PDF to a target size"]),
        ("Photo and AI", ["Passport and visa photo sizes", "Mark up images", "AI suit and tie (uses OpenAI when you choose it)"]),
        ("QR, ZIP and recorder", ["Read QR codes from files or the screen", "ZIP / unzip with passwords",
                                  "Screen recorder and screenshots"]),
    ]

    def _plans_html(self, full: bool = False) -> str:
        """The plan card(s). The switch only changes what is shown (JS); without
        JS both prices are visible."""
        inc = "".join(f'<li>{icon("check", 16)}{e(x)}</li>' for x in [
            "2 PCs — buy one, get one", "All 17 tools", "Free updates during your plan", "Works with your files offline",
            "Manage your PCs in the app", "Pay with bKash, Nagad or card"])
        return f"""
  <div class="billing" data-period="yearly">
    <div class="period-switch" role="radiogroup" aria-label="Billing period">
      <button type="button" role="radio" aria-checked="false" data-period="monthly">Monthly</button>
      <button type="button" role="radio" aria-checked="true" data-period="yearly">Yearly <span class="save">Save 17%</span></button>
    </div>
    <div class="plans">
      <div class="plan" data-plan="monthly">
        <h3>Monthly</h3>
        <div class="price">৳999<span>/ month</span></div>
        <p class="muted small">Billed every month · prepaid, no auto-renew</p>
        <ul class="inc">{inc}</ul>
        <a class="btn wide-btn" href="/download">{icon("download", 16)}Download and buy monthly</a>
      </div>
      <div class="plan best" data-plan="yearly">
        <div class="badge">Best value · Save 17%</div>
        <h3>Yearly</h3>
        <div class="price">৳9,999<span>/ year</span></div>
        <div class="per">≈ ৳833 a month · <s>৳11,988</s></div>
        <p class="muted small">Billed once a year · prepaid, no auto-renew</p>
        <ul class="inc">{inc}</ul>
        <a class="btn primary wide-btn" href="/download">{icon("download", 16)}Download and buy yearly</a>
      </div>
    </div>
    <p class="muted small center">You buy inside the app: open Office Axe → choose a plan → pay with bKash, Nagad or card.</p>
  </div>"""

    def _pricing_details_html(self) -> str:
        tick = icon("check", 16)
        rows = "".join(
            f'<tr class="grp"><th colspan="3">{e(g)}</th></tr>' +
            "".join(f'<tr><td>{e(x)}</td><td class="c">{tick}</td><td class="c">{tick}</td></tr>' for x in items)
            for g, items in self.PLAN_GROUPS)
        faq = [
            ("What happens when my plan ends?", "The tools lock until you renew. Your files stay exactly where they are — "
             "nothing on your PC is changed or removed."),
            ("Can I renew early?", "Yes. The new period starts when your current one ends, so no days are lost."),
            ("Can I switch from monthly to yearly?", "Yes — buy the yearly plan in the app. It starts when your current "
             "month ends."),
            ("Can I use a 3rd PC?", "One subscription covers 2 PCs. For more PCs, buy another subscription."),
            ("I got a new PC — how do I move?", "Open Subscription in the app and remove the old PC, then activate on "
             "the new one with your license key."),
            ("How do I pay?", "Inside the app with bKash, Nagad or a Visa / Mastercard card."),
            ("Do I get a receipt?", "Yes. Every payment is listed on the app's Subscription page with its order number "
             "and payment reference."),
            ("What are review and trial keys?", "Keys Office Axe gives out for testing or reviews. They run for a set "
             "number of days, usually on 1 PC."),
        ]
        faq_html = "".join(f'<details class="faq-item"><summary>{e(q)}</summary><p>{e(a)}</p></details>' for q, a in faq)
        return f"""
<section class="section">
  <div class="section-head"><p class="eyebrow">Compare</p><h2>What's included</h2>
    <p class="muted">Both plans include everything — the only difference is how you pay.</p></div>
  <div class="table-scroll"><table class="compare">
    <thead><tr><th></th><th>Monthly</th><th>Yearly</th></tr></thead>
    <tbody>
      <tr><td>Price</td><td class="c"><b>৳999</b> / month</td><td class="c"><b>৳9,999</b> / year<br><span class="muted small">≈ ৳833 / month</span></td></tr>
      <tr><td>PCs per subscription</td><td class="c">2 (buy one, get one)</td><td class="c">2 (buy one, get one)</td></tr>
      <tr><td>Free updates during the plan</td><td class="c">{tick}</td><td class="c">{tick}</td></tr>
      <tr><td>Works offline</td><td class="c">{tick}</td><td class="c">{tick}</td></tr>
      {rows}
    </tbody>
  </table></div>
</section>
<section class="section alt">
  <div class="section-head"><p class="eyebrow">Billing</p><h2>Questions about plans</h2></div>
  <div class="faq">{faq_html}</div>
</section>"""

    # ── pages the earlier OfficeAxe site had (same addresses) ────────────────
    def document_page(self) -> Response:
        guides = "".join(f'<article class="card guide"><h2>{e(g["title"])}</h2><div class="guide-body">{e(g["body"])}</div></article>'
                         for g in self.releases.guides())
        body = f"""
<section class="section narrow-wide">
  <p class="eyebrow">Guides</p>
  <h1>How to use OfficeAxe</h1>
  <p class="lead">Install the Windows app, open your documents, and update when a newer release is published.</p>
  <ol class="howto">
    <li><b>Download</b> — save the Windows installer. No account is required. <a href="/download">Download now</a></li>
    <li><b>Run the installer</b> — open the .exe and follow the setup steps.</li>
    <li><b>Use OfficeAxe</b> — the installed app checks for a newer version and shows <b>Update Now</b> when one is ready.</li>
    <li><b>Activate</b> — click <b>Activate with license key</b>, paste your key and click <b>Activate on this PC</b>.</li>
  </ol>
  <h2>More documentation</h2>
  {guides or '<p class="muted">Guides added by an administrator will appear here.</p>'}
</section>"""
        return Response(page("How to use OfficeAxe", body))

    def pricing_page(self) -> Response:
        body = f"""
<section class="section">
  <div class="section-head"><p class="eyebrow">Pricing</p><h1>One plan, every tool — on 2 PCs</h1>
    <p class="muted">Buy one, get one: each subscription works on two computers. Prices in Bangladeshi taka.</p></div>
  {self._plans_html(full=True)}
</section>
{self._pricing_details_html()}"""
        return Response(page("OfficeAxe pricing", body, description="Office Axe plans: ৳999 a month or ৳9,999 a year, "
                                                                      "2 PCs per subscription, all 17 tools."))

    def _release_strip(self) -> str:
        try:
            cur = self.releases.current()
        except Exception:
            cur = {}
        if not cur.get("version"):
            return ""
        when = as_dt(cur.get("publishedAt"))
        notes = str(cur.get("releaseNotes") or "").strip()
        return (f'<p class="release-strip"><span class="pill active">v{e(cur["version"])}</span> '
                f'Current release{" · " + when.strftime("%d %b %Y") if when else ""}</p>'
                + (f'<details class="whatsnew"><summary>What’s new</summary><p class="notes">{e(notes)}</p></details>' if notes else ""))

    def _tour_html(self) -> str:
        try:
            with open(os.path.join(STATIC, "video", "chapters.json"), encoding="utf-8") as fh:
                chapters = json.load(fh)
        except (OSError, ValueError):
            chapters = []
        if not (self.cfg.video_hosted or os.path.isfile(self.cfg.video_file)):
            return ""
        mmss = lambda t: f"{int(t) // 60}:{int(t) % 60:02d}"
        items = "".join(f'<li><button type="button" data-seek="{c["t"]}"><span class="ch-n">{c["n"]:02d}</span>'
                        f'<span class="ch-title">{e(c["title"])}</span><span class="ch-time">{mmss(c["t"])}</span>'
                        f'</button></li>' for c in chapters)
        return f"""
<section id="tour" class="section tour">
  <div class="section-head"><p class="eyebrow">Video tour</p><h2>Watch the 8-minute tour</h2>
  <p class="muted">Every feature, filmed in the real app. Jump to any chapter.</p></div>
  <div class="tour-grid">
    <div class="video-wrap">
      <video id="tour-video" controls preload="metadata" playsinline poster="/static/video/poster.jpg">
        <source src="/media/office-axe-demo.mp4" type="video/mp4">
        <track kind="captions" src="/static/video/captions.vtt" srclang="en" label="English">
        <track kind="chapters" src="/static/video/chapters.vtt" srclang="en" label="Chapters">
        Your browser can't play this video. <a href="/media/office-axe-demo.mp4">Download it</a> instead.
      </video>
    </div>
    <ol class="chapters" aria-label="Chapters">{items}</ol>
  </div>
</section>"""

    def _slider_html(self) -> str:
        try:
            with open(os.path.join(STATIC, "slides", "slides.json"), encoding="utf-8") as fh:
                slides = json.load(fh)
        except (OSError, ValueError):
            return ""
        n = len(slides)
        hidden, lazy, current = ' aria-hidden="true"', ' loading="lazy"', ' aria-current="true"'
        figs = "".join(f'<figure class="slide" role="group" aria-roledescription="slide" '
                       f'aria-label="{i + 1} of {n}"{hidden if i else ""}>'
                       f'<img src="/static/slides/{e(s["name"])}.jpg" alt="{e(s["caption"])}" '
                       f'width="1280" height="605"{lazy if i else ""}>'
                       f'<figcaption>{e(s["caption"])}</figcaption></figure>' for i, s in enumerate(slides))
        dots = "".join(f'<button type="button" class="dot" aria-label="Show slide {i + 1}"'
                       f'{"" if i else current} data-go="{i}"></button>' for i in range(n))
        return f"""
<section class="section showcase" aria-labelledby="showcase-title">
  <div class="section-head"><p class="eyebrow">Screens</p><h2 id="showcase-title">A closer look</h2></div>
  <div class="slider" id="slider" role="region" aria-roledescription="carousel" aria-label="App screenshots" tabindex="0">
    <div class="slides" aria-live="polite">{figs}</div>
    <button type="button" class="slider-btn prev" aria-label="Previous slide">&#8249;</button>
    <button type="button" class="slider-btn next" aria-label="Next slide">&#8250;</button>
    <div class="dots">{dots}</div>
  </div>
</section>"""

    def download(self) -> Response:
        cur = {}
        try:
            cur = self.releases.current()      # the live release (release manager)
        except Exception:
            pass
        url = str(cur.get("downloadUrl") or "") or self.cfg.download_url
        if url and url.startswith(("https://", "http://")):
            self.store.count_download(iso(self.lic._now()))
            if cur.get("downloadUrl"):
                try:
                    self.releases.log_download(cur)
                except Exception:
                    pass
            return redirect(url)
        f = self.cfg.download_file
        if f and os.path.isfile(f):
            self.store.count_download(iso(self.lic._now()))
            with open(f, "rb") as fh:
                data = fh.read()
            name = os.path.basename(f)
            return Response(data, ctype=mimetypes.guess_type(name)[0] or "application/octet-stream",
                            headers=[("Content-Disposition", f'attachment; filename="{quote(name)}"')])
        return Response(page("Download", "<section class='narrow'><h1>Download coming soon</h1>"
                             "<p>The installer will be available here shortly.</p></section>"))

    def static(self, name: str) -> Response:
        if not re.fullmatch(r"[A-Za-z0-9_\-/]+\.(css|js|jpg|png|svg|ico|vtt|json)", name) or ".." in name:
            return not_found()
        path = os.path.join(STATIC, *name.split("/"))
        if not os.path.isfile(path):
            return not_found()
        with open(path, "rb") as fh:
            data = fh.read()
        ctype = "text/vtt; charset=utf-8" if path.endswith(".vtt") else \
            (mimetypes.guess_type(path)[0] or "application/octet-stream")
        return Response(data, ctype=ctype, headers=[("Cache-Control", "public, max-age=3600")])

    # ── license API ──────────────────────────────────────────────────────────
    def api(self, req: Request, action: str) -> Response:
        if action == "revoked" and req.method == "GET":
            return json_response({"revoked": self.store.revoked_ids()})
        if action in ("activate", "check", "deactivate") and req.method == "POST":
            return json_response(self.lic.api(action, req.json(), req.ip))
        return json_response({"error": "not found"}, "404 Not Found")

    # ── admin ────────────────────────────────────────────────────────────────
    def _session(self, req: Request) -> Optional[dict]:
        return self.sessions.read(req.cookies.get(self.cfg.cookie_name, ""))

    def _cookie(self, value: str, max_age: int) -> tuple:
        attrs = f"{self.cfg.cookie_name}={value}; Path=/admin; HttpOnly; SameSite=Strict; Max-Age={max_age}"
        if self.cfg.cookie_secure:
            attrs += "; Secure"
        return ("Set-Cookie", attrs)

    def admin(self, req: Request, p: str, m: str) -> Response:
        if p == "/admin/setup":
            return self.setup(req)
        if not self.account.is_set():
            return redirect("/admin/setup")
        if p == "/admin/login":
            return self.login(req)
        sess = self._session(req)
        if not sess:
            return redirect("/admin/login")
        csrf = self.sessions.csrf(sess)
        if m == "POST" and not self.sessions.check_csrf(sess, req.form.get("csrf")):
            return Response(page("Expired", "<section class='narrow'><h1>This form expired</h1>"
                                 "<p><a href='/admin'>Reload the page</a> and try again.</p></section>"),
                            "403 Forbidden")
        actor = sess["u"]

        if p == "/admin/logout" and m == "POST":
            return redirect("/admin/login", [self._cookie("", 0)])
        if p == "/admin" and m == "GET":
            return self.keys_page(req, sess, csrf)
        if p == "/admin/keys" and m == "POST":
            f = req.form
            try:
                row = self.lic.issue(f.get("type", "review"), f.get("holder", ""), int(f.get("days") or 30),
                                     int(f.get("devices") or 0) or None, f.get("plan", ""), f.get("note", ""), actor)
            except ValueError as exc:
                return self.keys_page(req, sess, csrf, error=str(exc), form=f)
            return redirect(f"/admin/keys/{row['key_id']}?new=1")
        mk = re.fullmatch(r"/admin/keys/([0-9a-f]{12})(?:/(revoke|restore|note|release))?", p)
        if mk:
            key_id, act = mk.group(1), mk.group(2)
            if not act and m == "GET":
                return self.key_page(req, sess, csrf, key_id)
            if act and m == "POST":
                f = req.form
                if act in ("revoke", "restore"):
                    self.lic.set_revoked(key_id, act == "revoke", actor)
                elif act == "note":
                    self.lic.set_note(key_id, f.get("note", ""), actor)
                elif act == "release":
                    self.lic.release_device(key_id, f.get("device_id", ""), actor)
                return redirect(f"/admin/keys/{key_id}")
        if p == "/admin/export.csv":
            return Response(self.lic.export_csv(), ctype="text/csv; charset=utf-8",
                            headers=[("Content-Disposition", 'attachment; filename="office-axe-keys.csv"'),
                                     ("Cache-Control", "no-store")])
        if p == "/admin/revoked_keys.json":
            return Response(self.lic.revoked_json(), ctype="application/json",
                            headers=[("Content-Disposition", 'attachment; filename="revoked_keys.json"')])
        if p == "/admin/activity":
            return self.activity_page(sess, csrf)
        if p == "/admin/releases":
            if m == "POST":
                f = req.form
                try:
                    r = self.releases.create(f.get("version", ""), f.get("notes", ""), f.get("url", ""),
                                             f.get("mandatory") == "1", self._who(sess),
                                             publish=f.get("action") == "publish", allow_older=f.get("allow_older") == "1")
                except ReleaseError as exc:
                    return self.releases_page(req, sess, csrf, error=str(exc), form=f)
                self.store.log(iso(self.lic._now()), actor, "release_" + ("publish" if f.get("action") == "publish" else "draft"),
                               "", r["version"])
                return redirect("/admin/releases")
            return self.releases_page(req, sess, csrf)
        mr = re.fullmatch(r"/admin/releases/([A-Za-z0-9]{1,40})/(edit|publish|delete)", p)
        if mr and m == "POST":
            rid, act, f, note = mr.group(1), mr.group(2), req.form, ""
            try:
                if act == "edit":
                    r = self.releases.edit(rid, f.get("notes", ""), f.get("url", ""), f.get("mandatory") == "1", self._who(sess))
                elif act == "publish":
                    r = self.releases.publish(rid, self._who(sess))
                else:
                    r = self.releases.get(rid) or {}
                    note = self.releases.delete(rid)
                self.store.log(iso(self.lic._now()), actor, "release_" + act, "", str(r.get("version", rid)))
            except ReleaseError as exc:
                return self.releases_page(req, sess, csrf, error=str(exc))
            return self.releases_page(req, sess, csrf, notice=note or {"edit": "Release saved.", "publish": "It's now the live update.",
                                                                        "delete": "Release deleted."}[act])
        if p == "/admin/guides":
            if m == "POST":
                try:
                    g = self.releases.add_guide(req.form.get("title", ""), req.form.get("body", ""), self._who(sess))
                    self.store.log(iso(self.lic._now()), actor, "guide_add", "", g["title"])
                except ReleaseError as exc:
                    return self.guides_page(req, sess, csrf, error=str(exc))
                return redirect("/admin/guides")
            return self.guides_page(req, sess, csrf)
        mg = re.fullmatch(r"/admin/guides/([A-Za-z0-9]{1,40})/delete", p)
        if mg and m == "POST":
            self.releases.delete_guide(mg.group(1))
            self.store.log(iso(self.lic._now()), actor, "guide_delete", "", mg.group(1))
            return redirect("/admin/guides")
        if p == "/admin/import":
            return self.import_page(req, sess, csrf, actor)
        if p == "/admin/settings":
            return self.settings_page(req, sess, csrf)
        return not_found()

    def setup(self, req: Request) -> Response:
        """First run: create the admin password. Only from this computer."""
        if self.account.is_set():
            return redirect("/admin/login")
        if not req.is_local:
            return Response(page("Setup", "<section class='narrow'><h1>Set up the admin login first</h1>"
                                 "<p>On the server, run <code>python webportal/manage.py set-password</code>.</p>"
                                 "</section>"), "403 Forbidden")
        error = ""
        if req.method == "POST":
            f = req.form
            if f.get("password") != f.get("password2"):
                error = "The two passwords don’t match."
            else:
                try:
                    self.account.set_password(f.get("password", ""))
                    return redirect("/admin/login?setup=1")
                except ValueError as exc:
                    error = str(exc)
        body = f"""
<section class="narrow card">
  <h1>Create your admin password</h1>
  <p class="muted">User name: <strong>{e(self.cfg.admin_user)}</strong>. Use at least 10 characters.</p>
  {f'<div class="alert err">{e(error)}</div>' if error else ''}
  <form method="post" class="stack">
    <label>Password<input type="password" name="password" autocomplete="new-password" required minlength="10"></label>
    <label>Repeat password<input type="password" name="password2" autocomplete="new-password" required minlength="10"></label>
    <button class="btn primary">Save password</button>
  </form>
</section>"""
        return Response(page("Set up", body))

    def login(self, req: Request) -> Response:
        error = ""
        now = time.time()
        if req.method == "POST":
            if self.store.recent_failures(req.ip, now - FAILURE_WINDOW) >= MAX_FAILURES:
                error = "Too many attempts. Wait 15 minutes and try again."
            else:
                f = req.form
                ok = self.account.verify(f.get("user", ""), f.get("password", ""))
                self.store.record_login(req.ip, now, ok)
                if ok:
                    self.store.log(iso(self.lic._now()), self.cfg.admin_user, "login", "", req.ip)
                    return redirect("/admin", [self._cookie(self.sessions.create(self.cfg.admin_user),
                                                            12 * 3600)])
                error = "Wrong user name or password."
        note = '<div class="alert ok">Password saved. Log in to continue.</div>' if req.query.get("setup") else ""
        body = f"""
<section class="narrow card">
  <h1>Admin login</h1>
  {note}{f'<div class="alert err">{e(error)}</div>' if error else ''}
  <form method="post" class="stack">
    <label>User name<input name="user" autocomplete="username" required value="{e(self.cfg.admin_user)}"></label>
    <label>Password<input type="password" name="password" autocomplete="current-password" required autofocus></label>
    <button class="btn primary">Log in</button>
  </form>
</section>"""
        return Response(page("Admin login", body), "401 Unauthorized" if error else "200 OK")

    def keys_page(self, req, sess, csrf, error: str = "", form: Optional[dict] = None) -> Response:
        q, status = req.query.get("q", ""), req.query.get("status", "")
        rows = self.lic.list_keys(q, status)
        st = self.lic.stats()
        f = form or {}
        sel = lambda name, val, cur: f'<option value="{val}"{" selected" if cur == val else ""}>{name}</option>'
        ktype = f.get("type", "review")
        warn = "" if self.lic.app_matches() else (
            '<div class="alert err">The app doesn’t carry this portal’s public key, so keys made here won’t '
            'activate. See <a href="/admin/settings">Settings</a>.</div>')
        table = "".join(f"""
          <tr>
            <td><a href="/admin/keys/{r['key_id']}">{e(r['holder'])}</a>
              <div class="muted small">{r['key_id']}{' · ' + e(r['note']) if r['note'] else ''}</div></td>
            <td>{e(r['type'])}{f"<div class='muted small'>{e(r['plan'])}</div>" if r['plan'] else ''}</td>
            <td>{pill(r['status'])}</td>
            <td>{fmt_date(r['expires_at'])}<div class="muted small">{f"{r['days_left']} days left" if r['status'] == 'active' else ''}</div></td>
            <td>{r['devices_used'] or 0} / {r['max_devices']}</td>
            <td class="muted small">{fmt_date(r['last_seen'] or '') or '—'}</td>
            <td><button class="btn sm" data-copy="{e(r['key'])}">Copy key</button></td>
          </tr>""" for r in rows) or f'<tr><td colspan="7" class="empty">{"No keys match." if (q or status) else "No keys yet."}</td></tr>'
        body = f"""
{warn}
<section class="stats">
  <div><b>{st['active']}</b><span>active keys</span></div>
  <div><b>{st['expiring']}</b><span>expire in 7 days</span></div>
  <div><b>{st['devices']}</b><span>PCs activated</span></div>
  <div><b>{st['revoked']}</b><span>revoked</span></div>
  <div><b>{st['downloads']}</b><span>downloads</span></div>
</section>
<div class="admin-grid">
  <section class="card">
    <h2>New key</h2>
    {f'<div class="alert err">{e(error)}</div>' if error else ''}
    <form method="post" action="/admin/keys" class="stack" id="new-key">
      <input type="hidden" name="csrf" value="{e(csrf)}">
      <label>Type<select name="type" data-devices>
        {sel('Review (giveaway)', 'review', ktype)}{sel('Trial', 'trial', ktype)}{sel('Full licence', 'full', ktype)}
      </select></label>
      <label>For (name)<input name="holder" maxlength="48" required value="{e(f.get('holder', ''))}" placeholder="e.g. Rahim, Acme Ltd"></label>
      <div class="row">
        <label>Days<input type="number" name="days" min="1" max="3660" value="{e(f.get('days', '30'))}"></label>
        <label>PCs<input type="number" name="devices" min="1" max="255" value="{e(f.get('devices', '1'))}"></label>
      </div>
      <label>Plan shown in the app<select name="plan">
        {sel('None (shows the key type)', '', f.get('plan', ''))}{sel('Monthly', 'monthly', f.get('plan', ''))}{sel('Yearly', 'yearly', f.get('plan', ''))}
      </select></label>
      <label>Note (only you see it)<textarea name="note" maxlength="300">{e(f.get('note', ''))}</textarea></label>
      <button class="btn primary">Make key</button>
    </form>
  </section>
  <section class="card">
    <form class="toolbar" method="get" action="/admin">
      <h2>Keys</h2>
      <input name="q" value="{e(q)}" placeholder="Search name, id, note…">
      <select name="status">{sel('All', '', status)}{sel('Active', 'active', status)}{sel('Expired', 'expired', status)}{sel('Revoked', 'revoked', status)}</select>
      <button class="btn">Filter</button>
      <a class="btn" href="/admin/export.csv">Export CSV</a>
    </form>
    <div class="table-wrap"><table>
      <thead><tr><th>For</th><th>Type</th><th>Status</th><th>Expires</th><th>PCs</th><th>Last seen</th><th></th></tr></thead>
      <tbody>{table}</tbody>
    </table></div>
  </section>
</div>"""
        return Response(page("Keys", body, sess, csrf, active="/admin"))

    def key_page(self, req, sess, csrf, key_id: str) -> Response:
        k = self.lic.key_view(key_id)
        if not k:
            return not_found()
        label = {"review": "review ", "trial": "trial ", "full": ""}.get(k["type"], "")
        pcs = f" on up to {k['max_devices']} PCs" if k["max_devices"] > 1 else ""
        msg = (f"Hi {k['holder']},\n\nHere is your Office Axe {label}key, valid until {fmt_date(k['expires_at'])}"
               f"{pcs}:\n\n{k['key']}\n\n"
               "To use it: open Office Axe → \"Activate with license key\" → paste the key → Activate on this PC.")
        devs = "".join(f"""
          <tr class="{'released' if d['released'] else ''}">
            <td>{e(d['name'] or 'PC')}<div class="muted small">{e(d['os'])} · {e(d['device_id'])}</div></td>
            <td>{fmt_time(d['first_seen'])}</td><td>{fmt_time(d['last_seen'])}</td>
            <td>{'<span class="muted">removed</span>' if d['released'] else f'''
              <form method="post" action="/admin/keys/{key_id}/release" data-confirm="Remove this PC from the key? It will stop working there at its next check.">
                <input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="device_id" value="{e(d['device_id'])}">
                <button class="btn sm danger">Remove PC</button></form>'''}</td>
          </tr>""" for d in k["devices"]) or '<tr><td colspan="4" class="empty">Not activated on any PC yet.</td></tr>'
        new = '<div class="alert ok">Key made. Copy it or the message below and send it.</div>' if req.query.get("new") else ""
        rev_btn = (f'<form method="post" action="/admin/keys/{key_id}/restore"><input type="hidden" name="csrf" value="{e(csrf)}">'
                   f'<button class="btn">Restore key</button></form>') if k["revoked"] else (
                   f'<form method="post" action="/admin/keys/{key_id}/revoke" data-confirm="Revoke the key for {e(k["holder"])}? '
                   f'It stops working on every PC at its next online check.">'
                   f'<input type="hidden" name="csrf" value="{e(csrf)}"><button class="btn danger">Revoke key</button></form>')
        log = "".join(f"<li><span class='muted small'>{fmt_time(a['at'])}</span> {e(a['actor'])}: {e(a['action'])} "
                      f"<span class='muted small'>{e(a['detail'])}</span></li>"
                      for a in self.store.activity(50, key_id)) or "<li class='muted'>Nothing yet.</li>"
        body = f"""
<p><a href="/admin">← All keys</a></p>
{new}
<div class="admin-grid">
  <section class="card">
    <h1>{e(k['holder'])} {pill(k['status'])}</h1>
    <dl class="facts">
      <dt>Type</dt><dd>{e(k['type'])}{' · ' + e(k['plan']) if k['plan'] else ''}</dd>
      <dt>Key id</dt><dd>{k['key_id']}</dd>
      <dt>Issued</dt><dd>{fmt_date(k['issued_at'])}</dd>
      <dt>Expires</dt><dd>{fmt_date(k['expires_at'])}{f" · {k['days_left']} days left" if k['status'] == 'active' else ''}</dd>
      <dt>PCs</dt><dd>{k['devices_used']} of {k['max_devices']} in use</dd>
      <dt>Made in</dt><dd>{ {'portal': 'portal', 'desktop': 'desktop dashboard', 'seen': 'first seen by the API'}.get(k['source'], e(k['source'])) }</dd>
    </dl>
    <label class="small muted">License key</label>
    <div class="key">{e(k['key'])}</div>
    <div class="row-btns">
      <button class="btn primary" data-copy="{e(k['key'])}">Copy key</button>
      <button class="btn" data-copy="{e(msg)}">Copy message</button>
      {rev_btn}
    </div>
    <form method="post" action="/admin/keys/{key_id}/note" class="stack" style="margin-top:18px">
      <input type="hidden" name="csrf" value="{e(csrf)}">
      <label>Note<textarea name="note" maxlength="300">{e(k['note'])}</textarea></label>
      <button class="btn sm">Save note</button>
    </form>
  </section>
  <section class="card">
    <h2>Activated PCs</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>PC</th><th>Activated</th><th>Last check</th><th></th></tr></thead>
      <tbody>{devs}</tbody>
    </table></div>
    <h2 style="margin-top:24px">History</h2>
    <ul class="log">{log}</ul>
  </section>
</div>"""
        return Response(page(k["holder"], body, sess, csrf))

    # ── releases (the merged OfficeAxe release manager) ──────────────────────
    def _who(self, sess) -> str:
        return f"{sess['u']}@portal"

    def releases_page(self, req, sess, csrf, error: str = "", form: Optional[dict] = None, notice: str = "") -> Response:
        rm = self.releases
        cur = rm.current()
        rows = rm.list()
        stats = rm.download_stats(30)
        f = form or {}
        fmt = lambda v: (as_dt(v).strftime("%d %b %Y, %H:%M UTC") if as_dt(v) else "")
        live = f"""
          <div class="live-release">
            <div><span class="pill active">live</span> <b>OfficeAxe {e(cur.get('version', ''))}</b>
              {'<span class="pill revoked">required update</span>' if cur.get('mandatory') else ''}</div>
            <div class="muted small">Published {fmt(cur.get('publishedAt'))} · {e(cur.get('fileName') or '')}</div>
            <p class="notes">{e(cur.get('releaseNotes', ''))}</p>
            <div class="key">{e(cur.get('downloadUrl', ''))}</div>
          </div>""" if cur else '<p class="muted">No release is live yet. Installed apps see no update until one is published.</p>'
        def row(r):
            st = "live" if r.get("isCurrent") or cur.get("releaseId") == r["id"] else ("published" if r.get("status") == "published" else "draft")
            cls = {"live": "active", "published": "expired", "draft": ""}[st]
            pub_btn = "" if st == "live" else f"""
                <form method="post" action="/admin/releases/{r['id']}/publish" data-confirm="Make {e(r['version'])} the live update? Installed apps will offer it.">
                  <input type="hidden" name="csrf" value="{e(csrf)}"><button class="btn sm primary">Set live</button></form>"""
            return f"""
            <details class="release-row">
              <summary>
                <span class="rel-ver">{e(r['version'])}</span> <span class="pill {cls}">{st}</span>
                {'<span class="pill revoked">required</span>' if r.get('mandatory') else ''}
                <span class="muted small">{fmt(r.get('publishedAt') or r.get('createdAt'))} · {e(r.get('updatedBy', ''))}</span>
              </summary>
              <p class="notes">{e(r.get('releaseNotes', ''))}</p>
              <div class="muted small">{e(r.get('downloadUrl') or 'No download URL yet')}</div>
              <form method="post" action="/admin/releases/{r['id']}/edit" class="stack" style="margin-top:12px">
                <input type="hidden" name="csrf" value="{e(csrf)}">
                <label>Release notes<textarea name="notes" maxlength="8000" required>{e(r.get('releaseNotes', ''))}</textarea></label>
                <label>Download URL<input name="url" value="{e(r.get('downloadUrl', ''))}" placeholder="https://…/OfficeAxe-Setup.exe"></label>
                <label class="check"><input type="checkbox" name="mandatory" value="1"{' checked' if r.get('mandatory') else ''}> Required update (no “Later” button)</label>
                <div class="row-btns"><button class="btn sm">Save changes</button></div>
              </form>
              <div class="row-btns">{pub_btn}
                <form method="post" action="/admin/releases/{r['id']}/delete" data-confirm="Delete release {e(r['version'])}?{' It is live: installed apps will see no update until another is published.' if st == 'live' else ''}">
                  <input type="hidden" name="csrf" value="{e(csrf)}"><button class="btn sm danger">Delete</button></form>
              </div>
            </details>"""
        hist = "".join(row(r) for r in rows) or '<p class="muted">No releases yet.</p>'
        body = f"""
{f'<div class="alert ok">{e(notice)}</div>' if notice else ''}
<section class="stats">
  <div><b>{e(cur.get('version', '—'))}</b><span>live version</span></div>
  <div><b>{len(rows)}</b><span>releases</span></div>
  <div><b>{stats['downloads']}</b><span>downloads, 30 days</span></div>
  <div><b>{stats['problems']}</b><span>download problems, 30 days</span></div>
  <div><b>{self.store.download_count()}</b><span>Download button clicks</span></div>
</section>
<div class="admin-grid">
  <section class="card">
    <h2>New release</h2>
    {f'<div class="alert err">{e(error)}</div>' if error else ''}
    <form method="post" action="/admin/releases" class="stack">
      <input type="hidden" name="csrf" value="{e(csrf)}">
      <label>Version<input name="version" required placeholder="e.g. 1.4.2" value="{e(f.get('version', ''))}"></label>
      <label>What's new<textarea name="notes" maxlength="8000" required rows="5">{e(f.get('notes', ''))}</textarea></label>
      <label>Download URL (the .exe installer)<input name="url" value="{e(f.get('url', ''))}" placeholder="https://…/OfficeAxe-Setup.exe"></label>
      <label class="check"><input type="checkbox" name="mandatory" value="1"{' checked' if f.get('mandatory') else ''}> Required update (no “Later” button)</label>
      <label class="check"><input type="checkbox" name="allow_older" value="1"> Publish anyway if it isn't newer than the live version</label>
      <div class="row-btns">
        <button class="btn" name="action" value="draft">Save draft</button>
        <button class="btn primary" name="action" value="publish">Publish now</button>
      </div>
      <p class="muted small">Installed copies of OfficeAxe read the live release and show <b>Update Now</b>.
        Upload the installer somewhere public first (Firebase Storage, Google Drive, GitHub releases…) and paste its link.</p>
    </form>
  </section>
  <section class="card">
    <h2>Live release</h2>
    {live}
    <h2 style="margin-top:24px">History</h2>
    <div class="release-list">{hist}</div>
  </section>
</div>"""
        return Response(page("Releases", body, sess, csrf, active="/admin/releases"))

    def guides_page(self, req, sess, csrf, error: str = "") -> Response:
        items = "".join(f"""
          <details class="release-row"><summary><b>{e(g['title'])}</b></summary>
            <div class="guide-body">{e(g['body'])}</div>
            <form method="post" action="/admin/guides/{e(g['id'])}/delete" data-confirm="Delete the guide “{e(g['title'])}”?">
              <input type="hidden" name="csrf" value="{e(csrf)}"><button class="btn sm danger">Delete</button></form>
          </details>""" for g in self.releases.guides()) or '<p class="muted">No guides yet.</p>'
        body = f"""
<div class="admin-grid">
  <section class="card">
    <h2>New guide</h2>
    {f'<div class="alert err">{e(error)}</div>' if error else ''}
    <form method="post" action="/admin/guides" class="stack">
      <input type="hidden" name="csrf" value="{e(csrf)}">
      <label>Title<input name="title" maxlength="200" required></label>
      <label>Text<textarea name="body" maxlength="20000" rows="10" required></textarea></label>
      <button class="btn primary">Add guide</button>
    </form>
    <p class="muted small">Guides appear on the public <a href="/document/">Document</a> page.</p>
  </section>
  <section class="card"><h2>Guides</h2>{items}</section>
</div>"""
        return Response(page("Guides", body, sess, csrf, active="/admin/guides"))

    def activity_page(self, sess, csrf) -> Response:
        rows = "".join(f"""<tr><td class="small">{fmt_time(a['at'])}</td><td>{e(a['actor'])}</td><td>{e(a['action'])}</td>
            <td>{f'<a href="/admin/keys/{e(a["key_id"])}">{e(a["key_id"])}</a>' if a['key_id'] else ''}</td>
            <td class="muted small">{e(a['detail'])}</td></tr>""" for a in self.store.activity(300)) \
            or '<tr><td colspan="5" class="empty">Nothing yet.</td></tr>'
        body = f"""<section class="card"><h1>Activity</h1><div class="table-wrap"><table>
          <thead><tr><th>When</th><th>Who</th><th>What</th><th>Key</th><th>Details</th></tr></thead>
          <tbody>{rows}</tbody></table></div></section>"""
        return Response(page("Activity", body, sess, csrf, active="/admin/activity"))

    def import_page(self, req, sess, csrf, actor) -> Response:
        msg = ""
        desktop = os.path.join(os.path.expanduser("~"), "OfficeAxeAdmin", "issued_keys.json")
        if req.method == "POST":
            f = req.form
            try:
                if f.get("source") == "desktop":
                    res = self.lic.import_desktop(desktop, actor)
                else:
                    import tempfile
                    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
                        tf.write(f.get("json", ""))
                    try:
                        res = self.lic.import_desktop(tf.name, actor)
                    finally:
                        os.replace(tf.name, tf.name + ".imported")   # keep, never delete
                msg = f'<div class="alert ok">{res["added"]} key(s) added, {res["skipped"]} skipped (already here or not signed by this portal’s key).</div>'
            except (OSError, ValueError, KeyError) as exc:
                msg = f'<div class="alert err">Could not import: {e(str(exc))}</div>'
        has_desktop = os.path.isfile(desktop)
        body = f"""
<section class="narrow card">
  <h1>Import keys</h1>
  <p class="muted">Bring in the keys made with the desktop dashboard (<code>issued_keys.json</code>). Only keys
    signed with this portal’s signing key are accepted; keys already here are skipped.</p>
  {msg}
  {f'''<form method="post" class="stack"><input type="hidden" name="csrf" value="{e(csrf)}">
    <input type="hidden" name="source" value="desktop">
    <button class="btn primary">Import from this computer’s desktop dashboard</button></form>
    <p class="muted small">{e(desktop)}</p>''' if has_desktop else ''}
  <form method="post" class="stack">
    <input type="hidden" name="csrf" value="{e(csrf)}">
    <label>…or paste the contents of issued_keys.json<textarea name="json" rows="8"></textarea></label>
    <button class="btn">Import pasted keys</button>
  </form>
</section>"""
        return Response(page("Import", body, sess, csrf, active="/admin/import"))

    def settings_page(self, req, sess, csrf) -> Response:
        msg = ""
        if req.method == "POST":
            f = req.form
            if not self.account.verify(self.cfg.admin_user, f.get("current", "")):
                msg = '<div class="alert err">The current password is wrong.</div>'
            elif f.get("new") != f.get("new2"):
                msg = '<div class="alert err">The new passwords don’t match.</div>'
            else:
                try:
                    self.account.set_password(f.get("new", ""))
                    self.store.log(iso(self.lic._now()), sess["u"], "password_changed")
                    return redirect("/admin/login")
                except ValueError as exc:
                    msg = f'<div class="alert err">{e(str(exc))}</div>'
        match = self.lic.app_matches()
        body = f"""
<div class="admin-grid">
  <section class="card">
    <h1>Settings</h1>
    <h2>License signing key</h2>
    <p>Public key: <code class="wrap">{self.lic.public_hex}</code></p>
    <div class="alert {'ok' if match else 'err'}">{'The app’s built-in public key matches — keys made here activate in Office Axe.' if match else 'The app’s built-in public key is different. Put the public key above in <code>license_keys.py</code> (PUBLIC_KEY_HEX) and rebuild, or give the portal the same signing key as the app.'}</div>
    <p class="muted small">The private key is read from the server’s settings and never shown here.</p>
    <h2>Offline revoked list</h2>
    <p class="muted">Apps check revocations online. For PCs that stay offline, ship this file with the next app build as <code>revoked_keys.json</code>.</p>
    <a class="btn" href="/admin/revoked_keys.json">Download revoked_keys.json</a>
  </section>
  <section class="card">
    <h2>Change password</h2>
    {msg}
    <form method="post" class="stack">
      <input type="hidden" name="csrf" value="{e(csrf)}">
      <label>Current password<input type="password" name="current" autocomplete="current-password" required></label>
      <label>New password<input type="password" name="new" autocomplete="new-password" required minlength="10"></label>
      <label>Repeat new password<input type="password" name="new2" autocomplete="new-password" required minlength="10"></label>
      <button class="btn primary">Change password</button>
    </form>
    <p class="muted small">Changing it logs you out everywhere.</p>
  </section>
</div>"""
        return Response(page("Settings", body, sess, csrf, active="/admin/settings"))


def make_app(config: Optional[Config] = None, store=None) -> Portal:
    return Portal(config, store)
