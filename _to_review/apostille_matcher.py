"""
apostille_matcher.py — Full pipeline for apostille.mygov.bd processing.

Flow
----
1. Scan local PDF pages for QR codes → decode URL → visit apostille.mygov.bd
2. Playwright (headless Chromium) renders the page and captures a screenshot
   PLUS any citizenv2 document images that load in the background.
3. GPT-4o Vision reads the captured images → extracts student name, doc type, etc.
4. Match the apostille to the correct local PDF (certificate / transcript).
5. MERGE the apostille page(s) into the matched PDF (apostille first, then document).

Supported URL formats
---------------------
  https://apostille.mygov.bd/application-details/{id}
  https://apostille.mygov.bd/verify/{token}
  https://apostille.mygov.bd/certificate/{token}
"""
from __future__ import annotations

import os, re, json, tempfile, time
from typing import Callable

# ── Document-type classification ─────────────────────────────────────────────

DOC_TYPE_KEYWORDS: dict[str, list[str]] = {
    "ssc": [
        "ssc", "secondary school certificate", "secondary school cert",
        "class x", "class 10", "class ten", "10th grade",
        "secondary education", "madhyamik", "s.s.c",
        "junior school certificate", "jsc",
    ],
    "hsc": [
        "hsc", "higher secondary certificate", "higher secondary cert",
        "class xii", "class 12", "class twelve", "12th grade",
        "intermediate", "h.s.c", "alim", "higher secondary education",
        "dakhil",
    ],
    "diploma": [
        "diploma", "polytechnic", "technical education", "vocational",
        "btec", "diploma in engineering", "diploma in technology",
        "diploma in business", "certificate course",
    ],
    "bachelor": [
        "bachelor", "b.sc", "b.a.", "b.com", "b.b.a", "b.s.s", "b.pharm",
        "bsc", "honours", "honor", "undergraduate",
        "graduation certificate", "degree certificate",
        "llb", "mbbs",
    ],
    "master": [
        "master", "m.sc", "m.a.", "m.com", "m.b.a", "m.s.s",
        "msc", "postgraduate", "post graduate",
        "mphil", "m.phil", "m.ed",
    ],
    "phd": ["phd", "ph.d", "doctorate", "doctor of philosophy"],
    "birth_certificate": [
        "birth certificate", "birth cert", "birth registration",
        "birth reg", "janma nibandhan", "janma",
    ],
    "family_certificate": [
        "family certificate", "family cert", "family registration",
        "family info", "family information",
        "family member", "nagarik", "citizenship",
    ],
    "marriage_certificate": [
        "marriage certificate", "marriage cert", "nikahnama",
        "kabinnama", "marriage registration",
    ],
    "nid": [
        "national id", "national identity", "nid", "voter id",
        "jatiya porichay", "national identification card",
    ],
    "passport": ["passport", "travel document"],
    "police_clearance": [
        "police clearance", "police verification", "character certificate",
        "good conduct",
    ],
}

DOC_TYPE_LABELS: dict[str, str] = {
    "ssc":                  "SSC Certificate",
    "hsc":                  "HSC Certificate",
    "diploma":              "Diploma",
    "bachelor":             "Bachelor Degree",
    "master":               "Master Degree",
    "phd":                  "PhD",
    "birth_certificate":    "Birth Certificate",
    "family_certificate":   "Family Certificate",
    "marriage_certificate": "Marriage Certificate",
    "nid":                  "National ID",
    "passport":             "Passport",
    "police_clearance":     "Police Clearance",
    "other":                "Other Document",
}

ALL_DOC_TYPES = list(DOC_TYPE_LABELS.keys())


def detect_document_type(info: dict) -> str:
    """Classify extracted GPT-4o info into one of our standard doc-type keys."""
    combined = " ".join(filter(None, [
        info.get("degree"), info.get("doc_type"),
        info.get("institution"), info.get("subject"),
        info.get("issuing_authority"),
    ])).lower()
    best_type, best_score = "other", 0
    for dtype, keywords in DOC_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in combined)
        if score > best_score:
            best_score, best_type = score, dtype
    return best_type


# ── QR code scanning ──────────────────────────────────────────────────────────

def _scan_qr_pyzbar(img_bytes: bytes) -> list[str]:
    """Decode QR codes using pyzbar (most reliable)."""
    try:
        from pyzbar.pyzbar import decode as _pyzbar_decode  # type: ignore
        import cv2, numpy as np  # type: ignore
        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        mat = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        results = _pyzbar_decode(mat)
        return [r.data.decode("utf-8", errors="replace") for r in results if r.data]
    except Exception:
        return []


def _scan_qr_opencv(img_bytes: bytes) -> list[str]:
    """Decode QR codes using OpenCV's built-in detector."""
    try:
        import cv2, numpy as np  # type: ignore
        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        mat = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        det = cv2.QRCodeDetector()
        data, _, _ = det.detectAndDecode(mat)
        return [data] if data else []
    except Exception:
        return []


def _scan_qr_from_image_bytes(img_bytes: bytes) -> list[str]:
    """Try pyzbar first, then OpenCV. Return decoded QR strings."""
    results = _scan_qr_pyzbar(img_bytes) or _scan_qr_opencv(img_bytes)
    return [r.strip() for r in results if r.strip()]


# ── Apostille reference extraction from local PDFs ───────────────────────────

_APOSTILLE_URL_RE = re.compile(
    r'https?://apostille\.mygov\.bd/(?:application-details|verify|certificate)/([A-Za-z0-9_-]+)',
    re.IGNORECASE,
)
_APOSTILLE_TOKEN_RE = re.compile(
    r'\bBD[-/]?APO[-/]?\w{4,}\b|\bAPS[-/]?\d{6,}\b',
    re.IGNORECASE,
)


