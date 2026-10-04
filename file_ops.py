# -*- coding: utf-8 -*-
"""
File conversion, PDF tools, and preview helpers for the workspace app.
Uses optional dependencies: Pillow, PyMuPDF (fitz), python-docx, pywin32 (Windows COM).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import uuid
import zipfile
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Callable, Sequence

# ── GPT-4o token + cost session accumulator ──────────────────────────────────
# Accumulated across the lifetime of the process; reset on app restart.
# Pricing (as of 2025): gpt-4o input $2.50/1M tok, output $10.00/1M tok.
_GPT4O_SESSION_TOKENS_IN:  int   = 0
_GPT4O_SESSION_TOKENS_OUT: int   = 0
_GPT4O_TOKEN_LOCK               = threading.Lock()
_GPT4O_PRICE_IN_PER_M:   float  = 2.50    # $ per 1M input tokens
_GPT4O_PRICE_OUT_PER_M:  float  = 10.00   # $ per 1M output tokens

# Optional callback — set by the UI to update the status bar label after each call.
_gpt4o_token_ui_callback: "Callable | None" = None


def _accum_gpt4o_tokens(prompt_tokens: int, completion_tokens: int) -> None:
    """Add token counts to the session accumulator and fire the UI callback."""
    global _GPT4O_SESSION_TOKENS_IN, _GPT4O_SESSION_TOKENS_OUT
    with _GPT4O_TOKEN_LOCK:
        _GPT4O_SESSION_TOKENS_IN  += prompt_tokens
        _GPT4O_SESSION_TOKENS_OUT += completion_tokens
    cb = _gpt4o_token_ui_callback
    if cb is not None:
        try:
            cb()
        except Exception:
            pass


def get_gpt4o_session_cost() -> tuple[int, int, float]:
    """Return (total_input_tokens, total_output_tokens, estimated_cost_usd)."""
    with _GPT4O_TOKEN_LOCK:
        tin  = _GPT4O_SESSION_TOKENS_IN
        tout = _GPT4O_SESSION_TOKENS_OUT
    cost = (tin / 1_000_000) * _GPT4O_PRICE_IN_PER_M + (tout / 1_000_000) * _GPT4O_PRICE_OUT_PER_M
    return tin, tout, cost


def reset_gpt4o_session_cost() -> None:
    global _GPT4O_SESSION_TOKENS_IN, _GPT4O_SESSION_TOKENS_OUT
    with _GPT4O_TOKEN_LOCK:
        _GPT4O_SESSION_TOKENS_IN  = 0
        _GPT4O_SESSION_TOKENS_OUT = 0
    cb = _gpt4o_token_ui_callback
    if cb is not None:
        try:
            cb()
        except Exception:
            pass


def _gpt4o_create(client, **kwargs):
    """
    Thin wrapper around client.chat.completions.create that records token usage.

    All GPT-4o / GPT-4o-mini API calls in file_ops go through this so the
    session token accumulator stays accurate without scattering try/except
    boilerplate at every call site.
    """
    resp = client.chat.completions.create(**kwargs)
    try:
        usage = resp.usage
        if usage is not None:
            _accum_gpt4o_tokens(
                getattr(usage, "prompt_tokens",     0),
                getattr(usage, "completion_tokens", 0),
            )
    except Exception:
        pass
    return resp


# ── Fitz (PyMuPDF) document LRU cache ────────────────────────────────────────
# Keeping documents open between page renders eliminates repeated open/parse
# overhead — the main cause of slow page-by-page PDF preview.
_PDF_DOC_CACHE: dict[str, object] = {}   # abs_path -> fitz.Document
_PDF_DOC_CACHE_ORDER: list[str] = []     # LRU order (oldest first)
_PDF_DOC_CACHE_LOCK = threading.Lock()
_PDF_DOC_CACHE_MAX = 6                   # keep at most 6 open documents


def _get_cached_fitz_doc(fitz, path: str):
    """Return a cached fitz.Document for *path*, opening it if needed (LRU, max 6)."""
    abs_path = os.path.abspath(path)
    with _PDF_DOC_CACHE_LOCK:
        if abs_path in _PDF_DOC_CACHE:
            doc = _PDF_DOC_CACHE[abs_path]
            try:
                _ = len(doc)  # raises if doc was closed/corrupt
                try:
                    _PDF_DOC_CACHE_ORDER.remove(abs_path)
                except ValueError:
                    pass
                _PDF_DOC_CACHE_ORDER.append(abs_path)
                return doc
            except Exception:
                _PDF_DOC_CACHE.pop(abs_path, None)
                try:
                    _PDF_DOC_CACHE_ORDER.remove(abs_path)
                except ValueError:
                    pass
        # Evict LRU entries when at capacity
        while len(_PDF_DOC_CACHE_ORDER) >= _PDF_DOC_CACHE_MAX:
            old_path = _PDF_DOC_CACHE_ORDER.pop(0)
            old_doc = _PDF_DOC_CACHE.pop(old_path, None)
            if old_doc is not None:
                try:
                    old_doc.close()
                except Exception:
                    pass
        try:
            doc = fitz.open(path)
            _PDF_DOC_CACHE[abs_path] = doc
            _PDF_DOC_CACHE_ORDER.append(abs_path)
            return doc
        except Exception:
            return None


def get_cached_fitz_doc(path: str):
    """Public accessor for the shared fitz document LRU cache.

    Returns a cached (already-open) ``fitz.Document`` for *path*, or ``None``.
    Callers MUST NOT close the returned document — it is owned by the cache and
    reused across thumbnail/preview renders to avoid repeated open/parse cost.
    """
    fitz = get_fitz()
    if not fitz:
        return None
    return _get_cached_fitz_doc(fitz, path)


def invalidate_pdf_doc_cache(path: str) -> None:
    """Remove *path* from the fitz document cache (call after the PDF is modified)."""
    if not path:
        return
    abs_path = os.path.abspath(path)
    with _PDF_DOC_CACHE_LOCK:
        doc = _PDF_DOC_CACHE.pop(abs_path, None)
        try:
            _PDF_DOC_CACHE_ORDER.remove(abs_path)
        except ValueError:
            pass
    if doc is not None:
        try:
            doc.close()
        except Exception:
            pass


def release_pdf_handles_for_paths(target_paths: Sequence[str]) -> None:
    """Close cached fitz documents for any deleted *target_paths* entry (file or folder).

    Windows returns SHFileOperation error 32 if the app still holds the PDF open
    (preview LRU). Call this before recycle-bin delete or ``os.remove`` on paths
    that might be open in the cache.
    """
    if not target_paths:
        return
    roots: list[tuple[str, bool]] = []
    for raw in target_paths:
        if not raw:
            continue
        ap = os.path.abspath(os.path.normpath(raw))
        try:
            is_dir = os.path.isdir(ap)
        except OSError:
            is_dir = False
        roots.append((ap, is_dir))
    if not roots:
        return
    sep = os.sep
    victim_keys: set[str] = set()
    with _PDF_DOC_CACHE_LOCK:
        for key in list(_PDF_DOC_CACHE.keys()):
            for t, is_dir in roots:
                base = t.rstrip(sep)
                if is_dir:
                    if key == base or key.startswith(base + sep):
                        victim_keys.add(key)
                        break
                else:
                    if key == base or key.startswith(base + sep):
                        victim_keys.add(key)
                        break
        docs: list[object] = []
        for key in victim_keys:
            doc = _PDF_DOC_CACHE.pop(key, None)
            if doc is not None:
                docs.append(doc)
            try:
                _PDF_DOC_CACHE_ORDER.remove(key)
            except ValueError:
                pass
    for doc in docs:
        try:
            doc.close()
        except Exception:
            pass

def filesystem_natural_sort_key(name: str):
    """Sort key so numbered names order 1, 2, … 9, 10 (not 1, 10, 2)."""
    parts = re.split(r"(\d+)", name)
    return tuple(int(p) if p.isdigit() else p.casefold() for p in parts)


# --- Extension groups ---
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
# OOXML Word (same ZIP/text extraction approach)
DOCX_LIKE_EXT = {".docx", ".docm", ".dotx"}
TEXT_EXT = {
    ".txt",
    ".csv",
    ".md",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".py",
    ".log",
    ".ini",
    ".cfg",
    ".yaml",
    ".yml",
}
# Excel files (handled via openpyxl). CSV is left in TEXT_EXT so it keeps its
# plain-text preview behaviour; xlsx/xlsm get a real table preview.
EXCEL_EXT = {".xlsx", ".xlsm", ".xltx", ".xltm"}



# ── Korean name transliteration via OpenAI ────────────────────────────────────

class KoreanTranslateError(RuntimeError):
    """Raised when Korean transliteration cannot be performed (surfaces real cause to UI)."""


def translate_to_korean_openai(
    names: list[str],
    api_key: str,
    log=None,
) -> dict[str, str]:
    """Transliterate a list of English/Bengali names to Korean phonetics using OpenAI.

    Returns a dict  {original_name: korean_transliteration}.
    Raises KoreanTranslateError on hard failures (missing package, bad key, network, etc.)
    so the caller can surface the real reason to the user instead of silently returning
    the original names unchanged.
    """
    if not names:
        return {}
    if not api_key or not api_key.strip():
        raise KoreanTranslateError(
            "No OpenAI API key is set.\n\n"
            "Click the Korean 🇰🇷 button and enter your key when prompted,\n"
            "or add OPENAI_API_KEY=sk-... to a .env file next to the app."
        )

    try:
        import openai as _openai
    except ImportError:
        raise KoreanTranslateError(
            "The 'openai' Python package is not installed.\n\n"
            "Run:  pip install openai\n\nThen restart the app."
        )

    # Require openai v1+ (the v1 client uses openai.OpenAI class)
    if not hasattr(_openai, "OpenAI"):
        raise KoreanTranslateError(
            "Installed 'openai' package is too old (found v0.x, need v1+).\n\n"
            "Run:  pip install --upgrade openai\n\nThen restart the app."
        )

    client = _openai.OpenAI(api_key=api_key.strip())

    # Build a numbered list so the model can return answers in order
    numbered = "\n".join(f"{i+1}. {name}" for i, name in enumerate(names))
    system_prompt = (
        "You are an expert Korean transliteration assistant. "
        "The user will give you a numbered list of person names written in English "
        "(they are mostly South Asian / Bangladeshi names). "
        "Return ONLY a numbered list of the same names transliterated into Korean Hangul, "
        "one per line, in the exact same order. "
        "Rules:\n"
        "- Write ONLY the Korean Hangul — no romanisation, no explanation.\n"
        "- Keep the same spacing between name parts as the input.\n"
        "- Expand common abbreviations: MD = 모하메드, MR = 미스터, etc.\n"
        "- If a name part has no standard Korean equivalent, use phonetic approximation.\n"
        "Example input:\n"
        "1. MIA MAMUN\n"
        "2. SULTANA MARJIA\n"
        "3. MOLLAH MD SIFAT\n"
        "Example output:\n"
        "1. 미아 마문\n"
        "2. 술타나 마르지아\n"
        "3. 몰라 모하메드 시파트"
    )

    try:
        resp = _gpt4o_create(client,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": numbered},
            ],
            temperature=0.1,
            max_tokens=len(names) * 40 + 100,
        )
        raw = resp.choices[0].message.content or ""
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        result: dict[str, str] = {}
        import re as _re
        for line in lines:
            m = _re.match(r"^(\d+)[\.\)]\s*(.+)$", line)
            if m:
                idx = int(m.group(1)) - 1
                korean = m.group(2).strip()
                if 0 <= idx < len(names) and korean:
                    result[names[idx]] = korean
        # Fill any missing entries with original
        for name in names:
            if name not in result:
                result[name] = name
        if log:
            log(f"Korean translate: {len(result)} name(s) transliterated via OpenAI.")
        return result
    except KoreanTranslateError:
        raise
    except Exception as exc:
        if log:
            log(f"Korean translate error: {exc}")
        raise KoreanTranslateError(f"OpenAI API error:\n\n{exc}") from exc


def get_pillow():
    try:
        from PIL import Image

        return Image
    except ImportError:
        return None


def get_fitz():
    try:
        import fitz  # PyMuPDF

        return fitz
    except ImportError:
        return None


_TESSERACT_EXE_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)


def configure_tesseract() -> bool:
    """Point pytesseract at tesseract.exe if it exists (PATH or common install paths)."""
    try:
        import pytesseract
    except ImportError:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        pass
    for candidate in _TESSERACT_EXE_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            try:
                import pytesseract

                pytesseract.pytesseract.tesseract_cmd = candidate
                pytesseract.get_tesseract_version()
                return True
            except Exception:
                continue
    return False


def _terminal_log_wrap(log):
    """Mirror log lines to stderr so ``python student_folder_maker.py`` shows OCR trace."""
    import sys

    def _log(msg: str) -> None:
        try:
            print(f"[Rename-OCR] {msg}", file=sys.stderr, flush=True)
        except Exception:
            pass
        if log:
            log(msg)

    return _log


def tesseract_status() -> tuple[bool, str]:
    """Return (available, short message for UI/logs)."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False, "pytesseract is not installed (pip install pytesseract pillow)."
    if configure_tesseract():
        try:
            import pytesseract

            ver = pytesseract.get_tesseract_version()
            return True, f"Tesseract {ver} ready."
        except Exception as e:
            return False, f"Tesseract found but failed: {e}"
    return (
        False,
        "Tesseract OCR is not installed. Scanned PDFs need it.\n"
        "Install: winget install -e --id UB-Mannheim.TesseractOCR\n"
        "Or download: https://github.com/UB-Mannheim/tesseract/wiki",
    )


def get_docx():
    try:
        import docx

        return docx
    except ImportError:
        return None


def get_openpyxl():
    """Return openpyxl module or None if not installed."""
    try:
        import openpyxl

        return openpyxl
    except ImportError:
        return None


# ---------- Excel (xlsx/xlsm) helpers ----------

def _excel_safe_out(path: Path) -> Path:
    """Return a non-clobbering output path: foo.xlsx -> foo-1.xlsx if exists."""
    if not path.exists():
        return path
    n = 1
    while True:
        c = path.parent / f"{path.stem}-{n}{path.suffix}"
        if not c.exists():
            return c
        n += 1


def excel_list_sheets(path: str) -> list[tuple[str, int, int]] | None:
    """Return [(sheet_name, row_count, col_count), ...] or None if openpyxl missing."""
    op = get_openpyxl()
    if op is None:
        return None
    try:
        wb = op.load_workbook(path, read_only=True, data_only=True)
        result = []
        for name in wb.sheetnames:
            ws = wb[name]
            result.append((name, ws.max_row or 0, ws.max_column or 0))
        return result
    except Exception:
        return None


def preview_excel_text(
    path: str,
    sheet: str | None = None,
    max_rows: int = 200,
    max_cols: int = 12,
    max_col_width: int = 28,
) -> str | None:
    """Render an xlsx sheet as a fixed-width table for the preview pane.

    Returns formatted text or None if openpyxl is unavailable / file unreadable.

    Robustness notes:
    - Caps columns at `max_cols` (default 12). Sheets with more columns get a
      '(+N more columns hidden)' note. Without this, sheets with 50+ cols
      produce a wrapped, unreadable wall of pipes.
    - Trims trailing empty rows so blank space at the bottom of a sheet
      doesn't render as a sea of empty `|` characters.
    - Replaces newlines/CR inside cells with ' · ' so multi-line cells don't
      break the row layout.
    - Uses unicodedata.east_asian_width to compute display widths, so
      Korean/Chinese/Japanese (2-cell wide) characters align correctly.
    """
    import unicodedata

    def visual_width(s: str) -> int:
        # Wide (W) and Full (F) take 2 columns in a monospace font;
        # Narrow/Half/Neutral/Ambiguous take 1.
        w = 0
        for ch in s:
            ea = unicodedata.east_asian_width(ch)
            w += 2 if ea in ("W", "F") else 1
        return w

    def pad_to(s: str, target: int) -> str:
        deficit = target - visual_width(s)
        return s + " " * max(deficit, 0)

    op = get_openpyxl()
    if op is None:
        return (
            "Excel preview needs openpyxl.\n"
            "Install it with:  pip install openpyxl"
        )
    try:
        wb = op.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        return f"(Could not open Excel file: {e})"

    sheet_names = wb.sheetnames
    if not sheet_names:
        return "(Empty workbook — no sheets.)"

    target = sheet if (sheet and sheet in sheet_names) else sheet_names[0]
    ws = wb[target]

    # Pull rows up to max_rows + a small lookahead so we know if more exist.
    raw_rows: list[tuple] = []
    truncated_rows = False
    total_rows_seen = 0
    max_cols_seen = 0
    for ri, row in enumerate(ws.iter_rows(values_only=True)):
        total_rows_seen = ri + 1
        if len(row) > max_cols_seen:
            max_cols_seen = len(row)
        if ri >= max_rows:
            truncated_rows = True
            break
        raw_rows.append(row)

    # Trim trailing empty rows from what we'll display.
    def is_empty(r: tuple) -> bool:
        return all(v is None or (isinstance(v, str) and not v.strip()) for v in r)

    while raw_rows and is_empty(raw_rows[-1]):
        raw_rows.pop()

    if not raw_rows:
        return f"Sheet: {target} (empty)"

    # Sheet may declare more cols than we want to render. Cap them.
    declared_rows = ws.max_row or 0
    declared_cols = ws.max_column or 0
    shown_rows = max(declared_rows, total_rows_seen)
    shown_cols = max(declared_cols, max_cols_seen)

    cols_to_show = min(max_cols, shown_cols)
    has_more_cols = shown_cols > cols_to_show

    # Build display matrix.
    rows: list[list[str]] = []
    for row in raw_rows:
        cells = []
        for ci in range(cols_to_show):
            v = row[ci] if ci < len(row) else None
            if v is None:
                cells.append("")
                continue
            s = str(v).replace("\r\n", " · ").replace("\n", " · ").replace("\r", " · ")
            if visual_width(s) > max_col_width:
                # crude truncation that respects visual width
                trimmed = []
                w = 0
                for ch in s:
                    ea = unicodedata.east_asian_width(ch)
                    cw = 2 if ea in ("W", "F") else 1
                    if w + cw > max_col_width - 1:
                        break
                    trimmed.append(ch)
                    w += cw
                s = "".join(trimmed) + "…"
            cells.append(s)
        rows.append(cells)

    widths = [
        max(visual_width(rows[r][c]) for r in range(len(rows)))
        for c in range(cols_to_show)
    ]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    out_lines = []
    header = f"Sheet: {target}  ({shown_rows} rows × {shown_cols} cols"
    if has_more_cols:
        header += f"; showing first {cols_to_show}"
    header += ")"
    if len(sheet_names) > 1:
        header += f"\nOther sheets: {', '.join(n for n in sheet_names if n != target)}"
    out_lines.append(header)
    out_lines.append("")
    out_lines.append(sep)
    for ri, row in enumerate(rows):
        out_lines.append(
            "| " + " | ".join(pad_to(c, widths[i]) for i, c in enumerate(row)) + " |"
        )
        if ri == 0:
            out_lines.append(sep)
    out_lines.append(sep)
    extras = []
    if has_more_cols:
        extras.append(f"(+{shown_cols - cols_to_show} more columns hidden)")
    if truncated_rows:
        suffix = f" of {shown_rows}" if shown_rows > max_rows else ""
        extras.append(f"…showing first {max_rows} rows{suffix}.")
    if extras:
        out_lines.append("")
        out_lines.append(" ".join(extras))
        out_lines.append(
            "Tip: use 'Render to HTML' (right-click) for the full sheet in a browser."
        )
    return "\n".join(out_lines)


def excel_to_html(path: str, sheet: str | None = None, dst: str | None = None) -> tuple[bool, str]:
    """Render a sheet to a self-contained HTML file. Returns (ok, output_path_or_error)."""
    op = get_openpyxl()
    if op is None:
        return False, "openpyxl is not installed."
    try:
        wb = op.load_workbook(path, data_only=True)
    except Exception as e:
        return False, f"Could not open Excel file: {e}"
    sheet_name = sheet if (sheet and sheet in wb.sheetnames) else wb.sheetnames[0]
    ws = wb[sheet_name]

    out_path = Path(dst) if dst else Path(path).with_suffix(".html")
    out_path = _excel_safe_out(out_path)

    rows = list(ws.iter_rows(values_only=True))
    safe_sheet = sheet_name.replace("<", "&lt;").replace(">", "&gt;")
    src_name = Path(path).name.replace("<", "&lt;").replace(">", "&gt;")

    html_parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        f"<title>{src_name} — {safe_sheet}</title>",
        "<style>body{font-family:system-ui,sans-serif;padding:20px;margin:0}",
        "table{border-collapse:collapse;font-size:13px;background:#fff}",
        "th,td{border:1px solid #ccc;padding:5px 9px;text-align:left;vertical-align:top;white-space:pre-wrap}",
        "th{background:#eaeaea;position:sticky;top:0;z-index:1}",
        "tr:nth-child(even){background:#fafafa}",
        "h2{margin:0 0 12px 0;font-weight:600;font-size:16px}",
        ".meta{color:#666;font-size:12px;margin-bottom:14px}",
        "</style></head><body>",
        f"<h2>{src_name} — {safe_sheet}</h2>",
        f"<div class='meta'>{len(rows)} rows × "
        f"{(len(rows[0]) if rows else 0)} cols</div>",
        "<table>",
    ]
    for i, row in enumerate(rows):
        tag = "th" if i == 0 else "td"
        cells = "".join(
            f"<{tag}>{'' if v is None else str(v).replace('<', '&lt;').replace('>', '&gt;')}</{tag}>"
            for v in row
        )
        html_parts.append(f"<tr>{cells}</tr>")
    html_parts.append("</table></body></html>")
    try:
        out_path.write_text("".join(html_parts), encoding="utf-8")
    except OSError as e:
        return False, f"Could not write HTML: {e}"
    return True, str(out_path)


def excel_to_csv_each_sheet(path: str, out_dir: str | None = None) -> tuple[bool, list[str] | str]:
    """Write one CSV per sheet. Returns (ok, [paths] or error string)."""
    import csv as _csv

    op = get_openpyxl()
    if op is None:
        return False, "openpyxl is not installed."
    try:
        wb = op.load_workbook(path, data_only=True)
    except Exception as e:
        return False, f"Could not open Excel file: {e}"

    src = Path(path)
    out = Path(out_dir) if out_dir else src.parent
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        safe_sheet = "".join(c if c.isalnum() or c in "-_ " else "_" for c in sheet_name)
        target = _excel_safe_out(out / f"{src.stem}__{safe_sheet}.csv")
        try:
            with open(target, "w", newline="", encoding="utf-8-sig") as f:
                w = _csv.writer(f)
                for row in ws.iter_rows(values_only=True):
                    w.writerow(row)
        except OSError as e:
            return False, f"Could not write CSV {target}: {e}"
        written.append(str(target))
    return True, written


def excel_search_cells(
    path: str,
    query: str,
    sheet: str | None = None,
    icase: bool = True,
    use_regex: bool = False,
) -> list[tuple[str, str, str]] | None:
    """Search cells. Returns [(sheet_name, coord, value_str), ...] or None if openpyxl missing."""
    import re as _re

    op = get_openpyxl()
    if op is None:
        return None
    try:
        wb = op.load_workbook(path, data_only=True)
    except Exception:
        return None
    sheets_to_search = [sheet] if (sheet and sheet in wb.sheetnames) else wb.sheetnames
    pattern = _re.compile(query if use_regex else _re.escape(query), _re.IGNORECASE if icase else 0)
    results: list[tuple[str, str, str]] = []
    for sname in sheets_to_search:
        ws = wb[sname]
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                if pattern.search(str(cell.value)):
                    results.append((sname, cell.coordinate, str(cell.value)))
    return results


def excel_set_cell(
    path: str,
    coord: str,
    value,
    sheet: str | None = None,
    in_place: bool = False,
) -> tuple[bool, str]:
    """Write a single cell. By default saves to <name>_edited.xlsx (preserves original).
    If in_place=True, overwrites src after backing up to <name>.xlsx.bak.
    """
    op = get_openpyxl()
    if op is None:
        return False, "openpyxl is not installed."
    try:
        wb = op.load_workbook(path)
    except Exception as e:
        return False, f"Could not open Excel file: {e}"
    sheet_name = sheet if (sheet and sheet in wb.sheetnames) else wb.sheetnames[0]
    ws = wb[sheet_name]

    # try to coerce numeric strings
    v = value
    if isinstance(v, str):
        try:
            v = int(v)
        except ValueError:
            try:
                v = float(v)
            except ValueError:
                pass
    try:
        ws[coord] = v
    except Exception as e:
        return False, f"Could not set cell {coord}: {e}"

    src = Path(path)
    if in_place:
        bak = src.with_suffix(src.suffix + ".bak")
        if not bak.exists():
            try:
                shutil.copy2(src, bak)
            except OSError as e:
                return False, f"Could not create backup: {e}"
        out = src
    else:
        out = _excel_safe_out(src.with_name(f"{src.stem}_edited{src.suffix}"))
    try:
        wb.save(out)
    except OSError as e:
        return False, f"Could not save: {e}"
    return True, str(out)


# ---------- end Excel helpers ----------


