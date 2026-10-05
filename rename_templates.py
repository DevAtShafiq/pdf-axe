"""
rename_templates.py — document-name templates for the F2 rename suggestions.

Owns everything that used to live in ``student_folder_maker.py`` for document
name templates (built-in English↔Korean list, the ``document_name_templates.txt``
user file, scoring / filtering and filename stems) and adds a small
data-driven *language registry*:

* ``"en"`` — International: English-only names, file name ``Passport.pdf``.
* ``"ko"`` — Korean: today's bilingual behaviour, file name ``여권-passport.pdf``.

Adding another language (ja, zh, ar, vi, …) only needs one more entry in
``LANGUAGES`` with a built-in ``English-Local`` text block; its custom rows go
to ``document_name_templates_<code>.txt`` next to the app.

User-file format (unchanged, backward compatible): one ``English-Local`` pair per
line, split on the LAST hyphen, ``#`` starts a comment, one side may be blank
(``Passport-`` is an English-only row, ``-여권`` a Korean-only row).  A line with
no hyphen at all is accepted as a one-sided row (Hangul → local, else English).

Never imports tkinter; safe to import from the web bridge and tests.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from dataclasses import dataclass, field

INVALID_WIN_FILENAME_CHARS = frozenset('\\/:*?"<>|')

USER_FILE_NAME = "document_name_templates.txt"
DEFAULT_LANG = "ko"          # today's behaviour (KR-EN names)

# ---------------------------------------------------------------------------
# Built-in lists
# ---------------------------------------------------------------------------

# Bilingual document names (line format: English-Korean; filename uses KR-EN).
# Copied verbatim from student_folder_maker._RAW_DOCUMENT_RENAME_LINES.
_RAW_KO_LINES = """
admission application and other forms-입학지원서 및 기타 서식
application form-입학지원서
application registration number-수험표
study plan-학업계획서
photo-증명사진
ID photo-증명사진
passport-여권
applicant birth certificate-지원자 출생증명서
applicant birth certificate translation-지원자 출생증명서 번역본
birth certificate-출생증명서
birth certificate translation-출생증명서 번역본
applicant ID card-지원자 신분증
applicant NID translation-지원자 신분증 번역본
father ID card-아버지 신분증
father NID translation-아버지 신분증 번역본
mother ID card-어머니 신분증
mother NID translation-어머니 신분증 번역본
applicant and parents ID cards-지원자 및 부모 신분증
father passport-아버지 여권
mother passport-어머니 여권
father death certificate-아버지 사망진단서
father death certificate translation-아버지 사망진단서 번역본
mother death certificate-어머니 사망진단서
mother death certificate translation-어머니 사망진단서 번역본
death certificate-사망진단서
death certificate translation-사망진단서 번역본
family relationship certificate-가족관계증명서
family relationship certificate translation-가족관계증명서 번역본
marriage certificate-혼인증명서
marriage certificate translation-혼인증명서 번역본
AFFIDAVIT OF SAME NAME & SAME PERSON (Father)-아버지 동일인 확인서
AFFIDAVIT OF SAME NAME & SAME PERSON (Mother)-어머니 동일인 확인서
AFFIDAVIT OF SAME NAME & SAME PERSON (Parents)-부모 동일인 확인서
bank balance proof-은행잔고 증명서
bank solvency certificate-은행잔고 증명서
bank statement-은행 거래내역서
swift copy-SWIFT 송금내역
business registration-사업자 등록증
trade license-사업자 등록증
employment certificate-재직증명서
guarantor job certificate-보증인 재직증명서
tin certificate-납세자등록증명서
guarantor tin certificate-보증인 납세자등록증명서
guarantor tin certificate and tax certificate-보증인 납세자등록증명서 및 소득금액 증명서
tax certificate-소득금액 증명서
guarantor income tax-보증인 소득금액 증명서
tax return acknowledgment-소득금액신고 확인서
sponsor business registration-보증인 사업자 등록증
guarantor business registration and tax certificate-보증인 사업자 등록증 및 소득금액 증명서
guarantor business registration and tin certificate-보증인 사업자 등록증 및 납세자등록증명서
business registration tin and income tax certificate-보증인 사업자 등록증, 납세자등록증명서 및 소득금액 증명서
sponsor business registration tin income tax certificate and tax payment receipt-보증인 사업자 등록증, 납세자등록증명서, 소득금액 증명서 및 소득세 납부영수증
sponsor employment certificate tin and income tax certificate-보증인 재직증명서, 납세자등록증명서 및 소득금액 증명서
sponsor employment certificate tin income tax certificate & tax payment receipt-보증인 재직증명서, 납세자등록증명서, 소득금액 증명서 및 소득세 납부영수증
IELTS-아이엘츠
TOPIK-토픽
TB test certificate-결핵진단서
medical test report-건강검진 결과서
recommendation from college principal-전문대학 총장 추천서
recommendation from university professor-대학교수 추천서
sponsor letter-보증인 보증서
SSC certificate with apostille-중학교 졸업증명서 (아포스티유 포함)
SSC transcript with apostille-중학교 성적증명서 (아포스티유 포함)
SSC certificate (no apostille)-중학교 졸업증명서 (아포스티유 미포함)
SSC transcript (no apostille)-중학교 성적증명서 (아포스티유 미포함)
middle school graduation certificate & transcript-중학교 졸업 및 성적증명서 (아포스티유 포함)
middle school graduation certificate & transcript (no apostille)-중학교 졸업 및 성적증명서 (아포스티유 미포함)
HSC certificate with apostille-고등학교 졸업증명서 (아포스티유 포함)
HSC transcript with apostille-고등학교 성적증명서 (아포스티유 포함)
HSC certificate (no apostille)-고등학교 졸업증명서 (아포스티유 미포함)
HSC transcript (no apostille)-고등학교 성적증명서 (아포스티유 미포함)
high school graduation certificate & transcript-고등학교 졸업 및 성적증명서 (아포스티유 포함)
high school graduation certificate & transcript (no apostille)-고등학교 졸업 및 성적증명서 (아포스티유 미포함)
bachelor certificate with apostille-학사 학위증명서 (아포스티유 포함)
bachelor transcript with apostille-학사 성적증명서 (아포스티유 포함)
bachelor certificate (no apostille)-학사 학위증명서 (아포스티유 미포함)
bachelor transcript (no apostille)-학사 성적증명서 (아포스티유 미포함)
bachelor certificate & transcript with apostille-학부 졸업증명서 및 성적증명서 (아포스티유 포함)
bachelor certificate & transcript without apostille-학부 졸업증명서 및 성적증명서 (아포스티유 미포함)
e-apostille transcript-아포스티유 성적증명서
""".strip()

# Common international document names (English only).
_RAW_EN_LINES = """
Passport
Birth Certificate
ID Card
National ID Card
Driving License
Transcript
Academic Transcript
Diploma
Degree Certificate
Graduation Certificate
Bank Statement
Bank Solvency Certificate
Visa
Application Form
Photo
Passport Photo
Recommendation Letter
Certificate of Enrollment
Family Relation Certificate
Marriage Certificate
Death Certificate
Police Clearance Certificate
Medical Certificate
TB Test Certificate
Resume
CV
Statement of Purpose
Study Plan
Personal Statement
Language Test Score
IELTS Score
TOPIK Score
TOEFL Score
Sponsor Letter
Affidavit of Support
Employment Certificate
Tax Certificate
Business Registration
Trade License
Offer Letter
Admission Letter
Tuition Payment Receipt
Insurance Certificate
Lease Agreement
Utility Bill
Invoice
Receipt
Contract
""".strip()

# Map OCR checklist labels → template English keys (first match wins).
OCR_DOC_TYPE_TEMPLATE_KEYS: dict[str, tuple[str, ...]] = {
    "APPLICATION & OTHER FORMS": (
        "admission application and other forms",
        "application form",
    ),
    "PHOTO 35X45": ("photo", "ID photo"),
    "PHOTO": ("photo", "ID photo"),
    "PASSPORT": ("passport",),
    "BIRTH CERTIFICATE": ("applicant birth certificate", "birth certificate"),
    "APOSTILLE TRANSCRIPT": (
        "bachelor transcript with apostille",
        "bachelor certificate with apostille",
        "HSC transcript with apostille",
        "HSC certificate with apostille",
        "SSC transcript with apostille",
        "SSC certificate with apostille",
        "high school graduation certificate & transcript",
        "middle school graduation certificate & transcript",
        "e-apostille transcript",
    ),
    "APOSTILLE": (
        "bachelor certificate with apostille",
        "HSC certificate with apostille",
        "SSC certificate with apostille",
        "high school graduation certificate & transcript",
        "middle school graduation certificate & transcript",
    ),
    "TRANSCRIPT": (
        "bachelor transcript (no apostille)",
        "HSC transcript (no apostille)",
        "SSC transcript (no apostille)",
        "high school graduation certificate & transcript",
        "middle school graduation certificate & transcript",
    ),
    "NID": ("applicant ID card",),
    "PARENTS NID": ("applicant and parents ID cards", "father ID card", "mother ID card", "applicant ID card"),
    "FATHER NID": ("father ID card",),
    "MOTHER NID": ("mother ID card",),
    "FAMILY RELATIONSHIP CERTIFICATE": ("family relationship certificate",),
    "IELTS CERTIFICATE": ("IELTS",),
    "TOPIK CERTIFICATE": ("TOPIK",),
    "TRADE LICENSE": ("trade license", "business registration", "sponsor business registration"),
    "EMPLOYMENT CERTIFICATE": (
        "sponsor employment certificate tin and income tax certificate",
        "sponsor employment certificate tin income tax certificate & tax payment receipt",
        "guarantor job certificate",
    ),
    "TIN CERTIFICATE": ("tin certificate", "guarantor tin certificate"),
    "TAX CERTIFICATE": ("tax certificate", "guarantor income tax"),
    "ACKNOWLEDGEMENT CERTIFICATE": ("tax return acknowledgment",),
    "BANK STATEMENT": ("bank statement",),
    "BANK SOLVENCY": ("bank balance proof", "bank solvency certificate"),
    "MEDICAL TEST REPORT": ("TB test certificate", "medical test report"),
    "SWIFT COPY": ("swift copy",),
}


# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Language:
    code: str
    label: str                      # shown in the UI dropdown
    local_name: str = ""            # "Korean"; "" = English-only language
    builtin_text: str = ""          # "English-Local" lines (or English-only lines)
    user_file: str = ""             # file name next to the app ("" = base file)
    script_re: str = ""             # regex that detects the local script

    @property
    def bilingual(self) -> bool:
        return bool(self.local_name)


LANGUAGES: dict[str, Language] = {
    "en": Language(
        code="en",
        label="International (English)",
        builtin_text=_RAW_EN_LINES,
        user_file=USER_FILE_NAME,
    ),
    "ko": Language(
        code="ko",
        label="Korean (KR-EN)",
        local_name="Korean",
        builtin_text=_RAW_KO_LINES,
        user_file=USER_FILE_NAME,
        script_re=r"[가-힣ㄱ-ㅎㅏ-ㅣ]",
    ),
    # To add a language, e.g. Japanese:
    # "ja": Language(code="ja", label="Japanese (JA-EN)", local_name="Japanese",
    #                builtin_text=_RAW_JA_LINES, user_file="document_name_templates_ja.txt",
    #                script_re=r"[぀-ヿ一-鿿]"),
}


def normalize_lang(lang: str | None) -> str:
    code = str(lang or "").strip().lower()
    return code if code in LANGUAGES else DEFAULT_LANG


def languages() -> list[dict]:
    """Registry as plain dicts for the UI."""
    return [
        {"code": l.code, "label": l.label, "local_name": l.local_name,
         "bilingual": l.bilingual}
        for l in LANGUAGES.values()
    ]


# ---------------------------------------------------------------------------
# Parsing / formatting
# ---------------------------------------------------------------------------

def _contains_hangul(text: str) -> bool:
    return bool(re.search(r"[가-힣ㄱ-ㅎㅏ-ㅣ]", text))


def _contains_latin(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text))


def _is_local_script(text: str, lang: Language | None = None) -> bool:
    if lang is not None and lang.script_re:
        return bool(re.search(lang.script_re, text))
    # Any non-ASCII letter that is not Latin counts as "local".
    return any(ord(c) > 0x24F and c.isalpha() for c in text)


def parse_pairs(text: str, lang: Language | None = None) -> list[tuple[str, str]]:
    """Parse ``English-Local`` lines (split on last ``-``); one side may be blank.

    Lines without any hyphen are kept as one-sided rows (local script → local
    side, else English) so hand-written English-only files also work.
    """
    out: list[tuple[str, str]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "-" in line:
            en, loc = line.rsplit("-", 1)
            en_b, loc_b = en.strip(), loc.strip()
        elif _is_local_script(line, lang):
            en_b, loc_b = "", line
        else:
            en_b, loc_b = line, ""
        if en_b or loc_b:
            out.append((en_b, loc_b))
    return out


def pair_from_name_text(text: str, lang: str | None = None) -> tuple[str, str] | None:
    """Split a typed file name into an ``(english, local)`` template pair.

    ``여권-passport`` / ``passport-여권`` → ``("passport", "여권")``; a name with
    no script mix is one-sided.  International mode always returns ``(name, "")``.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    L = LANGUAGES[normalize_lang(lang)]
    if not L.bilingual:
        return (raw, "")
    candidates: list[tuple[int, int, int, str, str]] = []
    for idx, ch in enumerate(raw):
        if ch != "-":
            continue
        left, right = raw[:idx].strip(), raw[idx + 1:].strip()
        if not left or not right:
            continue
        left_h, right_h = _is_local_script(left, L), _is_local_script(right, L)
        if left_h == right_h:
            continue
        mixed = int(left_h and _contains_latin(left)) + int(right_h and _contains_latin(right))
        order = 0 if left_h and not right_h else 1
        candidates.append((mixed, order, idx, left, right))
    if candidates:
        _m, order, _i, left, right = min(candidates, key=lambda t: (t[0], t[1], t[2]))
        return (right, left) if order == 0 else (left, right)
    if _is_local_script(raw, L):
        return ("", raw)
    return (raw, "")


