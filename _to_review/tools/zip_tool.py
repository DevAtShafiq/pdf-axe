#!/usr/bin/env python3
"""
Zip toolkit — create and extract .zip archives.

SUBCOMMANDS:
  pack       Zip a single file, folder, or list of selections
  unpack     Extract a .zip
  list       Peek inside a .zip (list contents + sizes)
  add        Add files to an existing .zip

SAFETY: Never deletes source files when packing. Auto-suffixes output zip
if the name is already taken.
"""
from __future__ import annotations
import argparse, os, sys, zipfile
from pathlib import Path

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

def cmd_pack(args):
    items = [Path(p) for p in args.items]
    missing = [p for p in items if not p.exists()]
    if missing:
        sys.exit(f"Not found: {missing}")
    # determine output
    if args.output:
        out = Path(args.output)
    elif len(items) == 1:
        out = items[0].with_suffix(".zip") if items[0].is_file() else items[0].parent / f"{items[0].name}.zip"
    else:
        out = items[0].parent / "archive.zip"
    out = safe_out(out)
    compression = {"deflate": zipfile.ZIP_DEFLATED, "store": zipfile.ZIP_STORED, "bz2": zipfile.ZIP_BZIP2, "lzma": zipfile.ZIP_LZMA}[args.compression]
    with zipfile.ZipFile(out, "w", compression=compression, compresslevel=args.level) as zf:
        for item in items:
            if item.is_file():
                zf.write(item, arcname=item.name)
            else:  # directory
                for root, dirs, files in os.walk(item):
                    root_p = Path(root)
                    for f in files:
                        full = root_p / f
                        # arcname = relative to the parent of the chosen folder so the folder is preserved
                        arc = item.name + "/" + str(full.relative_to(item)).replace(os.sep, "/")
                        zf.write(full, arcname=arc)
    sz = out.stat().st_size
    print(f"Wrote {out} ({sz/1024:.1f} KB)")
    # quick stats
    with zipfile.ZipFile(out) as zf:
        info = zf.infolist()
        total = sum(i.file_size for i in info)
        comp = sum(i.compress_size for i in info)
    print(f"  {len(info)} files, {total/1024:.1f} KB uncompressed -> {comp/1024:.1f} KB compressed")

def cmd_unpack(args):
    src = Path(args.zipfile)
    if not src.exists():
        sys.exit(f"Not found: {src}")
    out_dir = Path(args.outdir) if args.outdir else src.with_suffix("")
    if out_dir.exists() and not args.force:
        out_dir = safe_out(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as zf:
        zf.extractall(out_dir)
        n = len(zf.infolist())
    print(f"Extracted {n} item(s) to {out_dir}")

def cmd_list(args):
    src = Path(args.zipfile)
    with zipfile.ZipFile(src) as zf:
        info = zf.infolist()
    print(f"{src} — {len(info)} item(s)")
    total_orig, total_comp = 0, 0
    for i in info:
        ratio = (1 - i.compress_size / i.file_size) * 100 if i.file_size else 0
        print(f"  {i.filename}  ({i.file_size/1024:.1f} KB -> {i.compress_size/1024:.1f} KB, {ratio:.0f}%)")
        total_orig += i.file_size
        total_comp += i.compress_size
    print(f"\nTotal: {total_orig/1024:.1f} KB uncompressed, {total_comp/1024:.1f} KB compressed")

def cmd_add(args):
    src = Path(args.zipfile)
    items = [Path(p) for p in args.items]
    missing = [p for p in items if not p.exists()]
    if missing:
        sys.exit(f"Not found: {missing}")
    with zipfile.ZipFile(src, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            if item.is_file():
                zf.write(item, arcname=item.name)
                print(f"  Added {item.name}")
            else:
                for root, _, files in os.walk(item):
                    for f in files:
                        full = Path(root) / f
                        arc = item.name + "/" + str(full.relative_to(item)).replace(os.sep, "/")
                        zf.write(full, arcname=arc)
                        print(f"  Added {arc}")
    print(f"Updated {src}")

def main():
    p = argparse.ArgumentParser(prog="zip_tool.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("pack", help="Create a zip from files/folders")
    s.add_argument("items", nargs="+")
    s.add_argument("-o", "--output")
    s.add_argument("--compression", default="deflate", choices=["deflate", "store", "bz2", "lzma"])
    s.add_argument("--level", type=int, default=6, help="0-9 (deflate only)")
    s.set_defaults(func=cmd_pack)

    s = sub.add_parser("unpack", help="Extract a zip")
    s.add_argument("zipfile")
    s.add_argument("--outdir")
    s.add_argument("--force", action="store_true", help="Allow overwriting existing outdir")
    s.set_defaults(func=cmd_unpack)

    s = sub.add_parser("list", help="List zip contents")
    s.add_argument("zipfile")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("add", help="Add to existing zip")
    s.add_argument("zipfile")
    s.add_argument("items", nargs="+")
    s.set_defaults(func=cmd_add)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