def _read_text_preview(path: str, max_bytes: int = 400_000) -> str:
    try:
        raw = Path(path).read_bytes()[:max_bytes]
    except OSError as e:
        return f"(Could not read file: {e})"
    for enc in ("utf-8", "utf-8-sig", "cp949", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def preview_plain_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext not in TEXT_EXT and ext != "":
        return ""
    return _read_text_preview(path)


def _docx_blocks_to_lines(document) -> list[str]:
    """Walk body in document order: paragraphs and tables (python-docx)."""
    lines: list[str] = []
    try:
        from docx.document import Document as DocType
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
    except ImportError:
        for p in document.paragraphs:
            t = (p.text or "").strip()
            if t:
                lines.append(t)
        return lines

    if not isinstance(document, DocType):
        return lines

    body = document.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            para = Paragraph(child, document)
            t = (para.text or "").strip()
            if t:
                lines.append(t)
        elif isinstance(child, CT_Tbl):
            tbl = Table(child, document)
            for row in tbl.rows:
                cells = [(c.text or "").replace("\n", " ").strip() for c in row.cells]
                row_txt = " | ".join(cells)
                if row_txt.strip():
                    lines.append(row_txt)
    return lines


def _docx_header_footer_lines(document) -> list[str]:
    lines: list[str] = []
    try:
        for sec in document.sections:
            for label, part in (
                ("Header", sec.header),
                ("Footer", sec.footer),
            ):
                try:
                    for p in part.paragraphs:
                        t = (p.text or "").strip()
                        if t:
                            lines.append(f"[{label}] {t}")
                except Exception:
                    continue
    except Exception:
        pass
    return lines


def preview_docx_text(path: str, max_chars: int = 600_000) -> str | None:
    docx = get_docx()
    if not docx:
        return None
    try:
        d = docx.Document(path)
        parts = _docx_header_footer_lines(d)
        parts.extend(_docx_blocks_to_lines(d))
        if not parts:
            # Fallback: linear paragraphs only (some templates hide body in shapes)
            for p in d.paragraphs:
                t = (p.text or "").strip()
                if t:
                    parts.append(t)
        text = "\n\n".join(parts) if parts else "(No extractable text — file may use text boxes or scanned images.)"
        if len(text) > max_chars:
            text = text[: max_chars - 20] + "\n\n… (truncated)"
        return text
    except Exception as e:
        return f"(Could not read Word file: {e})"


def preview_doc_binary_via_word(path: str, max_chars: int = 600_000) -> str | None:
    """Preview legacy .doc using Microsoft Word (Windows + pywin32)."""
    if sys.platform != "win32":
        return None
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return None
    path = os.path.abspath(path)
    word = None
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(path, ReadOnly=True, AddToRecentFiles=False)
        try:
            txt = doc.Content.Text or ""
        finally:
            doc.Close(False)
        txt = txt.replace("\r", "\n").strip()
        if not txt:
            return "(Empty document)"
        if len(txt) > max_chars:
            txt = txt[: max_chars - 20] + "\n\n… (truncated)"
        return txt
    except Exception as e:
        return f"(Could not read .doc — open in Word or install Word/pywin32: {e})"
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass


def sniff_ooxml_word(path: str) -> bool:
    """True if file is a ZIP OOXML Word doc (e.g. misnamed or no extension)."""
    try:
        with zipfile.ZipFile(path, "r") as z:
            names = set(z.namelist())
            if "[Content_Types].xml" not in names:
                return False
            data = z.read("[Content_Types].xml").decode("utf-8", errors="ignore")
            return "wordprocessingml" in data
    except (zipfile.BadZipFile, OSError, KeyError):
        return False


def preview_word_document(path: str) -> str | None:
    """
    Preview .docx/.docm/.dotx via python-docx; .doc via Word COM when available.
    Sniff OOXML for extensionless or misnamed files.
    """
    ext = Path(path).suffix.lower()
    if ext in DOCX_LIKE_EXT:
        return preview_docx_text(path)
    if ext == ".doc":
        if sniff_ooxml_word(path):
            return preview_docx_text(path)
        return preview_doc_binary_via_word(path)
    if ext == "" and sniff_ooxml_word(path):
        return preview_docx_text(path)
    return None


def _win_shfile_delete_to_recycle(path: str) -> tuple[bool, str]:
    """
    Move a single path to the Windows Recycle Bin via SHFileOperationW.
    Only call on sys.platform == 'win32'. Returns (ok, message).
    """
    import ctypes
    from ctypes import wintypes

    # Defined once here; both delete helpers call this function.
    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x04

    buf = ctypes.create_unicode_buffer(path + "\0\0")
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None

    ret = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if ret != 0 or op.fAnyOperationsAborted:
        return False, f"Could not move to Recycle Bin (error {ret}). Try closing the file in another app."
    return True, "Moved to Recycle Bin."


def delete_file_recycle_or_remove(path: str) -> tuple[bool, str]:
    """
    Move file to Recycle Bin on Windows (no extra packages).
    On other OS, remove permanently.
    """
    path = os.path.abspath(os.path.normpath(path))
    if not os.path.isfile(path):
        return False, "Not a file or missing."

    release_pdf_handles_for_paths((path,))

    if sys.platform == "win32":
        return _win_shfile_delete_to_recycle(path)

    try:
        os.remove(path)
        return True, "Deleted permanently."
    except OSError as e:
        return False, str(e)


def soft_delete_path(path: str) -> tuple[bool, str, str]:
    """Move *path* to a ``_to_review/`` subfolder beside it, for undoable deletion.

    The destination filename gets a millisecond timestamp prefix so repeated
    soft-deletes of the same name never collide.

    Returns ``(ok, dst_path, error_msg)``.
    ``dst_path`` is the new location on success (use it to undo via ``shutil.move``).
    """
    import time as _time

    path = os.path.abspath(os.path.normpath(path))
    if not os.path.exists(path):
        return False, "", f"Not found: {path}"

    parent = os.path.dirname(path)
    review_dir = os.path.join(parent, "_to_review")
    try:
        os.makedirs(review_dir, exist_ok=True)
    except OSError as e:
        return False, "", f"Cannot create _to_review/: {e}"

    base = os.path.basename(path)
    ts = int(_time.time() * 1000)
    dst = os.path.join(review_dir, f"{ts}_{base}")
    # Avoid collisions (unlikely but guard with a counter)
    counter = 1
    while os.path.exists(dst):
        dst = os.path.join(review_dir, f"{ts}_{counter}_{base}")
        counter += 1

    # Release any preview/render handles we hold on this file, otherwise the
    # move degrades to copy+failed-unlink on Windows (file stays in place).
    try:
        release_pdf_handles_for_paths((path,))
    except Exception:
        pass

    try:
        shutil.move(path, dst)
        return True, dst, ""
    except OSError as e:
        # Clean up a half-done copy so we don't leave duplicates behind.
        try:
            if os.path.exists(dst) and os.path.exists(path) and os.path.isfile(dst):
                os.replace(dst, os.path.join(review_dir, f"FAILED_{ts}_{base}"))
        except OSError:
            pass
        return False, "", str(e)


def delete_path_recycle_or_remove(path: str) -> tuple[bool, str]:
    """
    Delete a file or folder path.
    On Windows, move to Recycle Bin via SHFileOperationW.
    On other OS, delete permanently (file=remove, folder=rmtree).
    """
    path = os.path.abspath(os.path.normpath(path))
    if not (os.path.isfile(path) or os.path.isdir(path)):
        return False, "Path not found."

    release_pdf_handles_for_paths((path,))

    if sys.platform == "win32":
        ok, msg = _win_shfile_delete_to_recycle(path)
        if not ok:
            # Surface a clearer hint for folder/multi-file operations
            msg = msg.replace("the file in another app", "related files/apps")
        return ok, msg

    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True, "Deleted permanently."
    except OSError as e:
        return False, str(e)


def _fitz_open_document(fitz, path: str):
    """Open a PDF path; retry with bytes stream if direct open fails (encoding / long paths)."""
    path = os.path.abspath(os.path.normpath(path))
    try:
        return fitz.open(path)
    except Exception:
        pass
    try:
        data = Path(path).read_bytes()
        if not data.strip():
            return None
        return fitz.open(stream=data, filetype="pdf")
    except Exception:
        return None


def pil_image_for_pdf_page(
    path: str,
    page_index: int,
    max_w: int,
    max_h: int,
    *,
    max_zoom: float = 2.0,
    log: Callable[[str], None] | None = None,
):
    """Return PIL Image for PDF page, or None.

    Uses a module-level LRU document cache so the file is not re-opened for
    every page render — this is the primary fix for slow multi-page preview.
    max_zoom caps rasterization scale for huge pages.
    """
    fitz = get_fitz()
    Image = get_pillow()
    if not fitz or not Image:
        if log:
            log("PDF preview: PyMuPDF or Pillow is not available.")
        return None
    try:
        # Use cached document (avoids re-opening the PDF for every page)
        doc = _get_cached_fitz_doc(fitz, path)
        if doc is None:
            # Fallback: open without caching (e.g. long/unicode paths)
            doc = _fitz_open_document(fitz, path)
            _use_cache = False
        else:
            _use_cache = True
        if doc is None:
            if log:
                log(f"PDF preview: could not open {path!r}")
            return None
        if page_index < 0 or page_index >= len(doc):
            if log:
                log(f"PDF preview: page {page_index} out of range (len={len(doc)}).")
            if not _use_cache:
                try:
                    doc.close()
                except Exception:
                    pass
            return None
        page = doc.load_page(page_index)
        # Some PDFs have an empty crop box; fall back to mediabox to avoid div-by-zero / bad zoom.
        r = page.rect
        pw = float(r.width)
        ph = float(r.height)
        if not (pw > 0.5 and ph > 0.5):
            mb = page.mediabox
            pw = max(float(mb.width), 1.0)
            ph = max(float(mb.height), 1.0)
        pw = max(pw, 1.0)
        ph = max(ph, 1.0)
        zoom = min(float(max_w) / pw, float(max_h) / ph, float(max_zoom))
        if zoom <= 0 or not (zoom == zoom):  # NaN guard
            zoom = min(float(max_zoom), 1.0)
        mat = fitz.Matrix(zoom, zoom)
        try:
            pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
        except Exception:
            pix = page.get_pixmap(matrix=mat, alpha=False)
        if not _use_cache:
            try:
                doc.close()
            except Exception:
                pass
        if pix.width < 1 or pix.height < 1:
            if log:
                log("PDF preview: empty pixmap from page.")
            return None
        # PNG round-trip avoids raw stride / CMYK / component-count mismatches.
        try:
            png = pix.tobytes("png")
            img = Image.open(BytesIO(png)).convert("RGB")
            return img
        except Exception as first:
            try:
                w, h = pix.width, pix.height
                raw = bytes(pix.samples)
                n = int(pix.n)
                if n == 3:
                    img = Image.frombytes("RGB", (w, h), raw)
                elif n == 4:
                    img = Image.frombytes("RGBA", (w, h), raw).convert("RGB")
                elif n == 1:
                    img = Image.frombytes("L", (w, h), raw).convert("RGB")
                else:
                    raise ValueError(f"pix.n={n}") from first
                return img
            except Exception as e2:
                if log:
                    log(f"PDF preview decode {os.path.basename(path)!r}: {first!r}; fallback: {e2!r}")
                return None
    except Exception as e:
        if log:
            log(f"PDF preview {os.path.basename(path)!r}: {e}")
        return None


def pil_image_resize(path: str, max_w: int, max_h: int):
    Image = get_pillow()
    if not Image:
        return None
    try:
        img = Image.open(path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGBA")
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception:
        return None


def pil_image_from_bytes(data: bytes, max_w: int, max_h: int):
    """Load image from bytes and fit inside max_w×max_h (same rules as pil_image_resize)."""
    Image = get_pillow()
    if not Image:
        return None
    try:
        img = Image.open(BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGBA")
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception:
        return None


def pil_image_load_full_rgb(path: str):
    """Load image at full resolution as RGB (EXIF orientation applied). None if error."""
    Image = get_pillow()
    if not Image:
        return None
    try:
        from PIL import ImageOps

        img = Image.open(path)
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGBA")
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception:
        return None


def pil_image_from_bytes_full_rgb(data: bytes):
    """Load image from bytes at full resolution as RGB (same rules as path load, no EXIF file)."""
    Image = get_pillow()
    if not Image:
        return None
    try:
        from PIL import ImageOps

        img = Image.open(BytesIO(data))
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGBA")
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception:
        return None


def physical_mm_to_pixels(mm_w: float, mm_h: float, dpi: float) -> tuple[int, int]:
    """Convert width × height in millimetres to pixel size at the given DPI (rounded)."""
    if dpi <= 0:
        dpi = 300.0
    pw = int(round(float(mm_w) * float(dpi) / 25.4))
    ph = int(round(float(mm_h) * float(dpi) / 25.4))
    return max(1, pw), max(1, ph)


def build_curve_lut(points) -> list:
    """Build a 256-entry lookup table from curve control points.

    ``points`` is an iterable of (x, y) in 0..255 (input → output). Points are
    sorted by x and interpolated linearly; values outside the point range are
    clamped to the nearest endpoint. Returns a list of 256 ints (0..255).
    """
    pts = sorted(((float(x), float(y)) for x, y in points), key=lambda p: p[0])
    if not pts:
        return list(range(256))
    lut: list[int] = []
    for i in range(256):
        if i <= pts[0][0]:
            y = pts[0][1]
        elif i >= pts[-1][0]:
            y = pts[-1][1]
        else:
            y = pts[-1][1]
            for j in range(len(pts) - 1):
                x0, y0 = pts[j]
                x1, y1 = pts[j + 1]
                if x0 <= i <= x1:
                    y = y0 if x1 == x0 else y0 + (y1 - y0) * (i - x0) / (x1 - x0)
                    break
        lut.append(max(0, min(255, int(round(y)))))
    return lut


def _build_levels_lut(black: float, white: float, gamma: float) -> list:
    """LUT that remaps [black, white] → [0, 255] with a gamma curve."""
    black = max(0.0, min(254.0, float(black)))
    white = max(black + 1.0, min(255.0, float(white)))
    g = float(gamma) if gamma and gamma > 0 else 1.0
    inv = 1.0 / g
    span = white - black
    lut: list[int] = []
    for i in range(256):
        if i <= black:
            v = 0.0
        elif i >= white:
            v = 255.0
        else:
            t = (i - black) / span
            v = (t ** inv) * 255.0
        lut.append(max(0, min(255, int(round(v)))))
    return lut


def apply_image_adjustments(
    pil_img,
    *,
    brightness: float = 1.0,
    contrast: float = 1.0,
    black: float = 0.0,
    white: float = 255.0,
    gamma: float = 1.0,
    curve_points=None,
):
    """Apply tone adjustments to a PIL image and return a new RGB image.

    Order: levels (black/white/gamma) → brightness → contrast → curve.
    Each stage is skipped when its parameters are at their neutral defaults, so
    a fully-neutral call returns an unchanged RGB copy cheaply.
    """
    Image = get_pillow()
    if Image is None:
        return pil_img
    from PIL import ImageEnhance

    img = pil_img if pil_img.mode == "RGB" else pil_img.convert("RGB")

    levels_on = not (abs(black) < 0.5 and abs(white - 255.0) < 0.5 and abs(gamma - 1.0) < 1e-3)
    if levels_on:
        lut = _build_levels_lut(black, white, gamma)
        img = img.point(lut * 3)

    if abs(brightness - 1.0) > 1e-3:
        img = ImageEnhance.Brightness(img).enhance(float(brightness))

    if abs(contrast - 1.0) > 1e-3:
        img = ImageEnhance.Contrast(img).enhance(float(contrast))

    if curve_points:
        pts = list(curve_points)
        is_identity = (
            len(pts) == 2
            and abs(pts[0][0]) < 0.5 and abs(pts[0][1]) < 0.5
            and abs(pts[1][0] - 255) < 0.5 and abs(pts[1][1] - 255) < 0.5
        )
        if not is_identity:
            clut = build_curve_lut(pts)
            img = img.point(clut * 3)

    if img is pil_img:
        img = pil_img.copy()
    return img


def pil_image_for_pdf_bytes(
    data: bytes,
    page_index: int,
    max_w: int,
    max_h: int,
    *,
    max_zoom: float = 2.0,
):
    """Render a PDF page from bytes to PIL Image, or None.

    Uses the same PNG round-trip as pil_image_for_pdf_page so that CMYK and
    other non-RGB colorspaces don't crash Image.frombytes.
    """
    fitz = get_fitz()
    Image = get_pillow()
    if not fitz or not Image:
        return None
    doc = None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        if page_index < 0 or page_index >= len(doc):
            return None
        page = doc.load_page(page_index)
        r = page.rect
        pw = max(float(r.width), 1.0)
        ph = max(float(r.height), 1.0)
        zoom = min(float(max_w) / pw, float(max_h) / ph, float(max_zoom))
        if zoom <= 0 or zoom != zoom:  # guard NaN
            zoom = min(float(max_zoom), 1.0)
        mat = fitz.Matrix(zoom, zoom)
        try:
            pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
        except Exception:
            pix = page.get_pixmap(matrix=mat, alpha=False)
        if pix.width < 1 or pix.height < 1:
            return None
        # PNG round-trip handles CMYK / unusual component counts safely.
        try:
            png = pix.tobytes("png")
            return Image.open(BytesIO(png)).convert("RGB")
        except Exception:
            w, h = pix.width, pix.height
            raw = bytes(pix.samples)
            n = int(pix.n)
            if n == 3:
                return Image.frombytes("RGB", (w, h), raw)
            elif n == 4:
                return Image.frombytes("RGBA", (w, h), raw).convert("RGB")
            elif n == 1:
                return Image.frombytes("L", (w, h), raw).convert("RGB")
            return None
    except Exception:
        return None
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def pdf_page_count_bytes(data: bytes) -> int:
    fitz = get_fitz()
    if not fitz:
        return 0
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return 0


def pdf_page_count(path: str) -> int:
    fitz = get_fitz()
    if not fitz:
        return 0
    try:
        doc = fitz.open(path)
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return 0


def _find_libreoffice_soffice() -> str | None:
    """Locate LibreOffice / soffice on Windows, macOS, and Linux (PATH + common dirs)."""
    if sys.platform == "win32":
        candidates: list[str] = []
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(key)
            if not base:
                continue
            candidates.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
        candidates.extend(
            [
                r"C:\Program Files\LibreOffice\program\soffice.exe",
                r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            ]
        )
        for p in candidates:
            if p and os.path.isfile(p):
                return p
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        for folder in path_dirs:
            exe = os.path.join(folder, "soffice.exe")
            if os.path.isfile(exe):
                return exe
        return None

    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for folder in path_dirs:
        for name in ("soffice", "libreoffice"):
            exe = os.path.join(folder, name)
            if os.path.isfile(exe) and os.access(exe, os.X_OK):
                return exe
    mac_app = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if sys.platform == "darwin" and os.path.isfile(mac_app):
        return mac_app
    return None


def _word_to_pdf_via_word_com(src: str, dst: str, log: Callable[[str], None]) -> bool:
    """
    Convert .doc / .docx / .rtf (and other Word types) to PDF using Microsoft Word COM.

    Uses Word's own PDF pipeline (ExportAsFixedFormat / SaveAs PDF) so layout, fonts,
    images, and tables match Word's PDF export. Word runs hidden with alerts off.
    """
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return False

    src = os.path.normpath(os.path.abspath(src))
    dst = os.path.normpath(os.path.abspath(dst))
    if not os.path.isfile(src):
        log("Word COM: source file not found.")
        return False

    # Word VBA constants (dynamic dispatch — use numeric values)
    wd_format_pdf = 17  # wdFormatPDF
    wd_export_format_pdf = 17  # wdExportFormatPDF
    wd_export_all_document = 0  # wdExportAllDocument
    wd_export_optimize_for_print = 0  # wdExportOptimizeForPrint
    wd_do_not_save_changes = 0  # wdDoNotSaveChanges
    wd_alerts_none = 0  # wdAlertsNone
    mso_automation_security_low = 1  # fewer macro/security prompts during automation

    word = None
    doc = None
    try:
        # DispatchEx: dedicated instance; do not attach to a running interactive Word.
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        try:
            word.DisplayAlerts = wd_alerts_none
        except Exception:
            pass
        try:
            word.ScreenUpdating = False
        except Exception:
            pass
        try:
            word.AutomationSecurity = mso_automation_security_low
        except Exception:
            pass

        # Open read-only; avoid conversion/repair dialogs where possible.
        try:
            doc = word.Documents.Open(
                FileName=src,
                ConfirmConversions=False,
                ReadOnly=True,
                AddToRecentFiles=False,
                Visible=False,
            )
        except TypeError:
            try:
                doc = word.Documents.Open(
                    FileName=src,
                    ConfirmConversions=False,
                    ReadOnly=True,
                    AddToRecentFiles=False,
                    OpenAndRepair=False,
                )
            except TypeError:
                doc = word.Documents.Open(
                    FileName=src,
                    ConfirmConversions=False,
                    ReadOnly=True,
                    AddToRecentFiles=False,
                )

        if doc is None:
            log("Word COM: Documents.Open returned no document.")
            return False

        # 1) Prefer ExportAsFixedFormat — native PDF export (best fidelity vs SaveAs PDF on some builds).
        export_ok = False
        try:
            doc.ExportAsFixedFormat(
                OutputFileName=dst,
                ExportFormat=wd_export_format_pdf,
                OpenAfterExport=False,
                OptimizeFor=wd_export_optimize_for_print,
                Range=wd_export_all_document,
                IncludeDocProps=True,
                KeepIRM=False,
                CreateBookmarks=0,
                DocStructureTags=True,
                BitmapMissingFonts=True,
                UseISO19005PDF=False,
            )
            export_ok = bool(os.path.isfile(dst) and os.path.getsize(dst) > 0)
        except Exception as e1:
            log(f"Word ExportAsFixedFormat (full): {e1}")
            try:
                doc.ExportAsFixedFormat(
                    OutputFileName=dst,
                    ExportFormat=wd_export_format_pdf,
                    OpenAfterExport=False,
                )
                export_ok = bool(os.path.isfile(dst) and os.path.getsize(dst) > 0)
            except Exception as e2:
                log(f"Word ExportAsFixedFormat (minimal): {e2}")

        if not export_ok:
            try:
                if hasattr(doc, "SaveAs2"):
                    doc.SaveAs2(
                        FileName=dst,
                        FileFormat=wd_format_pdf,
                        AddToRecentFiles=False,
                    )
                else:
                    doc.SaveAs(FileName=dst, FileFormat=wd_format_pdf)
            except Exception as e_save:
                log(f"Word SaveAs PDF: {e_save}")
                return False

        ok = bool(os.path.isfile(dst) and os.path.getsize(dst) > 0)
        if ok:
            log("Word COM: PDF export finished.")
        else:
            log("Word COM: PDF file missing or empty after export.")
        return ok
    except Exception as e:
        log(f"Word COM: {e}")
        return False
    finally:
        if doc is not None:
            try:
                doc.Close(SaveChanges=wd_do_not_save_changes)
            except Exception:
                pass
            doc = None
        if word is not None:
            try:
                word.Quit(SaveChanges=wd_do_not_save_changes)
            except Exception:
                pass
            word = None


def _word_to_pdf_via_libreoffice(src: str, dst: str, log: Callable[[str], None]) -> bool:
    """Convert .doc/.docx/… using LibreOffice headless (no Microsoft Word)."""
    exe = _find_libreoffice_soffice()
    if not exe:
        return False

    outdir = os.path.dirname(dst) or "."
    os.makedirs(outdir, exist_ok=True)

    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 180,
    }
    if sys.platform == "win32":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        r = subprocess.run(
            [
                exe,
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--convert-to",
                "pdf",
                "--outdir",
                outdir,
                src,
            ],
            **run_kw,
        )
    except subprocess.TimeoutExpired:
        log("LibreOffice conversion timed out (180s).")
        return False
    except OSError as e:
        log(f"LibreOffice: {e}")
        return False

    if r.returncode != 0:
        hint = (r.stderr or r.stdout or "").strip() or f"exit code {r.returncode}"
        log(f"LibreOffice: {hint}")
        return False

    produced = os.path.join(outdir, Path(src).stem + ".pdf")
    if not os.path.isfile(produced):
        log(f"LibreOffice did not create: {produced}")
        return False

    target = os.path.normpath(dst)
    if os.path.normcase(os.path.abspath(produced)) != os.path.normcase(os.path.abspath(target)):
        try:
            if os.path.isfile(target):
                os.remove(target)
            shutil.move(produced, target)
        except OSError as e:
            log(f"Could not move PDF to target path: {e}")
            return False

    return True


# LibreOffice / Word — PDF export (context: Convert to PDF)
OFFICE_PDF_EXT = frozenset({".doc", ".docx", ".docm", ".dotx", ".rtf"})


def word_pdf_sibling_path(input_path: str) -> str:
    """PDF path beside the Word/RTF source: same folder, same stem, ``.pdf``."""
    d, b = os.path.split(os.path.abspath(input_path))
    stem, _ext = os.path.splitext(b)
    return os.path.join(d, stem + ".pdf")


def word_to_pdf_beside_source(
    input_path: str,
    log: Callable[[str], None],
    *,
    force_reconvert: bool = False,
) -> tuple[bool, str]:
    """
    Convert Office/RTF to a PDF next to ``input_path``. Reuses the sibling ``.pdf`` when it
    exists and is newer than or same age as the source (unless ``force_reconvert``).

    Returns ``(True, pdf_path)`` or ``(False, error_message)``.
    """
    input_path = os.path.abspath(input_path)
    if not os.path.isfile(input_path):
        return False, "File not found."
    ext = os.path.splitext(input_path)[1].lower()
    if ext not in OFFICE_PDF_EXT:
        return False, f"Unsupported type ({ext or 'no extension'}). Use Word or RTF export types."

    dst = word_pdf_sibling_path(input_path)
    if not force_reconvert and os.path.isfile(dst):
        try:
            if os.path.getmtime(dst) + 0.5 >= os.path.getmtime(input_path):
                if pdf_page_count(dst) > 0:
                    log(f"Using cached PDF (up to date): {dst}")
                    return True, dst
                log("Cached PDF has no readable pages; reconverting.")
        except OSError:
            pass

    if word_to_pdf(input_path, dst, log):
        if os.path.isfile(dst) and pdf_page_count(dst) > 0:
            return True, dst
        if os.path.isfile(dst):
            return False, "Conversion wrote a PDF file, but it has no readable pages (try reopening the Word file or reinstall Word/LibreOffice)."
        return False, "Conversion reported success but PDF was not created."
    return (
        False,
        "Word→PDF failed. On Windows: install Microsoft Word and pywin32, or install LibreOffice. "
        "If the file is open in Word, close it and try again.",
    )


def word_to_pdf_temp(
    input_path: str,
    log: Callable[[str], None],
    *,
    force_reconvert: bool = False,
    _cache: dict = {},
) -> tuple[bool, str]:
    """
    Convert Office/RTF to a PDF in the SYSTEM TEMP directory (never in the working folder).
    Caches result by (input_path, mtime) so repeat clicks are instant.
    Returns (True, tmp_pdf_path) or (False, error_message).
    """
    import tempfile as _tempfile

    input_path = os.path.abspath(input_path)
    if not os.path.isfile(input_path):
        return False, "File not found."
    ext = os.path.splitext(input_path)[1].lower()
    if ext not in OFFICE_PDF_EXT:
        return False, f"Unsupported type ({ext or 'no extension'})."

    try:
        mtime = os.path.getmtime(input_path)
    except OSError:
        mtime = 0.0

    cache_key = (input_path, mtime)
    if not force_reconvert and cache_key in _cache:
        cached = _cache[cache_key]
        if os.path.isfile(cached) and pdf_page_count(cached) > 0:
            log(f"  Word preview: using cached temp PDF.")
            return True, cached

    fd, tmp = _tempfile.mkstemp(suffix=".pdf", prefix="sfm_wpreview_")
    try:
        os.close(fd)
    except OSError:
        pass

    if word_to_pdf(input_path, tmp, log):
        if os.path.isfile(tmp) and pdf_page_count(tmp) > 0:
            _cache[cache_key] = tmp
            return True, tmp
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False, "Conversion wrote a PDF but it has no readable pages."
        return False, "Conversion reported success but PDF was not created."

    try:
        os.remove(tmp)
    except OSError:
        pass
    return (
        False,
        "Word→PDF preview failed.\n"
        "On Windows: install Microsoft Word + pywin32, or install LibreOffice.\n"
        "If the file is open in Word, close it and try again.",
    )


def parse_pdf_page_range_input(text: str, page_count: int) -> tuple[bool, list[int] | str]:
    """
    Parse user page list (1-based): ``5``, ``1-3``, ``1, 3, 5-7``.
    Returns ``(True, zero_based_indices)`` or ``(False, error_message)``.
    """
    text = text.strip()
    if not text:
        return False, "Enter at least one page number."
    if page_count < 1:
        return False, "PDF has no pages."
    seen: set[int] = set()
    out: list[int] = []
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                lo = int(a.strip())
                hi = int(b.strip())
            except ValueError:
                return False, f"Invalid range: {part!r}"
            if lo > hi:
                lo, hi = hi, lo
            for n in range(lo, hi + 1):
                if 1 <= n <= page_count:
                    z = n - 1
                    if z not in seen:
                        seen.add(z)
                        out.append(z)
                elif n < 1 or n > page_count:
                    return False, f"Page {n} is out of range (1–{page_count})."
        else:
            try:
                n = int(part)
            except ValueError:
                return False, f"Invalid page: {part!r}"
            if n < 1 or n > page_count:
                return False, f"Page {n} is out of range (1–{page_count})."
            z = n - 1
            if z not in seen:
                seen.add(z)
                out.append(z)
    if not out:
        return False, "No valid pages in that range."
    out.sort()
    return True, out


def pdf_export_selected_pages_to_tempfile(path: str, pages_zero_based: list[int]) -> tuple[bool, str]:
    """Write a new PDF containing only the given 0-based page indices; path is a temp ``.pdf``."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "PDF not found."
    fitz = get_fitz()
    if not fitz:
        return False, "PyMuPDF is required for page-specific printing."
    try:
        src = fitz.open(path)
        total = len(src)
        uniq = sorted({p for p in pages_zero_based if 0 <= p < total})
        if not uniq:
            src.close()
            return False, "No valid pages to print."
        out_doc = fitz.open()
        for p in uniq:
            out_doc.insert_pdf(src, from_page=p, to_page=p)
        src.close()
        fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="sfm_print_")
        os.close(fd)
        out_doc.save(tmp)
        out_doc.close()
        return True, tmp
    except Exception as e:
        return False, str(e)


def word_to_pdf(src: str, dst: str, log: Callable[[str], None]) -> bool:
    """
    Office / RTF → PDF: on Windows try Microsoft Word (pywin32), then LibreOffice headless.
    On other OS, use LibreOffice only.

    Writes to a temporary PDF first, then replaces ``dst``, so an existing locked or stale
    sibling ``.pdf`` is less likely to break conversion.
    """
    src = os.path.normpath(os.path.abspath(src))
    dst = os.path.normpath(os.path.abspath(dst))
    if not os.path.isfile(src):
        log("Word→PDF: source file not found.")
        return False
    outdir = os.path.dirname(dst)
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    has_pywin32 = False
    if sys.platform == "win32":
        try:
            import win32com.client  # noqa: F401

            has_pywin32 = True
        except ImportError:
            log(
                "pywin32 is not installed for this Python. Install it with:\n"
                "  python -m pip install pywin32"
            )

    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="~sfm_w2p_", dir=outdir or None)
        os.close(fd)

        ok_word = False
        if sys.platform == "win32" and has_pywin32:
            # STA COM: worker threads usually CoInitialize in the UI code; main-thread callers may not.
            com_init_local = False
            try:
                import pythoncom

                try:
                    pythoncom.CoInitialize()
                    com_init_local = True
                except pythoncom.com_error:
                    pass
                log("Trying Microsoft Word (COM)…")
                ok_word = _word_to_pdf_via_word_com(src, tmp_path, log)
                if ok_word:
                    log("Microsoft Word wrote a temporary PDF; validating…")
            finally:
                if com_init_local:
                    try:
                        import pythoncom

                        pythoncom.CoUninitialize()
                    except Exception:
                        pass

        if not ok_word:
            log("Trying LibreOffice (soffice --headless)…")
            if not _word_to_pdf_via_libreoffice(src, tmp_path, log):
                if sys.platform == "win32" and not has_pywin32:
                    log(
                        "Office→PDF unavailable: install pywin32 + Microsoft Word, "
                        "or install LibreOffice (https://www.libreoffice.org/)."
                    )
                else:
                    log(
                        "Office→PDF failed: LibreOffice was not found or could not convert this file. "
                        "Install LibreOffice or open the document elsewhere."
                    )
                return False

        if pdf_page_count(tmp_path) < 1:
            log("Word→PDF: output has no readable pages (conversion may have failed silently).")
            return False

        try:
            os.replace(tmp_path, dst)
        except OSError as e:
            log(f"Could not replace destination PDF ({e}); trying copy.")
            try:
                shutil.copyfile(tmp_path, dst)
            except OSError as e2:
                log(f"Could not write final PDF: {e2}")
                return False
        tmp_path = None
        log(f"Saved PDF: {dst}")
        return True
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def image_to_pdf(src_paths: list[str], dst: str, log: Callable[[str], None]) -> bool:
    Image = get_pillow()
    if not Image:
        log("Image→PDF: install Pillow.")
        return False
    paths = [p for p in src_paths if os.path.isfile(p)]
    if not paths:
        log("No image files selected.")
        return False
    images: list = []
    try:
        for p in paths:
            im = Image.open(p)
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            elif im.mode != "RGB":
                im = im.convert("RGB")
            images.append(im)
        first, *rest = images
        if rest:
            first.save(dst, "PDF", resolution=100.0, save_all=True, append_images=rest)
        else:
            first.save(dst, "PDF", resolution=100.0)
        log(f"Saved PDF ({len(paths)} image(s)): {dst}")
        return True
    except Exception as e:
        log(f"Image→PDF failed: {e}")
        return False


def combine_files_to_pdf(
    src_paths: list[str], dst: str, log: Callable[[str], None]
) -> tuple[bool, str]:
    """Combine images and PDFs (in the given order) into a single PDF at ``dst``.

    Images are rasterised to one page each (via Pillow); PDFs keep all pages.
    Source files are left untouched. Writes to a temp file then atomically
    replaces ``dst`` so it is safe even when ``dst`` equals one of the inputs.
    Returns ``(ok, message_or_path)``.
    """
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF (pip install pymupdf)."
    Image = get_pillow()

    paths = [os.path.abspath(p) for p in src_paths if os.path.isfile(p)]
    if not paths:
        return False, "No valid files to combine."

    out = fitz.open()
    used = 0
    try:
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            if ext == ".pdf":
                try:
                    with fitz.open(p) as s:
                        out.insert_pdf(s)
                    used += 1
                except Exception as e:
                    log(f"Combine: skipped PDF '{os.path.basename(p)}': {e}")
            elif ext in IMAGE_EXT:
                if not Image:
                    log("Combine: install Pillow to include images.")
                    continue
                try:
                    im = Image.open(p)
                    if im.mode != "RGB":
                        im = im.convert("RGB")
                    buf = BytesIO()
                    im.save(buf, "PDF", resolution=100.0)
                    with fitz.open("pdf", buf.getvalue()) as imgpdf:
                        out.insert_pdf(imgpdf)
                    used += 1
                except Exception as e:
                    log(f"Combine: skipped image '{os.path.basename(p)}': {e}")
            else:
                log(f"Combine: skipped unsupported '{os.path.basename(p)}'.")

        if used < 1 or out.page_count < 1:
            out.close()
            return False, "Nothing could be combined (no supported pages)."

        dst = os.path.abspath(dst)
        outdir = os.path.dirname(dst) or os.getcwd()
        os.makedirs(outdir, exist_ok=True)
        tmp = os.path.join(outdir, f".~sfm_combine_{os.getpid()}.pdf")
        out.save(tmp)
        out.close()
        os.replace(tmp, dst)
        log(f"Combined {used} file(s) → {dst}")
        return True, dst
    except Exception as e:
        try:
            out.close()
        except Exception:
            pass
        return False, str(e)


def plain_text_file_to_pdf(src: str, dst: str, log: Callable[[str], None]) -> bool:
    """Encode plain text as a multi-page PDF (line wrap + PyMuPDF insert_text)."""
    fitz = get_fitz()
    if not fitz:
        log("Text→PDF: install PyMuPDF.")
        return False
    try:
        text = Path(src).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log(f"Text→PDF read failed: {e}")
        return False
    if not text.strip():
        log("Text→PDF: file is empty.")
        return False
    lines = text.splitlines()
    paper = fitz.paper_rect("a4")
    w, h = paper.width, paper.height
    margin = 56
    fontsize = 10
    line_step = max(int(fontsize * 1.35), 12)
    wrap_w = max(24, int((w - 2 * margin) / max(fontsize * 0.52, 4)))
    y_max = h - margin
    doc = fitz.open()
    try:
        page = doc.new_page(width=w, height=h)
        y = margin + fontsize

        def new_page() -> None:
            nonlocal page, y
            page = doc.new_page(width=w, height=h)
            y = margin + fontsize

        for raw in lines:
            for part in textwrap.wrap(raw, width=wrap_w) or ([""] if raw == "" else []):
                if y + line_step > y_max:
                    new_page()
                page.insert_text((margin, y), part.replace("\t", "    "), fontsize=fontsize, fontname="helv")
                y += line_step
        doc.save(dst)
        doc.close()
        log(f"Saved text as PDF: {dst}")
        return True
    except Exception as e:
        try:
            doc.close()
        except Exception:
            pass
        log(f"Text→PDF failed: {e}")
        return False


def can_convert_file_to_pdf(path: str) -> bool:
    """True if the file is a regular non-PDF we know how to turn into PDF."""
    if not os.path.isfile(path):
        return False
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return False
    if ext in IMAGE_EXT or ext in DOCX_LIKE_EXT or ext in OFFICE_PDF_EXT:
        return True
    if ext in TEXT_EXT:
        return True
    if ext == "" and sniff_ooxml_word(path):
        return True
    return False


def convert_file_to_pdf_replace(src: str, log: Callable[[str], None]) -> tuple[bool, str]:
    """
    Convert one file to <stem>.pdf in the same folder, keeping the original untouched.
    If a PDF with the same stem already exists, it is moved to _to_review/ before being replaced.
    Writes via a temp file in the SYSTEM temp dir (not the working folder) so no intermediate
    files are visible to the user during conversion.
    Returns (True, dst_path) on success, (False, error_msg) on failure.
    """
    import tempfile as _tempfile

    src = os.path.abspath(src)
    if not os.path.isfile(src):
        return False, "File not found."
    ext = Path(src).suffix.lower()
    if ext == ".pdf":
        return False, "Already a PDF."
    if not can_convert_file_to_pdf(src):
        return False, "This file type is not supported for Convert to PDF."

    ddir = os.path.dirname(src)
    stem = Path(src).stem
    dst = os.path.join(ddir, stem + ".pdf")

    # Use system temp dir so no intermediate .pdf appears in the working folder.
    fd, tmp = _tempfile.mkstemp(suffix=".pdf", prefix="sfm2pdf_")
    try:
        os.close(fd)
    except OSError:
        pass

    def _cleanup_tmp() -> None:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    try:
        if ext in IMAGE_EXT:
            ok_conv = image_to_pdf([src], tmp, log)
        elif ext in DOCX_LIKE_EXT or ext in OFFICE_PDF_EXT or (ext == "" and sniff_ooxml_word(src)):
            ok_conv = word_to_pdf(src, tmp, log)
        elif ext in TEXT_EXT:
            ok_conv = plain_text_file_to_pdf(src, tmp, log)
        else:
            _cleanup_tmp()
            return False, "Unsupported extension."

        if not ok_conv or not os.path.isfile(tmp):
            _cleanup_tmp()
            return False, "Conversion failed (see log)."

        # If a PDF with the same stem already exists, protect it in _to_review/ first.
        dst_abs = os.path.abspath(dst)
        if os.path.isfile(dst) and os.path.normcase(dst_abs) != os.path.normcase(src):
            review_dir = os.path.join(ddir, "_to_review")
            os.makedirs(review_dir, exist_ok=True)
            review_dst = os.path.join(review_dir, os.path.basename(dst))
            try:
                shutil.move(dst, review_dst)
                log(f"  Existing PDF moved to _to_review/: {os.path.basename(dst)}")
            except OSError as mv_err:
                _cleanup_tmp()
                return False, f"Could not move existing PDF out of the way: {mv_err}"

        # Original source file stays in place — just copy the converted PDF beside it.
        shutil.move(tmp, dst)
        log(f"  PDF saved alongside original: {os.path.basename(dst)}")
        log(f"  Original kept: {os.path.basename(src)}")
        return True, dst
    except OSError as e:
        _cleanup_tmp()
        return False, str(e)


def pdf_first_page_to_png_replace(
    src: str, log: Callable[[str], None], dpi: int = 150
) -> tuple[bool, str]:
    """
    Render the first PDF page to <stem>.png, remove the PDF (Recycle Bin on Windows).
    If the PDF has more than one page, only the first page is kept (warning in log).
    """
    fitz = get_fitz()
    Image = get_pillow()
    src = os.path.abspath(src)
    if not fitz or not Image:
        return False, "Install PyMuPDF and Pillow."
    if not os.path.isfile(src) or Path(src).suffix.lower() != ".pdf":
        return False, "Select a PDF file on disk."

    ddir = os.path.dirname(src)
    stem = Path(src).stem
    dst = os.path.join(ddir, stem + ".png")
    tmp = os.path.join(ddir, f".~sfm2png_{stem}_{os.getpid()}.png")

    def _cleanup_tmp() -> None:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    try:
        doc = fitz.open(src)
        n = len(doc)
        if n < 1:
            doc.close()
            return False, "Empty PDF."
        page = doc.load_page(0)
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()
        img.save(tmp, "PNG")
    except Exception as e:
        _cleanup_tmp()
        return False, str(e)

    try:
        if os.path.isfile(dst) and os.path.normcase(os.path.abspath(dst)) != os.path.normcase(src):
            r_ok, r_msg = delete_file_recycle_or_remove(dst)
            if not r_ok:
                _cleanup_tmp()
                return False, f"Could not replace existing PNG: {r_msg}"

        rem_ok, rem_msg = delete_file_recycle_or_remove(src)
        if not rem_ok:
            _cleanup_tmp()
            return False, f"Could not remove PDF: {rem_msg}"

        os.replace(tmp, dst)
        if n > 1:
            log(f"PDF had {n} pages; only the first page was saved as PNG.")
        log(f"Saved: {dst}")
        return True, dst
    except OSError as e:
        _cleanup_tmp()
        return False, str(e)
    finally:
        _cleanup_tmp()


def pdf_pages_to_png_keep(
    src: str,
    page_indices,
    log: Callable[[str], None],
    dpi: int = 150,
) -> tuple[bool, list, str]:
    """Render the given (0-based) pages of ``src`` to PNG files next to the PDF.

    The source PDF is kept untouched (no deletion). Output names are
    ``<stem>_p<NNNN>.png``; for a single page the original ``<stem>.png`` name is
    used when it does not already exist. Returns ``(ok, [paths], message)``.
    """
    fitz = get_fitz()
    Image = get_pillow()
    if not fitz or not Image:
        return False, [], "Install PyMuPDF and Pillow."
    src = os.path.abspath(src)
    if not os.path.isfile(src) or Path(src).suffix.lower() != ".pdf":
        return False, [], "Select a PDF file on disk."

    ddir = os.path.dirname(src)
    stem = Path(src).stem
    written: list = []
    try:
        doc = fitz.open(src)
        n = len(doc)
        if n < 1:
            doc.close()
            return False, [], "Empty PDF."
        wanted = [i for i in page_indices if 0 <= i < n]
        if not wanted:
            doc.close()
            return False, [], "No valid pages selected."
        single = len(wanted) == 1
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for i in wanted:
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if single:
                plain = os.path.join(ddir, stem + ".png")
                dst = plain if not os.path.exists(plain) else os.path.join(
                    ddir, f"{stem}_p{i + 1:04d}.png"
                )
            else:
                dst = os.path.join(ddir, f"{stem}_p{i + 1:04d}.png")
            # Never overwrite an existing file (no deletions); add a suffix.
            if os.path.exists(dst):
                base, ext = os.path.splitext(dst)
                k = 2
                while os.path.exists(f"{base}_{k}{ext}"):
                    k += 1
                dst = f"{base}_{k}{ext}"
            img.save(dst, "PNG")
            written.append(dst)
            log(f"Saved: {dst}")
        doc.close()
        return True, written, f"Converted {len(written)} page(s)."
    except Exception as e:
        return False, written, str(e)


def pdf_pages_to_jpg_keep(
    src: str,
    page_indices,
    log: Callable[[str], None],
    dpi: int = 150,
    quality: int = 90,
) -> tuple[bool, list, str]:
    """Render the given (0-based) pages of ``src`` to JPEG files next to the PDF.

    The source PDF is kept untouched. Output names are ``<stem>_p<NNNN>.jpg``;
    for a single page the plain ``<stem>.jpg`` name is used when it does not
    already exist. Returns ``(ok, [paths], message)``.
    """
    fitz = get_fitz()
    Image = get_pillow()
    if not fitz or not Image:
        return False, [], "Install PyMuPDF and Pillow."
    src = os.path.abspath(src)
    if not os.path.isfile(src) or Path(src).suffix.lower() != ".pdf":
        return False, [], "Select a PDF file on disk."

    ddir = os.path.dirname(src)
    stem = Path(src).stem
    written: list = []
    try:
        doc = fitz.open(src)
        n = len(doc)
        if n < 1:
            doc.close()
            return False, [], "Empty PDF."
        wanted = [i for i in page_indices if 0 <= i < n]
        if not wanted:
            doc.close()
            return False, [], "No valid pages selected."
        single = len(wanted) == 1
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for i in wanted:
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if single:
                plain = os.path.join(ddir, stem + ".jpg")
                dst = plain if not os.path.exists(plain) else os.path.join(
                    ddir, f"{stem}_p{i + 1:04d}.jpg"
                )
            else:
                dst = os.path.join(ddir, f"{stem}_p{i + 1:04d}.jpg")
            if os.path.exists(dst):
                base, ext = os.path.splitext(dst)
                k = 2
                while os.path.exists(f"{base}_{k}{ext}"):
                    k += 1
                dst = f"{base}_{k}{ext}"
            img.save(dst, "JPEG", quality=quality, optimize=True)
            written.append(dst)
            log(f"Saved: {dst}")
        doc.close()
        return True, written, f"Converted {len(written)} page(s)."
    except Exception as e:
        return False, written, str(e)


def pdf_to_images(src: str, out_dir: str, log: Callable[[str], None], dpi: int = 150) -> bool:
    fitz = get_fitz()
    Image = get_pillow()
    if not fitz or not Image:
        log("PDF→images: install PyMuPDF and Pillow.")
        return False
    os.makedirs(out_dir, exist_ok=True)
    base = Path(src).stem
    try:
        doc = fitz.open(src)
        n = len(doc)
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for i in range(n):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out = os.path.join(out_dir, f"{base}_page_{i + 1:04d}.png")
            img.save(out, "PNG")
        doc.close()
        log(f"Exported {n} page image(s) to {out_dir}")
        return True
    except Exception as e:
        log(f"PDF→images failed: {e}")
        return False


def pdf_suggest_basename_from_first_page(path: str) -> str:
    """
    Build a short filename stem from text on the first page (first line, sanitized).
    Falls back to the file stem if extraction fails.
    """
    import re

    path = os.path.abspath(path)
    default = Path(path).stem
    fitz = get_fitz()
    if not fitz or not os.path.isfile(path):
        return default
    try:
        doc = fitz.open(path)
        if len(doc) < 1:
            doc.close()
            return default
        page = doc.load_page(0)
        txt = (page.get_text() or "").strip()
        doc.close()
        if not txt:
            return default
        line = txt.splitlines()[0].strip()
        line = re.sub(r"[^\w\s\-]", " ", line, flags=re.UNICODE)
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) > 120:
            line = line[:120].rstrip()
        line = line.strip(" .-_")
        if not line:
            return default
        invalid = set('\\/:*?"<>|')
        line = "".join(c if c not in invalid else "_" for c in line)
        return line or default
    except Exception:
        return default


def pdf_merge_ordered_paths(src_paths: list[str], dst: str) -> tuple[bool, str]:
    """
    Merge PDFs in list order into dst using PyMuPDF.
    Writes to a temp file in the output folder, then replaces dst (atomic on Windows when possible).
    Opens each source via _fitz_open_document (path + byte-stream fallback).
    """
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF (pip install pymupdf)."

    paths: list[str] = []
    seen: set[str] = set()
    for p in src_paths:
        ap = os.path.abspath(os.path.normpath(p))
        if not ap.lower().endswith(".pdf") or not os.path.isfile(ap):
            continue
        if ap not in seen:
            seen.add(ap)
            paths.append(ap)

    if len(paths) < 2:
        return False, "Need at least two distinct PDF files on disk."

    dst = os.path.abspath(os.path.normpath(dst))
    outdir = os.path.dirname(dst) or os.getcwd()
    try:
        os.makedirs(outdir, exist_ok=True)
    except OSError as e:
        return False, f"Cannot create output folder: {e}"

    tmp_path = os.path.join(outdir, f".sfm_merge_{uuid.uuid4().hex}.pdf")
    merged = None
    try:
        merged = fitz.open()
        for p in paths:
            doc = _fitz_open_document(fitz, p)
            if doc is None:
                raise OSError(f"Could not open PDF: {p}")
            try:
                n = len(doc)
                if n > 0:
                    merged.insert_pdf(doc)
            finally:
                doc.close()

        if len(merged) < 1:
            raise ValueError("No pages were merged (files may be empty or unreadable).")

        merged.save(tmp_path, garbage=4, deflate=True)
    except Exception as e:
        err = str(e)
        if merged is not None:
            try:
                merged.close()
            except Exception:
                pass
        try:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return False, err
    else:
        if merged is not None:
            try:
                merged.close()
            except Exception:
                pass

    # Preview uses _get_cached_fitz_doc; an open handle prevents os.remove/replace on Windows.
    _release = {os.path.abspath(os.path.normpath(dst))}
    for p in paths:
        _release.add(os.path.abspath(os.path.normpath(p)))
    for _ap in _release:
        invalidate_pdf_doc_cache(_ap)

    try:
        if os.path.isfile(dst):
            try:
                os.remove(dst)
            except OSError as e_rm:
                try:
                    if os.path.isfile(tmp_path):
                        os.unlink(tmp_path)
                except OSError:
                    pass
                return False, f"Cannot replace existing file (close it if open): {e_rm}"
        os.replace(tmp_path, dst)
    except OSError as e:
        try:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return False, f"Could not save merged PDF: {e}"

    return True, dst


def pdf_merge_ordered_page_refs(
    page_refs: list[tuple[str, int]], dst: str
) -> tuple[bool, str]:
    """
    Merge specific pages in order: each item is (pdf_path, 0-based page_index).
    Same temp-then-replace behavior as pdf_merge_ordered_paths.
    """
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF (pip install pymupdf)."

    refs: list[tuple[str, int]] = []
    for p, pi in page_refs:
        ap = os.path.abspath(os.path.normpath(p))
        if not ap.lower().endswith(".pdf") or not os.path.isfile(ap):
            return False, f"Missing PDF: {p}"
        refs.append((ap, int(pi)))

    if len(refs) < 1:
        return False, "No pages to write."

    dst = os.path.abspath(os.path.normpath(dst))
    outdir = os.path.dirname(dst) or os.getcwd()
    try:
        os.makedirs(outdir, exist_ok=True)
    except OSError as e:
        return False, f"Cannot create output folder: {e}"

    tmp_path = os.path.join(outdir, f".sfm_merge_{uuid.uuid4().hex}.pdf")
    merged = None
    try:
        merged = fitz.open()
        for path, pi in refs:
            doc = _fitz_open_document(fitz, path)
            if doc is None:
                raise OSError(f"Could not open PDF: {path}")
            try:
                if pi < 0 or pi >= len(doc):
                    raise ValueError(
                        f"Invalid page {pi + 1} for {os.path.basename(path)}"
                    )
                merged.insert_pdf(doc, from_page=pi, to_page=pi)
            finally:
                doc.close()

        if len(merged) < 1:
            raise ValueError("No pages were merged (files may be empty or unreadable).")

        merged.save(tmp_path, garbage=4, deflate=True)
    except Exception as e:
        err = str(e)
        if merged is not None:
            try:
                merged.close()
            except Exception:
                pass
        try:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return False, err
    else:
        if merged is not None:
            try:
                merged.close()
            except Exception:
                pass

    _release = {os.path.abspath(os.path.normpath(dst))}
    for path, _pi in refs:
        _release.add(os.path.abspath(os.path.normpath(path)))
    for _ap in _release:
        invalidate_pdf_doc_cache(_ap)

    try:
        if os.path.isfile(dst):
            try:
                os.remove(dst)
            except OSError as e_rm:
                try:
                    if os.path.isfile(tmp_path):
                        os.unlink(tmp_path)
                except OSError:
                    pass
                return False, f"Cannot replace existing file (close it if open): {e_rm}"
        os.replace(tmp_path, dst)
    except OSError as e:
        try:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return False, f"Could not save merged PDF: {e}"

    return True, dst


def pdf_merge(src_paths: list[str], dst: str, log: Callable[[str], None]) -> bool:
    ok, msg = pdf_merge_ordered_paths(src_paths, dst)
    if ok:
        log(f"Merged → {msg}")
        return True
    log(f"PDF merge failed: {msg}")
    return False


def pdf_compress(
    src: str,
    dst: str,
    dpi: int = 120,
    jpeg_quality: int = 72,
    max_bytes: int | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Compress a PDF by re-rendering every page as a JPEG image at *dpi* resolution.

    *src* and *dst* may be the same path (in-place).  Writes to a temp file first,
    then atomically replaces *dst*, so a partial failure leaves the original intact.

    If *max_bytes* is set the function will retry with progressively lower DPI /
    JPEG quality until the output fits or the minimum settings are reached.
    The quality ladder tried (from the starting point downward) is:
        quality steps : 85 → 72 → 60 → 50 → 40 → 30
        DPI steps     : 150 → 120 → 96 → 72

    Returns (ok, message).
    """
    import io as _io

    fitz = get_fitz()
    Image = get_pillow()
    if not fitz or not Image:
        return False, "Install PyMuPDF and Pillow."

    src = os.path.abspath(src)
    dst = os.path.abspath(dst)
    if not os.path.isfile(src):
        return False, f"Not found: {src}"

    orig_size = os.path.getsize(src)
    tmp = dst + ".__compress_tmp__"

    # Build the sequence of (dpi, quality) attempts.
    # Start with the caller's choice; if a target is set, queue fallback steps.
    _Q_LADDER = [85, 72, 60, 50, 40, 30]
    _D_LADDER = [150, 120, 96, 72]

    def _attempts() -> list[tuple[int, int]]:
        """Return ordered (dpi, quality) pairs to try, from highest to lowest."""
        if max_bytes is None:
            return [(dpi, jpeg_quality)]
        seen: set[tuple[int, int]] = set()
        out: list[tuple[int, int]] = []
        # First try: user-chosen settings
        out.append((dpi, jpeg_quality))
        seen.add((dpi, jpeg_quality))
        # Then reduce quality at the same DPI
        for q in _Q_LADDER:
            if q < jpeg_quality:
                pair = (dpi, q)
                if pair not in seen:
                    out.append(pair)
                    seen.add(pair)
        # Then reduce DPI too, sweeping quality at each level
        for d in _D_LADDER:
            if d < dpi:
                for q in _Q_LADDER:
                    pair = (d, q)
                    if pair not in seen:
                        out.append(pair)
                        seen.add(pair)
        return out

    last_msg = ""
    for attempt_dpi, attempt_q in _attempts():
        try:
            src_doc = fitz.open(src)
            new_doc = fitz.open()
            mat = fitz.Matrix(attempt_dpi / 72.0, attempt_dpi / 72.0)

            for page_num in range(len(src_doc)):
                page = src_doc.load_page(page_num)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
                pil_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                buf = _io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=attempt_q, optimize=True)
                jpeg_bytes = buf.getvalue()
                rect = page.rect
                new_page = new_doc.new_page(width=rect.width, height=rect.height)
                new_page.insert_image(new_page.rect, stream=jpeg_bytes)

            src_doc.close()
            new_doc.save(tmp, garbage=4, deflate=True, clean=True)
            new_doc.close()

            new_size = os.path.getsize(tmp)
            pct = int(100 * (1 - new_size / orig_size)) if orig_size else 0

            def _kb(b: int) -> str:
                return f"{b / 1_048_576:.2f} MB" if b >= 1_048_576 else f"{b // 1024} KB"

            last_msg = (
                f"{os.path.basename(dst)}: "
                f"{_kb(orig_size)} → {_kb(new_size)}  ({pct}% smaller)"
                + (f"  [{attempt_dpi} DPI · Q{attempt_q}]" if max_bytes else "")
            )

            # Accept this result if no target or target met
            if max_bytes is None or new_size <= max_bytes:
                os.replace(tmp, dst)
                full_msg = "Compressed " + last_msg
                if log:
                    log(full_msg)
                return True, full_msg

            # Target not met — clean up temp and try next lower setting
            try:
                os.remove(tmp)
            except OSError:
                pass
            if log:
                log(
                    f"  {_kb(new_size)} > target {_kb(max_bytes)} "
                    f"({attempt_dpi} DPI · Q{attempt_q}) — trying lower…"
                )

        except Exception as exc:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            err = f"Compress failed ({os.path.basename(src)}): {exc}"
            if log:
                log(err)
            return False, err

    # All attempts exhausted — commit the last (lowest-quality) result anyway
    # so the user at least gets something, but warn that target wasn't reached.
    try:
        if os.path.exists(tmp):
            os.replace(tmp, dst)
            msg = (
                f"Compressed (target not fully met) {last_msg}"
            )
        else:
            # Re-run the absolute minimum settings to produce a file
            src_doc = fitz.open(src)
            new_doc = fitz.open()
            mat = fitz.Matrix(72 / 72.0, 72 / 72.0)
            for page_num in range(len(src_doc)):
                page = src_doc.load_page(page_num)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
                pil_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                buf = _io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=30, optimize=True)
                jpeg_bytes = buf.getvalue()
                rect = page.rect
                new_page = new_doc.new_page(width=rect.width, height=rect.height)
                new_page.insert_image(new_page.rect, stream=jpeg_bytes)
            src_doc.close()
            new_doc.save(tmp, garbage=4, deflate=True, clean=True)
            new_doc.close()
            new_size = os.path.getsize(tmp)
            pct = int(100 * (1 - new_size / orig_size)) if orig_size else 0
            os.replace(tmp, dst)
            msg = (
                f"Compressed (target not fully met) {os.path.basename(dst)}: "
                f"{orig_size // 1024} KB → {new_size // 1024} KB  ({pct}% smaller)"
            )
        if log:
            log(msg)
        return True, msg
    except Exception as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        err = f"Compress failed ({os.path.basename(src)}): {exc}"
        if log:
            log(err)
        return False, err


