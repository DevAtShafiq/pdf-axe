#!/usr/bin/env python3
"""
Document Auto-Renaming System
==============================
Detects document type from PDF/image files using OCR + keyword scoring,
then renames them into structured names like:

    Passport.pdf, Father_NID_Card.pdf, Sponsor_TIN_Certificate.pdf

Folder mode also extracts family names from the passport and applies
relation prefixes automatically (Father_ / Mother_ / Sponsor_).

Usage:
    python doc_renamer.py                        # processes ./incoming/
    python doc_renamer.py --folder path/to/dir  # any folder
    python doc_renamer.py --file myfile.pdf      # single file (right-click)
    python doc_renamer.py --dry-run              # preview only
"""

import argparse
import base64
import io
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# ── CONFIGURATION ─────────────────────────────────────────────
CONFIDENCE_THRESHOLD = 60
GPT_MODEL = "gpt-4o"
OCR_DPI = 200
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}
PDF_EXT = ".pdf"

# Doc types that get Father_ / Mother_ prefix when the person's name matches
RELATION_TYPES = {
    "NID_Card", "Passport", "Death_Certificate", "Birth_Certificate",
    "Employment_Certificate", "Job_Experience_Certificate",
    "Medical_Certificate", "Bank_Balance_Certificate",
}

# Doc types that always get Sponsor_ prefix (guarantor financial docs)
SPONSOR_TYPES = {
    "TIN_Certificate", "Trade_License",
}

# Doc types that get Applicant_ prefix when neither Father_ nor Mother_ applies
APPLICANT_TYPES = {
    "NID_Card",
}

# ── DEFINITIVE MARKERS ────────────────────────────────────────
# Phrases so unique that a single match overrides the scoring system.
# Used for docs whose biographical page doesn't carry standard headers.
DEFINITIVE_MARKERS = {
    "Passport": "personal data and emergency contact",
}

# ── DOCUMENT RULES ────────────────────────────────────────────
DOCUMENT_RULES = {
    "Passport": [
        # Standard passport header (photo page)
        "passport", "passport no", "peoples republic of bangladesh",
        "nationality", "date of birth", "place of birth",
        "date of issue", "date of expiry", "given name", "surname", "machine readable",
        # Biographical data page (always present in single-page scans)
        "personal data and emergency contact", "emergency contact name",
        "spouse s name", "father s name", "mother s name",
    ],
    "NID_Card": [
        "national id", "national identity card", "national identification",
        "voter id", "nid no", "bangladesh national", "election commission",
    ],
    "Birth_Certificate": [
        "birth certificate", "birth registration", "date of birth",
        "registration no", "union parishad", "city corporation", "registration officer",
    ],
    "Death_Certificate": [
        "death registration certificate",
        "date of death",
        "registrar birth and death",
        "date of registration",
        "death registration",
        "death",
        "deceased",
    ],
    "SSC_Certificate": [
        "secondary school certificate", "ssc",
        "board of intermediate and secondary education", "board of education",
        "gpa", "roll no", "passed", "grade point average",
    ],
    "HSC_Certificate": [
        "higher secondary certificate", "hsc", "higher secondary",
        "board of intermediate", "intermediate and secondary",
        "gpa", "grade point", "examination result",
    ],
    "HSC_Transcript": [
        "academic transcript", "transcript of grades", "subject marks",
        "theory", "practical", "grade point", "cumulative gpa", "higher secondary",
    ],
    "SSC_Transcript": [
        "academic transcript", "transcript of grades", "subject marks",
        "grade point", "secondary school", "roll number",
    ],
    "Bachelor_Certificate": [
        "bachelor",
        "national university",
        "controller of examinations",
        "apostille",
        "convention de la haye",
        "conferred",
        "degree of bachelor",
        "university bangladesh",
        "graduation",
    ],
    "Trade_License": [
        "trade license", "trade licence", "business organization", "fiscal year",
        "city corporation", "municipality", "license no", "business address",
    ],
    "Family_Certificate": [
        "family certificate", "nagorik certificate", "citizen certificate",
        "union parishad", "permanent address", "ward no",
        "consists of the following members", "relation",
    ],
    "TIN_Certificate": [
        "taxpayer s identification number",
        "taxpayers identification number",
        "tin certificate",
        "national board of revenue",
        "registered taxpayer",
        "taxes circle",
        "taxes zone",
        "tax zone",
        "income tax",
    ],
    "Bank_Statement": [
        "bank statement", "account statement", "account number", "account no",
        "balance", "debit", "credit", "transaction",
        "opening balance", "closing balance", "bank",
    ],
    "Bank_Balance_Certificate": [
        "balance in taka",
        "swift code",
        "to whom it may concern",
        "satisfactory conducted",
        "maintaining",
        "deposit",
        "bank balance",
        "account",
        "bank",
    ],
    "Utility_Bill": [
        "electricity bill", "gas bill", "water bill", "utility bill",
        "consumer no", "meter no", "billing period", "amount due",
        "desco", "dpdc", "titas", "wasa",
    ],
    "Medical_Certificate": [
        "medical certificate", "fitness certificate", "medical officer",
        "hospital", "diagnosis", "patient name", "referring doctor", "blood group",
    ],
    "Police_Clearance": [
        "police clearance", "character certificate", "no criminal record",
        "superintendent of police", "district police", "bangladesh police",
    ],
    "Employment_Certificate": [
        "employment certificate",
        "employee certificate",
        "certify that",
        "working as",
        "working with",
    ],
    "Job_Experience_Certificate": [
        "experience certificate",
        "has been working as",
        "permanent employee",
        "to whom it may concern",
        "certify",
    ],
    "Medium_of_Instruction": [
        "medium of instruction",
        "certification of medium",
        "medium of instruction certificate",
        "medium of instruction at",
        "conferred degree",
        "obtained the degree",
    ],
    "Affidavit_Same_Person": [
        "affidavit before the notary public",
        "same person declaration",
        "do hereby solemnly affirm",
        "nationality bangladeshi",
        "permanent citizen of bangladesh",
        "national id card",
        "affidavit of same",
    ],
    # Korean university admission documents
    "Application_Form": [
        "application form for admission",
        "english language training",
        "passport name",
        "country of citizenship",
        "passport number",
        "d47 012 1",
        "amc",
        "ajou",
        "motor college",
    ],
    "Study_Plan": [
        "study plan",
        "english language training",
        "motor college",
        "ajou",
        "amc",
        "d47 012 2",
        "why do you want",
        "future plan",
        "studying",
    ],
    "Admission_Agreement": [
        "admission application agreement",
        "admission application",
        "agreement form",
        "applicant information",
        "program applying for",
        "d47 020",
        "amc",
        "ajou",
        "motor college",
        "passport number",
    ],
    "Self_Introduction": [
        "self-introduction",
        "self introduction",
        "my name is",
        "i was born",
        "i am from",
        "i was brought up",
        "d47 012 3",
        "family background",
        "education",
    ],
}