def extract_apostille_refs_from_pdf(pdf_path: str) -> list[str]:
    """Scan ALL pages of a local PDF for apostille.mygov.bd URLs (text, links, QR codes)."""
    urls: list[str] = []
    try:
        import fitz  # type: ignore
        doc = fitz.open(pdf_path)
        for page_num, page in enumerate(doc):
            # ── text & hyperlinks ──────────────────────────────────────────
            text = page.get_text()
            for m in _APOSTILLE_URL_RE.finditer(text):
                urls.append(m.group(0))
            for m in _APOSTILLE_TOKEN_RE.finditer(text):
                tok = m.group(0).strip("-/ ")
                urls.append(f"https://apostille.mygov.bd/verify/{tok}")
            for link in page.get_links():
                uri = link.get("uri", "")
                if "apostille.mygov.bd" in uri.lower():
                    urls.append(uri)

            # ── QR code scan at 200 DPI ────────────────────────────────────
            try:
                pix = page.get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
                for qr_text in _scan_qr_from_image_bytes(img_bytes):
                    if "apostille.mygov.bd" in qr_text.lower():
                        urls.append(qr_text)
                    elif _APOSTILLE_TOKEN_RE.search(qr_text):
                        tok = _APOSTILLE_TOKEN_RE.search(qr_text).group(0).strip("-/ ")
                        urls.append(f"https://apostille.mygov.bd/verify/{tok}")
            except Exception:
                pass

        doc.close()
    except Exception:
        pass

    # de-duplicate, preserve order
    seen: set[str] = set()
    result: list[str] = []
    for u in urls:
        u = u.strip()
        if u and u not in seen:
            seen.add(u)
            result.append(u)
    return result


def scan_folder_for_apostille_refs(folder: str) -> dict[str, list[str]]:
    """Walk folder, return {pdf_path: [apostille_url, ...]} for any PDFs with refs."""
    found: dict[str, list[str]] = {}
    for dirpath, _dirs, files in os.walk(folder):
        for fname in files:
            if not fname.lower().endswith(".pdf"):
                continue
            path = os.path.join(dirpath, fname)
            refs = extract_apostille_refs_from_pdf(path)
            if refs:
                found[path] = refs
    return found


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _browser_headers() -> dict:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://apostille.mygov.bd/",
    }


def _fetch_url(url: str, session=None, timeout: int = 30) -> bytes:
    try:
        import requests as _req  # type: ignore
        s = session or _req.Session()
        r = s.get(url, headers=_browser_headers(), timeout=timeout)
        r.raise_for_status()
        return r.content
    except ImportError:
        import urllib.request
        req = urllib.request.Request(url, headers=_browser_headers())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()


# ── citizenv2 URL pattern ─────────────────────────────────────────────────────

_CITIZENV2_RE = re.compile(
    r'https?://(?:www\.)?mygov\.bd/storage/citizenv2/[^"\'><\s\\]+\.(?:jpg|jpeg|png|webp)',
    re.IGNORECASE,
)
_CDN_IMG_RE = re.compile(
    r'(?:https?://)?mygov\.bd/storage/citizenv2/[^"\'><\s\\]+\.(?:jpg|jpeg|png|webp)',
    re.IGNORECASE,
)


def _extract_citizenv2(text: str) -> list[str]:
    raw = list(dict.fromkeys(_CITIZENV2_RE.findall(text)))
    if not raw:
        raw = [
            u if u.startswith("http") else "https://" + u
            for u in dict.fromkeys(_CDN_IMG_RE.findall(text))
        ]
    return raw


# ── Image acquisition strategies ─────────────────────────────────────────────

def _playwright_get_images(url: str, tmpdir: str, log: Callable | None = None) -> list[str]:
    """
    Use Playwright headless Chromium to:
      1. Navigate to the apostille.mygov.bd URL.
      2. Intercept and save any citizenv2 document images that load.
      3. Take a full-page screenshot of the rendered page.
    Returns list of local file paths (citizenv2 images first, then screenshot).
    """
    def _log(m):
        if log: log(m)
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        _log("  Playwright not installed — run: pip install playwright && playwright install chromium")
        return []

    paths: list[str] = []
    citizenv2_paths: list[str] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=_browser_headers()["User-Agent"],
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()

            # Intercept citizenv2 image responses as they load
            def _on_response(r):
                if "citizenv2" not in r.url.lower():
                    return
                try:
                    body = r.body()
                    ext = r.url.rsplit(".", 1)[-1].split("?")[0].lower()
                    if ext not in ("jpg", "jpeg", "png", "webp"):
                        ext = "jpg"
                    p2 = os.path.join(tmpdir, f"doc_{len(citizenv2_paths)}.{ext}")
                    with open(p2, "wb") as f:
                        f.write(body)
                    citizenv2_paths.append(p2)
                    _log(f"  ↳ Captured document image: {os.path.basename(p2)}")
                except Exception:
                    pass

            page.on("response", _on_response)

            _log(f"  Playwright: loading {url} …")
            page.goto(url, wait_until="networkidle", timeout=40000)

            # Wait an extra 3 s for any lazy-loaded images
            try:
                page.wait_for_timeout(3000)
            except Exception:
                pass

            # Also look for citizenv2 src attributes already in DOM
            try:
                srcs = page.eval_on_selector_all(
                    "img", "els => els.map(e => e.src)"
                )
                html = page.content()
                extra_urls = _extract_citizenv2(" ".join(srcs) + " " + html)
                if extra_urls and not citizenv2_paths:
                    # Try to download them
                    try:
                        import requests as _req  # type: ignore
                        s = _req.Session()
                    except ImportError:
                        s = None
                    for i, img_url in enumerate(extra_urls[:3]):
                        try:
                            data = _fetch_url(img_url, s)
                            ext = img_url.rsplit(".", 1)[-1].split("?")[0].lower()
                            if ext not in ("jpg", "jpeg", "png", "webp"):
                                ext = "jpg"
                            p2 = os.path.join(tmpdir, f"doc_dom_{i}.{ext}")
                            with open(p2, "wb") as f:
                                f.write(data)
                            citizenv2_paths.append(p2)
                        except Exception:
                            pass
            except Exception:
                pass

            # Take a full-page screenshot (this is our fallback readable image)
            screenshot_path = os.path.join(tmpdir, "apostille_screenshot.png")
            try:
                page.screenshot(path=screenshot_path, full_page=True)
                _log("  ✓ Page screenshot captured")
                paths.append(screenshot_path)
            except Exception as se:
                _log(f"  Screenshot failed: {se}")

            browser.close()

    except Exception as exc:
        _log(f"  Playwright error: {exc}")

    # citizenv2 document images come FIRST (they're the actual certificate/transcript)
    return citizenv2_paths + paths


