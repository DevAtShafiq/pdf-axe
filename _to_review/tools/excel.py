#!/usr/bin/env python3
"""
Excel viewer + editor.

SUBCOMMANDS:
  view       Pretty-print sheet contents to console
  sheets     List all sheet names + dimensions
  cell       Read or write a single cell
  set-row    Replace an entire row
  add-row    Append a row to a sheet
  add-sheet  Add a new sheet
  rename     Rename a sheet
  search     Find cells matching a value/regex
  to-html    Render a sheet to a self-contained HTML table (viewable in browser)
  diff       Show what changed between two .xlsx files

SAFETY: Edits write to a NEW .xlsx file by default (foo.xlsx -> foo-edited.xlsx).
Pass --in-place to overwrite (still keeps a .bak file alongside).
"""
from __future__ import annotations
import argparse, re, shutil, sys
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

def open_for_edit(src: Path, in_place: bool, suffix: str = "edited"):
    """Returns (workbook, output_path). Always writes to a new file unless in_place=True (then keeps .bak)."""
    from openpyxl import load_workbook
    if in_place:
        bak = src.with_suffix(src.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(src, bak)
        wb = load_workbook(src)
        return wb, src
    out = src.with_name(f"{src.stem}_{suffix}{src.suffix}")
    out = safe_out(out)
    wb = load_workbook(src)
    return wb, out

def _print_table(rows, max_col_width=40):
    """Print a 2D list as a formatted table."""
    if not rows:
        print("(empty)")
        return
    # normalize: pad rows to longest length
    ncols = max(len(r) for r in rows)
    rows = [list(r) + [""] * (ncols - len(r)) for r in rows]
    rows = [[("" if c is None else str(c))[:max_col_width] for c in r] for r in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(ncols)]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    print(sep)
    for ri, row in enumerate(rows):
        print("| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(row)) + " |")
        if ri == 0:
            print(sep)
    print(sep)

# -------- subcommands --------

def cmd_sheets(args):
    from openpyxl import load_workbook
    wb = load_workbook(args.xlsx, read_only=True, data_only=True)
    print(f"File: {args.xlsx}")
    print(f"Sheets: {len(wb.sheetnames)}")
    for name in wb.sheetnames:
        ws = wb[name]
        print(f"  {name!r}: {ws.max_row} rows x {ws.max_column} cols")

def cmd_view(args):
    from openpyxl import load_workbook
    wb = load_workbook(args.xlsx, data_only=True)
    sheet = args.sheet or wb.sheetnames[0]
    if sheet not in wb.sheetnames:
        sys.exit(f"Sheet {sheet!r} not found. Available: {wb.sheetnames}")
    ws = wb[sheet]
    print(f"Sheet: {sheet} ({ws.max_row} rows x {ws.max_column} cols)")
    rows = list(ws.iter_rows(values_only=True))
    if args.rows:
        rows = rows[: args.rows]
    if args.cols:
        rows = [r[: args.cols] for r in rows]
    _print_table(rows)

def cmd_cell(args):
    from openpyxl import load_workbook
    if args.value is not None:
        # write
        wb, out = open_for_edit(Path(args.xlsx), args.in_place)
        sheet = args.sheet or wb.sheetnames[0]
        ws = wb[sheet]
        # try to coerce value
        v = args.value
        try:
            v = int(v)
        except ValueError:
            try:
                v = float(v)
            except ValueError:
                pass
        ws[args.cell] = v
        wb.save(out)
        print(f"Wrote {args.cell}={v} to {out}")
    else:
        # read
        wb = load_workbook(args.xlsx, data_only=True)
        sheet = args.sheet or wb.sheetnames[0]
        ws = wb[sheet]
        v = ws[args.cell].value
        print(repr(v))

def cmd_set_row(args):
    wb, out = open_for_edit(Path(args.xlsx), args.in_place)
    sheet = args.sheet or wb.sheetnames[0]
    ws = wb[sheet]
    values = args.values
    for i, v in enumerate(values, 1):
        ws.cell(row=args.row, column=i, value=v)
    wb.save(out)
    print(f"Set row {args.row} to {values} in {out}")

def cmd_add_row(args):
    wb, out = open_for_edit(Path(args.xlsx), args.in_place)
    sheet = args.sheet or wb.sheetnames[0]
    ws = wb[sheet]
    ws.append(args.values)
    wb.save(out)
    print(f"Appended row {args.values} to {out}")

def cmd_add_sheet(args):
    wb, out = open_for_edit(Path(args.xlsx), args.in_place)
    if args.name in wb.sheetnames:
        sys.exit(f"Sheet {args.name!r} already exists.")
    wb.create_sheet(args.name)
    wb.save(out)
    print(f"Added sheet {args.name!r} to {out}")

def cmd_rename(args):
    wb, out = open_for_edit(Path(args.xlsx), args.in_place)
    if args.old not in wb.sheetnames:
        sys.exit(f"Sheet {args.old!r} not found. Available: {wb.sheetnames}")
    wb[args.old].title = args.new
    wb.save(out)
    print(f"Renamed {args.old!r} -> {args.new!r} in {out}")

def cmd_search(args):
    from openpyxl import load_workbook
    wb = load_workbook(args.xlsx, data_only=True)
    sheets = [args.sheet] if args.sheet else wb.sheetnames
    pattern = re.compile(args.regex if args.regex else re.escape(args.value), re.IGNORECASE if args.icase else 0)
    hits = 0
    for sname in sheets:
        ws = wb[sname]
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                if pattern.search(str(cell.value)):
                    print(f"  {sname}!{cell.coordinate}: {cell.value!r}")
                    hits += 1
    print(f"\n{hits} match(es)")

def cmd_to_html(args):
    from openpyxl import load_workbook
    wb = load_workbook(args.xlsx, data_only=True)
    sheet = args.sheet or wb.sheetnames[0]
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    out = Path(args.output) if args.output else Path(args.xlsx).with_suffix(".html")
    out = safe_out(out)
    html = ['<!doctype html><html><head><meta charset="utf-8">',
            f'<title>{sheet}</title>',
            '<style>body{font-family:system-ui,sans-serif;padding:20px}',
            'table{border-collapse:collapse;font-size:14px}',
            'th,td{border:1px solid #ccc;padding:6px 10px;text-align:left}',
            'th{background:#f0f0f0;position:sticky;top:0}',
            'tr:nth-child(even){background:#fafafa}</style></head><body>',
            f'<h2>{Path(args.xlsx).name} — {sheet}</h2>',
            '<table>']
    for i, row in enumerate(rows):
        tag = "th" if i == 0 else "td"
        cells = "".join(f"<{tag}>{'' if v is None else v}</{tag}>" for v in row)
        html.append(f"<tr>{cells}</tr>")
    html.append("</table></body></html>")
    out.write_text("".join(html), encoding="utf-8")
    print(f"Wrote {out} (open in any browser to view)")

def cmd_diff(args):
    from openpyxl import load_workbook
    wb1 = load_workbook(args.before, data_only=True)
    wb2 = load_workbook(args.after, data_only=True)
    print(f"Sheets in {args.before}: {wb1.sheetnames}")
    print(f"Sheets in {args.after}:  {wb2.sheetnames}")
    common = set(wb1.sheetnames) & set(wb2.sheetnames)
    diffs = 0
    for sname in common:
        ws1, ws2 = wb1[sname], wb2[sname]
        max_r = max(ws1.max_row, ws2.max_row)
        max_c = max(ws1.max_column, ws2.max_column)
        for r in range(1, max_r + 1):
            for c in range(1, max_c + 1):
                v1 = ws1.cell(r, c).value
                v2 = ws2.cell(r, c).value
                if v1 != v2:
                    coord = ws1.cell(r, c).coordinate
                    print(f"  {sname}!{coord}: {v1!r} -> {v2!r}")
                    diffs += 1
    print(f"\n{diffs} cell change(s)")

# -------- CLI --------

def main():
    p = argparse.ArgumentParser(prog="excel.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sheets", help="List sheets")
    s.add_argument("xlsx")
    s.set_defaults(func=cmd_sheets)

    s = sub.add_parser("view", help="Pretty-print a sheet")
    s.add_argument("xlsx")
    s.add_argument("--sheet")
    s.add_argument("--rows", type=int, help="Limit rows shown")
    s.add_argument("--cols", type=int, help="Limit columns shown")
    s.set_defaults(func=cmd_view)

    s = sub.add_parser("cell", help="Read or write a single cell")
    s.add_argument("xlsx")
    s.add_argument("cell", help="Like A1, B5, AA20")
    s.add_argument("--sheet")
    s.add_argument("--value", help="If provided, write this value (else read)")
    s.add_argument("--in-place", action="store_true", help="Overwrite source file (creates .bak)")
    s.set_defaults(func=cmd_cell)

    s = sub.add_parser("set-row", help="Replace a row's contents")
    s.add_argument("xlsx")
    s.add_argument("--row", type=int, required=True)
    s.add_argument("--values", nargs="+", required=True)
    s.add_argument("--sheet")
    s.add_argument("--in-place", action="store_true")
    s.set_defaults(func=cmd_set_row)

    s = sub.add_parser("add-row", help="Append a row")
    s.add_argument("xlsx")
    s.add_argument("--values", nargs="+", required=True)
    s.add_argument("--sheet")
    s.add_argument("--in-place", action="store_true")
    s.set_defaults(func=cmd_add_row)

    s = sub.add_parser("add-sheet", help="Add a new sheet")
    s.add_argument("xlsx")
    s.add_argument("--name", required=True)
    s.add_argument("--in-place", action="store_true")
    s.set_defaults(func=cmd_add_sheet)

    s = sub.add_parser("rename", help="Rename a sheet")
    s.add_argument("xlsx")
    s.add_argument("--old", required=True)
    s.add_argument("--new", required=True)
    s.add_argument("--in-place", action="store_true")
    s.set_defaults(func=cmd_rename)

    s = sub.add_parser("search", help="Find cells matching value or regex")
    s.add_argument("xlsx")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--value")
    g.add_argument("--regex")
    s.add_argument("--sheet")
    s.add_argument("--icase", action="store_true", help="Case-insensitive")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("to-html", help="Render sheet as standalone HTML")
    s.add_argument("xlsx")
    s.add_argument("--sheet")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_to_html)

    s = sub.add_parser("diff", help="Compare two xlsx files")
    s.add_argument("before")
    s.add_argument("after")
    s.set_defaults(func=cmd_diff)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
