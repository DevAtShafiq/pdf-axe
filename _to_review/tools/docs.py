#!/usr/bin/env python3
"""
Document conversion toolkit.

SUBCOMMANDS:
  pdf-to-docx     PDF -> editable Word doc (via LibreOffice)
  docx-to-pdf     Word -> PDF (via LibreOffice)
  docx-to-txt     Word -> plain text
  docx-to-md      Word -> Markdown (via pandoc)
  docx-to-html    Word -> HTML (via pandoc)
  xlsx-to-csv     Excel -> CSV (one CSV per sheet)
  csv-to-xlsx     CSV -> Excel
  pptx-to-pdf     PowerPoint -> PDF (via LibreOffice)
  any-to-pdf      Convert ANY supported file to PDF (LibreOffice)
  any-to-docx     Convert ANY supported file to DOCX (LibreOffice)

SAFETY: Never deletes input files; output paths auto-suffixed if they exist.
"""
from __future__ import annotations
import argparse, csv, subprocess, sys
from pathlib import Path

def safe_out(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 1
    while True:
        candidate = path.parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1

def libreoffice_convert(src: Path, target_format: str, outdir: Path) -> Path:
    """Run LibreOffice headless conversion. Returns the output Path."""
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = ["libreoffice", "--headless", "--convert-to", target_format,
           "--outdir", str(outdir), str(src)]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    # LibreOffice writes <stem>.<ext>; figure out the actual extension from target_format
    ext = target_format.split(":")[0]
    out = outdir / f"{src.stem}.{ext}"
    if not out.exists():
        sys.exit(f"LibreOffice conversion failed.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
    return out

# -------- subcommands --------

def cmd_pdf_to_docx(args):
    src = Path(args.pdf)
    outdir = Path(args.outdir) if args.outdir else src.parent
    out = libreoffice_convert(src, "docx", outdir)
    final = safe_out(out) if args.output is None else safe_out(Path(args.output))
    if final != out:
        out.rename(final)
    print(f"Wrote {final}")

def cmd_docx_to_pdf(args):
    src = Path(args.docx)
    outdir = Path(args.outdir) if args.outdir else src.parent
    out = libreoffice_convert(src, "pdf", outdir)
    final = safe_out(out) if args.output is None else safe_out(Path(args.output))
    if final != out:
        out.rename(final)
    print(f"Wrote {final}")

def cmd_docx_to_txt(args):
    from docx import Document
    src = Path(args.docx)
    out = Path(args.output) if args.output else src.with_suffix(".txt")
    out = safe_out(out)
    doc = Document(src)
    text = "\n".join(p.text for p in doc.paragraphs)
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out}")

def _pandoc(src: Path, out: Path, to_format: str):
    out = safe_out(out)
    subprocess.run(["pandoc", str(src), "-o", str(out), "-t", to_format], check=True)
    print(f"Wrote {out}")

def cmd_docx_to_md(args):
    src = Path(args.docx)
    out = Path(args.output) if args.output else src.with_suffix(".md")
    _pandoc(src, out, "markdown")

def cmd_docx_to_html(args):
    src = Path(args.docx)
    out = Path(args.output) if args.output else src.with_suffix(".html")
    _pandoc(src, out, "html")

def cmd_xlsx_to_csv(args):
    from openpyxl import load_workbook
    src = Path(args.xlsx)
    wb = load_workbook(src, data_only=True)
    outdir = Path(args.outdir) if args.outdir else src.parent
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        # sanitize sheet name for filename
        safe_sheet = "".join(c if c.isalnum() or c in "-_ " else "_" for c in sheet_name)
        out = outdir / f"{src.stem}__{safe_sheet}.csv"
        out = safe_out(out)
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for row in ws.iter_rows(values_only=True):
                writer.writerow(row)
        written.append(out)
    print(f"Wrote {len(written)} CSV file(s):")
    for w in written:
        print(f"  {w}")

def cmd_csv_to_xlsx(args):
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    written_to = None
    for csv_path in args.csvs:
        cp = Path(csv_path)
        ws = wb.create_sheet(cp.stem[:31])  # Excel sheet name max 31 chars
        with open(cp, encoding="utf-8") as f:
            for row in csv.reader(f):
                ws.append(row)
        written_to = cp
    out = Path(args.output) if args.output else (written_to.with_suffix(".xlsx"))
    out = safe_out(out)
    wb.save(out)
    print(f"Wrote {out}")

def cmd_pptx_to_pdf(args):
    src = Path(args.pptx)
    outdir = Path(args.outdir) if args.outdir else src.parent
    out = libreoffice_convert(src, "pdf", outdir)
    final = safe_out(out) if args.output is None else safe_out(Path(args.output))
    if final != out:
        out.rename(final)
    print(f"Wrote {final}")

def cmd_any_to_pdf(args):
    src = Path(args.file)
    outdir = Path(args.outdir) if args.outdir else src.parent
    out = libreoffice_convert(src, "pdf", outdir)
    final = safe_out(out)
    if final != out:
        out.rename(final)
    print(f"Wrote {final}")

def cmd_any_to_docx(args):
    src = Path(args.file)
    outdir = Path(args.outdir) if args.outdir else src.parent
    out = libreoffice_convert(src, "docx", outdir)
    final = safe_out(out)
    if final != out:
        out.rename(final)
    print(f"Wrote {final}")

# -------- CLI --------

def main():
    p = argparse.ArgumentParser(prog="docs.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn, infile in [
        ("pdf-to-docx", cmd_pdf_to_docx, "pdf"),
        ("docx-to-pdf", cmd_docx_to_pdf, "docx"),
        ("docx-to-txt", cmd_docx_to_txt, "docx"),
        ("docx-to-md", cmd_docx_to_md, "docx"),
        ("docx-to-html", cmd_docx_to_html, "docx"),
        ("pptx-to-pdf", cmd_pptx_to_pdf, "pptx"),
    ]:
        s = sub.add_parser(name, help=fn.__doc__ or name)
        s.add_argument(infile)
        s.add_argument("-o", "--output")
        if name in ("pdf-to-docx", "docx-to-pdf", "pptx-to-pdf"):
            s.add_argument("--outdir")
        s.set_defaults(func=fn)

    s = sub.add_parser("xlsx-to-csv", help="Excel to CSV (one CSV per sheet)")
    s.add_argument("xlsx")
    s.add_argument("--outdir")
    s.set_defaults(func=cmd_xlsx_to_csv)

    s = sub.add_parser("csv-to-xlsx", help="CSV(s) to Excel (one sheet per CSV)")
    s.add_argument("csvs", nargs="+")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_csv_to_xlsx)

    s = sub.add_parser("any-to-pdf", help="Convert any supported file to PDF (DOCX/XLSX/PPTX/ODT/RTF/etc.)")
    s.add_argument("file")
    s.add_argument("--outdir")
    s.set_defaults(func=cmd_any_to_pdf)

    s = sub.add_parser("any-to-docx", help="Convert any supported file to DOCX")
    s.add_argument("file")
    s.add_argument("--outdir")
    s.set_defaults(func=cmd_any_to_docx)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
