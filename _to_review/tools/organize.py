#!/usr/bin/env python3
"""
File organization toolkit.

SUBCOMMANDS:
  files-to-folders   For each file, create a folder named after it (and
                     optionally move the file inside).
  group-by-ext       Sort files into folders by extension (PDF/, JPG/, ...).
  group-by-date      Sort files into year/month folders by modified date.
  rename-pattern     Bulk-rename files with a pattern.
  flatten            Move all files from nested folders into one folder.
  soft-delete        SAFE delete: moves selected files/folders to a
                     _to_review/ quarantine folder (NEVER permanently deletes).
  restore            Move items out of _to_review/ back to original location.
  list-quarantine    Show what's in _to_review/.

SAFETY:
  - This script NEVER permanently deletes anything.
  - 'soft-delete' moves items to <workspace>/_to_review/, preserving relative paths.
  - To permanently delete, the user must do it themselves from File Explorer.
  - All bulk operations affecting >10 items prompt for confirmation.
"""
from __future__ import annotations
import argparse, os, re, shutil, sys
from pathlib import Path
from datetime import datetime

# -------- helpers --------

def confirm(prompt: str, n_items: int) -> bool:
    if n_items > 10:
        sys.stderr.write(f"\nThis will affect {n_items} items. {prompt} [y/N]: ")
        ans = input().strip().lower()
        return ans in ("y", "yes")
    return True

def safe_path(path: Path) -> Path:
    """Find a non-clobbering target path."""
    if not path.exists():
        return path
    stem, suffix = (path.stem, path.suffix) if path.is_file() or "." in path.name else (path.name, "")
    n = 1
    while True:
        c = path.parent / f"{stem}-{n}{suffix}" if suffix else path.parent / f"{path.name}-{n}"
        if not c.exists():
            return c
        n += 1

def expand_inputs(inputs: list[str]) -> list[Path]:
    """Expand a list of paths/globs to actual Path objects (files and folders)."""
    out = []
    for item in inputs:
        p = Path(item)
        if p.exists():
            out.append(p)
        else:
            # glob
            parent = p.parent if p.parent != Path() else Path(".")
            matches = sorted(parent.glob(p.name))
            if not matches:
                sys.stderr.write(f"WARNING: no match for {item!r}\n")
            out.extend(matches)
    return out

# -------- subcommands --------

def cmd_files_to_folders(args):
    """For each file, create a folder named after the file (sans extension)."""
    items = expand_inputs(args.files)
    files = [i for i in items if i.is_file()]
    if not files:
        sys.exit("No files matched.")
    if not confirm(f"Create folders for {len(files)} file(s)?", len(files)):
        sys.exit("Cancelled.")
    base = Path(args.outdir) if args.outdir else None
    moved, created = 0, 0
    for f in files:
        target_parent = base if base else f.parent
        target_parent.mkdir(parents=True, exist_ok=True)
        folder_name = f.stem  # strip extension
        folder = target_parent / folder_name
        if not folder.exists():
            folder.mkdir()
            created += 1
        else:
            print(f"  (folder exists, reusing) {folder}")
        if args.move:
            new_path = safe_path(folder / f.name)
            shutil.move(str(f), str(new_path))
            moved += 1
            print(f"  {f.name} -> {new_path}")
        else:
            print(f"  Created {folder}")
    print(f"\nCreated {created} folder(s); moved {moved} file(s).")

def cmd_group_by_ext(args):
    src = Path(args.indir)
    files = [p for p in src.iterdir() if p.is_file()]
    if not files:
        sys.exit("No files in directory.")
    if not confirm(f"Group {len(files)} files by extension?", len(files)):
        sys.exit("Cancelled.")
    moved = 0
    for f in files:
        ext = f.suffix.lstrip(".").upper() or "NO_EXT"
        target_dir = src / ext
        target_dir.mkdir(exist_ok=True)
        new_path = safe_path(target_dir / f.name)
        shutil.move(str(f), str(new_path))
        moved += 1
    print(f"Moved {moved} file(s) into extension folders under {src}")

def cmd_group_by_date(args):
    src = Path(args.indir)
    files = [p for p in src.iterdir() if p.is_file()]
    if not files:
        sys.exit("No files in directory.")
    if not confirm(f"Group {len(files)} files by date?", len(files)):
        sys.exit("Cancelled.")
    moved = 0
    for f in files:
        ts = f.stat().st_mtime
        d = datetime.fromtimestamp(ts)
        target_dir = src / f"{d.year}" / f"{d.month:02d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        new_path = safe_path(target_dir / f.name)
        shutil.move(str(f), str(new_path))
        moved += 1
    print(f"Moved {moved} file(s) into year/month folders under {src}")

def cmd_rename_pattern(args):
    items = expand_inputs(args.files)
    files = [i for i in items if i.is_file()]
    if not files:
        sys.exit("No files matched.")
    pat = re.compile(args.find)
    preview = [(f, f.with_name(pat.sub(args.replace, f.name))) for f in files]
    actually_changing = [(a, b) for a, b in preview if a != b]
    print(f"Will rename {len(actually_changing)} file(s):")
    for a, b in actually_changing[:20]:
        print(f"  {a.name} -> {b.name}")
    if len(actually_changing) > 20:
        print(f"  ... and {len(actually_changing) - 20} more")
    if not actually_changing:
        return
    if not confirm("Proceed?", len(actually_changing)):
        sys.exit("Cancelled.")
    for a, b in actually_changing:
        b = safe_path(b)
        a.rename(b)
    print(f"Renamed {len(actually_changing)} file(s).")