# ── GPT-4o AI RENAMING ────────────────────────────────────────

def _load_api_key():
    """Load OpenAI API key from .env or environment variable."""
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("OPENAI_API_KEY", "")


def _render_first_page_jpeg(file_path, dpi=150):
    """
    Render the first page of a PDF or image file to a JPEG bytes object.
    Uses PyMuPDF for PDFs and Pillow for images.
    Returns bytes or None on failure.
    """
    ext = Path(file_path).suffix.lower()
    try:
        if ext == PDF_EXT:
            import fitz  # PyMuPDF
            doc = fitz.open(str(file_path))
            page = doc[0]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            doc.close()
            return pix.tobytes("jpeg")
        elif ext in IMAGE_EXTS:
            from PIL import Image
            img = Image.open(str(file_path)).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
    except Exception as e:
        print("  [WARN] Could not render page for GPT-4o: " + str(e))
    return None


_GPT_PROMPT = """You are a document classification assistant.
Look at this document image and identify what type of document it is.

Reply with ONLY the label from this list (exact spelling, nothing else):
Passport, NID_Card, Birth_Certificate, Death_Certificate,
SSC_Certificate, SSC_Transcript, HSC_Certificate, HSC_Transcript,
Bachelor_Certificate, Trade_License, Family_Certificate,
TIN_Certificate, Bank_Statement, Bank_Balance_Certificate,
Utility_Bill, Medical_Certificate, Police_Clearance,
Employment_Certificate, Job_Experience_Certificate,
Medium_of_Instruction, Affidavit_Same_Person,
Application_Form, Study_Plan, Admission_Agreement, Self_Introduction,
IELTS_Score_Report, e_Apostille, Income_Tax_Certificate,
Tax_Return_Acknowledgement

If it doesn't match any of the above, reply: UNKNOWN"""


def _parse_gpt_label(raw, known):
    """Clean a raw GPT label string and match it to a known label."""
    label = re.sub(r"[\"'\.\s]", "", raw.strip())
    if label in known:
        return label
    for k in known:
        if label.lower() in k.lower() or k.lower() in label.lower():
            return k
    return "UNKNOWN"


_KNOWN_LABELS = (
    set(DOCUMENT_RULES.keys()) |
    {"IELTS_Score_Report", "e_Apostille", "Income_Tax_Certificate", "Tax_Return_Acknowledgement"}
)

