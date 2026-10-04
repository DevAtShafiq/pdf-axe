#!/usr/bin/env python3
"""
OCR / Rename diagnostics — run from terminal to see why Rename (OCR) failed.

Usage (from workspace root):
  python tools/ocr_diagnose.py
  python tools/ocr_diagnose.py "D:\\path\\to\\scan.pdf"
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import file_ops as fo  # noqa: E402


def _log(msg: str) -> None:
    print(msg, flush=True)


def diagnose_environment() -> bool:
    _log("=== Environment ===")
    fitz = fo.get_fitz()
    _log(f"  PyMuPDF:     {'OK' if fitz else 'MISSING (pip install pymupdf)'}")
    try:
        import pytesseract  # noqa: F401

        _log("  pytesseract: OK")
    except ImportError:
        _log("  pytesseract: MISSING (pip install pytesseract pillow)")
    ok, msg = fo.tesseract_status()
    _log(f"  Tesseract:   {msg}")
    return bool(fitz and ok)


def diagnose_pdf(path: str) -> None:
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        _log(f"ERROR: not a file: {path}")
        return
    fitz = fo.get_fitz()
    if not fitz:
        _log("ERROR: PyMuPDF not installed.")
        return

    _log(f"\n=== PDF: {path} ===")
    doc = fitz.open(path)
    n = len(doc)
    _log(f"  Pages: {n}")
    for i in range(n):
        page = doc.load_page(i)
        text_layer = (page.get_text() or "").strip()
        _log(f"\n--- Page {i + 1} ---")
        _log(f"  Text layer length: {len(text_layer)} chars")
        if text_layer:
            preview = " ".join(text_layer.split())[:200]
            _log(f"  Text preview: {preview!r}")
        else:
            _log("  Text layer: (empty — scan/OCR required)")

        _log("  Smart stem extraction:")
        stem = fo._ocr_extract_smart_stem_from_page(page, i, _log)
        _log(f"  Result stem: {stem!r}" if stem else "  Result stem: (none)")
    doc.close()

    _log("\n=== Simulated rename (dry run) ===")

    def _noop(_m: str) -> None:
        pass

    ok, out_paths, info = fo.pdf_rename_files_by_ocr_smart([path], _log)
    _log(f"  ok={ok}  renamed={len(out_paths)}  info={info}")
    for p in out_paths:
        _log(f"  would rename → {p}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Rename (OCR) / Tesseract.")
    parser.add_argument(
        "pdf",
        nargs="?",
        help="Optional PDF path to test (shows text layer + stem per page)",
    )
    args = parser.parse_args()
    env_ok = diagnose_environment()
    if args.pdf:
        diagnose_pdf(args.pdf)
    else:
        _log("\nTip: pass a PDF path to test extraction:")
        _log('  python tools/ocr_diagnose.py "D:\\folder\\passport.pdf"')
    return 0 if env_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
