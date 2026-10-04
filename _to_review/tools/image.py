#!/usr/bin/env python3
"""
Image toolkit — convert, resize, compress.

SUBCOMMANDS:
  convert      Any -> any across PNG/JPG/JPEG/WEBP/BMP/TIFF/GIF/HEIC
               (HEIC requires reading; writing HEIC not supported by Pillow)
  resize       Resize while preserving aspect (or to exact dims)
  compress     Reduce file size (JPEG/WEBP quality, PNG optimize)
  batch        Run convert/resize/compress over a whole folder
  info         Show image dimensions, mode, file size

SAFETY: Originals are NEVER modified. Outputs go to a sibling file or
specified output dir. Output paths auto-suffixed if they exist.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

SUPPORTED_OUT = {"png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif"}

def safe_out(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 1
    while True:
        c = path.parent / f"{stem}-{n}{suffix}"
        if not c.exists():
            return c
        n += 1

def _open_image(path: Path):
    """Open image; auto-handle HEIC if pillow-heif is available."""
    from PIL import Image
    if path.suffix.lower() in (".heic", ".heif"):
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            sys.exit("HEIC input requires pillow-heif. Convert HEIC files first via ImageMagick: "
                     "convert input.heic output.png")
    return Image.open(path)

def _save_image(img, out: Path, quality: int | None = None):
    """Save with sensible defaults per format."""
    fmt = out.suffix.lstrip(".").lower()
    if fmt not in SUPPORTED_OUT:
        sys.exit(f"Unsupported output format: {fmt}. Supported: {sorted(SUPPORTED_OUT)}")
    save_kwargs = {}
    if fmt in ("jpg", "jpeg"):
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        save_kwargs["quality"] = quality if quality is not None else 90
        save_kwargs["optimize"] = True
    elif fmt == "webp":
        save_kwargs["quality"] = quality if quality is not None else 85
    elif fmt == "png":
        save_kwargs["optimize"] = True
    elif fmt == "tiff":
        save_kwargs["compression"] = "tiff_lzw"
    img.save(out, **save_kwargs)

# -------- subcommands --------

def cmd_info(args):
    img = _open_image(Path(args.image))
    p = Path(args.image)
    print(f"File: {p}")
    print(f"Format: {img.format}")
    print(f"Mode: {img.mode}")
    print(f"Size: {img.size[0]} x {img.size[1]}")
    print(f"File size: {p.stat().st_size / 1024:.1f} KB")

def cmd_convert(args):
    src = Path(args.input)
    fmt = args.format.lower().lstrip(".")
    out = Path(args.output) if args.output else src.with_suffix(f".{fmt}")
    out = safe_out(out)
    img = _open_image(src)
    _save_image(img, out, quality=args.quality)
    print(f"Wrote {out}")

def cmd_resize(args):
    src = Path(args.input)
    img = _open_image(src)
    w, h = img.size
    if args.width and args.height:
        new_size = (args.width, args.height)
    elif args.width:
        new_size = (args.width, int(h * args.width / w))
    elif args.height:
        new_size = (int(w * args.height / h), args.height)
    elif args.percent:
        new_size = (int(w * args.percent / 100), int(h * args.percent / 100))
    elif args.max_dim:
        scale = args.max_dim / max(w, h)
        new_size = (int(w * scale), int(h * scale))
    else:
        sys.exit("Provide --width, --height, --percent, or --max-dim")
    from PIL import Image as PILImage
    img = img.resize(new_size, PILImage.LANCZOS)
    out = Path(args.output) if args.output else src.with_name(f"{src.stem}_resized{src.suffix}")
    out = safe_out(out)
    _save_image(img, out, quality=args.quality)
    print(f"Wrote {out} ({new_size[0]}x{new_size[1]})")

def cmd_compress(args):
    src = Path(args.input)
    img = _open_image(src)
    out = Path(args.output) if args.output else src.with_name(f"{src.stem}_compressed{src.suffix}")
    out = safe_out(out)
    _save_image(img, out, quality=args.quality)
    before = src.stat().st_size
    after = out.stat().st_size
    pct = (1 - after/before) * 100
    print(f"Wrote {out} ({before/1024:.1f} KB -> {after/1024:.1f} KB, {pct:.1f}% smaller)")

def cmd_batch(args):
    src_dir = Path(args.indir)
    out_dir = Path(args.outdir) if args.outdir else src_dir / "_converted"
    out_dir.mkdir(parents=True, exist_ok=True)
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif", ".heic", ".heif"}
    files = [p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in exts]
    print(f"Found {len(files)} image(s)")
    for f in files:
        try:
            img = _open_image(f)
            if args.resize_max:
                w, h = img.size
                scale = args.resize_max / max(w, h)
                if scale < 1:
                    from PIL import Image as PILImage
                    img = img.resize((int(w*scale), int(h*scale)), PILImage.LANCZOS)
            new_ext = f".{args.format.lstrip('.')}" if args.format else f.suffix
            out = out_dir / f"{f.stem}{new_ext}"
            out = safe_out(out)
            _save_image(img, out, quality=args.quality)
            print(f"  {f.name} -> {out.name}")
        except Exception as e:
            print(f"  SKIP {f.name}: {e}")
    print(f"Done. Outputs in {out_dir}")

# -------- CLI --------

def main():
    p = argparse.ArgumentParser(prog="image.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="Show image dims/mode/size")
    s.add_argument("image")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("convert", help="Convert format (any -> any)")
    s.add_argument("input")
    s.add_argument("--format", required=True, help="Target: png, jpg, webp, bmp, tiff, gif")
    s.add_argument("-o", "--output")
    s.add_argument("--quality", type=int, default=None, help="1-100 (JPEG/WEBP)")
    s.set_defaults(func=cmd_convert)

    s = sub.add_parser("resize", help="Resize image")
    s.add_argument("input")
    g = s.add_mutually_exclusive_group(required=False)
    g.add_argument("--width", type=int)
    g.add_argument("--height", type=int)
    g.add_argument("--percent", type=int, help="e.g. 50 = half size")
    g.add_argument("--max-dim", type=int, help="Fit within max-dim on longest side")
    s.add_argument("-o", "--output")
    s.add_argument("--quality", type=int, default=None)
    s.set_defaults(func=cmd_resize)

    s = sub.add_parser("compress", help="Reduce file size")
    s.add_argument("input")
    s.add_argument("--quality", type=int, default=75, help="1-100 (default 75)")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_compress)

    s = sub.add_parser("batch", help="Process all images in a folder")
    s.add_argument("indir")
    s.add_argument("--format", help="Convert all to this format (optional)")
    s.add_argument("--resize-max", type=int, help="Cap longest side at this many px")
    s.add_argument("--quality", type=int, default=None)
    s.add_argument("--outdir")
    s.set_defaults(func=cmd_batch)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