# Backward-compatible name used by the old app.
_parse_document_rename_pairs_from_text = parse_pairs


def format_pair_line(en: str, local: str) -> str:
    return f"{str(en).strip()}-{str(local).strip()}"


def sanitize_stem(raw: str) -> str:
    """Make *raw* safe as a Windows filename stem (never empty when input isn't)."""
    s = "".join(c if c not in INVALID_WIN_FILENAME_CHARS and ord(c) >= 32 else "_"
                for c in str(raw or ""))
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    return s


def stem_for(en: str, local: str, lang: str | None = None) -> str:
    """Filename stem for a template row in *lang* mode.

    * English-only language → English (falls back to local when English blank).
    * Bilingual language → ``LOCAL-EN`` (today's KR-EN), single side when one is blank.
    """
    L = LANGUAGES[normalize_lang(lang)]
    en_b, loc_b = str(en or "").strip(), str(local or "").strip()
    if not L.bilingual:
        return sanitize_stem(en_b or loc_b)
    parts: list[str] = []
    if loc_b:
        parts.append(loc_b)
    if en_b and en_b != loc_b:
        parts.append(en_b)
    return sanitize_stem("-".join(parts))


def label_for(en: str, local: str, lang: str | None = None) -> str:
    L = LANGUAGES[normalize_lang(lang)]
    en_b, loc_b = str(en or "").strip(), str(local or "").strip()
    if not L.bilingual:
        return en_b or loc_b
    if loc_b and en_b:
        return f"{loc_b}  ·  {en_b}"
    return loc_b or en_b