def _selenium_get_images(url: str, tmpdir: str, log: Callable | None = None) -> list[str]:
    """Selenium fallback: screenshot + any citizenv2 image src attrs."""
    def _log(m):
        if log: log(m)
    try:
        from selenium import webdriver  # type: ignore
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.support.ui import WebDriverWait  # type: ignore
        from selenium.webdriver.support import expected_conditions as EC  # type: ignore
        from selenium.webdriver.common.by import By  # type: ignore
    except ImportError:
        _log("  Selenium not installed.")
        return []

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument(f"--user-agent={_browser_headers()['User-Agent']}")
    paths: list[str] = []
    try:
        driver = webdriver.Chrome(options=opts)
        driver.get(url)
        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.TAG_NAME, "img"))
            )
        except Exception:
            pass
        time.sleep(3)

        # Screenshot
        screenshot_path = os.path.join(tmpdir, "apostille_selenium.png")
        driver.save_screenshot(screenshot_path)
        paths.append(screenshot_path)

        # citizenv2 images
        srcs = [el.get_attribute("src") or ""
                for el in driver.find_elements(By.TAG_NAME, "img")]
        html = driver.page_source
        extra_urls = _extract_citizenv2(" ".join(srcs) + " " + html)
        if extra_urls:
            try:
                import requests as _req  # type: ignore
                s = _req.Session()
            except ImportError:
                s = None
            for i, img_url in enumerate(extra_urls[:3]):
                try:
                    data = _fetch_url(img_url, s)
                    ext = img_url.rsplit(".", 1)[-1].split("?")[0].lower()
                    if ext not in ("jpg", "jpeg", "png", "webp"):
                        ext = "jpg"
                    p2 = os.path.join(tmpdir, f"doc_sel_{i}.{ext}")
                    with open(p2, "wb") as f:
                        f.write(data)
                    paths.insert(0, p2)  # document images first
                except Exception:
                    pass
        driver.quit()
    except Exception as exc:
        _log(f"  Selenium error: {exc}")
    return paths


def _requests_get_images(url: str, tmpdir: str, log: Callable | None = None) -> list[str]:
    """Pure HTTP strategy: works only when __NEXT_DATA__ includes citizenv2 URLs."""
    def _log(m):
        if log: log(m)
    try:
        import requests as _req  # type: ignore
        session = _req.Session()
    except ImportError:
        session = None

    try:
        html = _fetch_url(url, session).decode("utf-8", errors="replace")
    except Exception:
        return []

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    build_id = ""
    img_urls: list[str] = []
    if m:
        try:
            data = json.loads(m.group(1))
            img_urls = _extract_citizenv2(json.dumps(data))
            build_id = data.get("buildId", "")
        except Exception:
            pass

    if not img_urls:
        if not build_id:
            mb = re.search(r'/_next/static/([^/"]{10,})/_buildManifest\.js', html)
            if mb:
                build_id = mb.group(1)
        img_urls = _extract_citizenv2(html)

    if not img_urls and build_id:
        path_seg = re.sub(r'^https?://[^/]+', '', url).rstrip("/")
        data_url = f"https://apostille.mygov.bd/_next/data/{build_id}{path_seg}.json"
        try:
            resp = _fetch_url(data_url, session, timeout=20)
            img_urls = _extract_citizenv2(resp.decode("utf-8", errors="replace"))
        except Exception:
            pass

    if not img_urls:
        path_match = re.search(
            r'/(?:application-details|verify|certificate)/([A-Za-z0-9_-]+)', url
        )
        if path_match:
            token = path_match.group(1)
            for api_path in [
                f"https://apostille.mygov.bd/api/application/{token}",
                f"https://apostille.mygov.bd/api/verify/{token}",
                f"https://apostille.mygov.bd/api/certificate/{token}",
            ]:
                try:
                    resp = _fetch_url(api_path, session, timeout=15)
                    img_urls = _extract_citizenv2(resp.decode("utf-8", errors="replace"))
                    if img_urls:
                        break
                except Exception:
                    continue

    if not img_urls:
        return []

    paths: list[str] = []
    for i, img_url in enumerate(img_urls[:4]):
        try:
            data = _fetch_url(img_url, session)
            ext = img_url.rsplit(".", 1)[-1].split("?")[0].lower()
            if ext not in ("jpg", "jpeg", "png", "webp"):
                ext = "jpg"
            p2 = os.path.join(tmpdir, f"doc_req_{i}.{ext}")
            with open(p2, "wb") as f:
                f.write(data)
            paths.append(p2)
            _log(f"  ↳ Downloaded citizenv2 image {i+1}")
        except Exception:
            pass
    return paths