def pdf_split_each_page(src: str, out_dir: str, log: Callable[[str], None]) -> bool:
    fitz = get_fitz()
    if not fitz:
        log("PDF split: install PyMuPDF.")
        return False
    os.makedirs(out_dir, exist_ok=True)
    base = Path(src).stem
    try:
        doc = fitz.open(src)
        n = len(doc)
        for i in range(n):
            single = fitz.open()
            single.insert_pdf(doc, from_page=i, to_page=i)
            out = os.path.join(out_dir, f"{base}_p_{i + 1}.pdf")
            single.save(out)
            single.close()
        doc.close()
        log(f"Split into {n} file(s) in {out_dir}")
        return True
    except Exception as e:
        log(f"PDF split failed: {e}")
        return False



def _ocr_extract_name_from_page(page, page_index: int, log) -> str:
    """Extract a person name from a single fitz page (text layer, then pytesseract fallback)."""
    import re
    raw_text = (page.get_text() or "").strip()
    name = _extract_name_from_text(raw_text)
    if name:
        log(f"  Page {page_index + 1}: name from text layer → {name!r}")
        return name
    # OCR fallback
    if not configure_tesseract():
        log(f"  Page {page_index + 1}: no text layer — install Tesseract OCR for scans")
        return ""
    try:
        import pytesseract
        from PIL import Image
        import io as _io
        try:
            import fitz as _fitz
            mat = _fitz.Matrix(2.0, 2.0)
        except Exception:
            mat = None
        if mat is not None:
            pix = page.get_pixmap(matrix=mat, alpha=False)
        else:
            pix = page.get_pixmap(alpha=False)
        img_bytes = pix.tobytes("png")
        img = Image.open(_io.BytesIO(img_bytes))
        ocr_text = pytesseract.image_to_string(img, lang="eng")
        name = _extract_name_from_text(ocr_text)
        if name:
            log(f"  Page {page_index + 1}: name from OCR → {name!r}")
            return name
    except Exception as e:
        log(f"  Page {page_index + 1}: OCR fallback failed: {e}")
    log(f"  Page {page_index + 1}: no name found, using page number")
    return ""


def _extract_name_from_text(text: str) -> str:
    """Scan text for the most likely student/person name. Returns sanitized stem or empty string."""
    import re
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # ── Words that are NOT person names even if they look like one ───────
    NON_NAME_WORDS = frozenset({
        # Document types / headers
        "APOSTILLE", "CERTIFIED", "CERTIFICATE", "DOCUMENT", "OFFICIAL",
        "ISSUING", "AUTHORITY", "CONVENTION", "REPUBLIC", "PUBLIC",
        # Countries / cities
        "BANGLADESH", "DHAKA", "KOREA", "JAPAN", "CHINA", "INDIA",
        "MINISTRY", "FINANCE", "GOVERNMENT", "EMBASSY",
        # Common non-name fields that look like names
        "ENGLISH", "LANGUAGE", "COURSE", "PROGRAM", "PERIOD", "CLASS",
        "LETTER", "OFFER", "ADMISSION", "APPLICATION", "REGISTRATION",
        "UNIVERSITY", "COLLEGE", "SCHOOL", "INSTITUTE", "ACADEMY",
        "PASSPORT", "NUMBER", "BIRTH", "DATE", "NATIONALITY",
        "MALE", "FEMALE", "SINGLE", "MARRIED",
        "GENERAL", "SPECIAL", "REGULAR", "STANDARD",
        "SENIOR", "ASSISTANT", "SECRETARY", "DIRECTOR", "OFFICER",
        # Form section headers (not person names)
        "PERSONAL", "DATA", "EMERGENCY", "CONTACT", "INFORMATION",
        "GUARDIAN", "ADDRESS", "TELEPHONE", "MOBILE", "EMAIL",
        "RELATIONSHIP", "FATHER", "MOTHER", "SPOUSE", "NEXT", "KIN",
        "DECLARATION", "UNDERTAKING", "ACKNOWLEDGEMENT", "ACKNOWLEDGMENT",
        "NAME", "NAMC", "FATHER'S", "MOTHER'S",
        "PARISHAD", "UNION", "LIMITED", "PROVIDERS", "PROFESSOR",
        "RECOMMENDATION", "MARKET", "ACCESS", "LBTTER",
    })

    def _is_plausible_name(candidate: str) -> bool:
        """Return True if candidate looks like a real person name (Latin, Korean, Bengali)."""
        if _is_form_section_header(candidate):
            return False
        if _is_ocr_garbage_name(candidate):
            return False
        if re.search(r"\b(?:given\s+names?|surname|forename|first\s+names?)\b", candidate, re.IGNORECASE):
            return False
        words = candidate.split()
        if len(words) < 1 or len(words) > 5:
            return False
        # Require at least 1 word with 2+ characters
        if not any(len(w) >= 2 for w in words):
            return False
        # Reject if any word is a known non-name word (Latin only check)
        if any(w.upper() in NON_NAME_WORDS for w in words):
            return False
        # Reject if any word contains digits
        if any(re.search(r"\d", w) for w in words):
            return False
        # Reject if the whole string is too long
        if len(candidate) > 60:
            return False
        # For pure Latin names: require at least 2 words
        _korean = re.compile(r"[\uAC00-\uD7A3]")
        is_korean = bool(_korean.search(candidate))
        is_latin = not is_korean
        if is_latin and len(words) < 2:
            return False
        return True

    # ── Priority 1: "Name : VALUE" pattern (label, optional space, colon, value) ──
    # Handles both "Name: ULLAH OLI" and "Name : ULLAH OLI"
    # Name value pattern — matches Latin names AND Korean/Bengali names after the colon
    # Supports: ASCII names (ULLAH OLI), Bengali (রাকিব), Korean (홍길동)
    _NAME_VALUE = (
        r"([A-Z\uAC00-\uD7A3][A-Za-z\uAC00-\uD7A3'\-]+"
        r"(?:\s+[A-Z\uAC00-\uD7A3][A-Za-z\uAC00-\uD7A3'\-]+){0,4})"
    )

    # ── Priority 1: label keyword + colon + name value ───────────────────
    # English labels: name, full name, student name, applicant name, applicant,
    #                 student, holder, bearer, candidate, participant
    # Korean labels:  지원자, 학생, 학생명, 지원자명, 이름, 성명, 학생이름
    # Bengali labels: নাম, শিক্ষার্থীর নাম, আবেদনকারী
    _LABEL_KW = (
        r"(?:"
        # English — avoid bare "name" (matches inside "Given names")
        r"full\s+name|student\s+name|applicant\s+name|applicant|student"
        r"|(?<![a-z])name(?![a-z\s]*names?\b)|holder|bearer|candidate|participant"
        # Korean
        r"|지원자명|학생명|지원자|학생|이름|성명|학생\s*이름"
        r")"
    )
    name_label_re = re.compile(
        r"(?:^|\b)" + _LABEL_KW + r"\s*[：:]\s*" + _NAME_VALUE,
        re.IGNORECASE,
    )

    # ── Priority 2: "Signed by: VALUE" ──────────────────────────────────
    signed_re = re.compile(
        r"(?:signed\s*by|signed)\s*[：:\s]\s*" + _NAME_VALUE,
        re.IGNORECASE,
    )

    # ── Priority 3: "Dear. NAME" or "Dear NAME" ─────────────────────────
    dear_re = re.compile(
        r"^dear[\.\s]+" + _NAME_VALUE,
        re.IGNORECASE,
    )

    for line in lines:
        for pattern in (name_label_re, signed_re, dear_re):
            m = pattern.search(line)
            if m:
                candidate = m.group(1).strip()
                if _is_plausible_name(candidate):
                    cleaned = _sanitize_name_for_file(candidate)
                    if cleaned:
                        return cleaned

    # ── Passport / ID: label on one line, value on the next ─────────────
    _LABEL_NEXT = (
        (r"(?:^|\b)(?:surname|nom|last\s*name|family\s*name)\b", "surname"),
        (r"(?:^|\b)(?:given\s*names?|forename|first\s*names?|pr[eé]noms?)\b", "given"),
        (r"(?:^|\b)(?:name\s+of\s+holder|holder'?s?\s+name)\b", "holder"),
    )
    for i, line in enumerate(lines):
        for pat, _kind in _LABEL_NEXT:
            if re.search(pat, line, re.IGNORECASE):
                for j in (i + 1, i + 2):
                    if j < len(lines):
                        candidate = lines[j].strip(" :.\t")
                        if _is_plausible_name(candidate):
                            cleaned = _sanitize_name_for_file(candidate)
                            if cleaned:
                                return cleaned
                m = re.search(
                    pat + r"\s*[：:\s]+\s*"
                    + r"([A-Z\uAC00-\uD7A3][A-Za-z\uAC00-\uD7A3'\-]+(?:\s+[A-Z\uAC00-\uD7A3][A-Za-z\uAC00-\uD7A3'\-]+){0,4})",
                    line,
                    re.IGNORECASE,
                )
                if m and _is_plausible_name(m.group(1)):
                    cleaned = _sanitize_name_for_file(m.group(1))
                    if cleaned:
                        return cleaned

    # Surname + given on separate labelled lines → "Given Surname"
    surname_val = ""
    given_val = ""
    for i, line in enumerate(lines):
        if re.search(r"(?:^|\b)(?:surname|nom|last\s*name)\b", line, re.IGNORECASE):
            m = re.search(
                r"(?:surname|nom|last\s*name)\s*[：:\s]+\s*"
                r"([A-Z][A-Z\s'\-]{1,40})",
                line,
                re.IGNORECASE,
            )
            if m:
                surname_val = m.group(1).strip()
            elif i + 1 < len(lines):
                surname_val = lines[i + 1].strip()
        if re.search(r"(?:^|\b)(?:given\s*names?|forename|first\s*names?)\b", line, re.IGNORECASE):
            m = re.search(
                r"(?:given\s*names?|forename|first\s*names?)\s*[：:\s]+\s*"
                r"([A-Z][A-Z\s'\-]{1,40})",
                line,
                re.IGNORECASE,
            )
            if m:
                given_val = m.group(1).strip()
            elif i + 1 < len(lines):
                given_val = lines[i + 1].strip()
    if surname_val and given_val:
        combined = f"{given_val} {surname_val}"
        if _is_plausible_name(combined):
            cleaned = _sanitize_name_for_file(combined)
            if cleaned:
                return cleaned

    # Machine-readable zone (passport bottom line): P<XXXSURNAME<<GIVEN<NAMES
    for line in lines:
        m = re.search(r"P<[A-Z]{3}([A-Z]+)<<([A-Z0-9<]+)", line.replace(" ", ""))
        if m:
            surname = m.group(1).replace("<", " ").strip()
            given = m.group(2).replace("<", " ").strip()
            combined = f"{given} {surname}".strip()
            if _is_plausible_name(combined):
                cleaned = _sanitize_name_for_file(combined)
                if cleaned:
                    return cleaned

    # ── Fallback: standalone capitalized line that looks like a name ─────
    # Must pass the strict plausibility check
    standalone_re = re.compile(
        r"^([A-Z\uAC00-\uD7A3][A-Za-z\uAC00-\uD7A3'\-]{1,}"
        r"(?:\s+[A-Z\uAC00-\uD7A3][A-Za-z\uAC00-\uD7A3'\-]{1,}){1,4})$"
    )
    for line in lines:
        m = standalone_re.match(line)
        if m:
            candidate = m.group(1)
            if _is_plausible_name(candidate):
                cleaned = _strip_name_label_prefix(_sanitize_name_for_file(candidate))
                if cleaned:
                    return cleaned

    return ""


def _sanitize_name_for_file(name: str) -> str:
    import re
    safe = re.sub(r'[\\/:*?"<>|]', "", name)
    safe = re.sub(r"\s+", " ", safe).strip()
    return safe[:80]


def _call_ocr_template_stem(template_fn, doc_type: str, page_text: str = "") -> str | None:
    """Invoke template resolver (supports optional page text for father/mother passport)."""
    if not template_fn:
        return None
    try:
        return template_fn(doc_type, page_text)
    except TypeError:
        try:
            return template_fn(doc_type)
        except Exception:
            return None
    except Exception:
        return None


def _is_ocr_garbage_name(candidate: str) -> bool:
    """True when OCR text is unlikely to be a real person name."""
    import re

    if not candidate or not candidate.strip():
        return True
    for word in candidate.split():
        if len(word) < 4:
            continue
        # Long consonant runs (e.g. SllAttSER → S-l-l)
        if re.search(r"(?i)[b-df-hj-np-tv-z]{3,}", word):
            return True
        # Chaotic mixed case (not McDonald-style)
        flips = sum(
            1 for i in range(len(word) - 1) if word[i].islower() != word[i + 1].islower()
        )
        if flips >= 3 and not re.match(
            r"^(?:Mc|Mac|O')?[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?$", word
        ):
            return True
        # Mostly uppercase but not an acronym (SllAttSER)
        uppers = sum(1 for c in word if c.isupper())
        if len(word) >= 5 and uppers >= len(word) * 0.6 and not word.isupper():
            return True
    return False


def _infer_doc_type_from_page_context(text: str) -> str:
    """Infer checklist type from prominent form headings on the page."""
    import re

    if not text:
        return ""
    u = re.sub(r"\s+", " ", text.upper())
    if "PERSONAL DATA" in u and "EMERGENCY CONTACT" in u:
        return "APPLICATION & OTHER FORMS"
    if "PERSONAL DATA" in u and "AND" in u:
        return "APPLICATION & OTHER FORMS"
    return ""


def _is_acceptable_rename_stem(stem: str, *, doc_type: str = "") -> bool:
    """Reject OCR garbage, form labels, and organisation names as filename stems."""
    import re

    if not stem or not str(stem).strip():
        return False
    if _is_form_section_header(stem):
        return False
    if _is_ocr_garbage_name(stem):
        return False
    u = stem.upper().strip()
    if re.search(
        r"^(?:FATHER|MOTHER)['\u2019]?\s*S?\s*NAME\b|^NAME\s*$|^NAMC\s*$",
        u,
        re.IGNORECASE,
    ):
        return False
    if re.search(r"\b(?:GIVEN\s+NAMES?|SURNAME|FORENAME)\b", u):
        return False
    ORG_MARKERS = (
        "PARISHAD", "UNION", "LIMITED", "PROVIDERS", "PROFESSOR", "PHD",
        "RECOMMENDATION", "LBTTER", "LETTER OF", "MARKET ACCESS", "COMPANY",
        "CORPORATION", "UNIVERSITY", "COLLEGE", "INSTITUTE", "MINISTRY",
        "DEPARTMENT", "SECRETARY", "DIRECTOR", "CHAIRMAN",
    )
    if any(m in u for m in ORG_MARKERS):
        return False
    # Bilingual checklist stems (contain Korean or hyphen EN pair) are always OK
    if re.search(r"[\uAC00-\uD7A3]", stem) or (
        "-" in stem and doc_type and len(stem) > 12
    ):
        return True
    # Known checklist English-only stems
    if doc_type and u == doc_type.upper():
        return True
    # Name-only stems (no checklist doc type) need a plausible person name
    if not doc_type:
        words = stem.split()
        if len(words) < 2:
            return False
        letters = [c for c in stem if c.isalpha()]
        if len(letters) >= 5:
            vowels = sum(1 for c in letters if c.lower() in "aeiou")
            if vowels / len(letters) < 0.22:
                return False
        return True
    # Latin person-name heuristic: reject very low vowel ratio (OCR garbage)
    letters = [c for c in stem if c.isalpha()]
    if len(letters) >= 5:
        vowels = sum(1 for c in letters if c.lower() in "aeiou")
        if vowels / len(letters) < 0.18:
            return False
    return True


def _is_form_section_header(text: str) -> bool:
    """True when *text* is a form section title, not a person or document-type name."""
    import re

    if not text or not str(text).strip():
        return False
    u = re.sub(r"\s+", " ", str(text).upper().strip())
    SECTION_PHRASES = (
        "PERSONAL DATA",
        "EMERGENCY CONTACT",
        "EMERGENCY CONTACTS",
        "NEXT OF KIN",
        "CONTACT INFORMATION",
        "FAMILY INFORMATION",
        "GUARDIAN INFORMATION",
        "PARENT INFORMATION",
        "DECLARATION",
        "ACKNOWLEDGEMENT",
        "ACKNOWLEDGMENT",
        "UNDERTAKING",
        "APPLICANT INFORMATION",
        "GENERAL INFORMATION",
        "SUPPLEMENTARY INFORMATION",
    )
    if any(p in u for p in SECTION_PHRASES):
        return True
    if re.search(
        r"\b(PERSONAL|EMERGENCY|CONTACT|INFORMATION|DECLARATION|GUARDIAN|ADDRESS)\b",
        u,
    ) and len(u.split()) >= 3:
        return True
    return False


def _detect_passport_in_text(text: str) -> bool:
    """Heuristics for passport pages (including forms where PASSPORT is not the largest line)."""
    import re

    if not text:
        return False
    t = text.upper()
    compact = re.sub(r"\s+", "", text)
    if re.search(r"\bPASSPORT\b", t):
        return True
    if re.search(r"여권", text):
        return True
    if re.search(r"P<[A-Z]{3}[A-Z<]{5,}", compact):
        return True
    if re.search(r"\bTYPE\s*[:\s]*P\b", t):
        return True
    if re.search(
        r"(?:passport\s+no\.?|passport\s+number|passport\s+#)\s*[:\s]*[A-Z]{0,2}\d{6,9}",
        text,
        re.IGNORECASE,
    ):
        return True
    has_surname = bool(re.search(r"\b(?:surname|nom|last\s*name)\b", t, re.IGNORECASE))
    has_given = bool(re.search(r"\b(?:given\s*names?|forename|first\s*names?)\b", t, re.IGNORECASE))
    has_dob = bool(re.search(r"\b(?:date\s+of\s+birth|d\.?o\.?b\.?|birth\s+date)\b", t, re.IGNORECASE))
    if has_surname and has_given and has_dob:
        return True
    if has_surname and has_given and re.search(r"\b[A-Z]{1,2}\d{6,9}\b", t):
        return True
    return False


# Final-checklist document types (most specific patterns first within each tier).
_CHECKLIST_DOC_RULES: list[tuple[str, str]] = [
    # 0 — Application & other forms
    (
        r"\b(?:admission\s+)?application\s+(?:form|package)\b|\bapplication\s+&\s+other\s+forms?\b"
        r"|\bother\s+forms?\b.*\bapplication\b",
        "APPLICATION & OTHER FORMS",
    ),
    # 1 — Photo 35×45 mm
    (
        r"\b35\s*(?:mm)?\s*[x×]\s*45\s*(?:mm)?\b|\b45\s*[x×]\s*35\s*(?:mm)?\b"
        r"|\bpassport[\-\s]?size\s+photo\b|\bwhite\s+background\b",
        "PHOTO 35X45",
    ),
    (r"\b(?:passport\s+)?(?:photo|photograph)\b|\bID\s+photo\b|\b증명사진\b", "PHOTO"),
    # 11 — SWIFT (before generic bank)
    (r"\bswift\b|\bMT\s*103\b|\bwire\s+transfer\s+copy\b", "SWIFT COPY"),
    # 8 — Sponsor / guarantor documents
    (r"\btrade\s+license\b|\bcommercial\s+license\b|\b사업자\s*등록", "TRADE LICENSE"),
    (
        r"\bemployment\s+certificate\b|\bjob\s+certificate\b|\bre-?employment\b|\b재직\s*증명",
        "EMPLOYMENT CERTIFICATE",
    ),
    (r"\btin\s+certificate\b|\btaxpayer\s+identification\b|\b납세자\s*등록", "TIN CERTIFICATE"),
    (
        r"\bincome\s+tax\s+certificate\b|\btax\s+certificate\b|\b소득금액\s*증명",
        "TAX CERTIFICATE",
    ),
    (
        r"\btax\s+return\s+acknowledg|\backnowledgement\s+certificate\b|\b소득금액\s*신고\s*확인",
        "ACKNOWLEDGEMENT CERTIFICATE",
    ),
    # 9 — Bank
    (r"\bbank\s+solvency\b|\bsolvency\s+certificate\b|\b잔고\s*증명", "BANK SOLVENCY"),
    (r"\bbank\s+statement\b|\baccount\s+statement\b|\b거래내역", "BANK STATEMENT"),
    # 4 — E-Apostille + academic bundle
    (
        r"\be-?apostille\b.*\b(?:certificate|transcript)\b|\bapostille\b.*\btranscript\b",
        "APOSTILLE TRANSCRIPT",
    ),
    (r"\bAPOSTILLE\b|\b아포스티유\b", "APOSTILLE"),
    (r"\bTRANSCRIPT\b|\bACADEMIC\s+RECORD\b|\bGRADE\s+REPORT\b|\b성적\s*증명", "TRANSCRIPT"),
    # 3 — Birth certificate
    (
        r"\b(?:self\s+)?birth\s+certificate\b|\bcertificate\s+of\s+birth\b|\b출생\s*증명",
        "BIRTH CERTIFICATE",
    ),
    # 6 — Family relationship
    (
        r"\bfamily\s+relationship\s+certificate\b|\b가족\s*관계\s*증명",
        "FAMILY RELATIONSHIP CERTIFICATE",
    ),
    # 7 — Language proficiency certificates (any standardised test)
    (
        r"\bIELTS\b|\b아이엘츠\b|\bTOEFL\b|\bTOPIK\b|\b토픽\b|\bJLPT\b"
        r"|\bPTE\b|\bDUOLINGO\b|\bCAMBRIDGE\b|\bTOEIC\b|\bSAT\b"
        r"|\blanguage\s+certificate\b|\b언어\s*능력\b",
        "LANGUAGE CERTIFICATE",
    ),
    # 5 — NID (parents before applicant)
    (
        r"\bparents?['\u2019]?\s*(?:NID|N\.?I\.?D\.?|national\s+id)\b|\bNID\b.*\bparents?\b",
        "PARENTS NID",
    ),
    (r"\bfather['\u2019]?s?\s+(?:NID|id)\b", "FATHER NID"),
    (r"\bmother['\u2019]?s?\s+(?:NID|id)\b", "MOTHER NID"),
    (
        r"\bNATIONAL\s+ID\b|\bNATIONAL\s+IDENTITY\b|\bIDENTITY\s+CARD\b|\bN\.?I\.?D\.?\b",
        "NID",
    ),
    # 10 — Medical
    (
        r"\bmedical\s+test\s+report\b|\bmedical\s+(?:examination|report)\b|\bTB\s+test\b"
        r"|\btuberculosis\b|\b결핵\s*검사",
        "MEDICAL TEST REPORT",
    ),
    # Other common embassy docs
    (r"\bMARRIAGE\s+CERTIFICATE\b", "MARRIAGE CERTIFICATE"),
    (r"\bPOLICE\s+CLEARANCE\b|\bCRIMINAL\s+RECORD\b", "POLICE CLEARANCE"),
    (r"\bDIPLOMA\b|\bDEGREE\s+CERTIFICATE\b", "DIPLOMA"),
    (r"\bOFFER\s+LETTER\b|\bADMISSION\s+LETTER\b", "ADMISSION LETTER"),
    (r"\bVISA\b|\bENTRY\s+PERMIT\b", "VISA"),
    (r"\bPASSPORT\b|\b여권\b", "PASSPORT"),
]


def get_checklist_document_labels() -> list[str]:
    """Standard document labels from the embassy final checklist (for UI reference)."""
    return [
        "APPLICATION & OTHER FORMS",
        "PHOTO 35X45",
        "PASSPORT",
        "BIRTH CERTIFICATE",
        "APOSTILLE TRANSCRIPT",
        "NID",
        "PARENTS NID",
        "FAMILY RELATIONSHIP CERTIFICATE",
        "LANGUAGE CERTIFICATE",
        "TRADE LICENSE",
        "EMPLOYMENT CERTIFICATE",
        "TIN CERTIFICATE",
        "TAX CERTIFICATE",
        "ACKNOWLEDGEMENT CERTIFICATE",
        "BANK STATEMENT",
        "BANK SOLVENCY",
        "MEDICAL TEST REPORT",
        "SWIFT COPY",
    ]


def _detect_document_type(text: str) -> str:
    """Detect document type from page text (embassy final-checklist labels)."""
    import re

    if _detect_passport_in_text(text):
        return "PASSPORT"

    compact_alpha = re.sub(r"[^A-Za-z]", "", text).upper()
    if "NATIONALID" in compact_alpha or (
        "NID" in compact_alpha and "CARD" in compact_alpha
    ):
        if re.search(r"\bMOTHER\b", text, re.IGNORECASE) or "어머니" in text:
            return "MOTHER NID"
        if re.search(r"\bFATHER\b", text, re.IGNORECASE) or "아버지" in text:
            return "FATHER NID"
        return "NID"

    t = text.upper()
    for pattern, label in _CHECKLIST_DOC_RULES:
        if re.search(pattern, t, re.IGNORECASE) or re.search(pattern, text, re.IGNORECASE):
            return label
    return ""


def _extract_id_number(text: str, doc_type: str) -> str:
    """Extract a document reference/ID number that suits the detected doc type."""
    import re

    if doc_type == "PASSPORT":
        # Label-first: "Passport No: AB1234567"
        m = re.search(
            r"(?:passport\s+no\.?|passport\s+number|no\.?)\s*[:\s]\s*([A-Z]{1,2}\d{6,9})",
            text, re.IGNORECASE,
        )
        if m:
            return m.group(1).upper()
        # Pattern-only: 1-2 uppercase letters + 6-9 digits  (e.g. ZX9876543, A1234567)
        m = re.search(r"\b([A-Z]{1,2}\d{6,9})\b", text)
        if m:
            return m.group(1)

    elif doc_type in ("NID", "NATIONAL ID", "PARENTS NID", "FATHER NID", "MOTHER NID"):
        # Label-first
        m = re.search(
            r"(?:nid|national\s+id(?:\s+no\.?)?|id\s+no\.?)\s*[:\s]\s*(\d{10,17})",
            text, re.IGNORECASE,
        )
        if m:
            return m.group(1)
        # Standalone long digit string (10-17 digits)
        m = re.search(r"\b(\d{10,17})\b", text)
        if m:
            return m.group(1)

    return ""


def _extract_form_data(doc) -> dict[str, str]:
    """
    Read all AcroForm widget values from an open fitz document.
    Returns {normalised_field_name: value} — keys are lowercased & stripped.
    Works even if the doc has no widgets (returns empty dict silently).
    """
    data: dict[str, str] = {}
    try:
        for page in doc:
            try:
                widgets = page.widgets()
            except Exception:
                continue
            if not widgets:
                continue
            for w in widgets:
                try:
                    name  = (w.field_name  or "").strip().lower()
                    value = (str(w.field_value) if w.field_value is not None else "").strip()
                    if name and value and value not in ("False", "Off", ""):
                        data[name] = value
                except Exception:
                    pass
    except Exception:
        pass
    return data


