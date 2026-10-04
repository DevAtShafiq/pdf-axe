#!/usr/bin/env python3
"""
PDF toolkit — all PDF operations in one script.

SUBCOMMANDS (run `python pdf.py <cmd> --help` for each):
  info         Show page count + metadata
  text         Extract text from a PDF
  to-images    Render pages to PNG/JPG (supports page selection)
  from-images  Combine images into a single PDF
  merge        Merge multiple PDFs in given order
  split        Split a PDF into individual pages or ranges
  extract      Pull specific pages into a new PDF
  reorder      Reorder pages by a list of page numbers
  rotate       Rotate specific pages by 90/180/270 degrees
  compress     Reduce PDF file size (via ghostscript)

SAFETY: This script NEVER deletes input files. It always writes to a new
output path. If the output path exists, it appends -1, -2, ... so you don't
overwrite by accident.
"""
from __future__ import annotations
import argparse, os, subprocess, sys, shutil, re
from pathlib import Path

# -------- safety helpers --------

def safe_out(path: Path) -> Path:
    """Return a non-clobbering output path: foo.pdf -> foo-1.pdf if foo.pdf exists."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    parent = path.parent
    n = 1
    while True:
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1

def parse_page_spec(spec: str, total: int) -> list[int]:
    """Parse '1,3,5-8,11' (1-indexed) into a sorted unique list of 1-indexed page numbers, validated against total."""
    pages = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            a, b = int(a), int(b)
            if a > b:
                a, b = b, a
            for p in range(a, b + 1):
                pages.add(p)
        else:
            pages.add(int(chunk))
    bad = [p for p in pages if p < 1 or p > total]
    if bad:
        raise ValueError(f"Pages out of range (PDF has {total} pages): {sorted(bad)}")
    return sorted(pages)

# -------- subcommands --------

def cmd_info(args):
    from pypdf import PdfReader
    reader = PdfReader(args.pdf)
    print(f"File: {args.pdf}")
    print(f"Pages: {len(reader.pages)}")
    if reader.metadata:
        for k, v in reader.metadata.items():
            print(f"  {k}: {v}")
    # First page dimensions
    if reader.pages:
        p = reader.pages[0]
        w, h = float(p.mediabox.width), float(p.mediabox.height)
        print(f"Page 1 size: {w:.0f} x {h:.0f} pts ({w/72:.2f} x {h/72:.2f} in)")

def cmd_text(args):
    """Extract text using pdftotext (more reliable than pypdf for layout)."""
    out = Path(args.output) if args.output else Path(args.pdf).with_suffix(".txt")
    out = safe_out(out)
    cmd = ["pdftotext"]
    if args.layout:
        cmd.append("-layout")
    cmd += [args.pdf, str(out)]
    subprocess.run(cmd, check=True)
    print(f"Wrote {out}")

def cmd_to_images(args):
    """Render PDF pages to images via pdftoppm."""
    pdf = Path(args.pdf)
    out_dir = Path(args.outdir) if args.outdir else pdf.parent / f"{pdf.stem}_images"
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = args.format.lower()
    fmt_flag = {"png": "-png", "jpg": "-jpeg", "jpeg": "-jpeg", "tiff": "-tiff"}[fmt]

    # determine pages
    from pypdf import PdfReader
    total = len(PdfReader(args.pdf).pages)
    if args.pages:
        pages = parse_page_spec(args.pages, total)
    else:
        pages = list(range(1, total + 1))

    prefix = out_dir / pdf.stem
    written = []
    for p in pages:
        cmd = ["pdftoppm", fmt_flag, "-r", str(args.dpi), "-f", str(p), "-l", str(p), args.pdf, str(prefix)]
        subprocess.run(cmd, check=True)
        # pdftoppm pads with zeros based on total page count
        pad = len(str(total))
        # extension differs slightly: pdftoppm uses .jpg for jpeg
        ext_actual = "jpg" if fmt in ("jpg", "jpeg") else fmt
        produced = out_dir / f"{pdf.stem}-{str(p).zfill(pad)}.{ext_actual}"
        if produced.exists():
            written.append(produced)
    print(f"Wrote {len(written)} image(s) to {out_dir}")
    for w in written[:10]:
        print(f"  {w.name}")
    if len(written) > 10:
        print(f"  ... and {len(written)-10} more")

def cmd_from_images(args):
    """Combine images into a single PDF using PIL."""
    from PIL import Image
    images = [Path(p) for p in args.images]
    if not images:
        sys.exit("No images provided.")
    pil_imgs = []
    for img_path in images:
        img = Image.open(img_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        pil_imgs.append(img)
    out = Path(args.output) if args.output else images[0].with_suffix(".pdf")
    out = safe_out(out)
    pil_imgs[0].save(out, "PDF", save_all=True, append_images=pil_imgs[1:])
    print(f"Wrote {out} ({len(pil_imgs)} pages)")

def cmd_merge(args):
    from pypdf import PdfWriter
    writer = PdfWriter()
    for pdf in args.pdfs:
        writer.append(pdf)
    out = safe_out(Path(args.output))
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Wrote {out}")

def cmd_split(args):
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(args.pdf)
    pdf = Path(args.pdf)
    out_dir = Path(args.outdir) if args.outdir else pdf.parent / f"{pdf.stem}_split"
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(reader.pages)
    if args.ranges:
        # comma-separated ranges, each becomes its own PDF
        groups = []
        for chunk in args.ranges.split(","):
            pages = parse_page_spec(chunk, total)
            groups.append(pages)
    else:
        # one PDF per page
        groups = [[i] for i in range(1, total + 1)]
    for idx, pages in enumerate(groups, 1):
        writer = PdfWriter()
        for p in pages:
            writer.add_page(reader.pages[p - 1])
        if len(pages) == 1:
            label = f"page-{pages[0]}"
        else:
            label = f"pages-{pages[0]}-{pages[-1]}"
        out = out_dir / f"{pdf.stem}_{label}.pdf"
        out = safe_out(out)
        with open(out, "wb") as f:
            writer.write(f)
    print(f"Split into {len(groups)} file(s) in {out_dir}")

def cmd_extract(args):
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(args.pdf)
    total = len(reader.pages)
    pages = parse_page_spec(args.pages, total)
    writer = PdfWriter()
    for p in pages:
        writer.add_page(reader.pages[p - 1])
    out = Path(args.output) if args.output else Path(args.pdf).with_name(f"{Path(args.pdf).stem}_extract.pdf")
    out = safe_out(out)
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Wrote {out} ({len(pages)} pages)")

def cmd_reorder(args):
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(args.pdf)
    total = len(reader.pages)
    order = [int(x) for x in args.order.split(",")]
    bad = [p for p in order if p < 1 or p > total]
    if bad:
        sys.exit(f"Pages out of range (PDF has {total} pages): {bad}")
    writer = PdfWriter()
    for p in order:
        writer.add_page(reader.pages[p - 1])
    out = Path(args.output) if args.output else Path(args.pdf).with_name(f"{Path(args.pdf).stem}_reordered.pdf")
    out = safe_out(out)
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Wrote {out} (order: {order})")

def cmd_rotate(args):
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(args.pdf)
    total = len(reader.pages)
    pages = parse_page_spec(args.pages, total) if args.pages else list(range(1, total + 1))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages, 1):
        if i in pages:
            page.rotate(args.degrees)
        writer.add_page(page)
    out = Path(args.output) if args.output else Path(args.pdf).with_name(f"{Path(args.pdf).stem}_rotated.pdf")
    out = safe_out(out)
    with open(out, "wb") as f:
        writer.write(f)
    print(f"Wrote {out} (rotated pages {pages} by {args.degrees}°)")

def cmd_compress(args):
    """Reduce PDF size via ghostscript."""
    out = Path(args.output) if args.output else Path(args.pdf).with_name(f"{Path(args.pdf).stem}_compressed.pdf")
    out = safe_out(out)
    quality = {"low": "/screen", "medium": "/ebook", "high": "/printer", "max": "/prepress"}[args.quality]
    subprocess.run([
        "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={quality}", "-dNOPAUSE", "-dQUIET", "-dBATCH",
        f"-sOutputFile={out}", args.pdf
    ], check=True)
    before = Path(args.pdf).stat().st_size
    after = out.stat().st_size
    pct = (1 - after/before) * 100
    print(f"Wrote {out} ({before/1024:.1f} KB -> {after/1024:.1f} KB, {pct:.1f}% smaller)")

# -------- CLI --------

def main():
    p = argparse.ArgumentParser(prog="pdf.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="Show PDF page count and metadata")
    s.add_argument("pdf")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("text", help="Extract text from PDF")
    s.add_argument("pdf")
    s.add_argument("-o", "--output", help="Output .txt path (default: same name as PDF)")
    s.add_argument("--layout", action="store_true", help="Preserve layout (multi-column friendly)")
    s.set_defaults(func=cmd_text)

    s = sub.add_parser("to-images", help="Render PDF pages to images")
    s.add_argument("pdf")
    s.add_argument("--pages", help="Page selection like '1,3,5-8' (default: all)")
    s.add_argument("--format", default="png", choices=["png", "jpg", "jpeg", "tiff"])
    s.add_argument("--dpi", type=int, default=200)
    s.add_argument("--outdir", help="Output directory (default: <pdfname>_images/)")
    s.set_defaults(func=cmd_to_images)

    s = sub.add_parser("from-images", help="Combine images into PDF")
    s.add_argument("images", nargs="+")
    s.add_argument("-o", "--output", help="Output PDF path")
    s.set_defaults(func=cmd_from_images)

    s = sub.add_parser("merge", help="Merge PDFs")
    s.add_argument("pdfs", nargs="+")
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_merge)

    s = sub.add_parser("split", help="Split PDF into per-page or per-range files")
    s.add_argument("pdf")
    s.add_argument("--ranges", help="Comma-separated ranges like '1-3,4-6,7' (default: every page separately)")
    s.add_argument("--outdir", help="Output directory")
    s.set_defaults(func=cmd_split)

    s = sub.add_parser("extract", help="Pull specific pages into a new PDF")
    s.add_argument("pdf")
    s.add_argument("--pages", required=True, help="Page selection like '1,3,5-8'")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_extract)

    s = sub.add_parser("reorder", help="Reorder pages")
    s.add_argument("pdf")
    s.add_argument("--order", required=True, help="New order, e.g. '3,1,2,4' (1-indexed)")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_reorder)

    s = sub.add_parser("rotate", help="Rotate pages")
    s.add_argument("pdf")
    s.add_argument("--degrees", type=int, choices=[90, 180, 270], required=True)
    s.add_argument("--pages", help="Pages to rotate (default: all)")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_rotate)

    s = sub.add_parser("compress", help="Reduce PDF size via ghostscript")
    s.add_argument("pdf")
    s.add_argument("--quality", default="medium", choices=["low", "medium", "high", "max"],
                   help="low=72dpi, medium=150dpi, high=300dpi, max=lossless")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_compress)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