_GPT_MULTI_PROMPT = """You are a document classification assistant.
I will show you {n} pages from a PDF, numbered Page 1 to Page {n}.
Each page may be a different document type or a continuation of the same document.

For EACH page, reply with its document type using ONLY labels from this list:
Passport, NID_Card, Birth_Certificate, Death_Certificate,
SSC_Certificate, SSC_Transcript, HSC_Certificate, HSC_Transcript,
Bachelor_Certificate, Trade_License, Family_Certificate,
TIN_Certificate, Bank_Statement, Bank_Balance_Certificate,
Utility_Bill, Medical_Certificate, Police_Clearance,
Employment_Certificate, Job_Experience_Certificate,
Medium_of_Instruction, Affidavit_Same_Person,
Application_Form, Study_Plan, Admission_Agreement, Self_Introduction,
IELTS_Score_Report, e_Apostille, Income_Tax_Certificate, Tax_Return_Acknowledgement

Reply in this exact format (one line per page, no extra text):
1: Label
2: Label
3: Label
...

If a page is a continuation of the same document as the previous page, use the same label.
If a page doesn't match any label, use: UNKNOWN"""


def _render_all_pages_jpeg(file_path, dpi=120):
    """
    Render ALL pages of a PDF as JPEG bytes list.
    Uses PyMuPDF. Returns list of bytes objects.
    """
    ext = Path(file_path).suffix.lower()
    pages_data = []
    try:
        if ext == PDF_EXT:
            import fitz
            doc = fitz.open(str(file_path))
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            for page in doc:
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
                pages_data.append(pix.tobytes("jpeg"))
            doc.close()
        elif ext in IMAGE_EXTS:
            from PIL import Image
            img = Image.open(str(file_path)).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            pages_data.append(buf.getvalue())
    except Exception as e:
        print("  [WARN] Could not render pages: " + str(e))
    return pages_data


def classify_all_pages_gpt4o(file_path, batch_size=20):
    """
    Send ALL pages to GPT-4o and return a list of per-page labels.
    Pages are batched if > batch_size.
    Returns list of label strings (one per page), or None on failure.
    """
    api_key = _load_api_key()
    if not api_key:
        print("  [WARN] No OPENAI_API_KEY found — cannot use AI mode.")
        return None

    pages_data = _render_all_pages_jpeg(file_path)
    if not pages_data:
        return None

    import openai
    client = openai.OpenAI(api_key=api_key)
    all_labels = []
    offset = 0

    # Process in batches so we don't exceed image limits
    for batch_start in range(0, len(pages_data), batch_size):
        batch = pages_data[batch_start:batch_start + batch_size]
        n = len(batch)
        prompt = _GPT_MULTI_PROMPT.format(n=n)
        # Replace page numbers in prompt to reflect actual page numbers in this batch
        prompt = prompt.replace("Page 1 to Page {n}".format(n=n),
                                "Page {s} to Page {e}".format(
                                    s=batch_start + 1, e=batch_start + n))

        content = [{"type": "text", "text": prompt}]
        for i, jpeg_bytes in enumerate(batch):
            b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
            content.append({
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + b64, "detail": "low"}
            })

        try:
            response = client.chat.completions.create(
                model=GPT_MODEL,
                messages=[{"role": "user", "content": content}],
                max_tokens=n * 15,
                temperature=0,
            )
            raw = response.choices[0].message.content.strip()
            # Parse "1: Label\n2: Label\n..."
            batch_labels = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                if ":" in line:
                    _, lbl = line.split(":", 1)
                    batch_labels.append(_parse_gpt_label(lbl.strip(), _KNOWN_LABELS))
                else:
                    batch_labels.append(_parse_gpt_label(line, _KNOWN_LABELS))
            # Pad or trim to match batch size
            while len(batch_labels) < n:
                batch_labels.append(batch_labels[-1] if batch_labels else "UNKNOWN")
            all_labels.extend(batch_labels[:n])
        except Exception as e:
            print("  [WARN] GPT-4o API error on batch: " + str(e))
            return None

    return all_labels


def group_pages_by_label(labels):
    """
    Convert a flat list of per-page labels into segments:
    [(label, start_page, end_page), ...] where pages are 1-indexed.
    """
    if not labels:
        return []
    segments = []
    current_label = labels[0]
    start = 1
    for i, lbl in enumerate(labels[1:], 2):
        if lbl != current_label:
            segments.append((current_label, start, i - 1))
            current_label = lbl
            start = i
    segments.append((current_label, start, len(labels)))
    return segments


def split_pdf_pages(src_path, start_page, end_page, dest_path):
    """Extract pages start_page..end_page (1-indexed) from src into dest."""
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(str(src_path))
        writer = PdfWriter()
        for p in range(start_page - 1, end_page):
            writer.add_page(reader.pages[p])
        with open(str(dest_path), "wb") as f:
            writer.write(f)
        return True
    except Exception as e:
        print("  [WARN] Could not split PDF: " + str(e))
        return False