def cmd_flatten(args):
    src = Path(args.indir)
    target = Path(args.outdir) if args.outdir else src / "_flattened"
    target.mkdir(parents=True, exist_ok=True)
    files = [p for p in src.rglob("*") if p.is_file() and target not in p.parents]
    if not confirm(f"Flatten {len(files)} files into {target}?", len(files)):
        sys.exit("Cancelled.")
    for f in files:
        new_path = safe_path(target / f.name)
        shutil.copy2(str(f), str(new_path))  # COPY not move (safer)
    print(f"Copied {len(files)} files into {target} (originals preserved).")

def cmd_soft_delete(args):
    """Move items to <workspace>/_to_review/ — NEVER permanent delete."""
    items = expand_inputs(args.items)
    if not items:
        sys.exit("No items matched.")
    workspace = Path(args.workspace) if args.workspace else Path.cwd()
    quarantine = workspace / "_to_review"
    quarantine.mkdir(exist_ok=True)
    print(f"\nSAFETY MODE: This is a SOFT DELETE.")
    print(f"Items will be moved to: {quarantine}")
    print(f"To permanently remove them, you must delete them from there yourself.\n")
    for it in items:
        print(f"  {it}")
    if not confirm(f"\nMove {len(items)} item(s) to quarantine?", len(items)):
        sys.exit("Cancelled.")
    moved = 0
    log_path = quarantine / "_quarantine_log.txt"
    with open(log_path, "a", encoding="utf-8") as log:
        ts = datetime.now().isoformat(timespec="seconds")
        for it in items:
            try:
                rel = it.resolve().relative_to(workspace.resolve())
            except ValueError:
                rel = Path(it.name)
            target = quarantine / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target = safe_path(target)
            shutil.move(str(it), str(target))
            log.write(f"{ts}\t{it.resolve()}\t{target.resolve()}\n")
            print(f"  {it} -> {target}")
            moved += 1
    print(f"\nMoved {moved} item(s) to {quarantine}")
    print(f"Restore with: python organize.py restore <name>")

def cmd_restore(args):
    """Move items out of _to_review/ back. Reads the log to restore to original location."""
    workspace = Path(args.workspace) if args.workspace else Path.cwd()
    quarantine = workspace / "_to_review"
    log_path = quarantine / "_quarantine_log.txt"
    if not log_path.exists():
        sys.exit(f"No quarantine log found at {log_path}")
    # build mapping from latest entries
    mapping = {}  # current path -> original path
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) == 3:
                _, orig, curr = parts
                mapping[Path(curr)] = Path(orig)
    matches = []
    for curr, orig in mapping.items():
        if not curr.exists():
            continue
        if args.name and args.name not in curr.name:
            continue
        matches.append((curr, orig))
    if not matches:
        sys.exit("Nothing matched to restore.")
    print(f"Will restore {len(matches)} item(s):")
    for c, o in matches[:20]:
        print(f"  {c} -> {o}")
    if not confirm("Proceed?", len(matches)):
        sys.exit("Cancelled.")
    for curr, orig in matches:
        orig.parent.mkdir(parents=True, exist_ok=True)
        orig = safe_path(orig)
        shutil.move(str(curr), str(orig))
        print(f"  Restored to {orig}")
    print(f"Restored {len(matches)} item(s).")

def cmd_list_quarantine(args):
    workspace = Path(args.workspace) if args.workspace else Path.cwd()
    quarantine = workspace / "_to_review"
    if not quarantine.exists():
        print("(no quarantine folder yet)")
        return
    items = list(quarantine.rglob("*"))
    items = [p for p in items if p.name != "_quarantine_log.txt" and p.is_file() or p.is_dir()]
    print(f"Quarantine: {quarantine}")
    print(f"{len(items)} item(s)")
    total_size = 0
    for p in items:
        if p.is_file():
            sz = p.stat().st_size
            total_size += sz
            print(f"  {p.relative_to(quarantine)}  ({sz/1024:.1f} KB)")
        elif p.is_dir():
            print(f"  {p.relative_to(quarantine)}/  (folder)")
    print(f"\nTotal size: {total_size/1024/1024:.2f} MB")

# -------- CLI --------

def main():
    p = argparse.ArgumentParser(prog="organize.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("files-to-folders", help="Create a folder per file")
    s.add_argument("files", nargs="+", help="Files (or globs) to process")
    s.add_argument("--move", action="store_true", help="Move file inside its new folder")
    s.add_argument("--outdir", help="Where to create the folders (default: each file's parent)")
    s.set_defaults(func=cmd_files_to_folders)

    s = sub.add_parser("group-by-ext", help="Sort files by extension")
    s.add_argument("indir")
    s.set_defaults(func=cmd_group_by_ext)

    s = sub.add_parser("group-by-date", help="Sort files into year/month folders")
    s.add_argument("indir")
    s.set_defaults(func=cmd_group_by_date)

    s = sub.add_parser("rename-pattern", help="Bulk rename via regex")
    s.add_argument("files", nargs="+")
    s.add_argument("--find", required=True, help="Regex to find")
    s.add_argument("--replace", required=True, help="Replacement (use \\1 for groups)")
    s.set_defaults(func=cmd_rename_pattern)

    s = sub.add_parser("flatten", help="Copy all nested files into one folder")
    s.add_argument("indir")
    s.add_argument("--outdir")
    s.set_defaults(func=cmd_flatten)

    s = sub.add_parser("soft-delete", help="SAFE delete (moves to _to_review/)")
    s.add_argument("items", nargs="+")
    s.add_argument("--workspace", help="Workspace root (default: cwd)")
    s.set_defaults(func=cmd_soft_delete)

    s = sub.add_parser("restore", help="Restore items from quarantine")
    s.add_argument("--name", help="Filter by substring of name")
    s.add_argument("--workspace")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("list-quarantine", help="Show what's in _to_review/")
    s.add_argument("--workspace")
    s.set_defaults(func=cmd_list_quarantine)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