def get_apostille_images(
    apostille_url: str,
    tmpdir: str,
    log: Callable | None = None,
) -> list[str]:
    """
    Acquire images for the apostille page. Returns list of local file paths.
    Strategy order:
      1. HTTP + Next.js (fast, works when page is SSR)
      2. Playwright headless (handles JS-rendered pages, also takes screenshot)
      3. Selenium (last resort)
    Always returns at least a screenshot if Playwright/Selenium is available.
    """
    def _log(m):
        if log: log(m)

    _log("  [1/3] HTTP + Next.js data fetch …")
    paths = _requests_get_images(apostille_url, tmpdir, log)
    if paths:
        _log(f"  ✓ Got {len(paths)} image(s) via HTTP")
        return paths

    _log("  [2/3] Playwright headless browser (screenshot + intercept) …")
    paths = _playwright_get_images(apostille_url, tmpdir, log)
    if paths:
        _log(f"  ✓ Got {len(paths)} image(s) via Playwright")
        return paths

    _log("  [3/3] Selenium WebDriver (fallback) …")
    paths = _selenium_get_images(apostille_url, tmpdir, log)
    if paths:
        _log(f"  ✓ Got {len(paths)} image(s) via Selenium")
        return paths

    _log("  ✗ All strategies failed — no images captured")
    return []


# ── Legacy alias (kept for compatibility) ─────────────────────────────────────

def get_citizenv2_image_urls(apostille_url: str, log=None) -> list[str]:
    """Legacy function — returns local image paths using get_apostille_images()."""
    with tempfile.TemporaryDirectory(prefix="sfm_apo_legacy_") as tmpdir:
        return get_apostille_images(apostille_url, tmpdir, log)


# ── GPT-4o Vision reading ─────────────────────────────────────────────────────

_GPT4O_PROMPT = (
    "You are reading images from the Bangladesh e-Apostille / apostille.mygov.bd portal "
    "OR the underlying academic/government document (certificate, transcript, etc.).\n\n"
    "The images may show:\n"
    "  A) The apostille.mygov.bd VERIFICATION PAGE — a web page with the apostille stamp details.\n"
    "  B) The actual DOCUMENT — a scanned certificate, transcript, birth certificate, etc.\n"
    "  C) BOTH (apostille stamp attached to the document).\n\n"
    "Extract ALL available fields (use null for missing):\n"
    "  student_name        : Full name of the document holder (CAPS)\n"
    "  father_name         : Father's full name\n"
    "  mother_name         : Mother's full name\n"
    "  institution         : Issuing school / college / university / authority\n"
    "  degree              : Exact qualification (e.g. 'Secondary School Certificate', 'B.Sc Honours')\n"
    "  subject             : Major subject or field\n"
    "  doc_type            : One of — ssc | hsc | diploma | bachelor | master | phd | "
    "birth_certificate | family_certificate | marriage_certificate | nid | passport | "
    "police_clearance | other\n"
    "  roll_no             : Examination roll number\n"
    "  reg_no              : Registration number\n"
    "  session             : Academic session (e.g. '2019-2020')\n"
    "  board               : Education board (e.g. 'Dhaka Board', 'BTEB')\n"
    "  apostille_ref       : Apostille reference / token number\n"
    "  apostille_country   : Country for which apostille was issued (e.g. 'South Korea')\n"
    "  result              : Grade / GPA / class (e.g. 'GPA 5.00', 'CGPA 3.75')\n"
    "  issue_date          : Date of issue (as shown)\n\n"
    "IMPORTANT: If the image is a WEB PAGE SCREENSHOT (browser address bar visible, "
    "or you see a navigation header), focus on the certificate/document details shown "
    "on that web page — NOT the browser UI itself.\n\n"
    "Reply ONLY with valid compact JSON. No markdown, no explanation."
)


def read_document_with_gpt4o(image_path: str, api_key: str) -> dict:
    """Send a document image to GPT-4o Vision and return structured info dict."""
    import base64, urllib.request
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    payload = json.dumps({
        "model": "gpt-4o", "max_tokens": 1000,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _GPT4O_PROMPT},
            {"type": "image_url", "image_url": {
                "url": f"data:{mime};base64,{b64}", "detail": "high"}},
        ]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    text = result["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()
    info = json.loads(text)
    detected = detect_document_type(info)
    if info.get("doc_type") in (None, "other", "") and detected != "other":
        info["doc_type"] = detected
    info["_detected_type"] = detected
    return info


def read_pdf_page_with_gpt4o(pdf_path: str, page_num: int, api_key: str) -> dict:
    """Render a PDF page as image and send to GPT-4o — useful for apostille pages in local PDFs."""
    import base64, urllib.request
    import fitz  # type: ignore
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num)
    pix = page.get_pixmap(dpi=200)
    img_bytes = pix.tobytes("png")
    doc.close()
    b64 = base64.b64encode(img_bytes).decode()
    payload = json.dumps({
        "model": "gpt-4o", "max_tokens": 1000,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _GPT4O_PROMPT},
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{b64}", "detail": "high"}},
        ]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    text = result["choices"][0]["message"]["content"].strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()
    info = json.loads(text)
    detected = detect_document_type(info)
    if info.get("doc_type") in (None, "other", "") and detected != "other":
        info["doc_type"] = detected
    info["_detected_type"] = detected
    return info


# ── Local PDF matching ────────────────────────────────────────────────────────

def _tokenise(s: str) -> set[str]:
    return set(re.findall(r"\w+", s.upper()))