def classify_with_gpt4o(file_path):
    """
    Legacy single-label wrapper — classifies only the first page.
    Returns (label, confidence). Used when the file is a single-page image.
    """
    labels = classify_all_pages_gpt4o(file_path)
    if not labels:
        return None, 0
    label = labels[0]
    if label == "UNKNOWN":
        return "UNKNOWN", 0
    return label, 100.0


# ── OCR ───────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_path):
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(str(pdf_path), dpi=OCR_DPI)
    except Exception as e:
        print("  [WARN] Could not render PDF: " + str(e))
        return ""
    import pytesseract
    parts = []
    for i, img in enumerate(images, 1):
        try:
            parts.append(pytesseract.image_to_string(img, lang="eng"))
        except Exception as e:
            print("  [WARN] OCR failed on page " + str(i) + ": " + str(e))
    return "\n".join(parts)


def extract_text_from_image(img_path):
    try:
        from PIL import Image
        import pytesseract
        return pytesseract.image_to_string(Image.open(str(img_path)), lang="eng")
    except Exception as e:
        print("  [WARN] OCR failed: " + str(e))
        return ""

# ── NORMALIZE ─────────────────────────────────────────────────

def normalize(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

# ── SCORING ───────────────────────────────────────────────────

def score_document(norm_text):
    # Definitive markers override the scoring system entirely.
    # These phrases are so specific that a single match is conclusive.
    for label, marker in DEFINITIVE_MARKERS.items():
        if normalize(marker) in norm_text:
            total_kw = len(DOCUMENT_RULES[label])
            return {
                "best_label": label,
                "best_score": total_kw,
                "confidence": 100.0,
                "all_scores": {label: total_kw},
                "via_marker": True,
            }

    all_scores = {}
    for label, keywords in DOCUMENT_RULES.items():
        score = sum(1 for kw in keywords if normalize(kw) in norm_text)
        all_scores[label] = score
    # Rank by (raw_score, confidence) so a 3/5=60% beats a 3/11=27% tie.
    def rank(lbl):
        sc = all_scores[lbl]
        tot = len(DOCUMENT_RULES[lbl])
        return (sc, sc / tot if tot > 0 else 0.0)
    best_label = max(all_scores, key=rank)
    best_score = all_scores[best_label]
    total_kw = len(DOCUMENT_RULES[best_label])
    confidence = round((best_score / total_kw * 100) if total_kw > 0 else 0.0, 1)
    return {"best_label": best_label, "best_score": best_score,
            "confidence": confidence, "all_scores": all_scores}

# ── FAMILY NAME EXTRACTION ────────────────────────────────────

# Patterns to find father / mother names in passport OCR text
_FATHER_PAT = re.compile(
    r"fath[a-z]{0,3}\s+[a-z\s]{0,8}n[ae][nm][ae]?\s+"
    r"((?:md|mr|mrs|ms|mst|dr)\s+)?([a-z][a-z\s]{3,50}?)"
    r"(?=\s{2,}|\bmother\b|\bmolter\b|\bspouse\b|\bdate\b"
    r"|\bplace\b|\bnat\b|\bperm\b|\bemerg\b|\bpassport\b|$)"
)
_MOTHER_PAT = re.compile(
    r"mo[lt][hte][ae]?r\s+[a-z\s]{0,8}n[ae][nm][ae]?\s+"
    r"((?:md|mr|mrs|ms|mst|dr)\s+)?([a-z][a-z\s]{3,50}?)"
    r"(?=\s{2,}|\bfather\b|\bfath\b|\bspouse\b|\bdate\b"
    r"|\bplace\b|\bnat\b|\bperm\b|\bemerg\b|\bpassport\b|$)"
)

# Honorific prefixes that should be skipped when finding the primary first name
_HONORIFICS = {"md", "mr", "mrs", "ms", "mst", "dr"}


def _clean_name(raw):
    """Strip trailing noise words and short junk from an extracted name."""
    noise = {"of", "the", "and", "in", "at", "is", "a", "an", "no", "name",
             "date", "place", "bangladesh", "dhaka"}
    tokens = raw.strip().split()
    cleaned = [t for t in tokens if len(t) >= 3 and t not in noise and not t.isdigit()]
    return " ".join(cleaned[:6])  # cap at 6 tokens


def extract_family_names_from_text(norm_passport_text):
    """Parse normalized passport OCR text. Returns dict with father/mother."""
    family = {"father": None, "mother": None}
    fm = _FATHER_PAT.search(norm_passport_text)
    if fm:
        prefix = (fm.group(1) or "").strip()
        name = _clean_name((prefix + " " + fm.group(2)).strip())
        if len(name) >= 4:
            family["father"] = name
    mm = _MOTHER_PAT.search(norm_passport_text)
    if mm:
        prefix = (mm.group(1) or "").strip()
        name = _clean_name((prefix + " " + mm.group(2)).strip())
        if len(name) >= 4:
            family["mother"] = name
    return family


def _primary_name_token(name):
    """
    Return the most distinctive token: the first name (first non-honorific word).
    Skips prefixes like 'md', 'mst', 'dr' to get the actual given name.
    Minimum 4 characters required.
    """
    for word in normalize(name).split():
        if word not in _HONORIFICS and len(word) >= 4:
            return word
    return None


def name_in_text(name, norm_text):
    """
    True if the primary first name from `name` appears in `norm_text`.

    Uses only the first name (not shared surnames like 'rahman', 'begum')
    to avoid false positives when family members share a family name.

    For compound first names like 'rokeyarahman' (as OCR'd on passport),
    also tries shorter prefixes ('rokeya', 'rokeyar' ...) to match the
    split form 'rokeya begum' that may appear on the NID card.
    """
    if not name:
        return False
    primary = _primary_name_token(name)
    if not primary:
        return False
    tokens = {primary}
    # For compound names (8+ chars), try all prefix lengths >= 5
    # e.g. 'rokeyarahman' -> 'rokey','rokeya','rokeyar',...
    if len(primary) >= 8:
        for plen in range(5, len(primary)):
            tokens.add(primary[:plen])
    return any(t in norm_text for t in tokens)


def get_relation_prefix(doc_type, norm_text, family):
    """
    Returns "Father_", "Mother_", "Applicant_", "Sponsor_", or "".

    Priority:
      1. SPONSOR_TYPES  -> always "Sponsor_" (financial guarantor docs)
      2. RELATION_TYPES -> "Father_" or "Mother_" when name matches;
                          "Applicant_" for APPLICANT_TYPES when no match
      3. Everything else -> "" (no prefix)
    """
    # Financial sponsor docs always get Sponsor_ regardless of name
    if doc_type in SPONSOR_TYPES:
        return "Sponsor_"

    # Relation-aware docs: check name match
    if doc_type in RELATION_TYPES:
        father_hit = name_in_text(family.get("father"), norm_text)
        mother_hit = name_in_text(family.get("mother"), norm_text)
        if father_hit and not mother_hit:
            return "Father_"
        if mother_hit and not father_hit:
            return "Mother_"
        # Can't attribute to father or mother:
        # label as Applicant_ for designated types (e.g. NID_Card)
        if doc_type in APPLICANT_TYPES:
            return "Applicant_"
    return ""

# ── SAFE RENAME ───────────────────────────────────────────────

def safe_rename(src, new_name, dry_run):
    dest = src.parent / new_name
    counter = 1
    stem, suffix = dest.stem, dest.suffix
    while dest.exists() and dest != src:
        dest = src.parent / (stem + "-" + str(counter) + suffix)
        counter += 1
    if not dry_run:
        src.rename(dest)
    return dest

# ── SINGLE FILE (right-click) ─────────────────────────────────

def process_file(file, dry_run, threshold=CONFIDENCE_THRESHOLD, use_ai=False):
    ext = file.suffix.lower()
    if ext not in (IMAGE_EXTS | {PDF_EXT}):
        print("[ERROR] Unsupported file type: " + ext)
        sys.exit(1)

    DIV = "=" * 60
    print("")
    print(DIV)
    print("  Document Auto-Renaming System")
    print("  File : " + file.name)
    mode_label = "AI (GPT-4o)" if use_ai else "OCR + Keyword Scoring"
    if dry_run:
        print("  Mode : DRY RUN [" + mode_label + "] -- file will NOT be changed")
    else:
        print("  Mode : " + mode_label)
    print(DIV)
    print("")

    # ── AI path: classify all pages, split if multi-doc ──────
    if use_ai:
        print("  Sending all pages to GPT-4o ...")
        page_labels = classify_all_pages_gpt4o(file)

        if page_labels:
            segments = group_pages_by_label(page_labels)
            print("  Pages      : " + str(len(page_labels)))
            print("  Segments   : " + str(len(segments)))
            print("")

            log_path = file.parent / "rename_log.txt"

            if len(segments) == 1:
                # Single document — just rename the file
                label, start, end = segments[0]
                new_name = (label if label != "UNKNOWN" else "UNKNOWN_" + file.stem) + ext
                dest = safe_rename(file, new_name, dry_run)
                action = "(dry run)" if dry_run else "renamed ->"
                print("  Detected   : " + label + "  [GPT-4o, pages 1-" + str(end) + "]")
                print("  " + action + ": " + dest.name)
                if not dry_run:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with open(str(log_path), "a", encoding="utf-8") as lf:
                        lf.write(ts + " | OK       | " + file.name.ljust(40) +
                                 "| " + dest.name.ljust(40) + "| GPT-4o\n")
            else:
                # Multi-document PDF — split and rename each segment
                print("  Multi-document PDF detected. Splitting into " +
                      str(len(segments)) + " files ...")
                print("")
                # Count labels for deduplication suffixes
                label_counter = {}
                for label, start, end in segments:
                    label_counter[label] = label_counter.get(label, 0) + 1
                label_seen = {}

                for label, start, end in segments:
                    page_info = "p" + str(start) if start == end else "p" + str(start) + "-" + str(end)
                    # Build output name; add suffix if same label appears multiple times
                    label_counter_val = label_counter.get(label, 1)
                    if label_counter_val > 1:
                        label_seen[label] = label_seen.get(label, 0) + 1
                        base_name = label + "_" + str(label_seen[label]) + ext
                    else:
                        base_name = (label if label != "UNKNOWN" else "UNKNOWN_" + page_info) + ext

                    out_path = safe_rename.__func__.__globals__.get("Path", Path)(file.parent / base_name)
                    # Use safe naming without renaming src
                    counter = 1
                    stem2, suf2 = Path(base_name).stem, Path(base_name).suffix
                    out_path = file.parent / base_name
                    while out_path.exists():
                        out_path = file.parent / (stem2 + "-" + str(counter) + suf2)
                        counter += 1

                    action = "(dry run)" if dry_run else "→"
                    print("  Pages " + str(start) + "-" + str(end) +
                          "  [" + label + "]  " + action + "  " + out_path.name)

                    if not dry_run:
                        ok = split_pdf_pages(file, start, end, out_path)
                        if ok and not dry_run:
                            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            with open(str(log_path), "a", encoding="utf-8") as lf:
                                lf.write(ts + " | OK       | " + file.name.ljust(40) +
                                         "| " + out_path.name.ljust(40) +
                                         "| GPT-4o  pages=" + str(start) + "-" + str(end) + "\n")

                if not dry_run:
                    print("")
                    print("  Original file kept: " + file.name)
                    print("  (rename or move it manually once you've verified the splits)")

        else:
            print("  [INFO] GPT-4o could not classify — falling back to OCR ...")
            use_ai = False  # drop into OCR block below

    # ── OCR path (also fallback from AI) ──────────────────────
    if not use_ai:
        print("  Running OCR ...")
        raw_text = extract_text_from_pdf(file) if ext == PDF_EXT else extract_text_from_image(file)

        if not raw_text.strip():
            print("")
            print("  [SKIP] No text could be extracted from this file.")
            print("         The file may be a scanned image, blank, or corrupted.")
            print("         Tip: try --ai mode to use GPT-4o vision instead.")
            print("")
            sys.exit(0)

        norm_text = normalize(raw_text)
        result = score_document(norm_text)
        label = result["best_label"]
        confidence = result["confidence"]
        score = result["best_score"]
        total = len(DOCUMENT_RULES[label])

        if confidence >= threshold:
            new_name = label + ext
            status_tag = "OK"
        else:
            new_name = "UNKNOWN_" + file.name
            status_tag = "LOW_CONF"

        dest = safe_rename(file, new_name, dry_run)
        filled = int(confidence // 10)
        bar = ("#" * filled) + ("." * (10 - filled))
        action = "(dry run)" if dry_run else "renamed ->"
        marker_note = " [via definitive marker]" if result.get("via_marker") else ""
        print("  Detected   : " + label + marker_note)
        print("  Score      : " + str(score) + "/" + str(total) + " keywords matched")
        print("  Confidence : [" + bar + "] " + str(confidence) + "%")
        print("  " + action + ": " + dest.name)

        if not dry_run:
            log_path = file.parent / "rename_log.txt"
            with open(str(log_path), "a", encoding="utf-8") as lf:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                lf.write(ts + " | " + status_tag.ljust(9) + "| " + file.name.ljust(40) +
                         "| " + dest.name.ljust(40) + "| score=" + str(score) +
                         "  conf=" + str(confidence) + "%\n")

    print("")
    print(DIV)
    print("")


# ── FOLDER MODE ───────────────────────────────────────────────

def process_folder(folder, dry_run, threshold=CONFIDENCE_THRESHOLD, use_ai=False):
    log_path = folder / "rename_log.txt"
    candidates = sorted(
        f for f in folder.iterdir()
        if f.is_file()
        and f.suffix.lower() in (IMAGE_EXTS | {PDF_EXT})
        and f.name != log_path.name
    )
    if not candidates:
        print("\nNo PDF or image files found in: " + str(folder) + "\n")
        return

    DIV = "=" * 60
    mode_label = "AI (GPT-4o)" if use_ai else "OCR + Keyword Scoring"
    print("")
    print(DIV)
    print("  Document Auto-Renaming System")
    print("  Folder : " + str(folder))
    print("  Files  : " + str(len(candidates)))
    if dry_run:
        print("  Mode   : DRY RUN [" + mode_label + "] -- no files will be changed")
    else:
        print("  Mode   : " + mode_label)
    print(DIV)
    print("")

    # Step 1: extract family names from passport (OCR only)
    family = {"father": None, "mother": None}
    passport_files = [f for f in candidates
                      if any(kw in f.name.lower() for kw in ["passport", "travel"])]
    if passport_files:
        pf = passport_files[0]
        ext_p = pf.suffix.lower()
        print("  Reading passport for family names: " + pf.name)
        raw_p = (extract_text_from_pdf(pf) if ext_p == PDF_EXT else extract_text_from_image(pf))
        if raw_p.strip():
            family = extract_family_names_from_text(normalize(raw_p))
        if family["father"]:
            print("    Father : " + family["father"])
        if family["mother"]:
            print("    Mother : " + family["mother"])
        if not family["father"] and not family["mother"]:
            print("    (could not extract family names from passport)")
        print("")

    log_lines = [
        "# Document Auto-Rename Log",
        "# Run    : " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "# Mode   : " + ("DRY RUN" if dry_run else "LIVE") + " [" + mode_label + "]",
        "# Father : " + (family["father"] or "unknown"),
        "# Mother : " + (family["mother"] or "unknown"),
        "",
    ]

    # Step 2: classify + rename each file
    for file in candidates:
        ext = file.suffix.lower()
        print("  Processing: " + file.name)

        if use_ai:
            print("    Sending all pages to GPT-4o ...")
            page_labels = classify_all_pages_gpt4o(file)

            if page_labels:
                segments = group_pages_by_label(page_labels)
                print("    Pages: " + str(len(page_labels)) + "  Segments: " + str(len(segments)))

                if len(segments) == 1:
                    label, start, end = segments[0]
                    norm_text = ""
                    if label in DOCUMENT_RULES:
                        raw_t = (extract_text_from_pdf(file) if ext == PDF_EXT else extract_text_from_image(file))
                        norm_text = normalize(raw_t) if raw_t and raw_t.strip() else ""
                    prefix = get_relation_prefix(label, norm_text, family) if label in DOCUMENT_RULES else ""
                    new_name = (prefix + label if label != "UNKNOWN" else "UNKNOWN_" + file.stem) + ext
                    dest = safe_rename(file, new_name, dry_run)
                    action = "(dry run)" if dry_run else "renamed ->"
                    rel_note = ("  [" + prefix.rstrip("_") + "]") if prefix else ""
                    print("    Label : " + label + rel_note + "  [GPT-4o]")
                    print("    " + action + ": " + dest.name)
                    log_lines.append("OK       | " + file.name.ljust(40) + "| " + dest.name.ljust(40) + "| GPT-4o")

                else:
                    print("    Multi-doc: splitting into " + str(len(segments)) + " files ...")
                    label_count = {}
                    for lbl, s, e in segments:
                        label_count[lbl] = label_count.get(lbl, 0) + 1
                    label_seen = {}

                    for label, start, end in segments:
                        pg = "p" + str(start) if start == end else "p" + str(start) + "-" + str(end)
                        if label_count.get(label, 1) > 1:
                            label_seen[label] = label_seen.get(label, 0) + 1
                            base = label + "_" + str(label_seen[label]) + ext
                        else:
                            base = (label if label != "UNKNOWN" else "UNKNOWN_" + pg) + ext
                        out = file.parent / base
                        ctr = 1
                        stem2, suf2 = Path(base).stem, Path(base).suffix
                        while out.exists():
                            out = file.parent / (stem2 + "-" + str(ctr) + suf2)
                            ctr += 1
                        action = "(dry run)" if dry_run else "created ->"
                        print("    p" + str(start) + "-" + str(end) + "  [" + label + "]  " + action + "  " + out.name)
                        if not dry_run:
                            split_pdf_pages(file, start, end, out)
                        log_lines.append("OK       | " + file.name.ljust(40) + "| " + out.name.ljust(40) + "| GPT-4o pages=" + str(start) + "-" + str(end))

                    if not dry_run:
                        print("    Original kept: " + file.name)

            else:
                print("    [INFO] GPT-4o failed -- falling back to OCR ...")
                raw_text = (extract_text_from_pdf(file) if ext == PDF_EXT else extract_text_from_image(file))
                if not raw_text.strip():
                    print("    [SKIP] No text extracted either.\n")
                    log_lines.append("SKIP      | " + file.name + " | no text extracted")
                    print("")
                    continue
                norm_text = normalize(raw_text)
                result = score_document(norm_text)
                label = result["best_label"]
                confidence = result["confidence"]
                score = result["best_score"]
                prefix = get_relation_prefix(label, norm_text, family)
                status_tag = "OK" if confidence >= threshold else "LOW_CONF"
                new_name = (prefix + label + ext) if confidence >= threshold else ("UNKNOWN_" + file.name)
                dest = safe_rename(file, new_name, dry_run)
                action = "(dry run)" if dry_run else "renamed ->"
                print("    Label : " + label + "  [OCR fallback]")
                print("    " + action + ": " + dest.name)
                log_lines.append(status_tag.ljust(9) + "| " + file.name.ljust(40) + "| " + dest.name.ljust(40) + "| score=" + str(score) + " conf=" + str(confidence) + "% [OCR fallback]")

            print("")
            continue

        # OCR path
        raw_text = (extract_text_from_pdf(file) if ext == PDF_EXT else extract_text_from_image(file))
        if not raw_text.strip():
            print("    [SKIP] No text extracted. Tip: try --ai mode.\n")
            log_lines.append("SKIP      | " + file.name + " | no text extracted")
            continue

        norm_text = normalize(raw_text)
        result = score_document(norm_text)
        label = result["best_label"]
        confidence = result["confidence"]
        score = result["best_score"]
        total = len(DOCUMENT_RULES[label])
        prefix = get_relation_prefix(label, norm_text, family)

        if confidence >= threshold:
            new_name = prefix + label + ext
            status_tag = "OK"
        else:
            new_name = "UNKNOWN_" + file.name
            status_tag = "LOW_CONF"
            prefix = ""

        dest = safe_rename(file, new_name, dry_run)
        filled = int(confidence // 10)
        bar = ("#" * filled) + ("." * (10 - filled))
        action = "(dry run)" if dry_run else "renamed ->"
        rel_note = ("  [" + prefix.rstrip("_") + "]") if prefix else ""
        marker_note = " [marker]" if result.get("via_marker") else ""
        print("    Label      : " + label + rel_note + marker_note)
        print("    Score      : " + str(score) + "/" + str(total) + " keywords matched")
        print("    Confidence : [" + bar + "] " + str(confidence) + "%")
        print("    " + action + ": " + dest.name)
        print("")
        log_lines.append(status_tag.ljust(9) + "| " + file.name.ljust(40) + "| " + dest.name.ljust(40) + "| score=" + str(score) + "  conf=" + str(confidence) + "%")

    if not dry_run:
        log_path.write_text("\n".join(log_lines), encoding="utf-8")
        print("  Log saved -> " + log_path.name)
    else:
        print("  [DRY RUN] Log would be saved to: " + log_path.name)

    print("")
    print(DIV)
    print("  Done. Processed " + str(len(candidates)) + " file(s).")
    print(DIV)
    print("")


# ── MAIN ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OCR-based document auto-renaming system.")
    parser.add_argument("--file", default=None, metavar="FILE",
                        help="Rename a single file.")
    parser.add_argument("--folder", "-f", default=None,
                        help="Folder to process. Defaults to incoming/ next to this script.")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Preview only -- no files are renamed.")
    parser.add_argument("--threshold", "-t", type=float, default=CONFIDENCE_THRESHOLD,
                        help="Confidence threshold (default " + str(CONFIDENCE_THRESHOLD) + ").")
    parser.add_argument("--ai", action="store_true",
                        help="Use GPT-4o vision API. Scans ALL pages and splits multi-doc PDFs.")
    args = parser.parse_args()

    if args.file:
        f = Path(args.file).expanduser().resolve()
        if not f.is_file():
            print("[ERROR] File not found: " + str(f))
            sys.exit(1)
        process_file(f, dry_run=args.dry_run, threshold=args.threshold, use_ai=args.ai)
        return

    script_dir = Path(__file__).parent
    folder = Path(args.folder).expanduser().resolve() if args.folder else script_dir / "incoming"

    if not folder.exists():
        print("\n[INFO] Creating folder: " + str(folder))
        folder.mkdir(parents=True, exist_ok=True)
        print("[INFO] Drop your PDF/image files in there and re-run.\n")
        sys.exit(0)

    if not folder.is_dir():
        print("[ERROR] Not a directory: " + str(folder))
        sys.exit(1)

    process_folder(folder, dry_run=args.dry_run, threshold=args.threshold, use_ai=args.ai)


if __name__ == "__main__":
    main()