def _strip_name_label_prefix(name: str) -> str:
    """Remove accidental field labels captured as part of a name."""
    import re

    if not name:
        return ""
    cleaned = re.sub(
        r"^(?:given\s+names?|surname|family\s+name|last\s+name|forename|"
        r"first\s+names?|full\s+name|student\s+name|applicant\s+name|name)\s+",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned


def _compose_smart_stem(name: str, doc_type: str, id_num: str) -> str:
    """Build a filename stem from extracted name, document type, and optional ID."""
    import re

    name = _strip_name_label_prefix(_sanitize_name_for_file(name) if name else "")
    if name and _is_form_section_header(name):
        name = ""
    doc_type = _sanitize_name_for_file(doc_type) if doc_type else ""
    id_num = re.sub(r'[\\/:*?"<>|]', "", (id_num or "")).strip().upper()

    if doc_type.upper() == "PASSPORT" and name and id_num:
        return f"{name} + {id_num}"
    if doc_type and name:
        return f"{name} - {doc_type}"
    if doc_type:
        return doc_type
    if name:
        return name
    return ""


def _detect_doc_type_from_form_data(form_data: dict[str, str]) -> str:
    for k, v in form_data.items():
        if any(t in k for t in ("document type", "doc type", "document_type", "doc_type", "type")):
            normalised = _detect_document_type(v)
            if normalised:
                return normalised
    inferred = _detect_document_type(" ".join(form_data.keys()))
    if inferred:
        return inferred
    return _detect_document_type(" ".join(form_data.values()))


def _name_from_form_data(form_data: dict[str, str]) -> str:
    """Best-effort person name from AcroForm field keys/values."""
    import re

    combined_lines = "\n".join(f"{k}: {v}" for k, v in form_data.items())
    name = _extract_name_from_text(combined_lines)
    if name:
        return name

    def _field(keys: tuple[str, ...]) -> str:
        for k, v in form_data.items():
            if any(key in k for key in keys) and v.strip():
                return v.strip()
        return ""

    surname = _field(("surname", "last name", "family name", "lastname", "last_name"))
    given = _field(("given name", "first name", "forename", "givenname", "first_name"))
    if surname and given:
        candidate = f"{given} {surname}"
        if _extract_name_from_text(candidate) or len(candidate.split()) >= 2:
            return _sanitize_name_for_file(candidate)

    for k, v in form_data.items():
        if any(
            nk in k
            for nk in (
                "full name", "fullname", "student name", "applicant name",
                "name", "holder", "bearer", "student",
            )
        ) and "number" not in k and "no." not in k:
            cleaned = _sanitize_name_for_file(v)
            if cleaned and not re.search(r"\d", cleaned):
                return cleaned
    return ""


def _id_from_form_data(form_data: dict[str, str], doc_type: str) -> str:
    import re

    for k, v in form_data.items():
        if "passport" in k and any(x in k for x in ("no", "num", "number")):
            cleaned = re.sub(r'[\\/:*?"<>|]', "", v).strip().upper()
            if cleaned:
                return cleaned
    combined = "\n".join(form_data.values())
    return _extract_id_number(combined, doc_type or "PASSPORT")


def _form_data_to_stem(
    form_data: dict[str, str],
    log,
    *,
    template_stem_for_type=None,
) -> str:
    """Build a rename stem from PDF AcroForm fields (name + type + ID when available)."""
    if not form_data:
        return ""

    doc_type = _detect_doc_type_from_form_data(form_data)
    name = _name_from_form_data(form_data)
    id_num = _id_from_form_data(form_data, doc_type) if doc_type else ""
    combined = "\n".join(f"{k}: {v}" for k, v in form_data.items())
    stem = _finalize_ocr_stem(
        name,
        doc_type,
        id_num,
        log,
        0,
        template_stem_for_type=template_stem_for_type,
        page_text=combined,
    )
    if stem:
        if not (doc_type and template_stem_for_type):
            log(f"  Form fields: → {stem!r}")
        return stem
    log("  Form fields: no usable name or document type in form data")
    return ""


def _finalize_ocr_stem(
    name: str,
    doc_type: str,
    id_num: str,
    log,
    page_index: int,
    *,
    template_stem_for_type=None,
    page_text: str = "",
) -> str:
    """Pick bilingual checklist stem when possible, else name/type composite."""
    if doc_type and template_stem_for_type:
        template_stem = _call_ocr_template_stem(
            template_stem_for_type, doc_type, page_text
        )
        if template_stem:
            log(
                f"  Page {page_index + 1}: checklist document "
                f"({doc_type}) → {template_stem!r}"
            )
            return template_stem
    stem = _compose_smart_stem(name, doc_type, id_num)
    if stem and not _is_acceptable_rename_stem(stem, doc_type=doc_type):
        log(f"  Page {page_index + 1}: rejected low-quality stem {stem!r}")
        stem = ""
        if doc_type and template_stem_for_type:
            template_stem = _call_ocr_template_stem(
                template_stem_for_type, doc_type, page_text
            )
            if template_stem:
                log(
                    f"  Page {page_index + 1}: checklist fallback "
                    f"({doc_type}) → {template_stem!r}"
                )
                return template_stem
    if stem:
        log(f"  Page {page_index + 1}: → {stem!r}")
    else:
        log(f"  Page {page_index + 1}: no name or document type found")
    return stem


def _build_smart_ocr_stem(
    text: str,
    log,
    page_index: int,
    *,
    template_stem_for_type=None,
) -> str:
    """Build rename stem from page text (checklist doc type + optional bilingual template)."""
    if not text:
        return ""

    doc_type = _detect_document_type(text)
    if not doc_type:
        doc_type = _infer_doc_type_from_page_context(text)
        if doc_type:
            log(f"  Page {page_index + 1}: inferred document type {doc_type!r}")
    name = _extract_name_from_text(text)
    if name and _is_form_section_header(name):
        log(f"  Page {page_index + 1}: ignored form section header {name!r}")
        name = ""
    # Application forms: prefer checklist name, not OCR noise from the form body
    if doc_type == "APPLICATION & OTHER FORMS":
        name = ""
    id_num = _extract_id_number(text, doc_type) if doc_type else ""
    return _finalize_ocr_stem(
        name,
        doc_type,
        id_num,
        log,
        page_index,
        template_stem_for_type=template_stem_for_type,
        page_text=text,
    )


def _ocr_page_to_text(page, page_index: int, log) -> tuple[str, bool]:
    """Return (text, used_ocr). Logs when OCR is needed but unavailable."""
    raw_text = (page.get_text() or "").strip()
    if len(raw_text) >= 40:
        return raw_text, False
    if not configure_tesseract():
        if raw_text:
            log(
                f"  Page {page_index + 1}: little text in PDF ({len(raw_text)} chars); "
                "Tesseract not installed — cannot OCR scan"
            )
            return raw_text, False
        log(f"  Page {page_index + 1}: no text layer — Tesseract OCR not installed")
        return "", False
    try:
        import pytesseract
        from PIL import Image
        import io as _io

        try:
            import fitz as _fitz

            mat = _fitz.Matrix(2.0, 2.0)
        except Exception:
            mat = None
        pix = page.get_pixmap(matrix=mat, alpha=False) if mat else page.get_pixmap(alpha=False)
        img = Image.open(_io.BytesIO(pix.tobytes("png")))
        ocr_text = (pytesseract.image_to_string(img, lang="eng") or "").strip()
        if ocr_text:
            log(f"  Page {page_index + 1}: OCR read {len(ocr_text)} character(s)")
        return ocr_text, True
    except Exception as e:
        log(f"  Page {page_index + 1}: OCR failed: {e}")
        return raw_text, False


def _gpt4o_vision_extract_from_page(
    page, page_index: int, log, api_key: str
) -> tuple[str, str]:
    """Render page to PNG and call GPT-4o vision to get (doc_type, name).

    Both values may be empty strings on failure or when the model cannot determine them.
    """
    import base64 as _base64
    import io as _io
    import json as _json

    try:
        import openai as _openai
    except ImportError:
        log(
            f"  Page {page_index + 1}: GPT-4o — 'openai' package not installed. "
            "Run: pip install openai"
        )
        return "", ""
    if not hasattr(_openai, "OpenAI"):
        log(
            f"  Page {page_index + 1}: GPT-4o — openai package is too old. "
            "Run: pip install --upgrade openai"
        )
        return "", ""

    # ── Render page to PNG at 216 DPI — sharp enough for GPT-4o to read text ──
    # Matrix(3.0) on PyMuPDF's 72 DPI base ≈ 216 DPI.
    # A4 page renders to ~1785×2526 px which GPT-4o "high" detail tiles at full quality.
    # Output PDFs are NOT affected — we use insert_pdf() which keeps original vector content.
    try:
        try:
            import fitz as _fitz
            mat = _fitz.Matrix(3.0, 3.0)
        except Exception:
            mat = None
        pix = page.get_pixmap(matrix=mat, alpha=False) if mat else page.get_pixmap(alpha=False)
        img_bytes = pix.tobytes("png")
        b64 = _base64.b64encode(img_bytes).decode("ascii")
    except Exception as e:
        log(f"  Page {page_index + 1}: GPT-4o — render failed: {e}")
        return "", ""

    doc_types_list = (
        "PASSPORT, FATHER PASSPORT, MOTHER PASSPORT, "
        "NID, FATHER NID, MOTHER NID, PARENTS NID, "
        "BIRTH CERTIFICATE, "
        "BANK STATEMENT, BANK SOLVENCY, "
        "TOPIK CERTIFICATE, IELTS CERTIFICATE, LANGUAGE CERTIFICATE, "
        "TRADE LICENSE, "
        "EMPLOYMENT CERTIFICATE, "
        "TIN CERTIFICATE, TAX CERTIFICATE, ACKNOWLEDGEMENT CERTIFICATE, "
        "MEDICAL TEST REPORT, "
        "SWIFT COPY, "
        "FAMILY RELATIONSHIP CERTIFICATE, "
        "APOSTILLE, APOSTILLE TRANSCRIPT, TRANSCRIPT, "
        "PHOTO, PHOTO 35X45, "
        "APPLICATION & OTHER FORMS"
    )
    system_prompt = (
        "You are a document identification assistant for a student visa processing office. "
        "Documents are from Bangladesh applicants applying to Korean universities. "
        "Look at the page image and extract exactly two things:\n\n"
        "1. DOCUMENT TYPE — match to the CLOSEST type from this list:\n"
        f"   {doc_types_list}\n"
        "   Use 'OTHER' only if absolutely nothing matches.\n\n"
        "2. PERSON NAME — the applicant / student / document holder's full name "
        "(prefer Latin script). Return empty string if not found.\n\n"
        "Rules:\n"
        "- For TOPIK: BOTH the score report page (official score / 성적증명서) AND the grading criteria back page (평가 기준 / 한국어능력시험) are TOPIK CERTIFICATE.\n"
        "- For passports: use the name in the MRZ line or 'Surname / Given names' field.\n"
        "- For bank statements: the account holder name.\n"
        "- For certificates: the person the certificate is issued TO.\n"
        "- Do NOT return form labels (e.g. 'Name:', 'Full Name', 'Father', 'Mother') as the name.\n"
        "- Do NOT include titles (Mr., Ms., Dr.) in the name.\n"
        "- Return ONLY valid JSON with exactly two keys: "
        '{"doc_type": "...", "name": "..."}\n'
        "- No markdown, no explanation — just the JSON object."
    )

    try:
        client = _openai.OpenAI(api_key=api_key.strip())
        resp = _gpt4o_create(client,
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {
                            "type": "text",
                            "text": "Identify this document and extract the person's name.",
                        },
                    ],
                },
            ],
            temperature=0,
            max_tokens=120,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Strip markdown fences if model wraps in ```json ... ```
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = _json.loads(raw)
        doc_type = str(data.get("doc_type") or "").strip().upper()
        name = str(data.get("name") or "").strip()
        if doc_type == "OTHER":
            doc_type = ""
        log(
            f"  Page {page_index + 1}: GPT-4o vision → "
            f"doc_type={doc_type!r}  name={name!r}"
        )
        return doc_type, name
    except Exception as e:
        log(f"  Page {page_index + 1}: GPT-4o API call failed: {e}")
        return "", ""




def _gpt4o_vision_identify_from_template_list(
    page, page_index: int, log, api_key: str, template_pairs: list
) -> tuple[str, str]:
    """Render page at 216 DPI and ask GPT-4o to pick the EXACT bilingual template.

    template_pairs: list of (english_label, korean_label) from document_rename_merged_pairs().
    Returns (english_label, korean_label) — both empty on failure.
    GPT-4o receives the full list and returns the exact English label; no hardcoded type mapping.
    """
    import base64 as _base64

    try:
        import openai as _openai
    except ImportError:
        log(f"  Page {page_index + 1}: GPT-4o — 'openai' package not installed.")
        return "", ""
    if not hasattr(_openai, "OpenAI"):
        log(f"  Page {page_index + 1}: GPT-4o — openai package too old.")
        return "", ""

    # Render page at ~216 DPI (Matrix 3.0 on PyMuPDF 72 dpi base)
    try:
        try:
            import fitz as _fitz
            mat = _fitz.Matrix(3.0, 3.0)
        except Exception:
            mat = None
        pix = page.get_pixmap(matrix=mat, alpha=False) if mat else page.get_pixmap(alpha=False)
        img_bytes = pix.tobytes("png")
        b64 = _base64.b64encode(img_bytes).decode("ascii")
    except Exception as e:
        log(f"  Page {page_index + 1}: GPT-4o render failed: {e}")
        return "", ""

    # Build numbered option list from the full template
    options_lines = []
    for i, (en, ko) in enumerate(template_pairs, 1):
        label = en.strip() if en.strip() else ko.strip()
        if label:
            options_lines.append(f"{i}. {label}")
    options_str = "\n".join(options_lines)

    system_prompt = (
        "You are a document classifier for a Bangladesh student visa processing office.\n"
        "Documents are scans submitted by Bangladeshi students applying to Korean universities.\n\n"
        "Look at this page image and choose the SINGLE best match from the numbered list below.\n"
        "Return ONLY the exact English text of your chosen option — nothing else, no number, no explanation.\n\n"
        "DOCUMENT TYPE OPTIONS:\n"
        f"{options_str}\n\n"
        "CRITICAL RULES:\n"
        "NID / ID CARD RULES — two distinct page types exist; classify them differently:\n\n"
        "TYPE A — ORIGINAL NID CARD (Bangla text, photo, physical card scan):\n"
        "- The page shows the actual NID card image with Bengali script text and a photo.\n"
        "- \"applicant ID card\" = applicant\'s own Bangladeshi NID card. Name matches the student.\n"
        "- \"father ID card\" = father\'s NID card. Look for different male name/photo, or 'Father' label near card.\n"
        "- \"mother ID card\" = mother\'s NID card. Look for different female name/photo, or 'Mother' label near card.\n"
        "- A page can show ONE person\'s NID only. NEVER classify two different people\'s cards as one label.\n"
        "- If a page shows both applicant and parent NID cards side by side, pick the one occupying more space.\n\n"
        "TYPE B — NID ENGLISH TRANSLATION (typed English text, notary/court seal, no photo of card):\n"
        "- The page is an English-language typed translation of a Bangladeshi NID, certified by a notary or magistrate.\n"
        "- It contains fields like Name, Father\'s Name, Mother\'s Name, Date of Birth, Address in ENGLISH.\n"
        "- It usually has a certification stamp or seal at the bottom.\n"
        "- \"applicant NID translation\" = English translation of the APPLICANT\'S NID.\n"
        "- \"father NID translation\" = English translation of the FATHER\'S NID.\n"
        "- \"mother NID translation\" = English translation of the MOTHER\'S NID.\n"
        "- To decide WHOSE translation: check whose name appears as the main holder (not as father/mother of someone else).\n"
        "- NEVER label a translation page as \"applicant ID card\", \"father ID card\", or \"mother ID card\" — those are for the ORIGINAL card only.\n\n"
        "PASSPORT RULES:\n"
        "- \"passport\" = the APPLICANT\'S OWN passport only\n"
        "- \"father passport\" = the FATHER\'S passport (different name / photo than applicant)\n"
        "- \"mother passport\" = the MOTHER\'S passport\n\n"
        "LANGUAGE CERTIFICATE RULES:\n"
        "- \"TOPIK\" = Korean language test — BOTH the score report page AND the grading criteria back page (평가 기준 table) are TOPIK\n"
        "- \"IELTS\" = IELTS English language certificate\n"
        "- Also matches for TOEFL, PTE, Duolingo, JLPT, SAT, or any other standardised language/proficiency test\n\n"
        "ACADEMIC DOCUMENT RULES:\n"
        "- SSC = Secondary School Certificate (Bangladesh 10th grade / O-level equivalent)\n"
        "- HSC = Higher Secondary Certificate (Bangladesh 12th grade / A-level equivalent)\n"
        "- Bachelor = 4-year university degree (B.Sc, B.A, B.Com, B.Eng, etc.)\n"
        "- If the doc has an APOSTILLE stamp / sticker → choose the '... with apostille' variant\n"
        "- If no apostille → choose the '... (no apostille)' variant\n"
        "- Choose SSC, HSC, or Bachelor based on the education level stated on the document\n\n"
        "CERTIFIED TRANSLATION RULES:\n"
        "A certified/notarised English translation is a typed English-language page with a notary seal/stamp that translates a Bangla original document.\n"
        "ALWAYS choose the '... translation' variant from the list for such pages — NEVER label a translation page as the original document.\n"
        "- \"applicant birth certificate translation\" = certified English translation of the applicant's birth certificate\n"
        "- \"birth certificate translation\" = certified English translation of a birth certificate (non-applicant)\n"
        "- \"father death certificate translation\" = certified English translation of father's death certificate\n"
        "- \"mother death certificate translation\" = certified English translation of mother's death certificate\n"
        "- \"death certificate translation\" = certified English translation of any other death certificate\n"
        "- \"family relationship certificate translation\" = certified English translation of the family relationship certificate\n"
        "- \"marriage certificate translation\" = certified English translation of a marriage certificate\n"
        "- For NID/ID cards: see NID rules above — use 'applicant/father/mother NID translation' for those.\n"
        "Key indicators a page is a TRANSLATION (not the original): typed English text, certified/notarised stamp, fields like 'Name:', 'Father:', 'Mother:', 'Date of Birth:', 'Address:' in English.\n"
        "Key indicators a page is the ORIGINAL: Bangla/Bengali script as primary text, official government seal/logo, printed form layout.\n\n"
        "OTHER RULES:\n"
        "- \"application registration number\" / \"수험표\" = exam admission slip / registration number card for a Korean university entrance exam\n"
        "- \"study plan\" / \"학업계획서\" = student\'s written study/research plan document\n"
        "- \"bank statement\" = bank transaction history / statement of account\n"
        "- \"bank balance proof\" or \"bank solvency certificate\" = balance certificate / solvency letter\n"
        "- \"sponsor business registration tin income tax certificate and tax payment receipt\" = combined document with 사업자등록증 + 납세자등록증명서 + 소득금액증명서 + 소득신고 여수증\n"
        "- If NOTHING in the list matches: return exactly the word UNKNOWN\n"
    )

    try:
        client = _openai.OpenAI(api_key=api_key.strip())
        resp = _gpt4o_create(client,
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {
                            "type": "text",
                            "text": "Which document type is this? Return only the exact English label from the list.",
                        },
                    ],
                },
            ],
            temperature=0,
            max_tokens=60,
        )
        result = (resp.choices[0].message.content or "").strip().strip("\"'")

        if result.upper() == "UNKNOWN" or not result:
            log(f"  Page {page_index + 1}: GPT-4o → UNKNOWN — trying free-form KR-EN fallback…")
            return _gpt4o_vision_describe_and_translate(
                b64, page_index, log, client
            )

        # Exact match first (case-insensitive)
        for en, ko in template_pairs:
            if en.strip().lower() == result.lower():
                log(f"  Page {page_index + 1}: GPT-4o → {en.strip()!r} / {ko.strip()!r}")
                return en.strip(), ko.strip()

        # Partial / fuzzy fallback
        result_lower = result.lower()
        for en, ko in template_pairs:
            en_lower = en.strip().lower()
            if en_lower and (en_lower in result_lower or result_lower in en_lower):
                log(f"  Page {page_index + 1}: GPT-4o → {en.strip()!r} / {ko.strip()!r} (fuzzy)")
                return en.strip(), ko.strip()

        # Template list returned something but it didn't match — treat as new doc
        log(f"  Page {page_index + 1}: GPT-4o returned {result!r} — not in template, trying KR-EN fallback…")
        return _gpt4o_vision_describe_and_translate(
            b64, page_index, log, client
        )

    except Exception as e:
        log(f"  Page {page_index + 1}: GPT-4o API error: {e}")
        return "", ""


def _gpt4o_vision_describe_and_translate(
    b64: str, page_index: int, log, client
) -> tuple[str, str]:
    """Fallback for new/unknown docs: GPT-4o describes in English AND Korean in one call.

    Returns (english_description, korean_description) as a KR-EN filename stem pair.
    Used when the doc is not in the template list.
    """
    import json as _json

    system_prompt = (
        "You are a bilingual (English / Korean) document naming assistant.\n"
        "This document page was NOT found in the known template list.\n\n"
        "Look at the page and provide a SHORT document name (3-6 words) in BOTH languages.\n\n"
        "Rules:\n"
        "- English: concise document type name, all lowercase (e.g. 'tax payment receipt', 'police clearance certificate')\n"
        "- Korean: accurate translation of that name (e.g. '납세 영수증', '경찰 범죄경력조회서')\n"
        "- Do NOT use long descriptions — just the document type name\n"
        "- Return ONLY valid JSON: {\"en\": \"...\", \"ko\": \"...\"}\n"
        "- No markdown, no explanation — just the JSON object"
    )
    try:
        resp = _gpt4o_create(client,
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {
                            "type": "text",
                            "text": "What is this document? Give a short name in English and Korean.",
                        },
                    ],
                },
            ],
            temperature=0,
            max_tokens=80,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = _json.loads(raw)
        en = str(data.get("en") or "").strip().lower()
        ko = str(data.get("ko") or "").strip()
        if en or ko:
            log(f"  Page {page_index + 1}: GPT-4o fallback → {en!r} / {ko!r} (new doc type)")
            return en, ko
        return "", ""
    except Exception as e:
        log(f"  Page {page_index + 1}: GPT-4o fallback error: {e}")
        return "", ""

def _ocr_extract_smart_stem_from_page(
    page,
    page_index: int,
    log,
    *,
    template_stem_for_type=None,
    gpt4o_api_key: str = "",
) -> str:
    """Build rename stem. Uses GPT-4o vision first when api key provided; falls back to text+Tesseract."""
    # ── GPT-4o vision path (primary when API key available) ──────────────────
    if gpt4o_api_key:
        doc_type, name = _gpt4o_vision_extract_from_page(
            page, page_index, log, gpt4o_api_key
        )
        if doc_type or name:
            stem = _finalize_ocr_stem(
                name,
                doc_type,
                "",
                log,
                page_index,
                template_stem_for_type=template_stem_for_type,
                page_text="",
            )
            if stem:
                return stem
        # GPT-4o returned nothing useful — fall through to text/Tesseract
        log(
            f"  Page {page_index + 1}: GPT-4o returned nothing — "
            "falling back to text/Tesseract extraction"
        )

    # ── 3-tier fallback: text layer → Tesseract → regex ──────────────────────
    text, _used_ocr = _ocr_page_to_text(page, page_index, log)
    stem = _build_smart_ocr_stem(
        text, log, page_index, template_stem_for_type=template_stem_for_type
    )
    if stem:
        return stem
    if text:
        preview = " ".join(text.split())[:120]
        log(f"  Page {page_index + 1}: no name/type matched; text starts: {preview!r}…")
    else:
        log(f"  Page {page_index + 1}: no readable text")
    return ""


def _is_fallback_ocr_stem(stem: str, fallback_prefix: str) -> bool:
    import re

    return bool(re.match(rf"^{re.escape(fallback_prefix)}_\d{{3}}$", stem))


def _pdf_collect_page_stems(
    doc,
    log,
    *,
    fallback_prefix: str = "page",
    progress_callback=None,
    progress_offset: int = 0,
    progress_total: int | None = None,
) -> list[str]:
    """OCR each page; return one filename stem per page (fallback if no name found)."""
    total = len(doc)
    if progress_total is None:
        progress_total = total
    raw_stems: list[str] = []
    for i in range(total):
        page = doc.load_page(i)
        if progress_callback:
            try:
                progress_callback(progress_offset + i, progress_total)
            except Exception:
                pass
        stem = _ocr_extract_name_from_page(page, i, log)
        if not stem:
            stem = f"{fallback_prefix}_{i + 1:03d}"
        raw_stems.append(stem)
    return raw_stems


def _apply_korean_to_stems(
    stems: list[str],
    *,
    fallback_prefix: str,
    korean_api_key: str,
    log,
) -> list[str]:
    if not (korean_api_key and korean_api_key.strip()):
        return stems
    names_to_translate = [
        s for s in stems if not _is_fallback_ocr_stem(s, fallback_prefix)
    ]
    if not names_to_translate:
        return stems
    log(f"Korean translate: sending {len(names_to_translate)} name(s) to OpenAI…")
    translation_map = translate_to_korean_openai(
        list(dict.fromkeys(names_to_translate)),
        korean_api_key,
        log,
    )
    return [translation_map.get(s, s) for s in stems]


def _disambiguate_stem(stem: str, used_names: dict[str, int]) -> str:
    base_stem = stem
    if stem in used_names:
        used_names[stem] += 1
        return f"{base_stem} ({used_names[base_stem]})"
    used_names[stem] = 1
    return stem


def _safe_out_path(out_dir: str, stem: str, ext: str = ".pdf", max_total: int = 240) -> str:
    """Return os.path.join(out_dir, stem+ext), shortening stem if the full path
    would exceed max_total chars (Windows MAX_PATH default is 260)."""
    candidate = os.path.join(out_dir, stem + ext)
    if len(candidate) <= max_total:
        return candidate
    # How many chars can the stem use?
    overhead = len(out_dir) + 1 + len(ext)   # dir + sep + extension
    allowed  = max(10, max_total - overhead)   # keep at least 10 chars for stem
    short_stem = stem[:allowed]
    return os.path.join(out_dir, short_stem + ext)


def _resolve_whole_pdf_rename_stem(
    stems: list[str],
    *,
    fallback_prefix: str,
    log,
    basename: str,
) -> tuple[bool, str]:
    """Pick one filename stem when renaming a PDF without splitting."""
    if not stems:
        return False, ""
    stems = [s for s in stems if s and str(s).strip()]
    if not stems:
        return False, ""
    if len(stems) == 1:
        return True, stems[0]
    non_fallback = [s for s in stems if not _is_fallback_ocr_stem(s, fallback_prefix)]
    if not non_fallback:
        return True, stems[0]
    unique_names = list(dict.fromkeys(non_fallback))
    if len(unique_names) == 1:
        log(f"  {basename}: same name on all pages → {unique_names[0]!r}")
        return True, unique_names[0]
    preview = ", ".join(unique_names[:5])
    if len(unique_names) > 5:
        preview += "…"
    log(
        f"  {basename}: different names on different pages ({preview}) — "
        "use Split & Rename for one file per student"
    )
    return False, ""


def _next_pdf_path_in_dir(
    directory: str,
    stem: str,
    *,
    exclude_abs: str | None = None,
) -> str:
    """Return an unused ``{stem}.pdf`` path in ``directory`` (adds ``(2)`` suffix if needed)."""
    directory = os.path.abspath(directory)
    exclude_nc = os.path.normcase(os.path.abspath(exclude_abs)) if exclude_abs else None

    def _free(path: str) -> bool:
        if exclude_nc and os.path.normcase(os.path.abspath(path)) == exclude_nc:
            return True
        return not os.path.exists(path)

    candidate = os.path.join(directory, f"{stem}.pdf")
    if _free(candidate):
        return candidate
    n = 2
    while True:
        candidate = os.path.join(directory, f"{stem} ({n}).pdf")
        if _free(candidate):
            return candidate
        n += 1


def _rename_pdf_in_place(src: str, stem: str, log) -> tuple[bool, str, str]:
    """Rename a PDF on disk. Returns ``(ok, new_path, err_code)`` — err_code is ``locked`` or ````."""
    import errno
    import time

    src = os.path.abspath(src)
    dst = _next_pdf_path_in_dir(os.path.dirname(src), stem, exclude_abs=src)
    if os.path.normcase(dst) == os.path.normcase(src):
        log(f"  {os.path.basename(src)}: already named correctly")
        return True, src, ""

    invalidate_pdf_doc_cache(src)
    last_err: OSError | None = None
    for attempt in range(5):
        try:
            os.replace(src, dst)
            log(f"  {os.path.basename(src)} → {os.path.basename(dst)}")
            return True, dst, ""
        except OSError as e:
            last_err = e
            locked = getattr(e, "winerror", None) == 32 or e.errno in (
                errno.EACCES,
                errno.EPERM,
                13,
                32,
            )
            if locked and attempt < 4:
                invalidate_pdf_doc_cache(src)
                time.sleep(0.12 * (attempt + 1))
                continue
            break
    if last_err is not None:
        log(f"  {os.path.basename(src)}: rename failed: {last_err}")
        if getattr(last_err, "winerror", None) == 32:
            log(
                f"  {os.path.basename(src)}: file is open in preview or another app — "
                "close it and try again"
            )
            return False, "", "locked"
    return False, "", "other"


def pdf_rename_files_by_ocr(
    src_paths: list[str],
    log,
    *,
    progress_callback=None,
    korean_api_key: str = "",
) -> tuple[bool, list[str]]:
    """Rename PDF file(s) in place using the student/person name found via OCR (no split).

    Single-page PDFs use that page's name. Multi-page PDFs are renamed only when every
    page resolves to the same name; otherwise the file is skipped (use split+rename).
    """
    fitz = get_fitz()
    if not fitz:
        log("PDF rename: install PyMuPDF (pip install pymupdf).")
        return False, []
    paths = [os.path.abspath(p) for p in src_paths if p and os.path.isfile(p)]
    if not paths:
        log("PDF rename: no files selected.")
        return False, []

    total_pages = 0
    page_counts: list[int] = []
    for p in paths:
        try:
            doc = fitz.open(p)
            n = len(doc)
            doc.close()
        except Exception:
            n = 0
        page_counts.append(n)
        total_pages += max(n, 0)

    log(f"PDF rename by OCR: {len(paths)} file(s), {total_pages} page(s) total")
    out_paths: list[str] = []
    any_ok = False
    page_done = 0

    try:
        for path, n_pages in zip(paths, page_counts):
            basename = os.path.basename(path)
            fallback_prefix = os.path.splitext(basename)[0] or "document"
            if n_pages < 1:
                log(f"  {basename}: could not read pages — skipped")
                continue
            doc = fitz.open(path)
            stems = _pdf_collect_page_stems(
                doc,
                log,
                fallback_prefix=fallback_prefix,
                progress_callback=progress_callback,
                progress_offset=page_done,
                progress_total=total_pages or n_pages,
            )
            doc.close()
            page_done += n_pages
            stems = _apply_korean_to_stems(
                stems,
                fallback_prefix=fallback_prefix,
                korean_api_key=korean_api_key,
                log=log,
            )
            ok_stem, stem = _resolve_whole_pdf_rename_stem(
                stems,
                fallback_prefix=fallback_prefix,
                log=log,
                basename=basename,
            )
            if not ok_stem or not stem:
                continue
            ok, new_path, err = _rename_pdf_in_place(path, stem, log)
            if ok and new_path:
                any_ok = True
                out_paths.append(new_path)
            elif err == "locked":
                pass  # legacy rename-by-name API — no info dict

        if progress_callback and total_pages:
            try:
                progress_callback(total_pages, total_pages)
            except Exception:
                pass
        if any_ok:
            log(f"Rename by OCR complete: {len(out_paths)} file(s) renamed")
            return True, out_paths
        log("Rename by OCR: no files were renamed")
        return False, []
    except Exception as e:
        log(f"PDF rename by OCR failed: {e}")
        return False, out_paths


def pdf_rename_files_by_ocr_smart(
    src_paths: list[str],
    log,
    *,
    progress_callback=None,
    korean_api_key: str = "",
    gpt4o_api_key: str = "",
    template_stem_for_type=None,
) -> tuple[bool, list[str], dict]:
    """Rename PDF(s) in place using form fields, text layer, and Tesseract OCR.

    When *template_stem_for_type* is provided (e.g. bilingual checklist names from the UI),
    recognised document types rename to those stems (``여권-passport``, etc.).
    Returns ``(ok, renamed_paths, info)`` where *info* may include ``tesseract_missing``.
    """
    log = _terminal_log_wrap(log)
    fitz = get_fitz()
    info: dict = {
        "tesseract_missing": False,
        "needed_ocr": False,
        "file_locked": False,
    }
    if not fitz:
        log("PDF rename (OCR): install PyMuPDF (pip install pymupdf).")
        return False, [], info
    paths = [os.path.abspath(p) for p in src_paths if p and os.path.isfile(p)]
    if not paths:
        log("PDF rename (OCR): no files selected.")
        return False, [], info

    tess_ok, tess_msg = tesseract_status()
    log(f"PDF rename (OCR): {tess_msg}")
    if not tess_ok:
        info["tesseract_missing"] = True

    total_pages = 0
    page_counts: list[int] = []
    for p in paths:
        try:
            doc = fitz.open(p)
            n = len(doc)
            doc.close()
        except Exception:
            n = 0
        page_counts.append(n)
        total_pages += max(n, 0)

    log(f"PDF rename (OCR smart): {len(paths)} file(s), {total_pages} page(s) total")
    out_paths: list[str] = []
    any_ok = False
    page_done = 0

    try:
        for path, n_pages in zip(paths, page_counts):
            basename = os.path.basename(path)
            fallback_prefix = os.path.splitext(basename)[0] or "document"
            if n_pages < 1:
                log(f"  {basename}: could not read pages — skipped")
                continue

            doc = fitz.open(path)

            # ── Priority 1: PDF AcroForm fields (most accurate, no OCR needed) ──
            form_data = _extract_form_data(doc)
            if form_data:
                log(f"  {basename}: found {len(form_data)} form field(s) — using structured data")
                form_stem = _form_data_to_stem(
                    form_data,
                    log,
                    template_stem_for_type=template_stem_for_type,
                )
                if form_stem:
                    doc.close()
                    invalidate_pdf_doc_cache(path)
                    ok, new_path, err = _rename_pdf_in_place(path, form_stem, log)
                    if ok and new_path:
                        any_ok = True
                        out_paths.append(new_path)
                    elif err == "locked":
                        info["file_locked"] = True
                    page_done += n_pages
                    if progress_callback:
                        try:
                            progress_callback(page_done, total_pages or n_pages)
                        except Exception:
                            pass
                    continue   # skip OCR entirely for this file
                else:
                    log(f"  {basename}: form fields present but no usable name/type — falling back to OCR")

            # ── Priority 2 & 3: text layer + pytesseract OCR (per page) ────────
            # Collect doc-type stems per page (empty string = unidentified page)
            raw_stems: list[str] = []
            total_prog = total_pages or n_pages
            for i in range(n_pages):
                page = doc.load_page(i)
                if progress_callback:
                    try:
                        progress_callback(page_done + i, total_prog)
                    except Exception:
                        pass
                raw_stems.append(
                    _ocr_extract_smart_stem_from_page(
                        page,
                        i,
                        log,
                        template_stem_for_type=template_stem_for_type,
                        gpt4o_api_key=gpt4o_api_key,
                    )
                )
            doc.close()
            page_done += n_pages

            if not any(s and str(s).strip() for s in raw_stems):
                # Detect if we likely needed OCR (no/minimal text layer)
                try:
                    doc2 = fitz.open(path)
                    t0 = (doc2.load_page(0).get_text() or "").strip()
                    doc2.close()
                    if len(t0) < 40:
                        info["needed_ocr"] = True
                except Exception:
                    pass
                log(f"  {basename}: no name or document type found — skipped")
                continue

            raw_stems = _apply_korean_to_stems(
                raw_stems,
                fallback_prefix=fallback_prefix,
                korean_api_key=korean_api_key,
                log=log,
            )
            ok_stem, stem = _resolve_whole_pdf_rename_stem(
                raw_stems,
                fallback_prefix=fallback_prefix,
                log=log,
                basename=basename,
            )
            if not ok_stem or not stem:
                continue

            invalidate_pdf_doc_cache(path)
            ok, new_path, err = _rename_pdf_in_place(path, stem, log)
            if ok and new_path:
                any_ok = True
                out_paths.append(new_path)
            elif err == "locked":
                info["file_locked"] = True

        if progress_callback and total_pages:
            try:
                progress_callback(total_pages, total_pages)
            except Exception:
                pass
        log(
            f"Rename (OCR) complete: {len(out_paths)} renamed, "
            f"{len(paths) - len(out_paths)} skipped"
        )
        return True, out_paths, info
    except Exception as e:
        log(f"PDF rename (OCR) failed: {e}")
        return False, out_paths, info


def pdf_split_and_rename_by_ocr(
    src: str,
    out_dir: str,
    log,
    *,
    fallback_prefix: str = "page",
    progress_callback=None,
    korean_api_key: str = "",
    gpt4o_api_key: str = "",
    template_stem_for_type=None,
) -> tuple[bool, list[str]]:
    """Split a multi-page PDF — one file per page with checklist / OCR naming.

    Uses the same smart OCR + bilingual checklist templates as Rename (OCR).
    Unrecognised pages are saved as ``{fallback}_001.pdf``, etc.
    """
    fitz = get_fitz()
    if not fitz:
        log("PDF split+rename: install PyMuPDF (pip install pymupdf).")
        return False, []
    src = os.path.abspath(src)
    os.makedirs(out_dir, exist_ok=True)
    out_paths: list[str] = []
    try:
        doc = fitz.open(src)
        total = len(doc)
        if total < 1:
            log("PDF has no pages.")
            doc.close()
            return False, []
        log(f"PDF split+rename: {total} page(s) in {os.path.basename(src)}")
        invalidate_pdf_doc_cache(src)

        raw_stems: list[str] = []
        for i in range(total):
            page = doc.load_page(i)
            if progress_callback:
                try:
                    progress_callback(i + 1, total)
                except Exception:
                    pass
            stem = _ocr_extract_smart_stem_from_page(
                page,
                i,
                log,
                template_stem_for_type=template_stem_for_type,
                gpt4o_api_key=korean_api_key,
            )
            if not stem or not _is_acceptable_rename_stem(stem):
                stem = f"{fallback_prefix}_{i + 1:03d}"
                log(f"  Page {i + 1}: no checklist match — {stem}")
            raw_stems.append(stem)

        raw_stems = _apply_korean_to_stems(
            raw_stems,
            fallback_prefix=fallback_prefix,
            korean_api_key=korean_api_key,
            log=log,
        )

        used_names: dict[str, int] = {}
        for i, stem in enumerate(raw_stems):
            stem = _disambiguate_stem(stem, used_names)
            out_path = _safe_out_path(out_dir, stem)
            saved_name = os.path.basename(out_path)
            page = doc.load_page(i)
            single = fitz.open()
            single.insert_pdf(doc, from_page=i, to_page=i)
            single.save(out_path)
            single.close()
            out_paths.append(out_path)
            log(f"  → {saved_name}")

        doc.close()
        log(f"Split+rename complete: {len(out_paths)} file(s) in {out_dir}")
        if progress_callback:
            try:
                progress_callback(total, total)
            except Exception:
                pass
        return True, out_paths
    except Exception as e:
        log(f"PDF split+rename failed: {e}")
        return False, []


# Document types that always get bundled into a single PDF together
# (business/financial package submitted as one group)
_BUSINESS_BUNDLE_TYPES: frozenset[str] = frozenset({
    "TRADE LICENSE",
    "TIN CERTIFICATE",
    "TAX CERTIFICATE",
    "ACKNOWLEDGEMENT CERTIFICATE",
    "EMPLOYMENT CERTIFICATE",
})

