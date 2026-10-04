# Cowork Toolkit — Function Reference

This file documents every tool in the `tools/` folder.
**You don't need to memorize commands** — just tell Claude in chat what you want
("convert this PDF to images", "zip that folder", "soft-delete those files") and
Claude will run the right command. This reference is for transparency.

All tools follow the same safety contract from `CLAUDE.md`:
- **NEVER permanently delete** — `soft-delete` quarantines instead.
- **NEVER overwrite** — output files auto-suffix (`foo.pdf` → `foo-1.pdf`).
- **>10 items prompts confirmation**.

---

## tools/pdf.py — PDF operations

```
python pdf.py info <pdf>                              # page count + metadata
python pdf.py text <pdf> [-o out.txt] [--layout]      # extract text
python pdf.py to-images <pdf> [--pages 1,3,5-8] [--format png|jpg|tiff] [--dpi 200]
python pdf.py from-images img1 img2 ... [-o out.pdf]  # combine images into PDF
python pdf.py merge a.pdf b.pdf -o merged.pdf
python pdf.py split <pdf> [--ranges 1-3,4-6,7-10]
python pdf.py extract <pdf> --pages 1,3,5-8 [-o out.pdf]
python pdf.py reorder <pdf> --order 3,1,2,4
python pdf.py rotate <pdf> --degrees 90 [--pages 1,3]
python pdf.py compress <pdf> --quality low|medium|high|max
```

**"PDF to photo with page selection"** — that's `to-images --pages`.
Pages spec: `1,3,5-8,11` (1-indexed, ranges OK).

---

## tools/docs.py — Document conversions

```
python docs.py pdf-to-docx <pdf>            # via LibreOffice
python docs.py docx-to-pdf <docx>
python docs.py docx-to-txt <docx>
python docs.py docx-to-md <docx>            # via pandoc
python docs.py docx-to-html <docx>          # via pandoc
python docs.py xlsx-to-csv <xlsx>           # one CSV per sheet
python docs.py csv-to-xlsx a.csv b.csv -o out.xlsx
python docs.py pptx-to-pdf <pptx>
python docs.py any-to-pdf <file>            # universal: handles ODT/RTF/etc
python docs.py any-to-docx <file>
```

---

## tools/image.py — Image conversions & editing

```
python image.py info <image>
python image.py convert <input> --format png|jpg|webp|bmp|tiff|gif [--quality 1-100]
python image.py resize <input> [--width N | --height N | --percent 50 | --max-dim 1920]
python image.py compress <input> --quality 75
python image.py batch <indir> [--format jpg] [--resize-max 1920] [--quality 80]
```

Supported any↔any: PNG, JPG/JPEG, WEBP, BMP, TIFF, GIF.
HEIC reading requires `pillow-heif` (not installed by default; use ImageMagick fallback).

---

## tools/excel.py — Excel viewer + editor

```
python excel.py sheets <xlsx>                              # list sheets + dimensions
python excel.py view <xlsx> [--sheet name] [--rows 50] [--cols 10]
python excel.py cell <xlsx> A1                             # read
python excel.py cell <xlsx> A1 --value "hello"             # write
python excel.py set-row <xlsx> --row 5 --values v1 v2 v3
python excel.py add-row <xlsx> --values v1 v2 v3
python excel.py add-sheet <xlsx> --name NewSheet
python excel.py rename <xlsx> --old Sheet1 --new Customers
python excel.py search <xlsx> --value "John" [--icase]
python excel.py search <xlsx> --regex "^[A-Z]\d+"
python excel.py to-html <xlsx> [--sheet name]              # browser-viewable
python excel.py diff before.xlsx after.xlsx
```

By default edits write to `<name>_edited.xlsx`. Pass `--in-place` to overwrite
(a `.bak` is kept alongside).

---

## tools/organize.py — File organization + soft-delete

```
python organize.py files-to-folders <files...> [--move] [--outdir DIR]
python organize.py group-by-ext <indir>
python organize.py group-by-date <indir>
python organize.py rename-pattern <files...> --find REGEX --replace STR
python organize.py flatten <indir> [--outdir DIR]
python organize.py soft-delete <items...> [--workspace ROOT]
python organize.py restore [--name STR] [--workspace ROOT]
python organize.py list-quarantine [--workspace ROOT]
```

**Soft-delete** moves items to `_to_review/` inside the workspace root.
Originals' paths are logged to `_quarantine_log.txt` so `restore` can put them back.
**There is no "true delete" command. Use File Explorer to permanently remove
items from `_to_review/` yourself.**

---

## tools/zip_tool.py — Zip archives

```
python zip_tool.py pack <items...> [-o out.zip] [--compression deflate|store|bz2|lzma] [--level 0-9]
python zip_tool.py unpack <zip> [--outdir DIR] [--force]
python zip_tool.py list <zip>
python zip_tool.py add <zip> <items...>
```

Pack a single file, single folder, or any combination. Folder structure is preserved.

---

## How Claude uses these

When you say something like:

> "Take pages 3, 5, 7-10 from invoices.pdf as PNG images"

Claude runs:
```
python tools/pdf.py to-images invoices.pdf --pages 3,5,7-10 --format png
```

> "Convert all my .docx files in this folder to PDF"

Claude loops over them with `tools/docs.py docx-to-pdf` for each.

> "Group my Downloads by file type"

Claude runs `tools/organize.py group-by-ext Downloads/`.

> "Soft-delete those old reports"

Claude runs `tools/organize.py soft-delete report_2020*.pdf`.

You never have to type these commands yourself unless you want to.

---

## tools/ocr_diagnose.py — Rename (OCR) terminal diagnostics

When **Rename (OCR)** fails, run from the workspace root so output appears in the terminal:

```
python tools/ocr_diagnose.py
python tools/ocr_diagnose.py "D:\path\to\your.pdf"
```

Shows: PyMuPDF / pytesseract / Tesseract status, text layer length per page, extracted rename stem, and a dry-run rename trace.

If you start the GUI from a terminal, OCR lines are also mirrored there:

```
python student_folder_maker.py
```

Look for `[Rename-OCR]` and `[HH:MM:SS]` lines while using Rename (OCR).