def _name_similarity(a: str, b: str) -> float:
    ta, tb = _tokenise(a), _tokenise(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


_TYPE_FILENAME_HINTS: dict[str, list[str]] = {
    "ssc":                ["ssc", "secondary", "class10", "class-10", "jsc"],
    "hsc":                ["hsc", "higher secondary", "class12", "class-12", "alim", "dakhil"],
    "diploma":            ["diploma", "polytechnic", "dip"],
    "bachelor":           ["bachelor", "bsc", "honours", "degree", "grad", "llb", "mbbs"],
    "master":             ["master", "msc", "mphil", "postgrad"],
    "phd":                ["phd", "doctorate"],
    "birth_certificate":  ["birth", "janma"],
    "family_certificate": ["family", "nagarik"],
    "marriage_certificate": ["marriage", "nikah", "kabin"],
    "nid":                ["nid", "national id", "voter"],
    "passport":           ["passport"],
    "police_clearance":   ["police", "clearance", "character"],
}


def find_matching_pdf(folder: str, info: dict, threshold: float = 0.35) -> str | None:
    """Find the best-matching local PDF for the given apostille info dict."""
    student  = (info.get("student_name") or "").strip()
    detected = info.get("_detected_type") or detect_document_type(info)
    hints    = _TYPE_FILENAME_HINTS.get(detected, [])
    roll     = (info.get("roll_no") or "").strip()
    reg      = (info.get("reg_no") or "").strip()
    best_path, best_score = None, 0.0
    for dirpath, _dirs, files in os.walk(folder):
        for fname in files:
            if not fname.lower().endswith(".pdf"):
                continue
            stem = os.path.splitext(fname)[0].lower()
            score = 0.0
            if student:
                score += _name_similarity(student, stem) * 1.5
            for hint in hints:
                if hint in stem:
                    score += 0.6
                    break
            if roll and roll in stem:
                score += 0.8
            if reg and reg in stem:
                score += 0.6
            if score > best_score:
                best_score, best_path = score, os.path.join(dirpath, fname)
    return best_path if best_score >= threshold else None


# ── Document role classification ──────────────────────────────────────────────

# Keywords that identify the PURPOSE of a PDF from its filename
_CERT_ROLE_HINTS        = ["certificate", "cert", "সনদ", "সার্টিফিকেট"]
_TRANSCRIPT_ROLE_HINTS  = [
    "transcript", "marksheet", "mark sheet", "marks", "grade sheet",
    "result sheet", "academic record",
]
_APOSTILLE_ROLE_HINTS   = ["apostille", "appostille", "apostil", "e-apostille"]


def classify_pdf_role(filename: str) -> str:
    """Return 'apostille' | 'certificate' | 'transcript' | 'unknown' from a filename."""
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    if any(h in stem for h in _APOSTILLE_ROLE_HINTS):
        return "apostille"
    if any(h in stem for h in _TRANSCRIPT_ROLE_HINTS):
        return "transcript"
    if any(h in stem for h in _CERT_ROLE_HINTS):
        return "certificate"
    return "unknown"


def find_document_set(
    folder: str,
    doc_type: str,
    student_name: str = "",
    exclude_paths: set | None = None,
) -> dict:
    """
    Find the best certificate AND transcript PDF for a given doc_type in a folder.

    Returns:
        {
          "certificate": "/path/to/ssc_certificate.pdf" or None,
          "transcript":  "/path/to/ssc_transcript.pdf"  or None,
        }

    Scoring per file:
      +1.2  doc-type hint found in filename  (ssc, hsc, bachelor …)
      +1.0  role hint found  (certificate / transcript)
      +0.8  student name tokens match filename
      +0.8  roll_no or reg_no in filename (if provided via extra_info)
    """
    type_hints = _TYPE_FILENAME_HINTS.get(doc_type, [])
    exclude    = exclude_paths or set()

    # best[role] = (score, path)
    best: dict[str, tuple[float, str]] = {}

    for dirpath, _dirs, files in os.walk(folder):
        for fname in files:
            if not fname.lower().endswith(".pdf"):
                continue
            fpath = os.path.normpath(os.path.join(dirpath, fname))
            if fpath in exclude:
                continue

            stem = os.path.splitext(fname)[0].lower()

            # Skip apostille PDFs — we don't want to match them as cert/transcript
            if any(h in stem for h in _APOSTILLE_ROLE_HINTS):
                continue

            role = classify_pdf_role(fname)
            if role not in ("certificate", "transcript"):
                # If role is unknown, check stem for cert/transcript hints
                if any(h in stem for h in _CERT_ROLE_HINTS):
                    role = "certificate"
                elif any(h in stem for h in _TRANSCRIPT_ROLE_HINTS):
                    role = "transcript"
                else:
                    role = None  # unknown — may still qualify via type+name score

            score = 0.0

            # Doc-type hint: weighted bonus, NOT a hard requirement
            type_match = any(hint in stem for hint in type_hints)
            if type_match:
                score += 1.2

            # Role keyword bonus
            if role == "certificate":
                score += 1.0
            elif role == "transcript":
                score += 1.0

            # Student name match
            if student_name:
                score += _name_similarity(student_name, stem) * 0.8

            # Need at least some signal to avoid random matches
            if score < 0.5:
                continue

            # If we still don't know the role, default to certificate
            if role is None:
                role = "certificate"

            prev = best.get(role)
            if prev is None or score > prev[0]:
                best[role] = (score, fpath)

    return {
        "certificate": best["certificate"][1] if "certificate" in best else None,
        "transcript":  best["transcript"][1]  if "transcript"  in best else None,
    }


# ── Multi-PDF bundle merge ────────────────────────────────────────────────────

def merge_apostille_bundle(
    apostille_img_paths: list,
    ordered_pdf_paths: list,
    output_path: str,
) -> str:
    """
    Build one merged PDF in this order:
      1. Apostille verification image(s)  (converted from PNG/JPG to PDF pages)
      2. Each PDF in ordered_pdf_paths    (certificate first, then transcript, etc.)

    ordered_pdf_paths entries can be None — they are silently skipped.
    Returns output_path.
    """
    import fitz  # type: ignore
    try:
        from PIL import Image as _PIL  # type: ignore
        pil_ok = True
    except ImportError:
        pil_ok = False

    result = fitz.open()

    # ── apostille images → PDF pages ──────────────────────────────────────────
    for img_path in apostille_img_paths:
        try:
            if pil_ok:
                with _PIL.open(img_path) as im:
                    w_px, h_px = im.size
                    dpi_info = im.info.get("dpi", (200, 200))
                    dpi_v = dpi_info[0] if isinstance(dpi_info, tuple) else dpi_info
                    w_pt = w_px / dpi_v * 72
                    h_pt = h_px / dpi_v * 72
            else:
                w_pt, h_pt = 595.0, 842.0
            page = result.new_page(width=w_pt, height=h_pt)
            page.insert_image(fitz.Rect(0, 0, w_pt, h_pt), filename=img_path)
        except Exception:
            page = result.new_page(width=595, height=842)
            try:
                page.insert_image(fitz.Rect(0, 0, 595, 842), filename=img_path)
            except Exception:
                pass

    # ── append each source PDF ────────────────────────────────────────────────
    for pdf_path in ordered_pdf_paths:
        if not pdf_path:
            continue
        try:
            src = fitz.open(pdf_path)
            result.insert_pdf(src)
            src.close()
        except Exception:
            pass

    result.save(output_path, garbage=4, deflate=True)
    result.close()
    return output_path


# ── PDF merging ───────────────────────────────────────────────────────────────

def merge_apostille_into_pdf(
    apostille_img_paths: list[str],
    base_pdf_path: str,
    output_path: str,
    apostille_first: bool = True,
) -> str:
    """
    Merge apostille page images into the matched local PDF.

    apostille_first=True  → apostille page(s) come BEFORE the document pages
    apostille_first=False → apostille page(s) come AFTER the document pages

    Returns output_path.
    """
    import fitz  # type: ignore
    try:
        from PIL import Image as _PIL  # type: ignore
        pil_available = True
    except ImportError:
        pil_available = False

    # Build a PDF from the apostille images
    apo_pdf_path = output_path + "_apo_tmp.pdf"
    apo_doc = fitz.open()
    for img_path in apostille_img_paths:
        try:
            # Use PIL to get image dimensions if available
            if pil_available:
                with _PIL.open(img_path) as im:
                    w_px, h_px = im.size
                    dpi = im.info.get("dpi", (200, 200))
                    dpi_x = dpi[0] if isinstance(dpi, tuple) else dpi
                    w_pt = w_px / dpi_x * 72
                    h_pt = h_px / dpi_x * 72
            else:
                w_pt, h_pt = 595, 842  # A4 fallback
            rect = fitz.Rect(0, 0, w_pt, h_pt)
            page = apo_doc.new_page(width=w_pt, height=h_pt)
            page.insert_image(rect, filename=img_path)
        except Exception:
            # Fallback: A4 page
            page = apo_doc.new_page(width=595, height=842)
            page.insert_image(fitz.Rect(0, 0, 595, 842), filename=img_path)

    apo_doc.save(apo_pdf_path)
    apo_doc.close()

    # Merge with the base PDF
    base_doc = fitz.open(base_pdf_path)
    extra_doc = fitz.open(apo_pdf_path)

    result_doc = fitz.open()
    if apostille_first:
        result_doc.insert_pdf(extra_doc)
        result_doc.insert_pdf(base_doc)
    else:
        result_doc.insert_pdf(base_doc)
        result_doc.insert_pdf(extra_doc)

    result_doc.save(output_path, garbage=4, deflate=True)
    result_doc.close()
    base_doc.close()
    extra_doc.close()

    # Clean up temp file
    try:
        os.remove(apo_pdf_path)
    except Exception:
        pass

    return output_path


# ── Output helpers ────────────────────────────────────────────────────────────

def _safe_fname(s: str, n: int = 60) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(s)).strip()[:n]