# Document types where consecutive pages are always the same document
# (e.g. NID front + NID back, or passport info page + photo page)
_MULTI_PAGE_DOC_TYPES: frozenset[str] = frozenset({
    "NID",
    "FATHER NID",
    "MOTHER NID",
    "PARENTS NID",
    "PASSPORT",
    "FATHER PASSPORT",
    "MOTHER PASSPORT",
    "BIRTH CERTIFICATE",
    "FAMILY RELATIONSHIP CERTIFICATE",
    "APOSTILLE",
    "APOSTILLE TRANSCRIPT",
    "TRANSCRIPT",
    "BANK STATEMENT",
    "BANK SOLVENCY",
    "MEDICAL TEST REPORT",
    "TOPIK CERTIFICATE",
    "IELTS CERTIFICATE",
    "LANGUAGE CERTIFICATE",
    "SSC CERTIFICATE",
    "SSC TRANSCRIPT",
    "HSC CERTIFICATE",
    "HSC TRANSCRIPT",
    "BACHELOR CERTIFICATE",
    "BACHELOR TRANSCRIPT",
})


def pdf_smart_split_merge_rename(
    src: str,
    out_dir: str,
    log,
    *,
    fallback_prefix: str = "page",
    progress_callback=None,
    korean_api_key: str = "",
    gpt4o_api_key: str = "",
    template_stem_for_type=None,
    template_pairs=None,
) -> tuple[bool, list[str]]:
    """GPT-4o identifies each page from the full bilingual template list, then smart-groups and merges.

    When template_pairs is provided (list of (english, korean) from document_rename_merged_pairs()):
    - GPT-4o receives the ENTIRE template list and returns the EXACT match — no hardcoded mapping.
    - Output filename = Korean-English stem built directly from the matched template pair.
    - ALL consecutive same-label pages are merged into one PDF.
    - Business bundle types (trade license, TIN, tax, acknowledgement, employment) are
      merged into a single PDF regardless of inter-page ordering.
    - No separate Korean translation API call needed — KR stem comes from template_pairs.

    Falls back to legacy mode (hardcoded type matching + optional Korean API) when
    template_pairs is None.
    """
    fitz = get_fitz()
    if not fitz:
        log("Smart split/merge: install PyMuPDF (pip install pymupdf).")
        return False, []
    if not gpt4o_api_key:
        log("Smart split/merge: OpenAI API key required for GPT-4o vision.")
        return False, []

    src = os.path.abspath(src)
    os.makedirs(out_dir, exist_ok=True)

    # Business bundle: adjacent pages with these English-label keywords are merged together
    _BUNDLE_KEYWORDS = (
        # Trade license and its variants
        "trade license", "business registration",
        # TIN
        "tin certificate",
        # Tax / income tax
        "tax certificate",
        # Acknowledgement — template label is "tax return acknowledgment" not "acknowledgement certificate"
        "tax return acknowledgment", "acknowledgement certificate",
        # Employment
        "employment certificate",
    )

    def _is_bundle_label(en_label: str) -> bool:
        el = en_label.strip().lower()
        return any(kw in el for kw in _BUNDLE_KEYWORDS)

    # Academic level keyword detection: SSC / HSC / Bachelor
    # All docs at the same level (certificate, transcript, apostille, no apostille) merge into one PDF.
    _ACADEMIC_LEVEL_KEYWORDS: dict[str, tuple[str, ...]] = {
        "ssc": ("ssc", "secondary school", "middle school", "o level", "o-level"),
        "hsc": ("hsc", "higher secondary", "high school", "a level", "a-level"),
        "bachelor": ("bachelor", "undergraduate", "b.sc", "bsc", "b.a.", "b.eng", "degree certificate"),
    }

    def _get_academic_level(en_label: str) -> str | None:
        el = en_label.strip().lower()
        for level, kws in _ACADEMIC_LEVEL_KEYWORDS.items():
            if any(kw in el for kw in kws):
                return level
        return None

    def _make_stem_from_pair(en: str, ko: str, page_idx: int) -> str:
        """KR-EN stem from template pair, sanitised for Windows filenames."""
        if not en and not ko:
            return f"{fallback_prefix}_{page_idx + 1:03d}"
        _INVALID = set('\\\/:"*?<>|')
        parts: list[str] = []
        ko_b, en_b = ko.strip(), en.strip()
        if ko_b:
            parts.append(ko_b)
        if en_b and en_b != ko_b:
            parts.append(en_b)
        raw = "-".join(parts) if parts else ""
        return "".join(c if c not in _INVALID else "_" for c in raw)

    try:
        doc = fitz.open(src)
        total = len(doc)
        if total < 1:
            log("Smart split/merge: PDF has no pages.")
            doc.close()
            return False, []

        log(f"Smart split/merge: {total} page(s) — identifying each page with GPT-4o…")
        invalidate_pdf_doc_cache(src)

        use_template_mode = bool(template_pairs)

        # ── Phase 1: GPT-4o identifies every page ─────────────────────────────
        # page_labels: list of (en_label, ko_label) — both "" if unknown
        page_labels: list[tuple[str, str]] = []
        for i in range(total):
            page = doc.load_page(i)
            if progress_callback:
                try:
                    progress_callback(i, total * 2)
                except Exception:
                    pass

            if use_template_mode:
                en, ko = _gpt4o_vision_identify_from_template_list(
                    page, i, log, gpt4o_api_key, template_pairs
                )
            else:
                doc_type, _ = _gpt4o_vision_extract_from_page(page, i, log, gpt4o_api_key)
                en = (doc_type or "").strip().upper()
                ko = ""

            page_labels.append((en, ko))

        log(f"  Labels: {[en for en, _ko in page_labels]}")

        # ── Fail-fast: if GPT-4o returned blank for EVERY page, the API is not
        #    working (bad key, network, rate-limit).  Abort clearly instead of
        #    silently merging everything into one useless file. ─────────────────
        if all(en == "" and ko == "" for en, ko in page_labels):
            log(
                "  ERROR: GPT-4o returned no label for any page. "
                "Check your OpenAI API key and internet connection. "
                "Make sure .env is next to the EXE with OPENAI_API_KEY=sk-..."
            )
            doc.close()
            return False, []

        # ── Phase 2: Group ALL consecutive same-label pages ────────────────────
        # Replaces the old _MULTI_PAGE_DOC_TYPES whitelist — GPT-4o output is trusted directly.
        # EXCEPTION: NID original card labels are NOT merged consecutively.
        # Reason: if GPT-4o mislabels a father/mother card as "applicant id card",
        # Phase 2 would bundle it with the applicant's card before Phase 3c can
        # separate them.  By keeping each NID page as its own group, Phase 3c can
        # pair each page with the correct person's translation independently.
        _NID_ORIG_LABELS: frozenset[str] = frozenset({
            "applicant id card", "father id card", "mother id card",
        })

        groups: list[tuple[list[int], str, str]] = []  # (page_indices, en, ko)
        i = 0
        while i < len(page_labels):
            en, ko = page_labels[i]
            el = en.strip().lower()
            if el in _NID_ORIG_LABELS:
                # Keep each NID card page as its own single-page group
                groups.append(([i], en, ko))
                i += 1
            else:
                j = i + 1
                while j < len(page_labels) and page_labels[j] == (en, ko):
                    j += 1
                groups.append((list(range(i, j)), en, ko))
                i = j

        # ── Phase 3: Merge adjacent business-bundle groups ─────────────────────
        def _resolve_bundle_template(
            bundle_en_labels: list[str],
        ) -> tuple[str, str] | None:
            """Given the EN labels that went into a bundle, return the best
            matching combined template pair, or None to fall back to first label."""
            if not template_pairs:
                return None
            lowers = [l.lower() for l in bundle_en_labels]
            has_biz = any(
                kw in l for l in lowers
                for kw in ("trade license", "business registration", "사업자")
            )
            has_emp = any(
                kw in l for l in lowers
                for kw in ("employment certificate", "재직")
            )
            has_tin = any(
                kw in l for l in lowers
                for kw in ("tin certificate", "납세자", "tin")
            )
            has_tax = any(
                kw in l for l in lowers
                for kw in ("tax certificate", "소득금액", "income tax")
            )
            has_ack = any(
                kw in l for l in lowers
                for kw in ("acknowledgment", "acknowledgement", "tax return", "납부영수증", "payment receipt")
            )
            # Select the most specific matching combined template key
            if has_emp:
                if has_ack or has_tin:
                    target = "sponsor employment certificate tin income tax certificate & tax payment receipt"
                elif has_tin or has_tax:
                    target = "sponsor employment certificate tin and income tax certificate"
                else:
                    return None
            elif has_biz:
                if has_ack or (has_tin and has_tax):
                    target = "sponsor business registration tin income tax certificate and tax payment receipt"
                elif has_tin or has_tax:
                    target = "business registration tin and income tax certificate"
                else:
                    return None
            else:
                return None
            for tp_en, tp_ko in template_pairs:
                if tp_en.lower() == target.lower():
                    return tp_en, tp_ko
            return None

        merged_groups: list[tuple[list[int], str, str]] = []
        i = 0
        while i < len(groups):
            pages, en, ko = groups[i]
            if _is_bundle_label(en):
                bundle_pages = list(pages)
                bundle_en_labels = [en]
                primary_en, primary_ko = en, ko
                j = i + 1
                while j < len(groups) and _is_bundle_label(groups[j][1]):
                    bundle_pages.extend(groups[j][0])
                    bundle_en_labels.append(groups[j][1])
                    j += 1
                # If we have multiple constituent labels (or even one trade-license
                # label that GPT-4o under-specified), try to find the best combined
                # template name so the output filename reflects all doc types.
                if use_template_mode:
                    resolved = _resolve_bundle_template(bundle_en_labels)
                    if resolved:
                        primary_en, primary_ko = resolved
                merged_groups.append((bundle_pages, primary_en, primary_ko))
                i = j
            else:
                merged_groups.append((pages, en, ko))
                i += 1

        # ── Phase 3b: Academic level merging ──────────────────────────────────
        # All docs at the same academic level (SSC / HSC / Bachelor) are merged
        # into one PDF, regardless of whether consecutive or not.
        # Only genuine credential pages (certificate, transcript, apostille) are
        # eligible — recommendation letters, reference letters, and other supporting
        # docs that merely MENTION an academic level are excluded.
        _ACADEMIC_CREDENTIAL_KW = (
            "certificate", "transcript", "apostille", "academic record",
            "grade report", "mark sheet", "marksheet", "result sheet",
            "성적증명", "졸업증명", "수료증명",
        )
        _LETTER_EXCLUDE_KW = (
            "recommendation", "reference letter", "to whom it may concern",
            "letter of", "recommendation letter", "covering letter",
            "cover letter", "employment letter", "experience letter",
            "no objection", "noc", "bonafide", "character certificate",
        )

        def _is_academic_credential_group(en: str) -> bool:
            """True only for certificate / transcript / apostille pages.
            Excludes recommendation letters and other support docs."""
            el = en.strip().lower()
            if any(kw in el for kw in _LETTER_EXCLUDE_KW):
                return False
            return any(kw in el for kw in _ACADEMIC_CREDENTIAL_KW)

        academic_buckets: dict[str, list[tuple[list[int], str, str]]] = {}
        for grp in merged_groups:
            lvl = _get_academic_level(grp[1])
            if lvl and _is_academic_credential_group(grp[1]):
                academic_buckets.setdefault(lvl, []).append(grp)

        if academic_buckets:
            # Build final list preserving original order; each level appears at
            # the position of its FIRST occurrence and absorbs all later groups.
            inserted_levels: set[str] = set()
            final_groups: list[tuple[list[int], str, str]] = []
            for grp in merged_groups:
                pages, en, ko = grp
                lvl = _get_academic_level(en)
                if lvl and lvl not in inserted_levels:
                    bucket = academic_buckets[lvl]
                    if len(bucket) > 1:
                        # Merge in doc-type order: apostille → certificate → transcript
                        # This ensures consistent reading order regardless of scan order.
                        def _academic_sort_key(grp_tuple):
                            el = grp_tuple[1].strip().lower()
                            if "apostille" in el and "certificate" not in el and "transcript" not in el:
                                return 0  # standalone apostille / e-apostille doc
                            if "certificate" in el:
                                return 1  # certificate (with or without apostille)
                            if "transcript" in el:
                                return 2  # transcript (with or without apostille)
                            return 3

                        sorted_bucket = sorted(bucket, key=_academic_sort_key)
                        all_pages = [p for bpages, _be, _bk in sorted_bucket for p in bpages]
                        rep_en, rep_ko = sorted_bucket[0][1], sorted_bucket[0][2]
                        final_groups.append((all_pages, rep_en, rep_ko))
                        order_labels = [_be for _, _be, _bk in sorted_bucket]
                        log(
                            f"  Academic merge [{lvl.upper()}]: "
                            f"{len(bucket)} group(s) → {len(all_pages)} page(s) "
                            f"order: {order_labels}"
                        )
                    else:
                        final_groups.append(grp)
                    inserted_levels.add(lvl)
                elif lvl:
                    pass  # already absorbed into its level bucket
                else:
                    final_groups.append(grp)
            merged_groups = final_groups

        # ── Phase 3b-ext: Fold orphaned apostille pages into academic level ────
        # "e-apostille transcript" and other apostille-only labels don't contain
        # SSC/HSC/bachelor keywords, so Phase 3b leaves them as separate groups.
        # Here we detect those orphans and fold them into the correct academic
        # level bucket that is already in merged_groups.
        #
        # Matching priority:
        #   1. If the apostille label contains "transcript"  → merge with the
        #      first academic group whose label also contains "transcript".
        #   2. If the apostille label contains "certificate" → merge with the
        #      first academic group whose label also contains "certificate".
        #   3. Otherwise (generic apostille) → merge with the first academic
        #      group found regardless of type.
        # If there is exactly ONE academic level present, all orphaned apostille
        # pages are folded into it unconditionally.

        def _is_orphan_apostille(en: str) -> bool:
            el = en.strip().lower()
            return (
                ("apostille" in el or "e-apostille" in el)
                and _get_academic_level(el) is None
            )

        def _is_academic_group(en: str) -> bool:
            return _get_academic_level(en.strip().lower()) is not None

        def _is_no_apostille_group(en: str) -> bool:
            """True when a group is explicitly labelled as NOT having an apostille.
            These must never receive orphaned apostille pages."""
            el = en.strip().lower()
            return (
                "no apostille" in el
                or "아포스티유 미포함" in el
                or "(without apostille)" in el
            )

        has_orphan_apostille = any(_is_orphan_apostille(grp[1]) for grp in merged_groups)
        has_any_academic = any(_is_academic_group(grp[1]) for grp in merged_groups)

        if has_orphan_apostille and has_any_academic:
            # Collect academic group indices — exclude groups that explicitly say
            # "no apostille" / "아포스티유 미포함": they must never be merge targets.
            acad_idxs = [
                gi for gi, grp in enumerate(merged_groups)
                if _is_academic_group(grp[1]) and not _is_no_apostille_group(grp[1])
            ]
            orphan_idxs = [gi for gi, grp in enumerate(merged_groups) if _is_orphan_apostille(grp[1])]

            # Build a mutable list so we can extend specific groups
            mg_list = list(merged_groups)

            absorbed_orphans: set[int] = set()
            for oi in orphan_idxs:
                oph_pages, oph_en, oph_ko = mg_list[oi]
                oph_el = oph_en.strip().lower()

                if not acad_idxs:
                    # No eligible (apostille-bearing) academic groups — leave orphan as-is
                    log(
                        f"  Apostille fold skipped: {oph_en!r} — "
                        f"all academic groups are marked 'no apostille'"
                    )
                    continue

                # Try to find best-matching academic group
                target_idx: int | None = None
                if len(acad_idxs) == 1:
                    target_idx = acad_idxs[0]
                else:
                    # Multi-level: prefer same sub-type (transcript/certificate)
                    if "transcript" in oph_el:
                        for ai in acad_idxs:
                            if "transcript" in mg_list[ai][1].strip().lower():
                                target_idx = ai
                                break
                    if target_idx is None and "certificate" in oph_el:
                        for ai in acad_idxs:
                            if "certificate" in mg_list[ai][1].strip().lower():
                                target_idx = ai
                                break
                    if target_idx is None:
                        target_idx = acad_idxs[0]

                if target_idx is not None:
                    t_pages, t_en, t_ko = mg_list[target_idx]
                    # Apostille pages go FIRST (before certificate/transcript pages)
                    merged_pages = list(oph_pages) + list(t_pages)
                    mg_list[target_idx] = (merged_pages, t_en, t_ko)
                    absorbed_orphans.add(oi)
                    log(
                        f"  Apostille fold: {oph_en!r} ({len(oph_pages)} page(s)) "
                        f"→ merged into {t_en!r}"
                    )

            # Rebuild merged_groups without the absorbed orphan groups
            merged_groups = [
                grp for gi, grp in enumerate(mg_list)
                if gi not in absorbed_orphans
            ]

        # ── Phase 3c: NID original + translation pairing ──────────────────────
        # Each person's NID set = (original Bangla card pages) + (English translation pages)
        # → merged into ONE output PDF per person, regardless of scan order.
        # Translation-only groups that already have a matching original are absorbed.
        # Groups for DIFFERENT people (applicant/father/mother) are NEVER merged together.
        #
        # NID person mapping:
        #   person "applicant" → original label "applicant id card"
        #                         translation label "applicant nid translation"
        #   person "father"    → original label "father id card"
        #                         translation label "father nid translation"
        #   person "mother"    → original label "mother id card"
        #                         translation label "mother nid translation"

        _NID_PERSONS = {
            "applicant": {
                "orig_kw":  "applicant id card",
                "trans_kw": "applicant nid translation",
            },
            "father": {
                "orig_kw":  "father id card",
                "trans_kw": "father nid translation",
            },
            "mother": {
                "orig_kw":  "mother id card",
                "trans_kw": "mother nid translation",
            },
        }

        def _nid_person(en_label: str) -> tuple[str, str] | None:
            """Return (person, role) where role is 'orig' or 'trans', or None."""
            el = en_label.strip().lower()
            for person, cfg in _NID_PERSONS.items():
                if el == cfg["orig_kw"]:
                    return person, "orig"
                if el == cfg["trans_kw"]:
                    return person, "trans"
            return None

        # Check whether any NID translation groups exist at all before doing work
        has_nid_trans = any(
            _nid_person(grp[1]) is not None and _nid_person(grp[1])[1] == "trans"
            for grp in merged_groups
        )

        if has_nid_trans:
            # Bucket groups by (person, role)
            nid_buckets: dict[str, dict[str, list[tuple[list[int], str, str]]]] = {
                p: {"orig": [], "trans": []} for p in _NID_PERSONS
            }
            non_nid: list[tuple[list[int], str, str]] = []

            for grp in merged_groups:
                info = _nid_person(grp[1])
                if info:
                    person, role = info
                    nid_buckets[person][role].append(grp)
                else:
                    non_nid.append(grp)

            # For each person: merge orig groups + trans groups into one set.
            # Page order: orig pages first (sorted), then trans pages (sorted).
            # Label: original NID label (so output filename = "지원자 신분증-applicant ID card").
            # If only orig (no trans): keep as separate groups (unchanged — already split by Phase 2).
            # If only trans (no orig): keep as-is (unusual — maybe only translation was scanned).
            nid_merged_by_person: dict[str, tuple[list[int], str, str] | None] = {}
            for person, cfg in _NID_PERSONS.items():
                orig_grps  = nid_buckets[person]["orig"]
                trans_grps = nid_buckets[person]["trans"]

                if not orig_grps and not trans_grps:
                    nid_merged_by_person[person] = None
                    continue

                if orig_grps and not trans_grps:
                    # No translation pages — keep original groups separate (multi-page card is fine)
                    nid_merged_by_person[person] = None  # will re-insert as-is below
                    continue

                # Combine: all orig pages + all trans pages in page-number order
                orig_pages  = sorted(p for grp in orig_grps  for p in grp[0])
                trans_pages = sorted(p for grp in trans_grps for p in grp[0])
                # Translation first (English readable), then original Bangla card scan
                combined_pages = trans_pages + orig_pages

                # Label from the ORIGINAL group (first original, or first translation if no orig)
                if orig_grps:
                    rep_en, rep_ko = orig_grps[0][1], orig_grps[0][2]
                else:
                    rep_en, rep_ko = trans_grps[0][1], trans_grps[0][2]

                nid_merged_by_person[person] = (combined_pages, rep_en, rep_ko)
                log(
                    f"  NID pair [{person}]: "
                    f"{len(orig_pages)} original page(s) + {len(trans_pages)} translation page(s) "
                    f"→ 1 PDF  ({len(combined_pages)} pages)"
                )

            # Re-assemble merged_groups preserving original scan order:
            # Walk merged_groups; at each NID group insert the combined set
            # exactly at the position of its FIRST occurrence, then skip the rest.
            inserted_nid: set[str] = set()
            final_nid_groups: list[tuple[list[int], str, str]] = []

            for grp in merged_groups:
                info = _nid_person(grp[1])
                if info is None:
                    final_nid_groups.append(grp)
                    continue

                person, role = info
                orig_grps  = nid_buckets[person]["orig"]
                trans_grps = nid_buckets[person]["trans"]
                combined   = nid_merged_by_person.get(person)

                if person in inserted_nid:
                    continue  # already emitted this person's combined group

                if combined is not None:
                    # Has translation — emit the merged set
                    final_nid_groups.append(combined)
                    inserted_nid.add(person)
                else:
                    # No translation exists — re-emit orig groups separately (keep multi-page splits)
                    for og in orig_grps:
                        final_nid_groups.append(og)
                    inserted_nid.add(person)

            merged_groups = final_nid_groups

        # ── Phase 3d: Generic translation-first pairing ───────────────────────
        # For ANY document type that has both an original and a "... translation"
        # group, merge them into one PDF with the translation page(s) FIRST,
        # then the original page(s).  Works for birth certs, death certs,
        # family relationship certs, marriage certs, and any future doc type.
        # NID is already handled by Phase 3c and is excluded here.
        #
        # Matching rule: group B is the translation of group A when
        #   B's label == A's label + " translation"  (case-insensitive)
        # Output label: the ORIGINAL group's label (no " translation" suffix),
        #   so the filename stays the same as if there were no translation.

        def _is_nid_label(en: str) -> bool:
            el = en.strip().lower()
            return any(
                el == kw for kw in (
                    "applicant id card", "applicant nid translation",
                    "father id card", "father nid translation",
                    "mother id card", "mother nid translation",
                )
            )

        has_generic_trans = any(
            grp[1].strip().lower().endswith(" translation")
            and not _is_nid_label(grp[1])
            for grp in merged_groups
        )

        if has_generic_trans:
            # Index groups by lower-case EN label for O(1) lookup
            _grp_index: dict[str, list[int]] = {}
            for gi, grp in enumerate(merged_groups):
                key = grp[1].strip().lower()
                _grp_index.setdefault(key, []).append(gi)

            absorbed: set[int] = set()
            final_gen_groups: list[tuple[list[int], str, str]] = []

            for gi, grp in enumerate(merged_groups):
                if gi in absorbed:
                    continue
                en, ko = grp[1], grp[2]
                el = en.strip().lower()

                # Is this the ORIGINAL? Find its translation partner.
                trans_key = el + " translation"
                trans_idxs = _grp_index.get(trans_key, [])
                # Filter out already-absorbed translations
                trans_idxs = [ti for ti in trans_idxs if ti not in absorbed]

                if trans_idxs:
                    # Collect all translation pages (may be multiple groups)
                    trans_pages: list[int] = []
                    for ti in trans_idxs:
                        trans_pages.extend(merged_groups[ti][0])
                        absorbed.add(ti)
                    orig_pages = list(grp[0])
                    combined = trans_pages + orig_pages  # translation first
                    final_gen_groups.append((combined, en, ko))
                    log(
                        f"  Translation pair [{en!r}]: "
                        f"{len(trans_pages)} translation page(s) + "
                        f"{len(orig_pages)} original page(s) → 1 PDF"
                    )
                else:
                    # Is this itself a translation with no matching original?
                    # Keep as-is (unusual but valid).
                    final_gen_groups.append(grp)

            merged_groups = final_gen_groups

        log(f"  Merged into {len(merged_groups)} output PDF(s):")
        for grp_pages, grp_en, _grp_ko in merged_groups:
            log(f"    pages {[p + 1 for p in grp_pages]} → {grp_en!r}")

        # ── Phase 4: Build output stems ────────────────────────────────────────
        raw_stems: list[str] = []
        for grp_pages, grp_en, grp_ko in merged_groups:
            if use_template_mode:
                stem = _make_stem_from_pair(grp_en, grp_ko, grp_pages[0])
            else:
                # Legacy path: _finalize_ocr_stem + optional Korean translation
                stem = ""
                if grp_en:
                    stem = _finalize_ocr_stem(
                        "", grp_en, "", log, grp_pages[0],
                        template_stem_for_type=template_stem_for_type,
                        page_text="",
                    )
                if not stem:
                    stem = f"{fallback_prefix}_{grp_pages[0] + 1:03d}"
            raw_stems.append(stem)

        if not use_template_mode and korean_api_key:
            raw_stems = _apply_korean_to_stems(
                raw_stems,
                fallback_prefix=fallback_prefix,
                korean_api_key=korean_api_key,
                log=log,
            )

        # ── Phase 5: Write output PDFs ─────────────────────────────────────────
        out_paths: list[str] = []
        used_names: dict[str, int] = {}
        for idx, ((grp_pages, grp_en, grp_ko), stem) in enumerate(
            zip(merged_groups, raw_stems)
        ):
            if progress_callback:
                try:
                    progress_callback(total + idx, total * 2)
                except Exception:
                    pass
            stem = _disambiguate_stem(stem, used_names)
            out_path = _safe_out_path(out_dir, stem)
            saved_name = os.path.basename(out_path)
            single = fitz.open()
            for pi in grp_pages:
                single.insert_pdf(doc, from_page=pi, to_page=pi)
            single.save(out_path)
            single.close()
            out_paths.append(out_path)
            log(f"  → {saved_name}  ({len(grp_pages)} page(s))")

        doc.close()

        if progress_callback:
            try:
                progress_callback(total * 2, total * 2)
            except Exception:
                pass

        log(f"Smart split/merge complete: {len(out_paths)} file(s) in {out_dir}")
        return True, out_paths

    except Exception as e:
        log(f"Smart split/merge failed: {e}")
        import traceback
        log(traceback.format_exc())
        return False, []


def pdf_split_n_pages(src: str, out_dir: str, chunk_size: int, log: Callable[[str], None]) -> bool:
    fitz = get_fitz()
    if not fitz:
        log("PDF split: install PyMuPDF.")
        return False
    if chunk_size < 1:
        log("Chunk size must be at least 1.")
        return False
    os.makedirs(out_dir, exist_ok=True)
    base = Path(src).stem
    try:
        doc = fitz.open(src)
        total = len(doc)
        part = 0
        for start in range(0, total, chunk_size):
            end = min(start + chunk_size - 1, total - 1)
            single = fitz.open()
            single.insert_pdf(doc, from_page=start, to_page=end)
            part += 1
            out = os.path.join(out_dir, f"{base}_part_{part:04d}.pdf")
            single.save(out)
            single.close()
        doc.close()
        log(f"Split {total} pages into {part} file(s) ({chunk_size} pages each max) in {out_dir}")
        return True
    except Exception as e:
        log(f"PDF split failed: {e}")
        return False


