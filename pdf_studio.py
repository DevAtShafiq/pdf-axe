"""
pdf_studio.py — working-copy engine behind PDF Studio (ui/js/studio.js).

PDF Studio opens a PDF once and lets every PDF tool (edit text, annotate,
sign, add photos, arrange pages …) work on it, with one Undo/Redo and one
Save. It never touches the original while you work:

    begin(path)            copies the PDF into a private session folder as
                           step_000.pdf (unlocking it first when a password
                           is given) → {session, work, name, pages, ...}
    next_step(session)     a fresh, not-yet-existing step_NNN.pdf path; each
                           tool writes its result there as a NEW file, so
                           every step stays on disk and Undo is just "go back
                           to the previous step file"
    finish(work, original, out_path=…, replace=…, target_kb=…, password=…)
                           writes the result: a new file next to the original
                           (default) or over the original after a backup copy
                           in _to_review/. Optional: compress to a target size
                           and/or protect with a password.

Session folders live under the system temp folder (…/OfficeAxe-Studio/).
Nothing here deletes files; Windows' own temp clean-up reclaims them.
Text PDFs are never flattened: steps are written by the same engines as the
stand-alone tools (annotate.py, pdf_edit.py, pdf_tools.py), which keep text
selectable and form fields working.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import secrets
import shutil
import tempfile

import pdf_tools as _pt

ROOT_NAME = "OfficeAxe-Studio"
_STEP_RE = re.compile(r"^step_(\d{3,})\.pdf$", re.I)


class StudioError(Exception):
    def __init__(self, msg: str, code: str = "error"):
        super().__init__(msg)
        self.code = code


def root_dir() -> str:
    return os.path.join(tempfile.gettempdir(), ROOT_NAME)


def _inside_root(path: str) -> bool:
    try:
        root = os.path.normcase(os.path.abspath(root_dir()))
        p = os.path.normcase(os.path.abspath(path))
        return os.path.commonpath([root, p]) == root and p != root
    except ValueError:          # different drives on Windows
        return False


def _check_session(session: str) -> str:
    s = os.path.abspath(str(session or ""))
    if not _inside_root(s) or not os.path.isdir(s):
        raise StudioError("This PDF Studio session has ended. Open the PDF again.", "no_session")
    return s


def _check_work(work: str) -> str:
    w = os.path.abspath(str(work or ""))
    if not _inside_root(w) or not os.path.isfile(w):
        raise StudioError("The working copy is missing. Open the PDF in PDF Studio again.", "no_work")
    return w


def _fitz():
    import fitz  # PyMuPDF
    return fitz


def _fillable(doc) -> bool:
    """True when the PDF has a field someone can type into or tick (a form
    with only signature fields or buttons is not one)."""
    try:
        if not doc.is_form_pdf:
            return False
        fitz = _fitz()
        skip = (fitz.PDF_WIDGET_TYPE_SIGNATURE, fitz.PDF_WIDGET_TYPE_BUTTON)
        for page in doc:
            for w in page.widgets() or []:
                if w.field_type not in skip and not (int(w.field_flags or 0) & 1):
                    return True
    except Exception:
        pass
    return False


def _page_info(path: str) -> dict:
    fitz = _fitz()
    doc = fitz.open(path)
    try:
        has_text = False
        for i, pg in enumerate(doc):
            if i >= 5:
                break
            if pg.get_text("text").strip():
                has_text = True
                break
        return {"page_count": doc.page_count, "has_text": has_text,
                "has_form": _fillable(doc)}
    finally:
        doc.close()


def begin(path: str, password: str = "") -> dict:
    """Copy *path* into a new session as step_000.pdf.

    A password-protected PDF answers code 'need_password' (or
    'wrong_password'); call again with the password to unlock the working copy.
    """
    src = os.path.abspath(str(path or ""))
    if not os.path.isfile(src):
        raise StudioError(f"File not found: {os.path.basename(src)}", "not_found")
    if not src.lower().endswith(".pdf"):
        raise StudioError("PDF Studio works with PDF files", "not_pdf")
    fitz = _fitz()
    try:
        doc = fitz.open(src)
    except Exception as exc:
        raise StudioError(f"Could not open {os.path.basename(src)}: {exc}", "unreadable")
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    session = os.path.join(root_dir(), f"{stamp}-{secrets.token_hex(3)}")
    work = os.path.join(session, "step_000.pdf")
    protected = False
    try:
        if doc.needs_pass:
            protected = True
            if not password:
                raise StudioError("This PDF is password-protected", "need_password")
            if not doc.authenticate(password):
                raise StudioError("Wrong password", "wrong_password")
            os.makedirs(session, exist_ok=True)
            doc.save(work, garbage=1, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)
        else:
            doc.close()
            doc = None
            os.makedirs(session, exist_ok=True)
            shutil.copy2(src, work)
    finally:
        if doc is not None:
            doc.close()
    info = _page_info(work)
    return {"session": session, "work": work, "source": src, "name": os.path.basename(src),
            "protected": protected, **info}


def next_step(session: str) -> dict:
    """A fresh step file path in *session* (not created yet)."""
    s = _check_session(session)
    n = -1
    for f in os.listdir(s):
        m = _STEP_RE.match(f)
        if m:
            n = max(n, int(m.group(1)))
    while True:
        n += 1
        p = os.path.join(s, f"step_{n:03d}.pdf")
        if not os.path.exists(p):
            return {"path": p, "index": n}


def step_info(work: str) -> dict:
    w = _check_work(work)
    return {"work": w, "size": os.path.getsize(w), **_page_info(w)}


def finish(work: str, original: str, *, out_path: str = "", replace: bool = False,
           target_kb: float = 0, password: str = "", release_handles=None,
           make_dir: bool = False) -> dict:
    """Write the Studio result.

    replace=False → a NEW file at *out_path* (a free name is chosen if taken).
    replace=True  → over *original*, after a backup copy in _to_review/.
    target_kb > 0 → compress to at most that size first (kept as is when it
                    can't get smaller; 'compress' in the result says what happened).
    password      → protect the result (AES-256).
    make_dir      → create the output folder when it is missing (one level,
                    e.g. the "signature" folder next to the PDF; its parent
                    must exist).
    → {out_path, backup, replaced, size, compress, protected}
    """
    w = _check_work(work)
    orig = os.path.abspath(str(original or ""))
    session = os.path.dirname(w)
    src = w
    compress_info = None
    if target_kb and float(target_kb) > 0:
        import media_convert as _mc
        tmp = next_step(session)["path"]
        r = _mc.compress_pdf(src, "ebook", tmp, target_kb=float(target_kb), save_smallest=True)
        compress_info = {"before": r.get("before"), "after": r.get("after"),
                         "target_met": r.get("target_met"), "kept_original": bool(r.get("kept_original")),
                         "note": r.get("note") or ""}
        if not r.get("kept_original") and r.get("out") and os.path.isfile(r["out"]):
            src = r["out"]
    protected = False
    if password:
        import archive_tools as _at
        tmp = next_step(session)["path"]
        r = _at.pdf_set_password(src, password, out_path=tmp)
        src = r["out_path"]
        protected = True

    if not replace:
        target = str(out_path or "").strip()
        if not target:
            stem, _ = os.path.splitext(orig or w)
            target = stem + "_edited.pdf"
        target = os.path.abspath(target)
        if not target.lower().endswith(".pdf"):
            target += ".pdf"
        out_dir = os.path.dirname(target)
        if make_dir and not os.path.isdir(out_dir) and os.path.isdir(os.path.dirname(out_dir)):
            os.makedirs(out_dir, exist_ok=True)
        if not os.path.isdir(out_dir):
            raise StudioError("Choose an existing output folder", "bad_folder")
        final = _pt.unique_path(target)
        shutil.copy2(src, final)
        return {"out_path": final, "backup": "", "replaced": False, "size": os.path.getsize(final),
                "compress": compress_info, "protected": protected}

    if not os.path.isfile(orig):
        raise StudioError(f"Cannot overwrite {os.path.basename(orig)}: it no longer exists.", "not_found")
    backup = _pt.backup_to_review(orig)
    stem, ext = os.path.splitext(orig)
    tmp = _pt.unique_path(stem + ".saving" + ext)
    shutil.copy2(src, tmp)
    if release_handles:
        try:
            release_handles(orig)
        except Exception:
            pass
    try:
        os.replace(tmp, orig)
    except OSError as exc:
        alt = _pt.unique_path(stem + "_edited" + ext)
        os.replace(tmp, alt)
        raise StudioError(f"Could not overwrite {os.path.basename(orig)} ({exc.strerror or exc}). "
                          f"Your work was saved as {os.path.basename(alt)} instead.", "locked")
    return {"out_path": orig, "backup": backup, "replaced": True, "size": os.path.getsize(orig),
            "compress": compress_info, "protected": protected}