def slug(text: str) -> str:
    """Lowercase, unicode dashes folded, drop spaces/underscores/hyphens/punctuation."""
    t = str(text or "").lower()
    return "".join(c for c in t if c.isalnum())


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-zÀ-￿]+", str(text or "").lower()) if w]


def _normalize_query(s: str) -> str:
    t = str(s or "").strip().lower()
    for u in ("‐", "‑", "−", "–", "—"):
        t = t.replace(u, "-")
    return t


# ---------------------------------------------------------------------------
# Store (user files + merged lists)
# ---------------------------------------------------------------------------

def default_base_dir() -> str:
    """Portable: next to the .exe when frozen, else next to this script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


@dataclass
class Row:
    en: str
    local: str
    source: str            # "custom" | "builtin" | "derived"

    @property
    def builtin(self) -> bool:
        return self.source != "custom"


@dataclass
class TemplateStore:
    base_dir: str = field(default_factory=default_base_dir)

    # ── files ────────────────────────────────────────────────────────────────
    def user_file_path(self, lang: str | None = None) -> str:
        L = LANGUAGES[normalize_lang(lang)]
        return os.path.join(self.base_dir, L.user_file or USER_FILE_NAME)

    def _read_file_pairs(self, path: str, lang: Language | None = None) -> list[tuple[str, str]]:
        if not os.path.isfile(path):
            return []
        try:
            with open(path, encoding="utf-8-sig") as f:
                return parse_pairs(f.read(), lang)
        except (OSError, UnicodeDecodeError):
            return []

    def user_pairs(self, lang: str | None = None) -> list[tuple[str, str]]:
        """Raw custom rows from the language's user file (as written)."""
        code = normalize_lang(lang)
        L = LANGUAGES[code]
        pairs = self._read_file_pairs(self.user_file_path(code), L)
        if not L.bilingual:
            # International mode keeps only the English-only custom rows of the
            # shared base file; bilingual rows belong to their language.
            pairs = [(en, "") for en, loc in pairs if en and not loc]
        return pairs

    @staticmethod
    def builtin_pairs(lang: str | None = None) -> list[tuple[str, str]]:
        L = LANGUAGES[normalize_lang(lang)]
        pairs = parse_pairs(L.builtin_text, L)
        if not L.bilingual:
            pairs = [(en or loc, "") for en, loc in pairs]
        return pairs

    # ── merged list ──────────────────────────────────────────────────────────
    def rows(self, lang: str | None = None) -> list[Row]:
        """User rows first, then built-ins; exact duplicates dropped.

        International mode also lists the English side of every other
        language's rows (``derived``), de-duplicated case-insensitively.
        """
        code = normalize_lang(lang)
        L = LANGUAGES[code]
        out: list[Row] = []
        if L.bilingual:
            seen: set[tuple[str, str]] = set()
            for src, pairs in (("custom", self.user_pairs(code)),
                               ("builtin", self.builtin_pairs(code))):
                for en, loc in pairs:
                    if (en, loc) in seen:
                        continue
                    seen.add((en, loc))
                    out.append(Row(en, loc, src))
            return out

        seen_en: dict[str, Row] = {}

        def add(en: str, src: str, loc: str = "") -> None:
            # Derived rows keep their local name only so typing e.g. "여권"
            # still finds "passport"; the file name stays English-only.
            k = slug(en)
            if not k:
                return
            if k in seen_en:
                prev = seen_en[k]
                if loc.strip() and not prev.local:
                    prev.local = loc.strip()
                return
            row = Row(en.strip(), loc.strip(), src)
            seen_en[k] = row
            out.append(row)

        for en, _ in self.user_pairs(code):
            add(en, "custom")
        for en, _ in self.builtin_pairs(code):
            add(en, "builtin")
        for other in LANGUAGES.values():
            if not other.bilingual:
                continue
            for en, loc in self._read_file_pairs(self.user_file_path(other.code), other):
                add(en, "derived", loc)
            for en, loc in self.builtin_pairs(other.code):
                add(en, "derived", loc)
        return out

    def merged_pairs(self, lang: str | None = None) -> list[tuple[str, str]]:
        return [(r.en, r.local) for r in self.rows(lang)]

    # ── saving ───────────────────────────────────────────────────────────────
    def save_user_rows(self, lang: str | None, pairs) -> tuple[bool, str]:
        """Replace the custom rows of *lang* with *pairs*; returns (ok, path|error).

        International mode shares ``document_name_templates.txt`` with Korean:
        it rewrites only the English-only lines and keeps every bilingual line.
        """
        code = normalize_lang(lang)
        L = LANGUAGES[code]
        path = self.user_file_path(code)
        clean: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for p in pairs or []:
            en, loc = (p.get("en", ""), p.get("local", p.get("ko", ""))) if isinstance(p, dict) else p
            en_b, loc_b = str(en or "").strip(), str(loc or "").strip()
            if not L.bilingual:
                en_b, loc_b = (en_b or loc_b), ""
            if not en_b and not loc_b:
                continue
            if any(c in en_b + loc_b for c in "\n\r"):
                continue
            if (en_b, loc_b) in seen:
                continue
            seen.add((en_b, loc_b))
            clean.append((en_b, loc_b))

        if not L.bilingual:
            kept = [(en, loc) for en, loc in self._read_file_pairs(path) if loc]
            clean = kept + clean
            header = [
                "# Custom document rename templates (English-Korean, one per line).",
                "# One side may be blank for English-only or Korean-only templates.",
                "# Split is on the last hyphen. Lines starting with # are ignored.",
            ]
        else:
            header = [
                f"# Custom document rename templates (English-{L.local_name}, one per line).",
                "# One side may be blank for English-only or "
                f"{L.local_name}-only templates.",
                "# Split is on the last hyphen. Lines starting with # are ignored.",
            ]
        lines = header + [""] + [format_pair_line(en, loc) for en, loc in clean]
        body = "\n".join(lines).rstrip() + "\n"
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
        except OSError as e:
            return False, str(e)
        return True, path

    def add_user_row(self, lang: str | None, en: str, local: str = "") -> tuple[bool, str]:
        """Append one custom row (no-op if it already exists)."""
        code = normalize_lang(lang)
        en_b, loc_b = str(en or "").strip(), str(local or "").strip()
        if not LANGUAGES[code].bilingual:
            en_b, loc_b = (en_b or loc_b), ""
        if not en_b and not loc_b:
            return False, "Template name is empty."
        cur = self.user_pairs(code)
        if (en_b, loc_b) in cur:
            return True, self.user_file_path(code)
        return self.save_user_rows(code, cur + [(en_b, loc_b)])

    # ── filtering ────────────────────────────────────────────────────────────
    def filter_scored(self, query: str, lang: str | None = None, *, limit: int = 80):
        """Return ``[(priority, Row, stem)]``; lower priority = stronger match."""
        code = normalize_lang(lang)
        rows = self.rows(code)
        q_raw = str(query or "").strip()
        q = _normalize_query(q_raw)
        if not q:
            return [(0, r, stem_for(r.en, r.local, code)) for r in rows[:limit]]
        q_slug = slug(q)
        q_words = _words(q)
        if not q_slug and not q:
            return []

        matches: list[tuple[int, int, Row, str]] = []
        for idx, r in enumerate(rows):
            stem = stem_for(r.en, r.local, code)
            p = _score(q, q_raw, q_slug, q_words, r, stem)
            if p is not None:
                matches.append((p, idx, r, stem))
        matches.sort(key=lambda t: (t[0], t[1]))
        return [(p, r, st) for p, _i, r, st in matches[:limit]]

    def suggestions(self, query: str, lang: str | None = None, ext: str = "",
                    *, limit: int = 80) -> list[dict]:
        code = normalize_lang(lang)
        q = strip_ext(query, ext)
        out = []
        for p, r, st in self.filter_scored(q, code, limit=limit):
            out.append({
                "en": r.en, "local": r.local, "stem": st,
                "name": st + (ext_with_dot(ext) if ext else ""),
                "label": label_for(r.en, r.local, code),
                "builtin": r.builtin, "source": r.source, "score": p,
            })
        return out

    # ── OCR checklist type → stem ────────────────────────────────────────────
    def stem_for_ocr_type(self, doc_type: str, page_text: str = "",
                          lang: str | None = None) -> str | None:
        """Filename stem for an OCR-detected checklist document type."""
        if not doc_type or not str(doc_type).strip():
            return None
        code = normalize_lang(lang)
        key = str(doc_type).strip().upper()
        text = page_text or ""
        rows = self.rows(code)

        def find(cands) -> str | None:
            for cand in cands:
                c_low = cand.lower()
                for r in rows:
                    if r.en and (r.en.lower() == c_low or c_low in r.en.lower()):
                        return stem_for(r.en, r.local, code)
            return None

        if key == "PASSPORT":
            if re.search(r"\bFATHER\b", text, re.IGNORECASE) or "아버지" in text:
                cands: tuple | list = ("father passport",)
            elif re.search(r"\bMOTHER\b", text, re.IGNORECASE) or "어머니" in text:
                cands = ("mother passport",)
            else:
                cands = ("passport",)
            got = find(cands)
            if got:
                return got
        if key == "FATHER NID":
            cands = ("father id",)
        elif key == "MOTHER NID":
            cands = ("mother id",)
        else:
            cands = list(OCR_DOC_TYPE_TEMPLATE_KEYS.get(key, ()))
        if not cands:
            cands = [str(doc_type).strip().lower()]
        got = find(cands)
        if got:
            return got
        words = [w for w in key.replace("&", " ").split() if len(w) > 3]
        if words:
            for r in rows:
                hay = r.en.lower()
                if sum(1 for w in words if w.lower() in hay) >= min(2, len(words)):
                    return stem_for(r.en, r.local, code)
        return None

    def ocr_stem_fn(self, lang: str | None = None):
        """``fn(doc_type, page_text)`` for file_ops ``template_stem_for_type``."""
        code = normalize_lang(lang)
        return lambda doc_type, page_text="": self.stem_for_ocr_type(doc_type, page_text, code)