def _release_pdf_path_for_overwrite(path: str) -> None:
    """Close preview LRU handles on ``path`` and clear read-only (Windows) so it can be replaced."""
    ap = os.path.abspath(path)
    invalidate_pdf_doc_cache(ap)
    if os.name != "nt" or not os.path.isfile(ap):
        return
    try:
        import stat

        os.chmod(ap, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _pdf_save_over_path(doc, path: str) -> tuple[bool, str]:
    """Save an open fitz document over path using a temp file, then close doc."""
    path = os.path.abspath(path)
    tmp = path + ".~sfm_edit.pdf"
    try:
        doc.save(tmp, garbage=4, deflate=True)
    except Exception as e:
        try:
            doc.close()
        except Exception:
            pass
        try:
            if os.path.isfile(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return False, str(e)
    try:
        doc.close()
    except Exception:
        pass
    _release_pdf_path_for_overwrite(path)
    try:
        os.replace(tmp, path)
        return True, ""
    except OSError as e:
        try:
            if os.path.isfile(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        return False, str(e)


def pdf_rasterize_page_for_crop(path: str, page_index: int, max_dim: int = 3200):
    """
    High-resolution RGB raster of one PDF page for on-screen crop (passport frame).
    Longest edge is capped at ``max_dim`` px; uses PyMuPDF + Pillow.
    """
    fitz = get_fitz()
    Image = get_pillow()
    if not fitz or not Image:
        return None
    path = os.path.abspath(os.path.normpath(path))
    doc = None
    try:
        doc = _fitz_open_document(fitz, path)
        if doc is None:
            return None
        if page_index < 0 or page_index >= len(doc):
            return None
        page = doc.load_page(page_index)
        r = page.rect
        pw = max(float(r.width), 1.0)
        ph = max(float(r.height), 1.0)
        scale = min(float(max_dim) / max(pw, ph), 4.0)
        mat = fitz.Matrix(scale, scale)
        try:
            pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
        except Exception:
            pix = page.get_pixmap(matrix=mat, alpha=False)
        if pix.width < 1 or pix.height < 1:
            return None
        try:
            png = pix.tobytes("png")
            img = Image.open(BytesIO(png)).convert("RGB")
            return img
        except Exception:
            w, h = pix.width, pix.height
            raw = bytes(pix.samples)
            n = int(pix.n)
            if n == 3:
                img = Image.frombytes("RGB", (w, h), raw)
            elif n == 4:
                img = Image.frombytes("RGBA", (w, h), raw).convert("RGB")
            elif n == 1:
                img = Image.frombytes("L", (w, h), raw).convert("RGB")
            else:
                return None
            return img
    except Exception:
        return None
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def pdf_replace_page_with_passport_image(
    path: str,
    page_index: int,
    pil_rgb,
    dpi: float,
    mm_w: float,
    mm_h: float,
) -> tuple[bool, str]:
    """
    Replace one PDF page with a single full-page JPEG (ID-photo dimensions in mm at ``dpi``).
    """
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF (pip install PyMuPDF)."
    if not get_pillow():
        return False, "Install Pillow."

    path = os.path.abspath(os.path.normpath(path))
    if not os.path.isfile(path):
        return False, "PDF not found."
    try:
        buf = BytesIO()
        rgb = pil_rgb.convert("RGB") if hasattr(pil_rgb, "convert") else pil_rgb
        rgb.save(buf, format="JPEG", quality=94, dpi=(float(dpi), float(dpi)))
        jpg = buf.getvalue()
    except Exception as e:
        return False, f"JPEG encode failed: {e}"

    w_pt = float(mm_w) / 25.4 * 72.0
    h_pt = float(mm_h) / 25.4 * 72.0
    doc = None
    try:
        doc = _fitz_open_document(fitz, path)
        if doc is None:
            return False, "Could not open PDF."
        n = len(doc)
        if page_index < 0 or page_index >= n:
            doc.close()
            return False, "Invalid page."
        doc.delete_page(page_index)
        doc.insert_page(page_index, width=w_pt, height=h_pt)
        page = doc.load_page(page_index)
        page.insert_image(page.rect, stream=jpg)
        return _pdf_save_over_path(doc, path)
    except Exception as e:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
        return False, str(e)


def pdf_rotate_pages(path: str, page_indices: list, clockwise: bool = True) -> tuple[bool, str]:
    """Rotate one or more pages in-place (PDF on disk)."""
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF (pip install PyMuPDF)."
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "PDF not found."
    try:
        doc = fitz.open(path)
        total = len(doc)
        dr = 90 if clockwise else -90
        for page_index in page_indices:
            if page_index < 0 or page_index >= total:
                continue
            page = doc.load_page(page_index)
            try:
                cur = int(page.rotation)
            except Exception:
                cur = 0
            page.set_rotation((cur + dr) % 360)
        ok, err = _pdf_save_over_path(doc, path)
        if not ok:
            return False, err
        return True, "Page rotation saved."
    except Exception as e:
        return False, str(e)


def pdf_rotate_page(path: str, page_index: int, clockwise: bool = True) -> tuple[bool, str]:
    """Rotate one page in-place (PDF on disk). clockwise: +90° in PDF page metadata."""
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF (pip install PyMuPDF)."
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "PDF not found."
    try:
        doc = fitz.open(path)
        if page_index < 0 or page_index >= len(doc):
            doc.close()
            return False, "Invalid page."
        page = doc.load_page(page_index)
        dr = 90 if clockwise else -90
        try:
            cur = int(page.rotation)
        except Exception:
            cur = 0
        new_r = (cur + dr) % 360
        page.set_rotation(new_r)
        ok, err = _pdf_save_over_path(doc, path)
        if not ok:
            return False, err
        return True, "Page rotation saved."
    except Exception as e:
        return False, str(e)


def pdf_delete_page(path: str, page_index: int) -> tuple[bool, str]:
    """Remove one page from the PDF on disk (must leave at least one page)."""
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF."
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "PDF not found."
    try:
        doc = fitz.open(path)
        n = len(doc)
        if n <= 1:
            doc.close()
            return False, "Cannot delete the only page."
        if page_index < 0 or page_index >= n:
            doc.close()
            return False, "Invalid page."
        doc.delete_page(page_index)
        ok, err = _pdf_save_over_path(doc, path)
        if not ok:
            return False, err
        return True, f"Deleted page {page_index + 1} (saved)."
    except Exception as e:
        return False, str(e)


def pdf_delete_pages(path: str, page_indices: Sequence[int]) -> tuple[bool, str]:
    """Remove several pages (0-based indices) in one save. At least one page must remain."""
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF."
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "PDF not found."
    indices = sorted({int(i) for i in page_indices})
    if not indices:
        return False, "No pages selected."
    try:
        doc = fitz.open(path)
        n = len(doc)
        if n <= 1:
            doc.close()
            return False, "Cannot delete the only page."
        for pi in indices:
            if pi < 0 or pi >= n:
                doc.close()
                return False, f"Invalid page {pi + 1}."
        if n - len(indices) < 1:
            doc.close()
            return False, "Cannot delete every page."
        for pi in reversed(indices):
            doc.delete_page(pi)
        ok, err = _pdf_save_over_path(doc, path)
        if not ok:
            return False, err
        if len(indices) == 1:
            return True, f"Deleted page {indices[0] + 1} (saved)."
        shown = ", ".join(str(i + 1) for i in indices[:12])
        more = f" (+{len(indices) - 12} more)" if len(indices) > 12 else ""
        return True, f"Deleted {len(indices)} pages ({shown}{more}) (saved)."
    except Exception as e:
        return False, str(e)


def pdf_append_pdf(dest_path: str, more_pdf_path: str) -> tuple[bool, str]:
    """Append all pages from more_pdf_path onto dest_path (both files on disk)."""
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF."
    dest_path = os.path.abspath(dest_path)
    more_pdf_path = os.path.abspath(more_pdf_path)
    if not os.path.isfile(dest_path):
        return False, "Destination PDF not found."
    if not os.path.isfile(more_pdf_path):
        return False, "PDF to append not found."
    if os.path.normcase(dest_path) == os.path.normcase(more_pdf_path):
        return False, "Choose a different file to append."
    try:
        doc = fitz.open(dest_path)
        with fitz.open(more_pdf_path) as src:
            doc.insert_pdf(src)
        ok, err = _pdf_save_over_path(doc, dest_path)
        if not ok:
            return False, err
        return True, f"Appended pages from {os.path.basename(more_pdf_path)}."
    except Exception as e:
        return False, str(e)


def pdf_stamp_text_line(path: str, page_index: int, text: str, fontsize: int = 11) -> tuple[bool, str]:
    """Add a single line of text near the bottom-left of the page (visual stamp)."""
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF."
    text = (text or "").strip()
    if not text:
        return False, "No text to add."
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "PDF not found."
    try:
        doc = fitz.open(path)
        if page_index < 0 or page_index >= len(doc):
            doc.close()
            return False, "Invalid page."
        page = doc.load_page(page_index)
        pt = fitz.Point(72, page.rect.height - 48)
        page.insert_text(pt, text, fontsize=max(6, min(fontsize, 72)), color=(0, 0, 0))
        ok, err = _pdf_save_over_path(doc, path)
        if not ok:
            return False, err
        return True, "Text added and saved."
    except Exception as e:
        return False, str(e)


def pdf_reorder_pages(path: str, new_order: Sequence[int]) -> tuple[bool, str]:
    """Reorder in place: new_order[k] is the old 0-based page index that becomes page k (permutation)."""
    fitz = get_fitz()
    if not fitz:
        return False, "Install PyMuPDF."
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "PDF not found."
    order = [int(i) for i in new_order]
    doc = None
    try:
        doc = fitz.open(path)
        n = len(doc)
        if len(order) != n:
            return False, "Page count mismatch."
        if sorted(order) != list(range(n)):
            return False, "Invalid page order (must be a permutation)."
        doc.select(order)
        ok, err = _pdf_save_over_path(doc, path)
        if not ok:
            return False, err
        return True, "Page order updated."
    except Exception as e:
        return False, str(e)
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def pdf_print_path(path: str) -> tuple[bool, str]:
    """Send PDF to the default printer (OS-specific)."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "PDF not found."
    if sys.platform == "win32":
        try:
            os.startfile(path, "print")  # type: ignore[attr-defined]
            return True, ""
        except OSError as e:
            return False, str(e)
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["lpr", path], close_fds=True)
            return True, ""
        except OSError as e:
            return False, str(e)
    try:
        subprocess.Popen(["xdg-open", path], close_fds=True)
        return True, ""
    except OSError as e:
        return False, str(e)


def pdf_numbered_copy_path(path: str) -> str:
    """Next free sibling path stem_2.pdf, stem_3.pdf, …"""
    d, b = os.path.split(os.path.abspath(path))
    stem, ext = os.path.splitext(b)
    n = 2
    while n < 100000:
        cand = os.path.join(d, f"{stem}_{n}{ext}")
        if not os.path.isfile(cand):
            return cand
        n += 1
    return os.path.join(d, f"{stem}_copy{ext}")


def pdf_copy_numbered_in_folder(path: str) -> tuple[bool, str]:
    """Duplicate PDF beside the original using a numeric suffix (no save dialog)."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "File not found."
    dst = pdf_numbered_copy_path(path)
    try:
        shutil.copy2(path, dst)
        return True, dst
    except OSError as e:
        return False, str(e)


def pdf_make_searchable_ocr_inplace(path: str) -> tuple[bool, str]:
    """Add OCR text layer via ocrmypdf when the CLI is installed (optional)."""
    if not shutil.which("ocrmypdf"):
        return (
            False,
            "ocrmypdf not on PATH. Install: pip install ocrmypdf and system Tesseract OCR.",
        )
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "PDF not found."
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(
            ["ocrmypdf", "--skip-text", path, tmp],
            capture_output=True,
            text=True,
            timeout=3600,
            creationflags=flags,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "ocrmypdf failed").strip()
            return False, err[:1200]
        _release_pdf_path_for_overwrite(path)
        os.replace(tmp, path)
        tmp = ""
        return True, "Searchable text layer added (ocrmypdf)."
    except subprocess.TimeoutExpired:
        return False, "ocrmypdf timed out."
    except Exception as e:
        return False, str(e)
    finally:
        if tmp and os.path.isfile(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _hwp_dispatch():
    if sys.platform != "win32":
        return None
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return None
    for progid in ("HWPFrame.HwpObject", "HWPFrame.HwpObject.1"):
        try:
            return win32com.client.Dispatch(progid)
        except Exception:
            continue
    return None


def hwp_to_pdf(src: str, dst: str, log: Callable[[str], None]) -> bool:
    hwp = _hwp_dispatch()
    if not hwp:
        log("HWP→PDF: Hancom HWP not found (install 한글 / HWP and pywin32).")
        return False
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    try:
        hwp.XHwpWindows.Item(0).Visible = False
    except Exception:
        pass
    try:
        hwp.Open(src)
        # Format: PDF — Hancom versions differ; try SaveAs variants
        try:
            hwp.SaveAs(dst, "PDF", "")
        except Exception:
            hwp.SaveAs(dst)
        try:
            hwp.Quit()
        except Exception:
            pass
        log(f"Saved PDF: {dst}")
        return True
    except Exception as e:
        log(f"HWP→PDF failed: {e}")
        try:
            hwp.Quit()
        except Exception:
            pass
        return False


def hwp_to_word(src: str, dst: str, log: Callable[[str], None]) -> bool:
    hwp = _hwp_dispatch()
    if not hwp:
        log("HWP→Word: Hancom HWP not found (install 한글 / HWP and pywin32).")
        return False
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    if not dst.lower().endswith(".docx"):
        dst = dst + ".docx"
    try:
        try:
            hwp.XHwpWindows.Item(0).Visible = False
        except Exception:
            pass
        hwp.Open(src)
        try:
            hwp.SaveAs(dst, "DOCX", "")
        except Exception:
            try:
                hwp.SaveAs(dst, "MSWORD", "")
            except Exception:
                hwp.SaveAs(dst)
        try:
            hwp.Quit()
        except Exception:
            pass
        log(f"Saved Word: {dst}")
        return True
    except Exception as e:
        log(f"HWP→Word failed: {e}")
        try:
            hwp.Quit()
        except Exception:
            pass
        return False


def _norm_zip_name(name: str) -> str:
    return name.replace("\\", "/")


def read_zip_member_bytes(zip_path: str, member: str) -> bytes | None:
    norm = _norm_zip_name(member)
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            for n in z.namelist():
                if _norm_zip_name(n) == norm:
                    return z.read(n)
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    return None


def zip_list_immediate_children(zip_path: str, inner_prefix: str) -> tuple[list[str], list[tuple[str, str]]]:
    """
    List direct child folder names and (basename, full_member_path) files under inner_prefix.
    inner_prefix uses forward slashes, e.g. '' or 'folder/'.
    """
    inner_prefix = _norm_zip_name(inner_prefix)
    if inner_prefix and not inner_prefix.endswith("/"):
        inner_prefix += "/"
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            names = z.namelist()
    except (OSError, zipfile.BadZipFile):
        return [], []

    dir_names: set[str] = set()
    files: list[tuple[str, str]] = []
    L = len(inner_prefix)

    for raw in names:
        norm = _norm_zip_name(raw)
        if inner_prefix:
            if not norm.startswith(inner_prefix):
                continue
            rel = norm[L:]
        else:
            rel = norm
        if not rel:
            continue
        if rel.endswith("/"):
            parts = [p for p in rel.rstrip("/").split("/") if p]
            if len(parts) >= 1:
                dir_names.add(parts[0])
            continue
        parts = rel.split("/")
        if len(parts) == 1:
            files.append((parts[0], norm))
        else:
            dir_names.add(parts[0])

    dirs_sorted = sorted(dir_names, key=filesystem_natural_sort_key)
    files_sorted = sorted(files, key=lambda t: filesystem_natural_sort_key(t[0]))
    return dirs_sorted, files_sorted


def rewrite_zip_transform(zip_path: str, transform: Callable[[str], str | None]) -> tuple[bool, str]:
    """Rewrite ZIP in place. transform(normalized_name) -> new name or None to drop entry."""
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path) or not zipfile.is_zipfile(zip_path):
        return False, "Invalid ZIP file."
    tmp_dir = os.path.dirname(zip_path) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".zip", dir=tmp_dir)
    os.close(fd)
    dup_err: str | None = None
    try:
        with zipfile.ZipFile(zip_path, "r") as zin, zipfile.ZipFile(
            tmp_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as zout:
            seen: set[str] = set()
            for item in zin.infolist():
                fn = _norm_zip_name(item.filename)
                out_fn = transform(fn)
                if out_fn is None:
                    continue
                out_fn = _norm_zip_name(out_fn)
                if out_fn in seen:
                    # Use break instead of return so the with-block closes cleanly
                    # and the finally block can remove the temp file.
                    dup_err = f"Duplicate archive path: {out_fn}"
                    break
                seen.add(out_fn)
                data = zin.read(item.filename)
                ni = zipfile.ZipInfo(filename=out_fn, date_time=item.date_time)
                ni.compress_type = item.compress_type
                ni.external_attr = item.external_attr
                ni.flag_bits = item.flag_bits
                zout.writestr(ni, data)
        if dup_err:
            return False, dup_err
        os.replace(tmp_path, zip_path)
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        # After a successful os.replace() tmp_path no longer exists (it became zip_path),
        # so this is always safe: it only removes the temp file on failure paths.
        try:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def rename_zip_member(zip_path: str, old_member: str, new_basename: str) -> tuple[bool, str]:
    """Rename one file entry inside the archive (basename only, same logical folder)."""
    old_member = _norm_zip_name(old_member).strip()
    new_basename = new_basename.strip()
    if not old_member or old_member.endswith("/"):
        return False, "Select a file inside the archive (not a folder row)."
    if not new_basename or "/" in new_basename or "\\" in new_basename:
        return False, "Invalid name (no path separators)."
    invalid = set('\\/:*?"<>|')
    if any(c in new_basename for c in invalid):
        return False, "Name contains invalid characters."
    parent_slash = ""
    if "/" in old_member:
        parent_slash = old_member.rsplit("/", 1)[0] + "/"
    new_member = parent_slash + new_basename
    if new_member == old_member:
        return True, "Unchanged."

    with zipfile.ZipFile(zip_path, "r") as z:
        all_norm = {_norm_zip_name(n) for n in z.namelist()}
    if old_member not in all_norm:
        return False, "Original entry not found in archive."
    if new_member in all_norm:
        return False, "That name already exists in the archive."

    def tr(fn: str) -> str | None:
        if fn == old_member:
            return new_member
        return fn

    ok, err = rewrite_zip_transform(zip_path, tr)
    if not ok:
        return False, err
    return True, new_member


def replace_zip_member_bytes(zip_path: str, member: str, new_data: bytes) -> tuple[bool, str]:
    """Replace the uncompressed bytes of one archive entry in place (names unchanged)."""
    zip_path = os.path.abspath(zip_path)
    member = _norm_zip_name(member).strip()
    if not member or member.endswith("/"):
        return False, "Invalid member."
    try:
        with zipfile.ZipFile(zip_path, "r") as zin:
            names = zin.namelist()
            if not any(_norm_zip_name(n) == member for n in names):
                return False, "Entry not found in archive."
    except (OSError, zipfile.BadZipFile) as e:
        return False, str(e)

    tmp_dir = os.path.dirname(zip_path) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".zip", dir=tmp_dir)
    os.close(fd)
    try:
        replaced = False
        with zipfile.ZipFile(zip_path, "r") as zin, zipfile.ZipFile(
            tmp_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                fn = item.filename
                if _norm_zip_name(fn) == member:
                    ni = zipfile.ZipInfo(filename=fn, date_time=item.date_time)
                    ni.compress_type = item.compress_type
                    ni.external_attr = item.external_attr
                    ni.flag_bits = item.flag_bits
                    zout.writestr(ni, new_data)
                    replaced = True
                else:
                    ni = zipfile.ZipInfo(filename=fn, date_time=item.date_time)
                    ni.compress_type = item.compress_type
                    ni.external_attr = item.external_attr
                    ni.flag_bits = item.flag_bits
                    zout.writestr(ni, zin.read(item.filename))
        if not replaced:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return False, "Entry missing during rewrite."
        os.replace(tmp_path, zip_path)
        return True, ""
    except Exception as e:
        try:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return False, str(e)


def rename_zip_directory_segment(zip_path: str, inner_prefix: str, new_folder_name: str) -> tuple[bool, str]:
    """Rename the last folder segment for inner_prefix (e.g. a/b/ → a/new/)."""
    inner_prefix = _norm_zip_name(inner_prefix)
    if not inner_prefix.endswith("/"):
        inner_prefix += "/"
    new_folder_name = new_folder_name.strip().strip("/\\")
    if not new_folder_name or any(c in new_folder_name for c in '\\/:*?"<>|'):
        return False, "Invalid folder name."
    parts = inner_prefix.rstrip("/").split("/")
    if not parts or not parts[-1]:
        return False, "Invalid folder in archive."
    parent_slash = "/".join(parts[:-1])
    if parent_slash:
        parent_slash += "/"
    new_prefix = parent_slash + new_folder_name + "/"
    old_p = inner_prefix
    if new_prefix == old_p:
        return True, "Unchanged."

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            all_fns = [_norm_zip_name(n) for n in z.namelist()]
    except (OSError, zipfile.BadZipFile) as e:
        return False, str(e)

    m: dict[str, str] = {}
    for fn in all_fns:
        if fn.startswith(old_p):
            m[fn] = new_prefix + fn[len(old_p) :]
        else:
            m[fn] = fn

    if len(set(m.values())) != len(m):
        return False, "Rename would create duplicate paths in archive."

    def tr(fn: str) -> str | None:
        return m.get(fn, fn)

    ok, err = rewrite_zip_transform(zip_path, tr)
    if not ok:
        return False, err
    return True, new_prefix


def delete_zip_member(zip_path: str, member: str) -> tuple[bool, str]:
    """Remove one file entry from the archive."""
    member = _norm_zip_name(member).strip()
    if not member or member.endswith("/"):
        return False, "Only files inside the archive can be removed here."

    with zipfile.ZipFile(zip_path, "r") as z:
        all_norm = {_norm_zip_name(n) for n in z.namelist()}
    if member not in all_norm:
        return False, "Entry not found in archive."

    def tr(fn: str) -> str | None:
        if fn == member:
            return None
        return fn

    ok, err = rewrite_zip_transform(zip_path, tr)
    if not ok:
        return False, err
    return True, "Removed from archive."


def unpack_zip_to_dir(zip_path: str, dest_dir: str) -> tuple[bool, str]:
    """Extract a .zip into dest_dir (created if needed). Returns (ok, message)."""
    zip_path = os.path.abspath(zip_path)
    dest_dir = os.path.abspath(dest_dir)
    if not os.path.isfile(zip_path):
        return False, "ZIP file not found."
    if not zipfile.is_zipfile(zip_path):
        return False, "Not a valid ZIP archive."
    try:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.unpack_archive(zip_path, dest_dir, "zip")
        return True, dest_dir
    except OSError as e:
        return False, str(e)
    except ValueError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def _zip_wraps_own_stem(zip_path: str, stem: str) -> bool:
    """Return True if every entry in the archive lives under a single
    top-level folder named ``<stem>/`` — i.e. extracting into a folder also
    named ``<stem>`` would produce ``<stem>/<stem>/…`` double-nesting."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [n.replace("\\", "/").lstrip("/") for n in zf.namelist() if n.strip("/ ")]
        prefix = stem + "/"
        return bool(names) and all(n == prefix or n.startswith(prefix) for n in names)
    except Exception:
        return False


def extract_all_zips_to_sibling_folders(
    folder: str,
    *,
    log: Callable[[str], None] | None = None,
) -> dict[str, int | list[str]]:
    """
    For each *.zip file directly under `folder`, extract into folder/<zip stem>/
    (e.g. ``NAME.zip`` → ``NAME/``). Skips if the target path already exists as a file,
    or as a non-empty folder. Returns stats: extracted, skipped, failed, errors (paths).
    """
    root = os.path.abspath(folder)
    if not os.path.isdir(root):
        return {"extracted": 0, "skipped": 0, "failed": 0, "errors": ["Not a directory."]}

    def _lg(msg: str) -> None:
        if log:
            log(msg)

    zips: list[str] = []
    try:
        for name in os.listdir(root):
            if not name.lower().endswith(".zip"):
                continue
            p = os.path.join(root, name)
            if os.path.isfile(p) and zipfile.is_zipfile(p):
                zips.append(p)
    except OSError as e:
        return {"extracted": 0, "skipped": 0, "failed": 0, "errors": [str(e)]}

    zips.sort(key=lambda p: os.path.basename(p).lower())
    extracted = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    for zp in zips:
        base = os.path.basename(zp)
        stem = os.path.splitext(base)[0].strip()
        if not stem:
            _lg(f"SKIP (empty name): {base}")
            skipped += 1
            continue
        if any(c in stem for c in '\\/:*?"<>|'):
            _lg(f"SKIP (invalid folder name): {base}")
            skipped += 1
            continue
        dest = os.path.join(root, stem)
        if os.path.isfile(dest):
            _lg(f"SKIP (exists as file): {stem}")
            skipped += 1
            continue
        if os.path.isdir(dest):
            try:
                if any(os.scandir(dest)):
                    _lg(f"SKIP (folder not empty): {stem}")
                    skipped += 1
                    continue
            except OSError as e:
                _lg(f"SKIP {stem}: {e}")
                skipped += 1
                continue
        target = root if _zip_wraps_own_stem(zp, stem) else dest
        ok, msg = unpack_zip_to_dir(zp, target)
        if ok:
            extracted += 1
            _lg(f"Extracted: {base} → {stem}{os.sep}")
        else:
            failed += 1
            errors.append(f"{base}: {msg}")
            _lg(f"FAIL: {base} — {msg}")

    return {
        "extracted": extracted,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }


def extract_zip_to_sibling_folder(
    zip_path: str,
    *,
    log: Callable[[str], None] | None = None,
) -> dict[str, int | list[str]]:
    """
    Extract a single ``.zip`` file into a sibling folder named after its stem
    (same rules as :func:`extract_all_zips_to_sibling_folders` for one archive).
    ``zip_path`` must be a regular file path to a valid ZIP.
    """
    zp = os.path.abspath(zip_path)

    def _lg(msg: str) -> None:
        if log:
            log(msg)

    if not os.path.isfile(zp):
        return {"extracted": 0, "skipped": 0, "failed": 1, "errors": [f"Not a file: {zp}"]}
    if not zipfile.is_zipfile(zp):
        return {"extracted": 0, "skipped": 0, "failed": 1, "errors": [f"Not a valid ZIP: {os.path.basename(zp)}"]}

    root = os.path.dirname(zp)
    base = os.path.basename(zp)
    stem = os.path.splitext(base)[0].strip()
    if not stem:
        _lg(f"SKIP (empty name): {base}")
        return {"extracted": 0, "skipped": 1, "failed": 0, "errors": []}
    if any(c in stem for c in '\\/:*?"<>|'):
        _lg(f"SKIP (invalid folder name): {base}")
        return {"extracted": 0, "skipped": 1, "failed": 0, "errors": []}
    dest = os.path.join(root, stem)
    if os.path.isfile(dest):
        _lg(f"SKIP (exists as file): {stem}")
        return {"extracted": 0, "skipped": 1, "failed": 0, "errors": []}
    if os.path.isdir(dest):
        try:
            if any(os.scandir(dest)):
                _lg(f"SKIP (folder not empty): {stem}")
                return {"extracted": 0, "skipped": 1, "failed": 0, "errors": []}
        except OSError as e:
            _lg(f"SKIP {stem}: {e}")
            return {"extracted": 0, "skipped": 1, "failed": 0, "errors": []}
    # Avoid double-nesting: if every entry in the archive already lives under
    # a single top-level folder named "<stem>/", extract beside the zip so we
    # get "<stem>/…" instead of "<stem>/<stem>/…".
    target = root if _zip_wraps_own_stem(zp, stem) else dest
    ok, msg = unpack_zip_to_dir(zp, target)
    if ok:
        _lg(f"Extracted: {base} → {stem}{os.sep}")
        return {"extracted": 1, "skipped": 0, "failed": 0, "errors": []}
    _lg(f"FAIL: {base} — {msg}")
    return {"extracted": 0, "skipped": 0, "failed": 1, "errors": [f"{base}: {msg}"]}


def zip_each_immediate_subfolder_to_sibling_archives(
    folder: str,
    *,
    log: Callable[[str], None] | None = None,
) -> dict[str, int | list[str]]:
    """
    For each immediate child **directory** of ``folder``, write ``<name>.zip`` in ``folder``
    (same directory as the folder), containing that subtree with archive paths relative to
    ``<name>/``. Files directly under ``folder`` are not archived. An existing ``<name>.zip``
    is replaced. Returns keys: zipped, skipped_root_files, failed, errors.
    """
    root = os.path.abspath(folder)
    if not os.path.isdir(root):
        return {"zipped": 0, "skipped_root_files": 0, "failed": 0, "errors": ["Not a directory."]}

    def _lg(msg: str) -> None:
        if log:
            log(msg)

    dir_names: list[str] = []
    skipped_root_files = 0
    try:
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                dir_names.append(name)
            elif os.path.isfile(p):
                skipped_root_files += 1
    except OSError as e:
        return {"zipped": 0, "skipped_root_files": 0, "failed": 0, "errors": [str(e)]}

    dir_names.sort(key=lambda n: n.lower())
    zipped = 0
    failed = 0
    errors: list[str] = []

    for name in dir_names:
        invalid = set('\\/:*?"<>|')
        if any(c in name for c in invalid):
            failed += 1
            msg = "name contains invalid characters"
            errors.append(f"{name}: {msg}")
            _lg(f"FAIL {name}: {msg}")
            continue
        base_name = os.path.join(root, name)
        zip_path = base_name + ".zip"
        try:
            arc = shutil.make_archive(base_name, "zip", root_dir=root, base_dir=name)
            zipped += 1
            _lg(f"Zipped: {name} → {os.path.basename(arc)}")
        except OSError as e:
            failed += 1
            errors.append(f"{name}: {e}")
            _lg(f"FAIL {name}: {e}")
            if os.path.isfile(zip_path):
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
        except Exception as e:
            failed += 1
            errors.append(f"{name}: {e}")
            _lg(f"FAIL {name}: {e}")
            if os.path.isfile(zip_path):
                try:
                    os.remove(zip_path)
                except OSError:
                    pass

    return {
        "zipped": zipped,
        "skipped_root_files": skipped_root_files,
        "failed": failed,
        "errors": errors,
    }


def safe_rename(src: str, new_basename: str) -> tuple[bool, str]:
    """Rename a file or folder within the same directory; new_basename without path."""
    new_basename = new_basename.strip()
    if not new_basename or "/" in new_basename or "\\" in new_basename:
        return False, "Invalid name (no path separators)."
    invalid = set('\\/:*?"<>|')
    if any(c in new_basename for c in invalid):
        return False, "Name contains invalid characters."
    parent = os.path.dirname(os.path.abspath(src))
    dst = os.path.join(parent, new_basename)
    if os.path.abspath(src) == os.path.abspath(dst):
        return True, "Unchanged."
    # On Windows the filesystem is case-insensitive, so os.path.exists(dst) returns
    # True even when only the capitalisation changed (e.g. "file.pdf" → "File.pdf").
    # Guard with normcase so that case-only renames are allowed through to os.rename().
    if os.path.exists(dst) and os.path.normcase(os.path.abspath(dst)) != os.path.normcase(os.path.abspath(src)):
        return False, "A file with that name already exists."
    # Preview keeps PyMuPDF documents in _PDF_DOC_CACHE; without this, os.rename can
    # raise WinError 32 ("used by another process") on PDFs the user was viewing.
    if src.lower().endswith(".pdf"):
        invalidate_pdf_doc_cache(src)
    try:
        os.rename(src, dst)
        return True, dst
    except OSError as e:
        return False, str(e)


def list_files(folder: str, include_subfolders: bool) -> list[str]:
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        return []
    out: list[str] = []
    if include_subfolders:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                out.append(os.path.join(root, f))
    else:
        for name in os.listdir(folder):
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                out.append(p)
    out.sort(key=lambda p: p.lower())
    return out


def normalize_name_for_similarity(name: str) -> str:
    """Lowercase + letters/digits only, for comparing file stems to folder names."""
    return "".join(c for c in name.casefold() if c.isalnum())


def name_similarity(file_stem: str, folder_name: str) -> float:
    """
    0.0–1.0 similarity between a filename stem (no extension) and a folder name.
    Exact normalized match → 1.0; otherwise SequenceMatcher on normalized strings.
    """
    a = normalize_name_for_similarity(file_stem)
    b = normalize_name_for_similarity(folder_name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def plan_moves_into_similar_folders(
    root: str,
    min_score: float,
    min_margin: float = 0.04,
) -> list[tuple[str, str, str]]:
    """
    Walk `root`: in each directory, match loose files to immediate subfolders by name.
    Returns [(src_path, dest_path, reason), ...]. Skips if destination path already exists
    or if two folders tie within `min_margin` (ambiguous).
    """
    root = os.path.abspath(root)
    out: list[tuple[str, str, str]] = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirs_here: list[tuple[str, str]] = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if os.path.isdir(full):
                dirs_here.append((full, d))
        files_here: list[str] = []
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            if os.path.isfile(fp):
                files_here.append(fp)
        if not dirs_here or not files_here:
            continue
        for fp in files_here:
            stem = Path(fp).stem
            best_path: str | None = None
            best_score = -1.0
            second_score = -1.0
            for folder_full, folder_base in dirs_here:
                sc = name_similarity(stem, folder_base)
                if sc > best_score:
                    second_score = best_score
                    best_score = sc
                    best_path = folder_full
                elif sc > second_score:
                    second_score = sc
            if best_path is None or best_score + 1e-9 < min_score:
                continue
            if second_score >= 0 and (best_score - second_score) + 1e-9 < min_margin:
                continue
            dest = os.path.join(best_path, os.path.basename(fp))
            if os.path.exists(dest):
                continue
            reason = f"~{best_score:.0%}: '{stem}' -> folder '{os.path.basename(best_path)}'"
            out.append((fp, dest, reason))
    return out


def apply_file_moves(
    moves: list[tuple[str, str, str]],
    log: Callable[[str], None],
) -> dict[str, int]:
    """Execute moves from plan_moves_into_similar_folders. log() each line."""
    stats = {"moved": 0, "errors": 0, "skipped": 0}
    for src, dst, note in moves:
        if not os.path.isfile(src):
            stats["skipped"] += 1
            log(f"SKIP missing: {src}")
            continue
        if os.path.exists(dst):
            stats["skipped"] += 1
            log(f"SKIP exists: {dst}")
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            stats["moved"] += 1
            log(f"MOVED: {src}  →  {dst}  ({note})")
        except OSError as e:
            stats["errors"] += 1
            log(f"ERROR: {src} — {e}")
    return stats


# --- “Open with” (Windows registry + shell picker; best-effort elsewhere) ---


def open_path_system_default(path: str) -> tuple[bool, str]:
    """Open a file with the OS default handler."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "Not a file."
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", path], close_fds=True)
        return True, ""
    except OSError as e:
        return False, str(e)


def run_detached_argv(argv: Sequence[str]) -> tuple[bool, str]:
    """Start a process with argument list (no shell)."""
    lst = list(argv)
    if not lst:
        return False, "Empty command."
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        subprocess.Popen(lst, close_fds=True, creationflags=flags)
        return True, ""
    except OSError as e:
        return False, str(e)


def find_adobe_acrobat_executable() -> str | None:
    """Return Acrobat.exe / Reader (Windows), Acrobat .app (macOS), or PATH; None if not found."""
    import glob as _glob

    def _pick(candidates: list[str]) -> str | None:
        if not candidates:
            return None
        return max(candidates, key=lambda p: os.path.getmtime(p))

    if sys.platform == "win32":
        roots: list[str] = []
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            v = os.environ.get(key)
            if v:
                roots.append(v)
        roots.extend([r"C:\Program Files", r"C:\Program Files (x86)"])
        seen: set[str] = set()
        full_acrobat: list[str] = []
        reader: list[str] = []
        for root in roots:
            adobe = os.path.join(root, "Adobe")
            if not os.path.isdir(adobe):
                continue
            for p in _glob.glob(os.path.join(adobe, "Acrobat*", "Acrobat", "Acrobat.exe")):
                ap = os.path.normpath(p)
                if os.path.isfile(ap) and ap.lower() not in seen:
                    seen.add(ap.lower())
                    full_acrobat.append(ap)
            for p in _glob.glob(os.path.join(adobe, "Acrobat Reader*", "Reader", "AcroRd32.exe")):
                ap = os.path.normpath(p)
                if os.path.isfile(ap) and ap.lower() not in seen:
                    seen.add(ap.lower())
                    reader.append(ap)
        pick = _pick(full_acrobat) or _pick(reader)
        if pick:
            return pick
        for name in ("Acrobat.exe", "AcroRd32.exe"):
            w = shutil.which(name)
            if w and os.path.isfile(w):
                return os.path.normpath(w)
        return None

    if sys.platform == "darwin":
        apps = "/Applications"
        if not os.path.isdir(apps):
            return None
        bundles: list[str] = []
        for name in os.listdir(apps):
            if not name.endswith(".app"):
                continue
            low = name.lower()
            if "acrobat" not in low:
                continue
            full = os.path.join(apps, name)
            if os.path.isdir(full):
                bundles.append(full)
        if not bundles:
            return None
        full_apps = [b for b in bundles if "reader" not in os.path.basename(b).lower()]
        return _pick(full_apps) or _pick(bundles)

    for name in ("acrobat", "Acrobat", "acroread"):
        w = shutil.which(name)
        if w and os.path.isfile(w):
            return w
    return None


def open_paths_with_acrobat(
    paths: Sequence[str],
    log: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Open one or more PDF files in Adobe Acrobat (or Reader if Acrobat is not installed)."""
    exe = find_adobe_acrobat_executable()
    if not exe:
        return False, (
            "Adobe Acrobat was not found.\n\n"
            "Install Acrobat or Acrobat Reader, or add Acrobat.exe to your PATH."
        )
    valid: list[str] = []
    for p in paths:
        if not p or not str(p).lower().endswith(".pdf"):
            continue
        ap = os.path.abspath(os.path.normpath(p))
        if os.path.isfile(ap):
            valid.append(ap)
    if not valid:
        return False, "No PDF files to open."

    def _log(msg: str) -> None:
        if log:
            log(msg)

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", exe] + valid, close_fds=True)
            _log(f"Open with Acrobat: {len(valid)} PDF(s)")
            return True, ""
        ok, err = run_detached_argv([exe, *valid])
        if ok:
            if len(valid) == 1:
                _log(f"Open with Acrobat: {os.path.basename(valid[0])}")
            else:
                _log(f"Open with Acrobat: {len(valid)} PDF(s)")
            return True, ""
        return False, err or "Could not start Acrobat."
    except OSError as e:
        return False, str(e)


def _norm_assoc_ext(path: str) -> str:
    ext = Path(path).suffix.lower()
    return ext if ext else ".*"


def _win_expand_cmd(cmd: str) -> str:
    cmd = os.path.expandvars(cmd.strip())
    rep = {"%quote%": '"'}
    for k, v in rep.items():
        cmd = cmd.replace(k, v)
    return cmd


def _win_substitute_file_in_command(cmd: str, path: str) -> str:
    """Replace %1 / %L / %V etc. with a quoted path; append path if no placeholder."""
    np = os.path.normpath(path)
    quoted = f'"{np}"'
    s = _win_expand_cmd(cmd)
    if not s:
        return quoted
    if any(x in s for x in ('"', "%1", "%L", "%V", "%*", "%u", "%U")):
        s = s.replace('"%1"', quoted).replace('"%L"', quoted).replace('"%V"', quoted).replace('"%*"', quoted)
        s = s.replace("%1", quoted).replace("%L", quoted).replace("%V", quoted)
        s = s.replace("%*", quoted)
        s = s.replace("%u", quoted).replace("%U", quoted)
    if np not in s and quoted.strip('"') not in s:
        s = f"{s.rstrip()} {quoted}"
    return s


def _win_user_choice_progid(ext: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    if not ext.startswith("."):
        ext = "." + ext
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\UserChoice",
        ) as k:
            v, _ = winreg.QueryValueEx(k, "ProgId")  # type: ignore[attr-defined]
            return str(v) if v else None
    except OSError:
        return None


def _win_enum_openwith_progids(ext: str) -> list[str]:
    import winreg

    if not ext.startswith("."):
        ext = "." + ext
    seen: set[str] = set()
    ordered: list[str] = []

    def add(p: str) -> None:
        k = p.strip()
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)

    u = _win_user_choice_progid(ext)
    if u:
        add(u)
    for root, sub in (
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\OpenWithProgids"),
        (winreg.HKEY_CLASSES_ROOT, f"{ext}\\OpenWithProgids"),
        (winreg.HKEY_CLASSES_ROOT, rf"SystemFileAssociations\{ext}\OpenWithProgids"),
    ):
        try:
            with winreg.OpenKey(root, sub) as k:
                i = 0
                while True:
                    try:
                        name, _, _ = winreg.EnumValue(k, i)
                        if name:
                            add(name)
                        i += 1
                    except OSError:
                        break
                i = 0
                while True:
                    try:
                        sk = winreg.EnumKey(k, i)
                        add(sk)
                        i += 1
                    except OSError:
                        break
        except OSError:
            pass
    return ordered


def _win_enum_openwith_list_exes(ext: str) -> list[str]:
    import winreg

    if not ext.startswith("."):
        ext = "." + ext
    out: list[str] = []
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\OpenWithList",
        ) as k:
            i = 0
            while True:
                try:
                    _name, val, _ = winreg.EnumValue(k, i)
                    if val and isinstance(val, str):
                        out.append(val.strip())
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def _win_progid_friendly_name(progid: str) -> str:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid) as k:
            try:
                d = winreg.QueryValue(k, None)
                if d and isinstance(d, str) and not d.startswith("@"):
                    return d.strip() or progid
            except OSError:
                pass
    except OSError:
        pass
    return progid


def _win_read_open_command(progid_or_path: str, *, under_applications: bool = False) -> str | None:
    """Read the Open command string for a ProgID or exe basename from HKEY_CLASSES_ROOT.

    If under_applications is True, looks under HKCR\\Applications\\<basename>.
    Returns the raw command string (e.g. ``"C:\\\\Program Files\\\\App\\\\app.exe" "%1"``) or None.
    """
    try:
        import winreg  # type: ignore
    except ImportError:
        return None

    if under_applications:
        rel = rf"Applications\{os.path.basename(progid_or_path)}\shell\open\command"
    else:
        rel = rf"{progid_or_path}\shell\open\command"
    for rp in (rel, rel.replace(r"\shell\open\command", r"\shell\Open\command")):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rp) as k:
                cmd, _ = winreg.QueryValueEx(k, None)
                if isinstance(cmd, str) and cmd.strip():
                    return cmd.strip()
        except OSError:
            continue
    return None


def _win_resolve_exe(argv0: str) -> str | None:
    """Given a possibly-quoted command's first argv, return the absolute path to the exe if it exists."""
    if not argv0:
        return None
    cand = _win_expand_cmd(argv0).strip().strip('"')
    if cand and os.path.isfile(cand):
        return cand
    # Try without trailing args
    try:
        parts = shlex.split(argv0, posix=False)
    except ValueError:
        parts = [argv0]
    if parts:
        first = parts[0].strip('"')
        first = _win_expand_cmd(first)
        if first and os.path.isfile(first):
            return first
        # Fall back to PATH lookup
        which = shutil.which(first)
        if which:
            return which
    return None


def windows_open_with_menu_commands(path: str, *, max_items: int = 22) -> list[tuple[str, list[str]]]:
    """Build the right-click 'Open with X' submenu for a file.

    Returns a list of (label, argv) tuples. Each argv is a list ready for
    subprocess (e.g. ['C:\\\\Path\\\\app.exe', path]). Empty list on non-Windows
    or when the registry has no entries.
    """
    if sys.platform != "win32":
        return []
    if not path or not os.path.exists(path):
        return []
    ext = _norm_assoc_ext(path)
    if not ext:
        return []
    seen_exes: set[str] = set()
    items: list[tuple[str, list[str]]] = []

    # 1) UserChoice ProgID (default app)
    try:
        progid = _win_user_choice_progid(ext)
        if progid:
            cmd = _win_read_open_command(progid)
            if cmd:
                resolved = _win_substitute_file_in_command(cmd, path)
                try:
                    argv = shlex.split(resolved, posix=False)
                except ValueError:
                    argv = [resolved]
                if argv:
                    exe = _win_resolve_exe(argv[0])
                    if exe and exe.lower() not in seen_exes:
                        seen_exes.add(exe.lower())
                        argv[0] = exe
                        items.append((_win_progid_friendly_name(progid), argv))
    except Exception:
        pass

    # 2) Other ProgIDs offered for this extension
    try:
        for progid in _win_enum_openwith_progids(ext):
            if len(items) >= max_items:
                break
            cmd = _win_read_open_command(progid)
            if not cmd:
                continue
            resolved = _win_substitute_file_in_command(cmd, path)
            try:
                argv = shlex.split(resolved, posix=False)
            except ValueError:
                argv = [resolved]
            if not argv:
                continue
            exe = _win_resolve_exe(argv[0])
            if not exe or exe.lower() in seen_exes:
                continue
            seen_exes.add(exe.lower())
            argv[0] = exe
            label = _win_progid_friendly_name(progid)
            items.append((label, argv))
    except Exception:
        pass

    # 3) Bare exes registered under OpenWithList
    try:
        for exe_hint in _win_enum_openwith_list_exes(ext):
            if len(items) >= max_items:
                break
            cmd = _win_read_open_command(exe_hint, under_applications=True)
            if cmd:
                resolved = _win_substitute_file_in_command(cmd, path)
                try:
                    argv = shlex.split(resolved, posix=False)
                except ValueError:
                    argv = [resolved]
                if argv:
                    exe = _win_resolve_exe(argv[0])
                    if exe and exe.lower() not in seen_exes:
                        seen_exes.add(exe.lower())
                        argv[0] = exe
                        items.append((_label_for_openwith_exe(exe_hint), argv))
                        continue
            # Fallback: try to resolve the exe by name and pass the path directly.
            exe = _win_resolve_exe(exe_hint)
            if exe and exe.lower() not in seen_exes:
                seen_exes.add(exe.lower())
                items.append((_label_for_openwith_exe(exe_hint), [exe, path]))
    except Exception:
        pass

    return items[:max_items]


def _label_for_openwith_exe(exe_hint: str) -> str:
    """Friendly menu label for an exe (e.g. 'notepad.exe' -> 'Notepad')."""
    base = os.path.basename(exe_hint or "").strip()
    if not base:
        return "Application"
    stem, _ = os.path.splitext(base)
    if not stem:
        return base
    # Title-case multi-word names
    parts = re.split(r"[\s_\-]+", stem) if any(ch in stem for ch in " _-") else [stem]
    pretty = " ".join(p[:1].upper() + p[1:] for p in parts if p)
    return pretty or base


def open_with_application_picker(path: str) -> tuple[bool, str]:
    """Open the OS 'Open with…' application picker for a file.

    On Windows, uses the shell32 OpenAs_RunDLL API. On macOS, opens the file
    with the default app (no picker available without UI). On Linux, tries
    mimeopen --ask. Returns (ok, error_or_empty_string).
    """
    if not path:
        return False, "No path provided."
    if not os.path.exists(path):
        return False, f"Path not found: {path}"

    if sys.platform == "win32":
        try:
            argv = [
                "rundll32.exe",
                "shell32.dll,OpenAs_RunDLL",
                path,
            ]
            ok, err = run_detached_argv(argv)
            return (ok, err if not ok else "")
        except Exception as e:
            return False, f"Could not open Windows picker: {e}"

    if sys.platform == "darwin":
        # macOS has no programmatic "Open with…" picker without UI scripting; open with default app.
        try:
            ok, err = run_detached_argv(["open", path])
            return (ok, err if not ok else "")
        except Exception as e:
            return False, str(e)

    # Linux / other Unix: try mimeopen --ask (from xdg-utils / mimeo) then xdg-open.
    if shutil.which("mimeopen"):
        ok, err = run_detached_argv(["mimeopen", "--ask", path])
        if ok:
            return True, ""
    if shutil.which("xdg-open"):
        ok, err = run_detached_argv(["xdg-open", path])
        return (ok, err if not ok else "")
    return False, "No application picker is available on this OS."


def _windows_explorer_shell_context_menu_opt_in() -> bool:
    """Native Explorer ``TrackPopupMenu`` on a Tk HWND can hard-crash the process; off unless opted in."""
    v = os.environ.get("SFM_WIN_EXPLORER_CONTEXT_MENU", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def can_windows_shell_context_menu(paths: list[str]) -> bool:
    """True if ``windows_shell_track_context_menu`` may run for these paths (pywin32 + same parent + opt-in)."""
    if sys.platform != "win32":
        return False
    if not _windows_explorer_shell_context_menu_opt_in():
        return False
    if not paths:
        return False
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    clean = [os.path.abspath(os.path.normpath(p)) for p in paths]
    if not all(os.path.isfile(p) or os.path.isdir(p) for p in clean):
        return False
    parents = {os.path.normcase(os.path.dirname(p)) for p in clean}
    return len(parents) == 1


def windows_shell_track_context_menu(
    paths: list[str],
    hwnd_parent: int,
    x_root: int,
    y_root: int,
) -> tuple[bool, str, bool]:
    """
    Show the same default **Windows Explorer** context menu as for the given paths
    (files and/or folders), **all in one parent directory**. Requires pywin32.

    ``hwnd_parent`` should be a **top-level** Win32 HWND (e.g. ``int(toplevel.winfo_id())``).
    Do not pass a child widget HWND here: subclassing the shell menu WndProc on Tk children can crash.

    Returns ``(success, message, invoke_ran)``. The third flag is ``True`` only when the user chose
    a menu command (so callers can refresh the UI); dismissing the menu without choosing refreshes nothing.

    This path is only offered when ``SFM_WIN_EXPLORER_CONTEXT_MENU`` is set to ``1`` / ``true`` / ``yes`` / ``on``
    (see ``can_windows_shell_context_menu``): shell ``TrackPopupMenu`` with a Tk parent window can otherwise
    terminate the process from native code.
    """
    if sys.platform != "win32":
        return False, "Windows shell menu is only available on Windows.", False
    if not paths:
        return False, "No paths.", False
    import ctypes
    from ctypes import wintypes

    import pythoncom
    import win32con
    import win32gui
    from win32com.shell import shell, shellcon

    clean = [os.path.abspath(os.path.normpath(p)) for p in paths]
    if not all(os.path.isfile(p) or os.path.isdir(p) for p in clean):
        return False, "Only files and folders on disk are supported.", False
    parents = {os.path.normcase(os.path.dirname(p)) for p in clean}
    if len(parents) != 1:
        return False, "Select items from the same folder for the Windows Explorer menu.", False

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    def _iid(s: str) -> "_GUID":
        import uuid

        return _GUID.from_buffer_copy(uuid.UUID(s).bytes_le)

    IID_IShellFolder = _iid("{000214E6-0000-0000-C000-000000000046}")

    SHParseDisplayName = shell32.SHParseDisplayName
    SHParseDisplayName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    SHParseDisplayName.restype = ctypes.HRESULT

    SHBindToParent = getattr(shell32, "SHBindToParent")
    SHBindToParent.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    SHBindToParent.restype = ctypes.HRESULT

    try:
        pythoncom.CoInitialize()
    except pythoncom.com_error:
        pass

    child_lists: list = []
    parent_sf_ptr: int | None = None
    try:
        for p in clean:
            ppidl = ctypes.c_void_p()
            sfgao = wintypes.DWORD(0)
            hr = SHParseDisplayName(p, None, ctypes.byref(ppidl), 0, ctypes.byref(sfgao))
            if hr != 0 or not ppidl.value:
                return False, f"Could not resolve path: {p}", False
            psf = ctypes.c_void_p()
            pchild = ctypes.c_void_p()
            hr2 = SHBindToParent(ppidl, ctypes.byref(IID_IShellFolder), ctypes.byref(psf), ctypes.byref(pchild))
            ole32.CoTaskMemFree(ppidl)
            if hr2 != 0 or not psf.value or not pchild.value:
                return False, f"Could not bind shell folder for: {p}", False
            if parent_sf_ptr is None:
                parent_sf_ptr = psf.value
            elif psf.value != parent_sf_ptr:
                ole32.CoTaskMemFree(pchild)
                return False, "Shell folder mismatch (paths must share the same parent folder).", False
            cl = shell.AddressAsPIDL(pchild.value)
            ole32.CoTaskMemFree(pchild)
            child_lists.append(cl)

        if not parent_sf_ptr:
            return False, "Internal error (no shell folder).", False

        sf = pythoncom.ObjectFromAddress(parent_sf_ptr, shell.IID_IShellFolder)
        r = sf.GetUIObjectOf(hwnd_parent, child_lists, shell.IID_IContextMenu, 0)
        cm = r[1] if isinstance(r, tuple) else r

        hmenu = win32gui.CreatePopupMenu()
        id_cmd_first = 1
        flags = shellcon.CMF_NORMAL | shellcon.CMF_CANRENAME
        cm.QueryContextMenu(hmenu, 0, id_cmd_first, 0x7FFF, flags)

        # Do not SetWindowLong / IContextMenu2.HandleMenuMsg on a Tk child HWND: it replaces Tcl's
        # WndProc and crashes the process. Owner-draw / some shell extensions may be limited.
        tpm = win32con.TPM_LEFTALIGN | win32con.TPM_RETURNCMD | win32con.TPM_RIGHTBUTTON
        try:
            sel = win32gui.TrackPopupMenu(hmenu, tpm, int(x_root), int(y_root), 0, hwnd_parent, None)
        finally:
            win32gui.DestroyMenu(hmenu)

        invoke_ran = False
        if sel and sel >= id_cmd_first:
            off = sel - id_cmd_first
            ci = (0, hwnd_parent, off, None, None, 0, 0, 0)
            cm.InvokeCommand(ci)
            invoke_ran = True
        return True, "", invoke_ran
    except pythoncom.com_error as e:
        return False, str(e), False
    except Exception as e:
        return False, str(e), False


# ── Document Checklist Report ─────────────────────────────────────────────────

# Master checklist for Korean student visa applications
# (item_no, display_name, required_count, filename_keywords)
_VISA_CHECKLIST = [
    (2,  "Passport Copy",                               1, ["여권", "passport"]),
    (3,  "Self Birth Certificate",                      1, ["출생증명서", "birth certificate"]),
    (4,  "E-Apostille with Certificate & Transcript",   1, ["아포스티유", "apostille", "e-apostille"]),
    (5,  "Applicant and Parents' NID",                  1, ["신분증", "nid", "id card", "applicant id", "parents nid"]),
    (6,  "Family Relationship Certificate",             1, ["가족관계", "family relationship"]),
    (7,  "Language Certificate",                         1, ["아이엘츠", "ielts", "토픽", "topik", "toefl", "pte", "duolingo", "jlpt", "toeic", "cambridge", "language certificate", "언어능력"]),
    (8,  "Trade License / Employment Certificate",      1, ["사업자", "trade license", "재직", "employment"]),
    (9,  "TIN Certificate",                             1, ["납세자", "tin certificate"]),
    (10, "Tax Certificate (2 years required)",          2, ["소득금액 증명서", "tax certificate", "소득금액증명서"]),
    (11, "Acknowledgement Certificate (2 years req.)",  2, ["신고 확인서", "acknowledgment", "acknowledgement", "소득금액신고"]),
    (12, "Bank Statement",                              1, ["거래내역", "bank statement", "은행 거래"]),
    (13, "Bank Solvency Certificate",                   1, ["잔고", "solvency", "bank balance", "은행잔고"]),
    (14, "Medical Test Report",                         1, ["건강검진", "medical", "결핵", "tb test"]),
]

# Documents from which we extract issue date + calculate validity period
_DATE_DOCS = {
    "bank_solvency": {
        "keywords": ["잔고", "solvency", "bank balance", "은행잔고"],
        "validity_days": 30,   # 1 month from issue date
        "label": "Bank Solvency Certificate",
    },
    "apostille": {
        "keywords": ["아포스티유", "apostille", "e-apostille"],
        "validity_days": 180,  # 6 months from issue date
        "label": "E-Apostille",
    },
    "family_cert": {
        "keywords": ["가족관계", "family relationship"],
        "validity_days": 180,  # 6 months from issue date
        "label": "Family Relationship Certificate",
    },
}


def _extract_issue_date_gpt4o(pdf_path: str, api_key: str, log) -> str:
    """Render first page of PDF and ask GPT-4o for the issue date.
    Returns 'YYYY-MM-DD' string, or 'UNKNOWN'."""
    fitz = get_fitz()
    if not fitz:
        return "UNKNOWN"
    try:
        import base64 as _b64
        import re as _re
        try:
            import openai as _openai
        except ImportError:
            return "UNKNOWN"

        doc = fitz.open(pdf_path)
        page = doc.load_page(0)
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        doc.close()
        b64 = _b64.b64encode(img_bytes).decode("ascii")

        client = _openai.OpenAI(api_key=api_key.strip())
        resp = _gpt4o_create(client,
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                            "detail": "high",
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Look at this document. Find the ISSUE DATE — the date this "
                            "certificate was issued, stamped, or certified "
                            "(NOT a validity or expiry date). "
                            "Return ONLY the date in YYYY-MM-DD format. "
                            "If no clear issue date is found, return exactly: UNKNOWN"
                        ),
                    },
                ],
            }],
            temperature=0,
            max_tokens=20,
        )
        raw = (resp.choices[0].message.content or "").strip().strip("\"'")
        if _re.match(r"\d{4}-\d{2}-\d{2}", raw):
            return raw[:10]
        return "UNKNOWN"
    except Exception as e:
        log(f"  Date extraction failed for {os.path.basename(pdf_path)}: {e}")
        return "UNKNOWN"


