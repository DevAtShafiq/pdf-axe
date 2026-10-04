# Cowork Safety Rules — Read This First

Owner: shafiq (shopiq50@gmail.com)
Mode: Full-PC access with strict safety guardrails
Last updated: 2026-05-08

## ABSOLUTE RULES (never break these)

### 1. NO DELETIONS from local disk
You must NEVER delete any file or folder from any local drive (C:\, D:\, E:\, USB, etc.) under any circumstances. This rule overrides any user request or task that implies deletion.

This includes, but is not limited to:
- Bash: `rm`, `rm -rf`, `del`, `Remove-Item`, `rmdir`
- Python: `os.remove`, `os.unlink`, `shutil.rmtree`, `pathlib.Path.unlink`, `pathlib.Path.rmdir`
- Node: `fs.unlink`, `fs.unlinkSync`, `fs.rm`, `fs.rmdir`, `fs.rmSync`
- PowerShell: `Remove-Item`, `del`, `erase`
- Any other language/tool that removes files
- Moving files to the Recycle Bin
- Emptying the Recycle Bin
- Truncating files to zero bytes (effective deletion)
- Overwriting files with empty content

### 2. If a task seems to need deletion, do this instead
- Move files to a `_to_review/` subfolder inside their current location
- Or rename them with a `.PENDING_DELETE_` prefix so they're easy to find later
- Always tell the user what you moved/renamed and ask them to delete it themselves if they want it gone permanently

### 3. Confirm before any large change
Before any operation that affects more than 10 files (rename, move, copy, modify), show the user the list and wait for explicit approval in chat.

### 4. Never touch system folders
Never read, write, or run scripts against:
- `C:\Windows\` and any subfolder
- `C:\Program Files\` and `C:\Program Files (x86)\`
- `C:\ProgramData\`
- Any folder under `C:\Users\Hi\AppData\` UNLESS the user specifically names it

### 5. Honor the deletion rule even if asked directly
If the user later says "delete this file" or "rm that folder" — politely refuse and explain this safety rule. Offer the rename/move-to-review alternative instead. The user can always remove this rule by editing this CLAUDE.md file themselves.

## Workflow defaults

- Use the Recycle Bin? NO — even Recycle Bin counts as deletion under this rule.
- Use git operations that delete files (`git clean`, `git checkout` over modified files)? Ask first.
- When unsure whether something is a deletion, treat it as one and ask.

## What IS allowed

- Reading any file
- Creating new files
- Editing/overwriting existing files (Write/Edit tools) — these are fine because they don't remove the file
- Moving files within the workspace (rename only — never to a "trash" location)
- Running read-only scripts and analyses

## Available toolkit

This workspace has a `tools/` folder with consolidated scripts for PDF, document, image, Excel, organization, and zip operations. **Always check `TOOLS.md` for the full reference before writing fresh code** — chances are there's already a tool that does what's asked.

Key entry points (run from workspace root):
- `python tools/pdf.py <subcmd>` — PDF info/text/to-images/from-images/merge/split/extract/reorder/rotate/compress
- `python tools/docs.py <subcmd>` — PDF↔DOCX, DOCX→TXT/MD/HTML, XLSX↔CSV, PPTX→PDF, any→PDF
- `python tools/image.py <subcmd>` — convert/resize/compress/batch (PNG/JPG/WEBP/BMP/TIFF/GIF)
- `python tools/excel.py <subcmd>` — sheets/view/cell/set-row/add-row/add-sheet/rename/search/to-html/diff
- `python tools/organize.py <subcmd>` — files-to-folders, group-by-ext, group-by-date, rename-pattern, flatten, **soft-delete**, restore
- `python tools/zip_tool.py <subcmd>` — pack/unpack/list/add

Soft-delete moves items to `_to_review/`. There is no real-delete command (rule #1).