def _is_subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(c in it for c in needle)


def _score(q: str, q_raw: str, q_slug: str, q_words: list[str], r: Row, stem: str) -> int | None:
    en_l = r.en.lower()
    loc = r.local.strip()
    loc_l = loc.lower()
    stem_l = stem.lower()
    en_s, loc_s, stem_s = slug(r.en), slug(loc), slug(stem)

    # 0 — exact (also slug-exact: "birth_certificate" == "Birth Certificate")
    if q in (en_l, loc_l, stem_l) or (q_slug and q_slug in (en_s, loc_s, stem_s)):
        return 0
    # 1 — prefix of English / local / stem (raw or slug)
    if len(q) >= 2 and (en_l.startswith(q) or stem_l.startswith(q) or loc.startswith(q_raw)):
        return 1
    if len(q_slug) >= 2 and (en_s.startswith(q_slug) or loc_s.startswith(q_slug)
                             or stem_s.startswith(q_slug)):
        return 1
    # 2 — every typed word is a prefix of a word in the name ("birth cert")
    if q_words:
        name_words = _words(r.en) + _words(loc)
        if name_words and all(any(w.startswith(qw) for w in name_words) for qw in q_words) \
                and sum(len(w) for w in q_words) >= 2:
            return 2
    # 3 — substring (3+ chars)
    if len(q) >= 3 and (q in loc or q in en_l or q in stem_l):
        return 3
    if len(q_slug) >= 3 and (q_slug in en_s or q_slug in loc_s):
        return 3
    # 4 — typo tolerant ("passprt", "pasport", "certficate")
    if len(q_slug) >= 4 and en_s:
        if en_s[0] == q_slug[0] and _is_subsequence(q_slug, en_s) \
                and len(q_slug) >= 0.6 * min(len(en_s), len(q_slug) + 4):
            return 4
        head = en_s[: len(q_slug) + 1]
        if difflib.SequenceMatcher(None, q_slug, head).ratio() >= 0.8:
            return 4
        for w in _words(r.en):
            if len(w) >= 4 and difflib.SequenceMatcher(None, q_slug, w).ratio() >= 0.8:
                return 4
    return None