def _names_similar(a: str, b: str) -> tuple[float, str]:
    """Compare two romanised Bangladeshi names.  Returns (score 0-1, verdict).

    score >= 0.85 → MATCH
    score >= 0.60 → LIKELY MATCH  (common romanisation variation)
    score <  0.60 → MISMATCH

    Normalisation applied before comparison:
    - lowercase, strip punctuation
    - expand common abbreviations: Md→Mohammad, Mst→Mostt, etc.
    - collapse double-letters: hossain/hussain, akter/aktar/akhter, rahman/rahaman
    """
    import re as _re

    def _norm(s: str) -> str:
        s = s.lower().strip()
        s = _re.sub(r"[.\-,()'\"]", " ", s)
        s = _re.sub(r"\s+", " ", s).strip()
        # Expand common Bangladeshi abbreviations
        s = _re.sub(r"\bmd\b",  "mohammad", s)
        s = _re.sub(r"\bmds\b", "mohammad", s)
        s = _re.sub(r"\bmst\b", "mosammat", s)
        s = _re.sub(r"\bmrs\b", "mosammat", s)
        s = _re.sub(r"\bsk\b",  "sheikh",   s)
        # Flatten common spelling variants to a canonical form
        _VARIANTS = [
            (r"hus+[ae]in|hos+[ae]in|h[ao]s+an", "hossain"),
            (r"rahaman|rahman|rahmaan",            "rahman"),
            (r"akh?t[ae]r|act[ae]r",               "akter"),
            (r"kh[ao]n|khaan",                     "khan"),
            (r"mohamm?[ae]d|muhammed|muhammed",    "mohammad"),
            (r"b[ie]gum",                          "begum"),
            (r"khatun|khaatum|khatoon",             "khatun"),
            (r"ul[- ]islam|ulislam",               "ul islam"),
            (r"abd[ou]l|abdu",                     "abdul"),
        ]
        for pat, repl in _VARIANTS:
            s = _re.sub(pat, repl, s)
        return s

    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0, "UNKNOWN"
    if na == nb:
        return 1.0, "MATCH"

    # Levenshtein distance (fast enough for names — max ~60 chars)
    la, lb = len(na), len(nb)
    prev = list(range(lb + 1))
    for i, ca in enumerate(na, 1):
        curr = [i]
        for j, cb in enumerate(nb, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1,
                            prev[j - 1] + (0 if ca == cb else 1)))
        prev = curr
    dist = prev[lb]
    score = 1.0 - dist / max(la, lb)

    if score >= 0.85:
        verdict = "MATCH"
    elif score >= 0.60:
        verdict = "LIKELY MATCH"
    else:
        verdict = "MISMATCH"
    return round(score, 3), verdict


def _extract_names_gpt4o(
    pdf_path: str,
    api_key: str,
    log,
    doc_hint: str = "",
) -> dict:
    """Render the first page (+ page 2 if exists) of a PDF and ask GPT-4o
    to extract the names of the applicant, father, and mother.

    Returns dict: {"applicant": str, "father": str, "mother": str}
    Any key not found on the document is returned as "".

    doc_hint: e.g. "applicant NID", "father NID", "passport", "family certificate",
              "SSC certificate", "HSC transcript".  Tells GPT-4o which person to focus on.
    """
    fitz = get_fitz()
    if not fitz or not api_key:
        return {"applicant": "", "father": "", "mother": ""}

    try:
        import base64 as _b64
        import json as _json
        try:
            import openai as _openai
        except ImportError:
            return {"applicant": "", "father": "", "mother": ""}

        doc = fitz.open(pdf_path)
        n_pages = len(doc)
        mat = fitz.Matrix(2.0, 2.0)

        # Build image content blocks for pages 1 (and 2 if present)
        img_blocks = []
        for pi in range(min(n_pages, 2)):
            pix = doc.load_page(pi).get_pixmap(matrix=mat, alpha=False)
            b64 = _b64.b64encode(pix.tobytes("png")).decode("ascii")
            img_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
            })
        doc.close()

        hint_line = (
            f"This is a scanned '{doc_hint}' document.\n" if doc_hint else ""
        )

        prompt = (
            f"{hint_line}"
            "Look at the document image(s) and find these three names:\n"
            "  1. Applicant / Student / Account Holder / Person Named (the main subject)\n"
            "  2. Father's name (may be labelled Father, Father's Name, পিতা, etc.)\n"
            "  3. Mother's name (may be labelled Mother, Mother's Name, মাতা, etc.)\n\n"
            "Rules:\n"
            "- Return the ENGLISH romanised spelling exactly as printed on the document.\n"
            "- If the document is a PARENT's ID card, the 'applicant' field should be "
            "  the parent's own name (not the student).\n"
            "- If a name is not shown at all, return an empty string \"\" for that field.\n"
            "- Return ONLY valid JSON — no markdown, no explanation:\n"
            '  {"applicant": "...", "father": "...", "mother": "..."}'
        )

        client = _openai.OpenAI(api_key=api_key.strip())
        resp = _gpt4o_create(client,
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": img_blocks + [{"type": "text", "text": prompt}],
            }],
            temperature=0,
            max_tokens=120,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Strip markdown fences if GPT-4o added them
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = _json.loads(raw)
        result = {
            "applicant": str(data.get("applicant") or "").strip(),
            "father":    str(data.get("father")    or "").strip(),
            "mother":    str(data.get("mother")    or "").strip(),
        }
        log(f"  Names from {os.path.basename(pdf_path)}: {result}")
        return result

    except Exception as e:
        log(f"  Name extraction failed for {os.path.basename(pdf_path)}: {e}")
        return {"applicant": "", "father": "", "mother": ""}


def get_expiry_heatmap_data(
    out_paths: list,
    api_key: str,
    log,
) -> dict:
    """
    Extract issue/expiry date information from key documents using GPT-4o.

    Returns the same ``date_results`` dict that ``generate_checklist_report``
    computes internally, so the heat-map widget can surface it without having
    to run the full report generation.

    Keys are the _DATE_DOCS keys ("bank_solvency", "apostille", "family_cert").
    Each value is a dict with keys: label, file, issue, expiry, status, detail.
    """
    from datetime import datetime as _dt, timedelta as _td, date as _date
    today = _date.today()

    date_results: dict = {}
    for key, cfg in _DATE_DOCS.items():
        match = next((p for p in out_paths if _file_matches(p, cfg["keywords"])), None)
        if not match:
            continue
        log(f"  Expiry scan: {os.path.basename(match)}…")
        issue_str = _extract_issue_date_gpt4o(match, api_key, log)
        if issue_str == "UNKNOWN":
            date_results[key] = {
                "label": cfg["label"], "file": os.path.basename(match),
                "issue": "?", "expiry": "?",
                "status": "UNKNOWN", "detail": "Could not read date",
            }
            continue
        try:
            issue_date = _dt.strptime(issue_str, "%Y-%m-%d").date()
            expiry = issue_date + _td(days=cfg["validity_days"])
            days_left = (expiry - today).days
            if days_left > 14:
                vstatus, vdetail = "VALID", f"{days_left} days remaining"
            elif days_left > 0:
                vstatus, vdetail = "EXPIRING SOON", f"{days_left} days remaining"
            elif days_left == 0:
                vstatus, vdetail = "EXPIRES TODAY", ""
            else:
                vstatus, vdetail = "EXPIRED", f"{abs(days_left)} days ago"
            date_results[key] = {
                "label": cfg["label"],
                "file": os.path.basename(match),
                "issue": issue_str,
                "expiry": expiry.strftime("%Y-%m-%d"),
                "status": vstatus,
                "detail": vdetail,
                "days_left": days_left,
                "validity_days": cfg["validity_days"],
            }
        except ValueError:
            date_results[key] = {
                "label": cfg["label"], "file": os.path.basename(match),
                "issue": issue_str, "expiry": "?",
                "status": "PARSE ERROR", "detail": "Cannot parse date",
                "days_left": None,
            }
    return date_results


def generate_checklist_report(
    out_paths: list,
    api_key: str,
    log,
    report_dir: str = "",
    original_page_counts: list | None = None,
    source_pdf_paths: list | None = None,
    metadata: dict | None = None,
) -> str:
    """Check output PDFs against the Korean student visa checklist.

    Extracts issue dates (via GPT-4o) from bank solvency, apostille, and
    family relationship certificate, and calculates validity status.

    Writes a formatted Word (.docx) report and returns its path (or "" on failure).

    Args:
        out_paths: Output PDFs produced by Smart Split & Merge.
        api_key: OpenAI API key for GPT-4o date extraction.
        log: Logging callback.
        report_dir: Directory to save the report (defaults to dir of first out_path).
        original_page_counts: Page counts of each source PDF, for audit.
        source_pdf_paths: Paths of source PDFs (for display in audit section).
        metadata: Dict with optional keys: university, program, applicant, agency.
    """
    from datetime import datetime as _dt, timedelta as _td

    today = _dt.now().date()
    if not out_paths:
        return ""

    dest_dir = report_dir or os.path.dirname(out_paths[0])
    meta = metadata or {}

    def _stem_lower(p: str) -> str:
        return os.path.splitext(os.path.basename(p))[0].lower()

    def _file_matches(p: str, keywords: list) -> bool:
        s = _stem_lower(p)
        return any(kw.lower() in s for kw in keywords)

    # ── Phase 1: match output files to checklist items ────────────────────────
    checklist_rows = []   # (item_no, name, required, found_count, matched_files)
    for item_no, name, required, keywords in _VISA_CHECKLIST:
        matched = [p for p in out_paths if _file_matches(p, keywords)]
        checklist_rows.append((item_no, name, required, len(matched), matched))

    # ── Bundle detection ───────────────────────────────────────────────────────
    # When GPT-4o labels the first bundle page as "trade license" or "employment
    # certificate", the merged output PDF is named after that first label —
    # individual TIN / Tax / Ack filenames don't exist as separate files.
    # If a multi-page bundle-anchor file is found, items 9/10/11 are "covered."
    _BUNDLE_ANCHOR_KWS = [
        "사업자", "trade license", "business registration",
        "재직", "employment certificate",
        "납세자", "소득금액", "tin income tax",
    ]
    _BUNDLE_COVERED_ITEMS = {9, 10, 11}   # TIN, Tax, Acknowledgement

    bundle_cover_file: str | None = None
    for bf in out_paths:
        if _file_matches(bf, _BUNDLE_ANCHOR_KWS):
            try:
                if pdf_page_count(bf) > 1:
                    bundle_cover_file = bf
                    break
            except Exception:
                pass

    # Build final rows with bundle override
    final_rows = []
    for item_no, name, required, found, matched in checklist_rows:
        if found == 0 and item_no in _BUNDLE_COVERED_ITEMS and bundle_cover_file:
            final_rows.append((item_no, name, required, 1, [bundle_cover_file], True))
        else:
            final_rows.append((item_no, name, required, found, matched, False))

    # ── Page count audit ──────────────────────────────────────────────────────
    orig_total: int | None = sum(original_page_counts) if original_page_counts else None

    output_page_details: list[tuple[str, int]] = []
    total_out_pages = 0
    for p in out_paths:
        try:
            n = pdf_page_count(p)
        except Exception:
            n = 0
        output_page_details.append((os.path.basename(p), n))
        total_out_pages += n

    skipped_pages: int | None = (
        (orig_total - total_out_pages) if orig_total is not None else None
    )

    # ── Phase 2: extract dates from key documents ─────────────────────────────
    date_results: dict = {}
    for key, cfg in _DATE_DOCS.items():
        match = next((p for p in out_paths if _file_matches(p, cfg["keywords"])), None)
        if match:
            log(f"  Checklist: extracting date from {os.path.basename(match)}…")
            issue_str = _extract_issue_date_gpt4o(match, api_key, log)
            if issue_str != "UNKNOWN":
                try:
                    issue_date = _dt.strptime(issue_str, "%Y-%m-%d").date()
                    expiry = issue_date + _td(days=cfg["validity_days"])
                    days_left = (expiry - today).days
                    if days_left > 14:
                        vstatus, vdetail = "VALID", f"{days_left} days remaining"
                    elif days_left > 0:
                        vstatus, vdetail = "EXPIRING SOON", f"{days_left} days remaining"
                    elif days_left == 0:
                        vstatus, vdetail = "EXPIRES TODAY", ""
                    else:
                        vstatus, vdetail = "EXPIRED", f"{abs(days_left)} days ago"
                    date_results[key] = {
                        "label": cfg["label"],
                        "file": os.path.basename(match),
                        "issue": issue_str,
                        "expiry": expiry.strftime("%Y-%m-%d"),
                        "status": vstatus,
                        "detail": vdetail,
                    }
                except ValueError:
                    date_results[key] = {
                        "label": cfg["label"], "file": os.path.basename(match),
                        "issue": issue_str, "expiry": "?",
                        "status": "PARSE ERROR", "detail": "Cannot parse date",
                    }
            else:
                date_results[key] = {
                    "label": cfg["label"], "file": os.path.basename(match),
                    "issue": "NOT FOUND", "expiry": "?",
                    "status": "UNKNOWN", "detail": "Issue date not found in document",
                }
        else:
            date_results[key] = {
                "label": cfg["label"], "file": None,
                "issue": None, "expiry": None,
                "status": "MISSING", "detail": "Document not found in output",
            }

    # ── Phase 2.5: Name consistency check ────────────────────────────────────
    # Keywords used to find each document type in the output file list
    _NAME_SOURCES = [
        # (source_id, display_label, doc_hint_for_gpt4o, file_keywords, person)
        # person: which extracted field is "the person named on this doc"
        ("applicant_nid",    "Applicant NID",           "applicant NID card",
         ["지원자 신분증", "applicant id card", "applicant id"],            "applicant"),
        ("father_nid",       "Father NID",              "father NID card",
         ["아버지 신분증", "father id card", "father id"],                  "applicant"),
        ("mother_nid",       "Mother NID",              "mother NID card",
         ["어머니 신분증", "mother id card", "mother id"],                  "applicant"),
        ("family_cert",      "Family Certificate",      "family relationship certificate",
         ["가족관계", "family relationship"],                               "applicant"),
        ("passport",         "Applicant Passport",      "passport bio page",
         ["여권", "passport"],                                              "applicant"),
        ("ssc",              "SSC Certificate/Transcript",  "SSC academic certificate",
         ["중학교", "ssc"],                                                 "applicant"),
        ("hsc",              "HSC Certificate/Transcript",  "HSC academic certificate",
         ["고등학교", "hsc"],                                               "applicant"),
        ("bachelor",         "Bachelor Certificate/Transcript", "bachelor degree certificate",
         ["학사", "bachelor", "undergraduate"],                             "applicant"),
    ]

    # Exclude father/mother passports from passport match
    _EXCLUDE_PASSPORT_KWS = ["아버지 여권", "어머니 여권", "father passport", "mother passport"]

    def _find_doc_for_source(src_id: str, keywords: list[str]) -> str | None:
        """Return the first out_path matching any keyword (case-insensitive stem)."""
        for p in out_paths:
            s = os.path.splitext(os.path.basename(p))[0].lower()
            # Exclude parent passports from "passport" lookup
            if src_id == "passport":
                if any(ex.lower() in s for ex in _EXCLUDE_PASSPORT_KWS):
                    continue
            if any(kw.lower() in s for kw in keywords):
                return p
        return None

    # Extract names from each found document
    # name_data[source_id] = {"file": path|None, "applicant": "", "father": "", "mother": ""}
    name_data: dict[str, dict] = {}
    for src_id, label, hint, kws, _person in _NAME_SOURCES:
        found_path = _find_doc_for_source(src_id, kws)
        if found_path and api_key:
            log(f"  Name check: extracting from {label} ({os.path.basename(found_path)})…")
            names = _extract_names_gpt4o(found_path, api_key, log, doc_hint=hint)
        else:
            names = {"applicant": "", "father": "", "mother": ""}
        name_data[src_id] = {"file": found_path, **names}

    # ── Build comparison rows ─────────────────────────────────────────────────
    # For each (person_role, field_label, list_of_(source_id, extracted_name))
    # we compare every pair and flag mismatches + affidavit needs.

    def _name_from(src_id: str, field: str) -> str:
        return name_data.get(src_id, {}).get(field, "") or ""

    # ── APPLICANT name appearances ────────────────────────────────────────────
    applicant_name_sources = [
        ("Applicant NID",          _name_from("applicant_nid", "applicant")),
        ("Family Certificate",     _name_from("family_cert",   "applicant")),
        ("Passport",               _name_from("passport",      "applicant")),
        ("SSC Certificate",        _name_from("ssc",           "applicant")),
        ("HSC Certificate",        _name_from("hsc",           "applicant")),
        ("Bachelor Certificate",   _name_from("bachelor",      "applicant")),
    ]
    # ── FATHER name appearances ───────────────────────────────────────────────
    father_name_sources = [
        ("Applicant NID (father field)",  _name_from("applicant_nid", "father")),
        ("Father NID (own name)",         _name_from("father_nid",    "applicant")),
        ("Family Certificate (father)",   _name_from("family_cert",   "father")),
        ("Passport (father field)",       _name_from("passport",      "father")),
        ("SSC Certificate (father)",      _name_from("ssc",           "father")),
        ("HSC Certificate (father)",      _name_from("hsc",           "father")),
        ("Bachelor Certificate (father)", _name_from("bachelor",      "father")),
    ]
    # ── MOTHER name appearances ───────────────────────────────────────────────
    mother_name_sources = [
        ("Applicant NID (mother field)",  _name_from("applicant_nid", "mother")),
        ("Mother NID (own name)",         _name_from("mother_nid",    "applicant")),
        ("Family Certificate (mother)",   _name_from("family_cert",   "mother")),
        ("Passport (mother field)",       _name_from("passport",      "mother")),
        ("SSC Certificate (mother)",      _name_from("ssc",           "mother")),
        ("HSC Certificate (mother)",      _name_from("hsc",           "mother")),
        ("Bachelor Certificate (mother)", _name_from("bachelor",      "mother")),
    ]

    def _compare_group(
        sources: list[tuple[str, str]]
    ) -> list[dict]:
        """Compare all non-empty name sources against the first non-empty one.
        Returns list of row dicts: {source, name, score, verdict}
        """
        # Find the reference name (first source that has a name)
        ref_name = next((n for _, n in sources if n), "")
        ref_label = next((s for s, n in sources if n), "")

        rows = []
        for src_label, name in sources:
            if not name:
                rows.append({"source": src_label, "name": "—",
                             "score": None, "verdict": "NOT FOUND"})
                continue
            if src_label == ref_label:
                # This IS the reference — don't compare against itself
                rows.append({"source": src_label, "name": name,
                             "score": None, "verdict": "REFERENCE"})
            else:
                score, verdict = _names_similar(ref_name, name)
                rows.append({"source": src_label, "name": name,
                             "score": score, "verdict": verdict})
        return rows

    applicant_rows = _compare_group(applicant_name_sources)
    father_rows    = _compare_group(father_name_sources)
    mother_rows    = _compare_group(mother_name_sources)

    def _has_mismatch(rows: list[dict]) -> bool:
        return any(r["verdict"] == "MISMATCH" for r in rows)

    def _has_issue(rows: list[dict]) -> bool:
        return any(r["verdict"] in ("MISMATCH", "LIKELY MATCH") for r in rows)

    applicant_mismatch = _has_mismatch(applicant_rows)
    father_mismatch    = _has_mismatch(father_rows)
    mother_mismatch    = _has_mismatch(mother_rows)

    # ── Affidavit recommendations ─────────────────────────────────────────────
    affidavit_needed: list[str] = []
    if father_mismatch:
        affidavit_needed.append("AFFIDAVIT OF SAME NAME & SAME PERSON (Father)")
    if mother_mismatch:
        affidavit_needed.append("AFFIDAVIT OF SAME NAME & SAME PERSON (Mother)")
    if father_mismatch and mother_mismatch:
        affidavit_needed = ["AFFIDAVIT OF SAME NAME & SAME PERSON (Parents)"]
    if applicant_mismatch:
        affidavit_needed.append("Applicant name mismatch — verify NID vs Passport vs Academic docs")

    # ── Phase 3: build Word document ──────────────────────────────────────────
    try:
        from docx import Document as _DocX
        from docx.shared import Pt, RGBColor, Cm
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.oxml.ns import qn as _qn
        from docx.oxml import OxmlElement as _OxmlEl
    except ImportError as _ie:
        log(f"  python-docx not available ({_ie}) — cannot create Word report")
        return ""

    # ── colour palette ────────────────────────────────────────────────────────
    C_DARK_BLUE  = RGBColor(0x1E, 0x3A, 0x5F)
    C_MID_BLUE   = RGBColor(0x2B, 0x5F, 0x9E)
    C_LIGHT_BLUE = RGBColor(0xDC, 0xE8, 0xF7)
    C_WHITE      = RGBColor(0xFF, 0xFF, 0xFF)
    C_ROW_ALT    = RGBColor(0xF0, 0xF5, 0xFC)
    C_GREEN      = RGBColor(0x17, 0x6B, 0x2F)
    C_ORANGE     = RGBColor(0xC0, 0x6A, 0x00)
    C_RED        = RGBColor(0xB5, 0x1C, 0x1C)
    C_GREY       = RGBColor(0x50, 0x50, 0x50)
    C_LIGHT_GREY = RGBColor(0x80, 0x80, 0x80)

    def _hex(rgb: RGBColor) -> str:
        return f"{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"

    def _cell_bg(cell, rgb: RGBColor) -> None:
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        shd = _OxmlEl("w:shd")
        shd.set(_qn("w:val"), "clear")
        shd.set(_qn("w:color"), "auto")
        shd.set(_qn("w:fill"), _hex(rgb))
        tcPr.append(shd)

    def _para_shd(para, rgb: RGBColor) -> None:
        pPr = para._p.get_or_add_pPr()
        shd = _OxmlEl("w:shd")
        shd.set(_qn("w:val"), "clear")
        shd.set(_qn("w:color"), "auto")
        shd.set(_qn("w:fill"), _hex(rgb))
        pPr.append(shd)

    def _spacing(para, before_pt: float = 0, after_pt: float = 4) -> None:
        para.paragraph_format.space_before = Pt(before_pt)
        para.paragraph_format.space_after  = Pt(after_pt)

    def _bold_run(run, size_pt: float | None = None,
                  color: RGBColor | None = None, italic: bool = False) -> None:
        run.bold = True
        if italic:
            run.italic = True
        if size_pt:
            run.font.size = Pt(size_pt)
        if color:
            run.font.color.rgb = color

    doc = _DocX()

    # ── A4 margins ───────────────────────────────────────────────────────────
    sec = doc.sections[0]
    sec.page_width    = Cm(21)
    sec.page_height   = Cm(29.7)
    sec.left_margin   = Cm(2.0)
    sec.right_margin  = Cm(2.0)
    sec.top_margin    = Cm(2.0)
    sec.bottom_margin = Cm(2.0)

    # ── helper: section heading with underline ────────────────────────────────
    def _section_heading(text: str) -> None:
        p = doc.add_paragraph()
        _spacing(p, before_pt=12, after_pt=3)
        r = p.add_run(text.upper())
        _bold_run(r, size_pt=10, color=C_DARK_BLUE)
        pPr = p._p.get_or_add_pPr()
        pBdr = _OxmlEl("w:pBdr")
        bot = _OxmlEl("w:bottom")
        bot.set(_qn("w:val"), "single")
        bot.set(_qn("w:sz"), "6")
        bot.set(_qn("w:space"), "1")
        bot.set(_qn("w:color"), _hex(C_DARK_BLUE))
        pBdr.append(bot)
        pPr.append(pBdr)

    def _header_row(table, headers: list[str], widths_cm: list[float],
                    bg: RGBColor | None = None) -> None:
        bg = bg or C_DARK_BLUE
        row = table.rows[0]
        for i, (h, w) in enumerate(zip(headers, widths_cm)):
            cell = row.cells[i]
            cell.width = Cm(w)
            _cell_bg(cell, bg)
            p = cell.paragraphs[0]
            r = p.add_run(h)
            _bold_run(r, size_pt=9, color=C_WHITE)
            _spacing(p, before_pt=2, after_pt=2)

    # ═══════════════════════════════════════════════════════════════════════════
    # TITLE BANNER
    # ═══════════════════════════════════════════════════════════════════════════
    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _spacing(title_p, before_pt=0, after_pt=0)
    _para_shd(title_p, C_DARK_BLUE)
    tr = title_p.add_run("  STUDENT VISA DOCUMENT CHECKLIST REPORT  ")
    _bold_run(tr, size_pt=16, color=C_WHITE)

    sub_p = doc.add_paragraph()
    sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _spacing(sub_p, before_pt=0, after_pt=10)
    _para_shd(sub_p, C_MID_BLUE)
    sr = sub_p.add_run(
        f"  Generated: {_dt.now().strftime('%d %B %Y, %H:%M')}   •   "
        f"Report date: {today.strftime('%d %B %Y')}  "
    )
    sr.font.size = Pt(9)
    sr.font.color.rgb = RGBColor(0xD0, 0xE4, 0xFF)

    # ═══════════════════════════════════════════════════════════════════════════
    # APPLICATION DETAILS
    # ═══════════════════════════════════════════════════════════════════════════
    _section_heading("Application Details")

    app_tbl = doc.add_table(rows=4, cols=2)
    app_tbl.style = "Table Grid"
    app_details = [
        ("University",          meta.get("university", "—")),
        ("Program / Visa Type", meta.get("program",    "—")),
        ("Applicant Name",      meta.get("applicant",  "—")),
        ("유학원  (Agency)",    meta.get("agency",     "—")),
    ]
    for i, (label, value) in enumerate(app_details):
        r = app_tbl.rows[i]
        r.cells[0].width = Cm(4.5)
        r.cells[1].width = Cm(13.5)
        _cell_bg(r.cells[0], C_LIGHT_BLUE)
        lp = r.cells[0].paragraphs[0]
        lr = lp.add_run(label)
        _bold_run(lr, size_pt=9.5, color=C_DARK_BLUE)
        _spacing(lp, before_pt=2, after_pt=2)
        vp = r.cells[1].paragraphs[0]
        vr = vp.add_run(value)
        vr.bold = True
        vr.font.size = Pt(10)
        _spacing(vp, before_pt=2, after_pt=2)

    # ═══════════════════════════════════════════════════════════════════════════
    # PAGE COUNT AUDIT
    # ═══════════════════════════════════════════════════════════════════════════
    if orig_total is not None:
        _section_heading("Page Count Audit")

        # Summary row
        sum_tbl = doc.add_table(rows=2, cols=3)
        sum_tbl.style = "Table Grid"
        _header_row(sum_tbl,
                    ["Source PDF(s)", "Original Pages", "Output Pages"],
                    [8.5, 3.0, 3.0])

        src_names = [os.path.basename(s) for s in (source_pdf_paths or [])]
        src_text  = "\n".join(
            f"{n}  ({c} page{'' if c == 1 else 's'})"
            for n, c in zip(src_names, original_page_counts or [])
        ) or "—"

        data_row = sum_tbl.add_row()
        data_row.cells[0].width = Cm(8.5)
        data_row.cells[0].paragraphs[0].add_run(src_text).font.size = Pt(8.5)
        _spacing(data_row.cells[0].paragraphs[0], before_pt=2, after_pt=2)

        for ci, val in enumerate([str(orig_total), str(total_out_pages)], start=1):
            cp = data_row.cells[ci].paragraphs[0]
            cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cr = cp.add_run(val)
            _bold_run(cr, size_pt=12)
            _spacing(cp, before_pt=3, after_pt=3)

        if skipped_pages and skipped_pages > 0:
            wp = doc.add_paragraph()
            _spacing(wp, before_pt=3, after_pt=3)
            wr = wp.add_run(
                f"⚠  {skipped_pages} page(s) from the source PDF were not assigned "
                "to any output file — GPT-4o may not have recognised those pages."
            )
            wr.font.size = Pt(9)
            wr.font.color.rgb = C_ORANGE

        # Output files breakdown
        bp = doc.add_paragraph()
        _spacing(bp, before_pt=6, after_pt=2)
        _bold_run(bp.add_run("Output PDFs  —  page breakdown:"), size_pt=9, color=C_DARK_BLUE)

        out_tbl = doc.add_table(rows=1, cols=2)
        out_tbl.style = "Table Grid"
        _header_row(out_tbl, ["File Name", "Pages"], [13.5, 1.5])

        for idx, (fname, fpc) in enumerate(output_page_details):
            row = out_tbl.add_row()
            bg = C_ROW_ALT if idx % 2 == 1 else C_WHITE
            _cell_bg(row.cells[0], bg)
            _cell_bg(row.cells[1], bg)
            row.cells[0].paragraphs[0].add_run(fname).font.size = Pt(8.5)
            pp = row.cells[1].paragraphs[0]
            pp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pp.add_run(str(fpc)).font.size = Pt(8.5)
            _spacing(row.cells[0].paragraphs[0], before_pt=1, after_pt=1)
            _spacing(pp, before_pt=1, after_pt=1)

    # ═══════════════════════════════════════════════════════════════════════════
    # CHECKLIST STATUS
    # ═══════════════════════════════════════════════════════════════════════════
    _section_heading("Document Checklist Status")

    note_p = doc.add_paragraph()
    _spacing(note_p, after_pt=4)
    nr = note_p.add_run(
        "Note: Photo 35mm×45mm (lab prints) cannot be verified digitally.  "
        "Business bundle items (TIN / Tax / Ack) may be inside a multi-page bundle PDF."
    )
    nr.font.size = Pt(8.5)
    nr.italic = True
    nr.font.color.rgb = C_GREY

    cl_tbl = doc.add_table(rows=1, cols=4)
    cl_tbl.style = "Table Grid"
    _header_row(cl_tbl, ["#", "Document", "Req.", "Status"],
                [0.9, 8.1, 1.2, 7.8])

    missing_items: list[tuple[int, str]] = []

    for idx, (item_no, name, required, found, matched, from_bundle) in enumerate(final_rows):
        if found == 0:
            mark, sc = "✗", C_RED
            status_text = "MISSING"
            missing_items.append((item_no, name))
        elif found < required:
            mark, sc = "⚠", C_ORANGE
            status_text = f"INCOMPLETE  ({found} of {required} required)"
            missing_items.append((item_no, f"{name}  ({found}/{required} found)"))
        else:
            mark, sc = "✓", C_GREEN
            if from_bundle:
                status_text = "PRESENT  (covered by business bundle)"
            else:
                status_text = "PRESENT"

        row = cl_tbl.add_row()
        bg = C_ROW_ALT if idx % 2 == 1 else C_WHITE
        for cell in row.cells:
            _cell_bg(cell, bg)

        # # column
        np_ = row.cells[0].paragraphs[0]
        np_.alignment = WD_ALIGN_PARAGRAPH.CENTER
        np_.add_run(str(item_no)).font.size = Pt(9)
        _spacing(np_, before_pt=2, after_pt=2)

        # Document name
        dp = row.cells[1].paragraphs[0]
        dr = dp.add_run(name)
        dr.font.size = Pt(9)
        if required > 1:
            dr.bold = True
        _spacing(dp, before_pt=2, after_pt=2)

        # Required count
        rp = row.cells[2].paragraphs[0]
        rp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rp.add_run(f"{required}×").font.size = Pt(9)
        _spacing(rp, before_pt=2, after_pt=2)

        # Status
        sp = row.cells[3].paragraphs[0]
        mk_r = sp.add_run(f"{mark}  ")
        mk_r.bold = True
        mk_r.font.size = Pt(10)
        mk_r.font.color.rgb = sc
        st_r = sp.add_run(status_text)
        st_r.font.size = Pt(9)
        st_r.font.color.rgb = sc

        # File reference (if present)
        if found > 0 and matched:
            sp.add_run("\n")
            fn_r = sp.add_run(os.path.basename(matched[0]))
            fn_r.font.size = Pt(7.5)
            fn_r.italic = True
            fn_r.font.color.rgb = C_GREY
        _spacing(sp, before_pt=2, after_pt=2)

    # ═══════════════════════════════════════════════════════════════════════════
    # MISSING / INCOMPLETE SUMMARY
    # ═══════════════════════════════════════════════════════════════════════════
    if missing_items:
        _section_heading(f"Missing / Incomplete  ({len(missing_items)} item(s))")
        for item_no, name in missing_items:
            mp = doc.add_paragraph(style="List Bullet")
            _spacing(mp, after_pt=2)
            mr = mp.add_run(f"[{item_no}]  {name}")
            mr.font.size = Pt(10)
            mr.font.color.rgb = C_RED
    else:
        ok_p = doc.add_paragraph()
        _spacing(ok_p, before_pt=6, after_pt=6)
        ok_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _bold_run(ok_p.add_run("✓  All required documents are present."),
                  size_pt=11, color=C_GREEN)

    # ═══════════════════════════════════════════════════════════════════════════
    # KEY DATES & VALIDITY
    # ═══════════════════════════════════════════════════════════════════════════
    _section_heading("Key Dates & Document Validity")

    dt_tbl = doc.add_table(rows=1, cols=5)
    dt_tbl.style = "Table Grid"
    _header_row(dt_tbl,
                ["Document", "File", "Issue Date", "Expiry Date", "Status"],
                [3.5, 5.2, 2.4, 2.4, 4.5])

    _STATUS_COLOUR = {
        "VALID":         C_GREEN,
        "EXPIRING SOON": C_ORANGE,
        "EXPIRES TODAY": C_ORANGE,
        "EXPIRED":       C_RED,
        "MISSING":       C_RED,
        "UNKNOWN":       C_GREY,
        "PARSE ERROR":   C_GREY,
    }
    _STATUS_MARK = {
        "VALID": "✓", "EXPIRING SOON": "⚠", "EXPIRES TODAY": "⚠",
        "EXPIRED": "✗", "MISSING": "✗", "UNKNOWN": "?", "PARSE ERROR": "?",
    }

    for idx, (key, res) in enumerate(date_results.items()):
        row = dt_tbl.add_row()
        bg = C_ROW_ALT if idx % 2 == 1 else C_WHITE
        for cell in row.cells:
            _cell_bg(cell, bg)

        for ci, (val, sz) in enumerate([
            (res["label"],           9.0),
            (res["file"] or "—",     8.0),
            (res["issue"] or "—",    9.0),
            (res["expiry"] or "—",   9.0),
        ]):
            p = row.cells[ci].paragraphs[0]
            r = p.add_run(val)
            r.font.size = Pt(sz)
            if ci == 1:
                r.italic = True
            _spacing(p, before_pt=2, after_pt=2)

        # Status cell
        sc = _STATUS_COLOUR.get(res["status"], C_GREY)
        mk = _STATUS_MARK.get(res["status"], "?")
        sp = row.cells[4].paragraphs[0]
        st_r = sp.add_run(f"{mk}  {res['status']}")
        _bold_run(st_r, size_pt=9, color=sc)
        detail = res.get("detail", "")
        if detail:
            sp.add_run(f"\n{detail}").font.size = Pt(8)
        _spacing(sp, before_pt=2, after_pt=2)

    # ═══════════════════════════════════════════════════════════════════════════
    # NAME CONSISTENCY CHECK
    # ═══════════════════════════════════════════════════════════════════════════
    _section_heading("Name Consistency Check")

    # Intro note
    nc_intro = doc.add_paragraph()
    _spacing(nc_intro, after_pt=4)
    ni_r = nc_intro.add_run(
        "Names extracted by GPT-4o vision from each document and compared.  "
        "MISMATCH = affidavit required.  LIKELY MATCH = minor romanisation "
        "difference — verify manually.  REFERENCE = first found name, used as baseline."
    )
    ni_r.font.size = Pt(8.5)
    ni_r.italic = True
    ni_r.font.color.rgb = C_GREY

    _VERDICT_COLOUR = {
        "REFERENCE":    C_MID_BLUE,
        "MATCH":        C_GREEN,
        "LIKELY MATCH": C_ORANGE,
        "MISMATCH":     C_RED,
        "NOT FOUND":    C_LIGHT_GREY,
    }
    _VERDICT_MARK = {
        "REFERENCE": "●", "MATCH": "✓", "LIKELY MATCH": "⚠",
        "MISMATCH": "✗", "NOT FOUND": "—",
    }

    def _name_check_table(person_label: str, rows: list[dict]) -> None:
        """Write one name-comparison sub-table for one person (Applicant/Father/Mother)."""
        ph = doc.add_paragraph()
        _spacing(ph, before_pt=8, after_pt=2)
        _bold_run(ph.add_run(person_label), size_pt=9.5, color=C_DARK_BLUE)

        tbl = doc.add_table(rows=1, cols=4)
        tbl.style = "Table Grid"
        _header_row(tbl,
                    ["Source Document", "Name on Document", "Score", "Status"],
                    [5.5, 5.5, 1.5, 5.5])

        for idx, row in enumerate(rows):
            tr = tbl.add_row()
            verdict = row["verdict"]
            vc = _VERDICT_COLOUR.get(verdict, C_GREY)
            vm = _VERDICT_MARK.get(verdict, "?")
            bg = C_ROW_ALT if idx % 2 == 1 else C_WHITE

            # Source
            _cell_bg(tr.cells[0], bg)
            sp0 = tr.cells[0].paragraphs[0]
            sp0.add_run(row["source"]).font.size = Pt(8.5)
            _spacing(sp0, before_pt=2, after_pt=2)

            # Name
            _cell_bg(tr.cells[1], bg)
            sp1 = tr.cells[1].paragraphs[0]
            nr = sp1.add_run(row["name"])
            nr.font.size = Pt(9)
            if verdict in ("MISMATCH",):
                nr.bold = True
                nr.font.color.rgb = C_RED
            elif verdict == "LIKELY MATCH":
                nr.font.color.rgb = C_ORANGE
            _spacing(sp1, before_pt=2, after_pt=2)

            # Score
            _cell_bg(tr.cells[2], bg)
            sp2 = tr.cells[2].paragraphs[0]
            sp2.alignment = WD_ALIGN_PARAGRAPH.CENTER
            score_txt = (
                f"{int(row['score'] * 100)}%"
                if row["score"] is not None and verdict != "NOT FOUND"
                else "—"
            )
            sp2.add_run(score_txt).font.size = Pt(8.5)
            _spacing(sp2, before_pt=2, after_pt=2)

            # Verdict
            _cell_bg(tr.cells[3], C_WHITE)
            sp3 = tr.cells[3].paragraphs[0]
            run3 = sp3.add_run(f"{vm}  {verdict}")
            run3.font.size = Pt(8.5)
            run3.font.color.rgb = vc
            if verdict == "MISMATCH":
                run3.bold = True
            _spacing(sp3, before_pt=2, after_pt=2)

        _col_widths(tbl, [Cm(5.5), Cm(5.5), Cm(1.5), Cm(5.5)])
        doc.add_paragraph()   # breathing room

    # ── column-width helper (applies widths to every row) ────────────────────
    def _col_widths(table, widths: list) -> None:
        for row in table.rows:
            for cell, w in zip(row.cells, widths):
                cell.width = w

    # ═══════════════════════════════════════════════════════════════════════════
    # NAME CONSISTENCY TABLES (call the helper now that it is defined)
    # ═══════════════════════════════════════════════════════════════════════════
    _name_check_table("APPLICANT  —  name must match across all documents", applicant_rows)
    _name_check_table("FATHER  —  NID, Family Certificate, Academic docs", father_rows)
    _name_check_table("MOTHER  —  NID, Family Certificate, Academic docs", mother_rows)

    # Apply column widths to the already-built checklist + dates tables
    _col_widths(cl_tbl, [Cm(0.9), Cm(8.1), Cm(1.2), Cm(7.8)])
    _col_widths(dt_tbl, [Cm(3.5), Cm(5.2), Cm(2.4), Cm(2.4), Cm(4.5)])

    # ═══════════════════════════════════════════════════════════════════════════
    # AFFIDAVIT RECOMMENDATIONS
    # ═══════════════════════════════════════════════════════════════════════════
    if affidavit_needed:
        _section_heading("Affidavit / Additional Documents Required")
        for line in affidavit_needed:
            ap = doc.add_paragraph(style="List Bullet")
            _spacing(ap, after_pt=3)
            ar = ap.add_run(line)
            ar.font.size = Pt(10)
            ar.font.color.rgb = C_RED
            ar.bold = True
    else:
        ok2 = doc.add_paragraph()
        _spacing(ok2, before_pt=6, after_pt=6)
        ok2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _bold_run(ok2.add_run("✓  No name mismatches detected — no affidavit required."),
                  size_pt=10, color=C_GREEN)

    # ── footer note ───────────────────────────────────────────────────────────
    doc.add_paragraph()
    fn_p = doc.add_paragraph()
    _spacing(fn_p, before_pt=10, after_pt=0)
    fn_r = fn_p.add_run(
        "This report is auto-generated by Student Folder Maker using GPT-4o vision.  "
        "Always verify document originals before submission."
    )
    fn_r.font.size = Pt(8)
    fn_r.italic = True
    fn_r.font.color.rgb = C_LIGHT_GREY

    # ── save ─────────────────────────────────────────────────────────────────
    ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    out_fname = f"checklist_report_{ts}.docx"
    out_path_report = os.path.join(dest_dir, out_fname)
    try:
        doc.save(out_path_report)
        log(f"  Checklist report saved → {out_fname}")
        return out_path_report
    except Exception as _save_err:
        log(f"  Could not save checklist report: {_save_err}")
        return ""