# ── Core processing ───────────────────────────────────────────────────────────

def process_apostille_url(
    apostille_url: str,
    local_folder: str,
    output_folder: str,
    api_key: str,
    log: Callable = print,
    manual_image_urls: list[str] | None = None,
    stop_flag: list[bool] | None = None,
) -> dict:
    """
    Full pipeline for one apostille URL:
      1. Acquire page images (HTTP → Playwright screenshot → Selenium)
      2. GPT-4o reads the images to extract student/doc info
      3. Find matching local PDF
      4. MERGE apostille images into the matched PDF (apostille first)
    """
    def _stopped() -> bool:
        return bool(stop_flag and stop_flag[0])

    os.makedirs(output_folder, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sfm_apostille_") as tmpdir:
        try:
            # ── 1. Get images ──────────────────────────────────────────────
            if manual_image_urls:
                log(f"  Using {len(manual_image_urls)} manually provided image URL(s)")
                img_paths: list[str] = []
                try:
                    import requests as _req  # type: ignore
                    s = _req.Session()
                except ImportError:
                    s = None
                for i, img_url in enumerate(manual_image_urls[:4]):
                    try:
                        data = _fetch_url(img_url, s)
                        ext = img_url.rsplit(".", 1)[-1].split("?")[0].lower()
                        if ext not in ("jpg", "jpeg", "png", "webp"):
                            ext = "jpg"
                        p2 = os.path.join(tmpdir, f"manual_{i}.{ext}")
                        with open(p2, "wb") as f:
                            f.write(data)
                        img_paths.append(p2)
                    except Exception:
                        pass
            else:
                img_paths = get_apostille_images(apostille_url, tmpdir, log)

            if not img_paths:
                return {
                    "error": (
                        "No images could be captured from the apostille page.\n"
                        "• Make sure Playwright is installed: pip install playwright && playwright install chromium\n"
                        "• Or paste the raw citizenv2 image URL manually in the 'Manual URL' box."
                    ),
                    "url": apostille_url,
                }

            if _stopped():
                return {"error": "Stopped by user.", "url": apostille_url}

            # ── 2. GPT-4o reads the images ────────────────────────────────
            log(f"  Reading {len(img_paths)} image(s) with GPT-4o Vision …")
            info: dict = {}
            last_err = ""
            for img_path in img_paths:
                try:
                    info = read_document_with_gpt4o(img_path, api_key)
                    # If we got a student name we're done
                    if info.get("student_name"):
                        log(f"  ✓ GPT-4o read: {os.path.basename(img_path)}")
                        break
                except Exception as gpt_err:
                    last_err = str(gpt_err)
                    log(f"  GPT-4o error on {os.path.basename(img_path)}: {gpt_err}")

            if not info:
                return {"error": f"GPT-4o failed on all images: {last_err}", "url": apostille_url}

            dtype_label = DOC_TYPE_LABELS.get(info.get("_detected_type") or "other", "?")
            log(
                f"  → Name: {info.get('student_name') or '?'}  |  "
                f"Type: {dtype_label}  |  Degree: {info.get('degree') or '?'}"
            )
            for extra in [
                f"Roll: {info['roll_no']}" if info.get("roll_no") else "",
                f"Board: {info['board']}" if info.get("board") else "",
                f"Result: {info['result']}" if info.get("result") else "",
                f"Session: {info['session']}" if info.get("session") else "",
                f"Apostille ref: {info['apostille_ref']}" if info.get("apostille_ref") else "",
            ]:
                if extra:
                    log(f"     {extra}")

            if _stopped():
                return {"error": "Stopped by user.", "url": apostille_url}

            # ── 3. Find certificate + transcript for this doc type ────────
            student  = _safe_fname(info.get("student_name") or "Unknown")
            degree   = _safe_fname(info.get("degree") or "")
            detected = info.get("_detected_type") or "other"

            doc_set: dict = {"certificate": None, "transcript": None}
            if local_folder:
                doc_set = find_document_set(local_folder, detected, student_name=student)
                cert_pdf = doc_set.get("certificate")
                trans_pdf = doc_set.get("transcript")
                if cert_pdf:
                    log(f"  ✓ Certificate : {os.path.basename(cert_pdf)}")
                else:
                    log("  ✗ Certificate  : not found")
                if trans_pdf:
                    log(f"  ✓ Transcript  : {os.path.basename(trans_pdf)}")
                else:
                    log("  ✗ Transcript   : not found")

            cert_pdf  = doc_set.get("certificate")
            trans_pdf = doc_set.get("transcript")

            # ── 4. Build merged bundle ─────────────────────────────────────
            # Order: apostille page(s) → certificate → transcript
            base_name = _safe_fname(student + (f" - {degree}" if degree else ""))
            os.makedirs(output_folder, exist_ok=True)

            merged_pdf_path: str | None = None
            apostille_only_path: str | None = None

            ordered_pdfs = [p for p in [cert_pdf, trans_pdf] if p]

            if ordered_pdfs or img_paths:
                merged_pdf_path = os.path.join(
                    output_folder,
                    _safe_fname(f"{base_name} - {dtype_label} with apostille.pdf"),
                )
                try:
                    merge_apostille_bundle(img_paths, ordered_pdfs, merged_pdf_path)
                    parts = []
                    if img_paths:   parts.append("apostille")
                    if cert_pdf:    parts.append("certificate")
                    if trans_pdf:   parts.append("transcript")
                    log(f"  ✓ Merged PDF  : {os.path.basename(merged_pdf_path)}")
                    log(f"     Contents    : {' + '.join(parts)}")
                except Exception as merge_err:
                    log(f"  Merge error: {merge_err}")
                    merged_pdf_path = None

            # If nothing merged, at least save the apostille images as a PDF
            if not merged_pdf_path and img_paths:
                apostille_only_path = os.path.join(
                    output_folder,
                    _safe_fname(f"{base_name} - Apostille only.pdf"),
                )
                try:
                    import fitz  # type: ignore
                    apo_doc = fitz.open()
                    for img_path in img_paths:
                        pg = apo_doc.new_page(width=595, height=842)
                        pg.insert_image(fitz.Rect(0, 0, 595, 842), filename=img_path)
                    apo_doc.save(apostille_only_path)
                    apo_doc.close()
                    log(f"  Apostille-only PDF: {os.path.basename(apostille_only_path)}")
                except Exception as e:
                    log(f"  Could not save apostille PDF: {e}")
                    apostille_only_path = None

            result = {
                "url":            apostille_url,
                "info":           info,
                "merged_pdf":     merged_pdf_path,
                "apostille_only": apostille_only_path,
                "matched_local":  cert_pdf or trans_pdf,
                "certificate_pdf": cert_pdf,
                "transcript_pdf":  trans_pdf,
                "student_name":   student,
                "degree":         degree,
                "doc_type":       dtype_label,
                "detected_type":  detected,
                "roll_no":        info.get("roll_no") or "",
                "reg_no":         info.get("reg_no") or "",
                "board":          info.get("board") or "",
                "result":         info.get("result") or "",
                "apostille_ref":  info.get("apostille_ref") or "",
                "issue_date":     info.get("issue_date") or "",
            }
            return result

        except Exception as exc:
            import traceback
            log(f"  Exception: {exc}")
            return {"error": str(exc), "url": apostille_url, "_tb": traceback.format_exc()}


def process_apostille_batch(
    apostille_urls: list[str],
    local_folder: str,
    output_folder: str,
    api_key: str,
    log: Callable = print,
    progress: Callable | None = None,
    manual_urls_per_apostille: list | None = None,
    stop_flag: list[bool] | None = None,
) -> list[dict]:
    """Process a list of apostille URLs sequentially."""
    results: list[dict] = []
    for i, url in enumerate(apostille_urls):
        if stop_flag and stop_flag[0]:
            log("Stopped by user.")
            break
        if progress:
            progress(i, len(apostille_urls))
        manual = (manual_urls_per_apostille[i]
                  if manual_urls_per_apostille and i < len(manual_urls_per_apostille)
                  else None)
        results.append(
            process_apostille_url(
                url, local_folder, output_folder, api_key,
                log=log, manual_image_urls=manual, stop_flag=stop_flag,
            )
        )
        time.sleep(0.4)
    if progress:
        progress(len(results), len(apostille_urls))
    return results


# ── Full folder pipeline ──────────────────────────────────────────────────────

def process_student_folder(
    folder: str,
    output_folder: str,
    api_key: str,
    log: Callable = print,
    progress: Callable | None = None,
    stop_flag: list[bool] | None = None,
) -> list[dict]:
    """
    100% automatic pipeline for a student folder.

    Steps
    -----
    1. Scan every PDF in `folder` for apostille.mygov.bd URLs
       (text, hyperlinks, and QR codes on all pages — no API needed).
    2. For each apostille URL found:
       a. Download the apostille verification page via Playwright
          (headless browser, no API).
       b. Send the captured screenshot / doc images to GPT-4o Vision
          → extracts student name, doc type, roll number, board, result …
       c. Classify doc type: ssc / hsc / diploma / bachelor / master / …
       d. Find the matching certificate PDF  }  by scanning filenames
          Find the matching transcript PDF   }  in the same folder
       e. Merge in order:
            apostille verification page(s)
            + certificate PDF              (if found)
            + transcript PDF               (if found)
          → saved to output_folder as "{Name} - {TYPE} with apostille.pdf"
    3. Return a list of result dicts (one per apostille processed).

    Result dict keys
    ----------------
    merged_pdf        – path to the merged bundle (or None if merge failed)
    apostille_only    – path to apostille-only PDF (when cert/transcript missing)
    certificate_pdf   – matched certificate path
    transcript_pdf    – matched transcript path
    student_name      – extracted name
    doc_type          – human label  (e.g. "SSC Certificate")
    detected_type     – machine key  (e.g. "ssc")
    roll_no / reg_no / board / result / apostille_ref / issue_date
    source_pdf        – PDF in which the apostille URL was found
    apostille_url     – the URL that was processed
    error             – set only on failure
    """
    def _stopped() -> bool:
        return bool(stop_flag and stop_flag[0])

    os.makedirs(output_folder, exist_ok=True)
    results: list[dict] = []

    # ── Step 1: scan every PDF for apostille URLs ─────────────────────────────
    log("Scanning folder for apostille references (QR codes, text, hyperlinks) …")
    log(f"  Folder: {folder}")

    pdf_to_urls: dict[str, list[str]] = scan_folder_for_apostille_refs(folder)

    if not pdf_to_urls:
        log("  No apostille references found in any PDF.")
        return results

    # Flatten into (apostille_url, source_pdf) pairs, de-duplicate URLs
    seen_urls: set[str] = set()
    tasks: list[tuple[str, str]] = []  # (url, source_pdf)
    for pdf_path, urls in pdf_to_urls.items():
        for url in urls:
            url = url.strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                tasks.append((url, pdf_path))
                log(f"  [{len(tasks)}] {os.path.basename(pdf_path)}  →  {url}")

    log(f"\nFound {len(tasks)} unique apostille URL(s) to process.\n")

    # ── Steps 2–5: process each apostille URL ─────────────────────────────────
    for i, (apostille_url, source_pdf) in enumerate(tasks):
        if _stopped():
            log("Stopped by user.")
            break

        if progress:
            progress(i, len(tasks))

        log(f"{'─'*60}")
        log(f"[{i+1}/{len(tasks)}]  {apostille_url}")
        log(f"  Source PDF : {os.path.basename(source_pdf)}")

        result = process_apostille_url(
            apostille_url=apostille_url,
            local_folder=folder,
            output_folder=output_folder,
            api_key=api_key,
            log=log,
            stop_flag=stop_flag,
        )
        result["source_pdf"]     = source_pdf
        result["apostille_url"]  = apostille_url

        results.append(result)

        if result.get("error"):
            log(f"  ✗ Error: {result['error']}")
        else:
            merged = result.get("merged_pdf")
            if merged:
                parts: list[str] = ["apostille"]
                if result.get("certificate_pdf"):
                    parts.append("certificate")
                if result.get("transcript_pdf"):
                    parts.append("transcript")
                log(f"  ✓ Output : {os.path.basename(merged)}")
                log(f"     = {' + '.join(parts)}")
            else:
                log("  ✗ No merged PDF produced")

        time.sleep(0.3)

    if progress:
        progress(len(results), len(tasks))

    log(f"\n{'═'*60}")
    ok_count  = sum(1 for r in results if not r.get("error") and r.get("merged_pdf"))
    err_count = sum(1 for r in results if r.get("error"))
    log(f"Done.  {ok_count} bundle(s) created,  {err_count} error(s).")
    log(f"Output folder: {output_folder}")
    return results