# ---------------------------------------------------------------------------
# File-name helpers
# ---------------------------------------------------------------------------

def ext_with_dot(ext: str) -> str:
    e = str(ext or "").strip()
    if not e:
        return ""
    return e if e.startswith(".") else "." + e


def strip_ext(query: str, ext: str = "") -> str:
    """Drop a trailing ``.ext`` the user may have typed (``passport.pdf`` → ``passport``)."""
    q = str(query or "").strip()
    e = ext_with_dot(ext).lower()
    if e and q.lower().endswith(e) and len(q) > len(e):
        return q[: -len(e)].rstrip()
    return q


def unique_name(folder: str, stem: str, ext: str = "", *, src_path: str = "") -> str:
    """``stem+ext`` inside *folder*, auto-suffixed `` (2)``, `` (3)`` … if taken.

    *src_path* (the file being renamed) never counts as a clash, so renaming a
    file to its own name or a case-only change keeps the plain name.
    """
    stem = sanitize_stem(stem) or "file"
    e = ext_with_dot(ext)
    src_n = os.path.normcase(os.path.abspath(src_path)) if src_path else ""

    def taken(name: str) -> bool:
        p = os.path.join(folder, name)
        if src_n and os.path.normcase(os.path.abspath(p)) == src_n:
            return False
        return os.path.exists(p)

    cand = stem + e
    n = 2
    while taken(cand):
        cand = f"{stem} ({n}){e}"
        n += 1
    return cand


# ---------------------------------------------------------------------------
# Module-level default store + old-API shims
# ---------------------------------------------------------------------------

_default_store = TemplateStore()


def store() -> TemplateStore:
    return _default_store


def set_base_dir(path: str) -> None:
    """Point the default store somewhere else (tests)."""
    global _default_store
    _default_store = TemplateStore(path)


def document_rename_user_file_path() -> str:
    return _default_store.user_file_path("ko")


def document_rename_merged_pairs(lang: str | None = None) -> list[tuple[str, str]]:
    return _default_store.merged_pairs(lang)


def document_rename_stem_for_ocr_type(doc_type: str, page_text: str = "",
                                      lang: str | None = None) -> str | None:
    return _default_store.stem_for_ocr_type(doc_type, page_text, lang)