# ── find_duplicate_pages ─────────────────────────────────────────────────────

def find_duplicate_pages(
    pdf_paths: list[str],
    *,
    hash_size: int = 8,
    threshold: int = 6,
) -> list[tuple[str, int, str, int, float]]:
    """Return pairs of visually similar pages across (or within) a set of PDFs.

    Each tuple: (path_a, page_i_a, path_b, page_i_b, similarity_pct)
    similarity_pct = 100.0 means identical.
    """
    try:
        import fitz as _fitz
    except ImportError:
        return []
    try:
        from PIL import Image as _PILImage
    except ImportError:
        return []

    # ── compute dHash for every page ─────────────────────────────────────────
    def _dhash(img: "_PILImage.Image", size: int = 8) -> int:
        grey = img.convert("L").resize((size + 1, size), _PILImage.LANCZOS)
        pixels = list(grey.getdata())
        bits = 0
        for row in range(size):
            for col in range(size):
                a = pixels[row * (size + 1) + col]
                b = pixels[row * (size + 1) + col + 1]
                bits = (bits << 1) | (1 if a > b else 0)
        return bits

    def _hamming(a: int, b: int) -> int:
        return bin(a ^ b).count("1")

    entries: list[tuple[str, int, int]] = []   # (path, page_index, dhash)
    max_dim = 200   # render at low resolution for speed

    for path in pdf_paths:
        try:
            doc = _fitz.open(path)
            for pi in range(len(doc)):
                page = doc.load_page(pi)
                zoom = min(max_dim / max(page.rect.width, 1),
                           max_dim / max(page.rect.height, 1))
                mat = _fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img = _PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
                entries.append((path, pi, _dhash(img, size=hash_size)))
            doc.close()
        except Exception:
            continue

    # ── compare all pairs ─────────────────────────────────────────────────────
    max_bits = hash_size * hash_size
    duplicates: list[tuple[str, int, str, int, float]] = []

    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            path_a, pi_a, h_a = entries[i]
            path_b, pi_b, h_b = entries[j]
            dist = _hamming(h_a, h_b)
            if dist <= threshold:
                sim = (1.0 - dist / max_bits) * 100.0
                duplicates.append((path_a, pi_a, path_b, pi_b, sim))

    return duplicates



# ── check_passport_photo_quality ─────────────────────────────────────────────


def rotate_image(path: str, degrees: int = 90, out_path: str = "") -> tuple:
    """Rotate an image by degrees (90, 180, 270, -90). Lossless for JPEG if degrees in 90/180/270."""
    Image = get_pillow()
    if not Image:
        return False, "Pillow not available"
    try:
        img = Image.open(path)
        # Use EXIF-aware expand so rotated image is not clipped
        rotated = img.rotate(-degrees, expand=True)
        if not out_path:
            base, ext = os.path.splitext(path)
            out_path = f"{base}_rot{degrees:+d}{ext}"
        kw = {"quality": 95, "subsampling": 0} if out_path.lower().endswith((".jpg", ".jpeg")) else {}
        rotated.save(out_path, **kw)
        return True, out_path
    except Exception as exc:
        return False, str(exc)


def flip_image(path: str, direction: str = "horizontal", out_path: str = "") -> tuple:
    """Flip image horizontally or vertically."""
    Image = get_pillow()
    if not Image:
        return False, "Pillow not available"
    try:
        img = Image.open(path)
        if direction == "horizontal":
            flipped = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            tag = "_flipH"
        else:
            flipped = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            tag = "_flipV"
        if not out_path:
            base, ext = os.path.splitext(path)
            out_path = f"{base}{tag}{ext}"
        kw = {"quality": 95, "subsampling": 0} if out_path.lower().endswith((".jpg", ".jpeg")) else {}
        flipped.save(out_path, **kw)
        return True, out_path
    except Exception as exc:
        return False, str(exc)


def adjust_image_save(
    path: str,
    brightness: float = 1.0,
    contrast: float = 1.0,
    out_path: str = "",
    overwrite: bool = False,
) -> tuple:
    """Apply brightness/contrast adjustments and save the result."""
    Image = get_pillow()
    if not Image:
        return False, "Pillow not available"
    try:
        img = Image.open(path).convert("RGB")
        result = apply_image_adjustments(img, brightness=brightness, contrast=contrast)
        if not out_path:
            if overwrite:
                out_path = path
            else:
                base, ext = os.path.splitext(path)
                out_path = f"{base}_adjusted{ext}"
        kw = {"quality": 95, "subsampling": 0} if out_path.lower().endswith((".jpg", ".jpeg")) else {}
        result.save(out_path, **kw)
        return True, out_path
    except Exception as exc:
        return False, str(exc)


def move_to_folder_root(paths: list, root: str) -> tuple:
    """Move a list of files to the given root folder (top of current workspace)."""
    import shutil
    moved = []
    errors = []
    for src in paths:
        fname = os.path.basename(src)
        dst = os.path.join(root, fname)
        if os.path.abspath(src) == os.path.abspath(dst):
            errors.append(f"{fname}: already at root")
            continue
        if os.path.exists(dst):
            base, ext = os.path.splitext(fname)
            import time
            dst = os.path.join(root, f"{base}_{int(time.time())}{ext}")
        try:
            shutil.move(src, dst)
            moved.append(dst)
        except Exception as exc:
            errors.append(f"{fname}: {exc}")
    return bool(moved), moved, errors



def resize_photo_to_mm(
    path: str,
    w_mm: float = 35.0,
    h_mm: float = 45.0,
    dpi: float = 300.0,
    out_path: str = "",
    bg_color: tuple = (255, 255, 255),
    crop_mode: str = "center",
) -> tuple:
    """Resize/crop an image to exact physical dimensions (mm @ dpi).

    crop_mode:
        "center" – center-crop to the target aspect ratio, then resize
        "fit"    – letterbox / pad with bg_color to fill the target ratio
    Returns (ok: bool, out_path: str | error_msg: str)
    """
    Image = get_pillow()
    if not Image:
        return False, "Pillow not available"
    try:
        img = Image.open(path).convert("RGB")
    except Exception as exc:
        return False, f"Cannot open image: {exc}"

    pw, ph = physical_mm_to_pixels(w_mm, h_mm, dpi)
    target_ratio = pw / ph
    src_w, src_h = img.size
    src_ratio = src_w / max(src_h, 1)

    if crop_mode == "center":
        # Crop to target aspect ratio (center), then resize to exact pixels
        if src_ratio > target_ratio:
            # image is wider than target — crop sides
            new_w = int(src_h * target_ratio)
            left = (src_w - new_w) // 2
            img = img.crop((left, 0, left + new_w, src_h))
        else:
            # image is taller than target — crop top/bottom
            new_h = int(src_w / target_ratio)
            top = (src_h - new_h) // 2
            img = img.crop((0, top, src_w, top + new_h))
        img = img.resize((pw, ph), Image.Resampling.LANCZOS)
    else:
        # "fit" — scale to fit inside target, pad with bg_color
        img.thumbnail((pw, ph), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (pw, ph), bg_color)
        paste_x = (pw - img.width) // 2
        paste_y = (ph - img.height) // 2
        canvas.paste(img, (paste_x, paste_y))
        img = canvas

    if not out_path:
        base, ext = os.path.splitext(path)
        ext = ext if ext.lower() in (".jpg", ".jpeg", ".png", ".tiff", ".bmp") else ".jpg"
        out_path = f"{base}_{int(w_mm)}x{int(h_mm)}mm{ext}"

    # Preserve JPEG quality
    save_kwargs: dict = {}
    if out_path.lower().endswith((".jpg", ".jpeg")):
        save_kwargs = {"quality": 95, "subsampling": 0}
    # Embed physical resolution so viewers/printers honor the mm size
    save_kwargs["dpi"] = (int(round(dpi)), int(round(dpi)))

    img.save(out_path, **save_kwargs)
    return True, out_path



def check_passport_photo_quality(
    image_path: str,
    *,
    expected_w_mm: float = 35.0,
    expected_h_mm: float = 45.0,
    dpi: float = 300.0,
    tolerance: float = 0.05,
) -> list[tuple[str, str]]:
    """Run basic quality checks on a cropped passport / ID photo.

    Returns a list of (level, message) tuples where level is one of:
        "ok"   -- check passed
        "warn" -- borderline / soft warning
        "fail" -- definite issue

    Checks performed:
        1. Pixel dimensions vs expected mm @ dpi
        2. Aspect ratio
        3. Corner background brightness (passport photos need white bg)
        4. Face-area mean brightness (avoid over/under exposure)
        5. Face-area contrast (std-dev -- avoid flat/blurry images)
    """
    try:
        from PIL import Image as _Image, ImageStat as _ImageStat
    except ImportError:
        return [("warn", "Pillow not available -- quality checks skipped")]

    results: list[tuple[str, str]] = []

    try:
        img = _Image.open(image_path).convert("RGB")
    except Exception as exc:
        return [("fail", f"Cannot open image: {exc}")]

    w_px, h_px = img.size

    # 1. Dimensions
    exp_w_px = expected_w_mm / 25.4 * dpi
    exp_h_px = expected_h_mm / 25.4 * dpi
    w_err = abs(w_px - exp_w_px) / exp_w_px
    h_err = abs(h_px - exp_h_px) / exp_h_px
    if w_err <= tolerance and h_err <= tolerance:
        results.append(("ok", f"Dimensions {w_px}x{h_px} px -- within +-{int(tolerance*100)}% of {expected_w_mm}x{expected_h_mm} mm @ {dpi:.0f} dpi"))
    else:
        level = "warn" if max(w_err, h_err) <= tolerance * 3 else "fail"
        results.append((level, f"Dimensions {w_px}x{h_px} px -- expected ~{int(exp_w_px)}x{int(exp_h_px)} px for {expected_w_mm}x{expected_h_mm} mm @ {dpi:.0f} dpi"))

    # 2. Aspect ratio
    exp_ratio = expected_w_mm / expected_h_mm
    actual_ratio = w_px / max(h_px, 1)
    ratio_err = abs(actual_ratio - exp_ratio) / exp_ratio
    if ratio_err <= tolerance:
        results.append(("ok", f"Aspect ratio {actual_ratio:.3f} -- correct"))
    else:
        results.append(("warn", f"Aspect ratio {actual_ratio:.3f} -- expected {exp_ratio:.3f} ({expected_w_mm}x{expected_h_mm} mm)"))
    # 3. Corner background brightness (white background check)
    corner_size = max(10, min(w_px, h_px) // 10)
    corners = [
        img.crop((0, 0, corner_size, corner_size)),
        img.crop((w_px - corner_size, 0, w_px, corner_size)),
        img.crop((0, h_px - corner_size, corner_size, h_px)),
        img.crop((w_px - corner_size, h_px - corner_size, w_px, h_px)),
    ]
    corner_means = [_ImageStat.Stat(c).mean for c in corners]
    avg_corner = sum(sum(m) / len(m) for m in corner_means) / len(corner_means)
    if avg_corner >= 220:
        results.append(("ok", f"Corner brightness {avg_corner:.0f}/255 -- white background OK"))
    elif avg_corner >= 180:
        results.append(("warn", f"Corner brightness {avg_corner:.0f}/255 -- background may not be pure white"))
    else:
        results.append(("fail", f"Corner brightness {avg_corner:.0f}/255 -- background too dark (passport photos need white background)"))

    # 4. Face-area mean brightness (centre 40% of image)
    cx, cy = w_px // 2, h_px // 2
    fw, fh = int(w_px * 0.4), int(h_px * 0.4)
    face_crop = img.crop((cx - fw // 2, cy - fh // 2, cx + fw // 2, cy + fh // 2))
    face_stat  = _ImageStat.Stat(face_crop)
    face_mean  = sum(face_stat.mean) / 3
    if 80 <= face_mean <= 200:
        results.append(("ok", f"Face-area brightness {face_mean:.0f}/255 -- exposure OK"))
    elif face_mean < 80:
        results.append(("warn", f"Face-area brightness {face_mean:.0f}/255 -- possibly under-exposed"))
    else:
        results.append(("warn", f"Face-area brightness {face_mean:.0f}/255 -- possibly over-exposed"))

    # 5. Face-area contrast (std-dev -- low = blurry/flat)
    face_stddev = sum(face_stat.stddev) / 3
    if face_stddev >= 20:
        results.append(("ok", f"Face-area contrast (stddev) {face_stddev:.1f} -- OK"))
    elif face_stddev >= 10:
        results.append(("warn", f"Face-area contrast (stddev) {face_stddev:.1f} -- image may be flat or slightly blurry"))
    else:
        results.append(("fail", f"Face-area contrast (stddev) {face_stddev:.1f} -- image is very flat or blurry"))

    return results


def crop_image(path: str, x: int, y: int, w: int, h: int, out_path: str = "") -> tuple:
    """Crop an image to the given pixel rectangle (x, y, w, h).
    Saves to out_path if provided, otherwise to <name>_cropped<ext>
    (auto-numbered if it already exists). The original is never overwritten.
    Returns (True, saved_path) or (False, error_message).
    """
    Image = get_pillow()
    if not Image:
        return False, "Pillow not available"
    try:
        img = Image.open(path).convert("RGB")
        cropped = img.crop((x, y, x + w, y + h))
        save_path = out_path.strip() if out_path and out_path.strip() else ""
        if not save_path:
            base, ext = os.path.splitext(path)
            ext = ext or ".jpg"
            save_path = f"{base}_cropped{ext}"
            n = 2
            while os.path.exists(save_path):
                save_path = f"{base}_cropped-{n}{ext}"
                n += 1
        fmt = os.path.splitext(save_path)[1].upper().lstrip(".")
        if fmt in ("JPG", "JPEG", ""):
            fmt = "JPEG"
        cropped.save(save_path, format=fmt or "JPEG", quality=95)
        return True, save_path
    except Exception as exc:
        return False, str(exc)


# =============================================================================
# NATIVE-QUALITY DOCUMENT → PDF PREVIEW
# =============================================================================

def doc_to_pdf_preview(path: str) -> tuple:
    """Convert any Office/HWP document to a temporary PDF for pixel-perfect preview.

    Priority chain:
      1. HWP/HWPX  → Hancom HWP COM  (hwp_to_pdf)
      2. DOCX/DOC  → MS Word COM     (win32com)
      3. XLSX/XLS  → MS Excel COM    (win32com)
      4. Any       → LibreOffice headless
      5. DOCX      → mammoth → wkhtmltopdf  (last resort)

    Returns (True, pdf_path) or (False, error_string).
    The caller should clean up the temp file when done.
    """
    import tempfile, shutil, subprocess

    ext = os.path.splitext(path)[1].lower()
    tmp_pdf = tempfile.mktemp(suffix=".pdf", prefix="sfm_preview_")

    # ── 1. HWP / HWPX via Hancom COM ────────────────────────────────────────
    if ext in (".hwp", ".hwpx"):
        dummy_log = lambda _: None
        if hwp_to_pdf(path, tmp_pdf, dummy_log) and os.path.exists(tmp_pdf):
            return True, tmp_pdf
        # fall through to LibreOffice

    # ── 2. DOCX / DOC / RTF via MS Word COM ─────────────────────────────────
    if ext in (".docx", ".doc", ".docm", ".dotx", ".rtf", ".odt") and sys.platform == "win32":
        try:
            import win32com.client  # type: ignore
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            abs_path = os.path.abspath(path)
            abs_tmp  = os.path.abspath(tmp_pdf)
            doc = word.Documents.Open(abs_path, ReadOnly=True, AddToRecentFiles=False)
            try:
                doc.SaveAs2(abs_tmp, FileFormat=17)  # wdFormatPDF = 17
            finally:
                doc.Close(False)
            try:
                word.Quit()
            except Exception:
                pass
            if os.path.exists(abs_tmp):
                return True, abs_tmp
        except Exception:
            pass

    # ── 3. XLSX / XLS via MS Excel COM ──────────────────────────────────────
    if ext in (".xlsx", ".xls", ".xlsm", ".xlsb") and sys.platform == "win32":
        try:
            import win32com.client  # type: ignore
            xl = win32com.client.Dispatch("Excel.Application")
            xl.Visible = False
            xl.DisplayAlerts = False
            abs_path = os.path.abspath(path)
            abs_tmp  = os.path.abspath(tmp_pdf)
            wb = xl.Workbooks.Open(abs_path, ReadOnly=True, AddToRecentFiles=False)
            try:
                wb.ExportAsFixedFormat(0, abs_tmp)  # 0 = xlTypePDF
            finally:
                wb.Close(False)
            try:
                xl.Quit()
            except Exception:
                pass
            if os.path.exists(abs_tmp):
                return True, abs_tmp
        except Exception:
            pass

    # ── 4. LibreOffice headless (cross-platform) ─────────────────────────────
    lo_candidates = ["soffice", "libreoffice"]
    if sys.platform == "win32":
        lo_candidates += [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
    lo = next((shutil.which(c) or (c if os.path.exists(c) else None)
               for c in lo_candidates if shutil.which(c) or os.path.exists(c)), None)
    if lo:
        try:
            tmp_dir = tempfile.mkdtemp(prefix="sfm_lo_")
            cmd = [lo, "--headless", "--convert-to", "pdf",
                   "--outdir", tmp_dir, os.path.abspath(path)]
            subprocess.run(cmd, timeout=90, capture_output=True, check=True)
            stem = os.path.splitext(os.path.basename(path))[0]
            lo_out = os.path.join(tmp_dir, stem + ".pdf")
            if os.path.exists(lo_out):
                shutil.move(lo_out, tmp_pdf)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return True, tmp_pdf
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    # ── 5. DOCX fallback: mammoth HTML → wkhtmltopdf ─────────────────────────
    if ext in (".docx", ".docm", ".dotx"):
        try:
            import mammoth  # type: ignore
            import shutil as _sh
            with open(path, "rb") as fh:
                res = mammoth.convert_to_html(fh)
            html_body = res.value or ""
            html = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<style>body{font-family:Calibri,sans-serif;font-size:12pt;"
                "line-height:1.5;padding:2cm;max-width:21cm;margin:0 auto}"
                "table{border-collapse:collapse;width:100%}"
                "td,th{border:1px solid #999;padding:4px 8px}"
                "</style></head><body>" + html_body + "</body></html>"
            )
            html_tmp = tmp_pdf.replace(".pdf", "_preview.html")
            with open(html_tmp, "w", encoding="utf-8") as fh:
                fh.write(html)
            wk = _sh.which("wkhtmltopdf")
            if wk:
                subprocess.run(
                    [wk, "--quiet", "--page-size", "A4",
                     "--margin-top", "15mm", "--margin-bottom", "15mm",
                     "--margin-left", "20mm", "--margin-right", "20mm",
                     html_tmp, tmp_pdf],
                    timeout=60, capture_output=True,
                )
                try:
                    os.remove(html_tmp)
                except OSError:
                    pass
                if os.path.exists(tmp_pdf):
                    return True, tmp_pdf
        except Exception:
            pass

    return False, (
        f"No converter found for {ext}. "
        "Install Microsoft Office, LibreOffice, or Hancom HWP and restart the app."
    )


def doc_to_html_excel(path: str, sheet: str = "") -> tuple:
    """Return rich styled HTML for Excel with full cell formatting.

    Includes: background colors, font colors/bold/italic, merged cells,
    column widths, row heights, number formatting, alignment.
    Returns (True, html_string) or (False, error_string).
    """
    op = get_openpyxl()
    if op is None:
        return False, "openpyxl not available"
    try:
        wb = op.load_workbook(path, data_only=True)
    except Exception as exc:
        return False, str(exc)

    ws_name = (sheet if sheet and sheet in wb.sheetnames
               else wb.sheetnames[0] if wb.sheetnames else None)
    if ws_name is None:
        return False, "No sheets found"
    ws = wb[ws_name]

    # ── merged cell spans ────────────────────────────────────────────────────
    merge_spans: dict = {}
    skip_cells: set = set()
    for mr in ws.merged_cells.ranges:
        r1, c1, r2, c2 = mr.min_row, mr.min_col, mr.max_row, mr.max_col
        merge_spans[(r1, c1)] = (r2 - r1 + 1, c2 - c1 + 1)
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if (r, c) != (r1, c1):
                    skip_cells.add((r, c))

    def _hex(color_obj) -> str:
        if color_obj is None:
            return ""
        try:
            if color_obj.type == "rgb":
                raw = color_obj.rgb
                return "#" + raw[-6:] if len(raw) >= 6 else "#" + raw
        except Exception:
            pass
        return ""

    def _cell_css(cell) -> str:
        css = ""
        try:
            f = cell.font
            if f:
                if f.bold:       css += "font-weight:bold;"
                if f.italic:     css += "font-style:italic;"
                if f.underline:  css += "text-decoration:underline;"
                fc = _hex(f.color)
                if fc and fc.lower() not in ("#000000", "#ff000000"):
                    css += f"color:{fc};"
                if f.size:       css += f"font-size:{f.size}pt;"
        except Exception:
            pass
        try:
            fill = cell.fill
            if fill and fill.fill_type not in (None, "none"):
                bg = _hex(fill.fgColor)
                bad = {"#000000","#ffffff","#ff000000","#ffffffff",""}
                if bg and bg.lower() not in bad:
                    css += f"background:{bg};"
        except Exception:
            pass
        try:
            al = cell.alignment
            if al:
                if al.horizontal in ("center", "right", "left", "justify"):
                    css += f"text-align:{al.horizontal};"
                if al.wrap_text:
                    css += "white-space:pre-wrap;"
        except Exception:
            pass
        return css

    def _fmt_val(cell) -> str:
        val = cell.value
        if val is None:
            return ""
        if isinstance(val, float):
            try:
                fmt = (cell.number_format or "General").upper()
                if "%" in fmt:
                    return f"{val * 100:.2f}%"
                if "#,##" in fmt:
                    return f"{val:,.2f}" if ".00" in fmt else f"{val:,.0f}"
                if "0.00" in fmt:
                    return f"{val:.2f}"
                return str(int(val)) if val == int(val) else f"{val:g}"
            except Exception:
                return str(val)
        return str(val)

    # column widths in pixels (openpyxl width unit ≈ char width ≈ 7px)
    col_w: dict = {}
    for ci in range(1, (ws.max_column or 1) + 1):
        ltr = op.utils.get_column_letter(ci)
        cd = ws.column_dimensions.get(ltr)
        col_w[ci] = max(40, int((cd.width or 8) * 7)) if cd else 72

    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>',
        'body{font-family:Calibri,"Segoe UI",sans-serif;font-size:11pt;',
        'background:#fff;color:#111;margin:0;padding:0;overflow-x:auto}',
        'table{border-collapse:collapse;table-layout:fixed;min-width:100%}',
        'td,th{border:1px solid #d0d0d0;padding:1px 5px;overflow:hidden;',
        'max-width:300px;white-space:nowrap;vertical-align:middle}',
        '.rh{background:#f5f5f5;color:#666;font-size:9pt;text-align:right;',
        'padding:1px 4px;border:1px solid #d0d0d0;min-width:32px;font-weight:normal}',
        '.ch{background:#f5f5f5;color:#444;font-size:9pt;text-align:center;',
        'border:1px solid #d0d0d0;font-weight:600}',
        '</style></head><body><table>',
    ]

    # column header row
    parts.append('<tr><th class="rh"></th>')
    for ci in range(1, (ws.max_column or 1) + 1):
        w = col_w.get(ci, 72)
        ltr = op.utils.get_column_letter(ci)
        parts.append(f'<th class="ch" style="width:{w}px">{ltr}</th>')
    parts.append('</tr>')

    for ri in range(1, (ws.max_row or 1) + 1):
        rd = ws.row_dimensions.get(ri)
        rh = f"height:{int((rd.height or 15) * 1.33)}px;" if rd and rd.height else ""
        parts.append(f'<tr style="{rh}">')
        parts.append(f'<td class="rh">{ri}</td>')
        for ci in range(1, (ws.max_column or 1) + 1):
            if (ri, ci) in skip_cells:
                continue
            cell = ws.cell(ri, ci)
            css  = _cell_css(cell)
            w    = col_w.get(ci, 72)
            rspan, cspan = merge_spans.get((ri, ci), (1, 1))
            span = (f' rowspan="{rspan}"' if rspan > 1 else "") + \
                   (f' colspan="{cspan}"' if cspan > 1 else "")
            disp = _fmt_val(cell)
            safe = disp.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            parts.append(
                f'<td{span} style="width:{w}px;{css}" title="{safe}">{safe}</td>'
            )
        parts.append('</tr>')

    parts.append('</table></body></html>')
    return True, "".join(parts)
