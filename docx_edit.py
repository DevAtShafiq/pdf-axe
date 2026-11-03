"""
docx_edit.py — quick text edits in a Word (.docx) file, keeping its look.

The Word preview (doc_preview.docx_to_html) tags every paragraph with
data-pi="<n>" (its place among all the body's paragraphs, tables included)
and marks paragraphs that can't be edited safely (pictures, fields) with
data-ro. The editor sends back what changed:

    save(path, edits, out_path, sig="") → {out_path, changed}
    edits = [{"pi": 3, "old": "text shown before", "new": "text now"}]

Only the characters that differ are rewritten: the common start and end of
the old and new text keep their runs (bold, font, colour…); the new middle
goes into the run where the change starts. Tabs and line breaks inside the
removed part are removed with it. The original file is never changed: the
result is always written to *out_path*.
"""
from __future__ import annotations

import os

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# a paragraph holding any of these is shown but not editable
_READONLY = {_W + "drawing", _W + "pict", _W + "fldChar", _W + "instrText", _W + "sym", _W + "object",
             "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"}


class DocxEditError(Exception):
    def __init__(self, msg: str, code: str = "error"):
        super().__init__(msg)
        self.code = code


def file_sig(path: str) -> str:
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


def paragraph_index(body) -> dict:
    """{w:p element: n} for every paragraph in the body, in document order."""
    return {p: i for i, p in enumerate(body.iter(_W + "p"))}


def _own(el, p) -> bool:
    """el belongs to paragraph p itself (not to a text box inside it)."""
    for anc in el.iterancestors(_W + "p"):
        return anc is p
    return False


def is_editable(p) -> bool:
    for el in p.iter():
        if el.tag in _READONLY:
            return False
    return True


def _tokens(p) -> list:
    """[(element, kind, text)] in reading order: kind 't' (text), 'x' (tab / break)."""
    out = []
    for el in p.iter(_W + "t", _W + "tab", _W + "br", _W + "cr", _W + "noBreakHyphen"):
        if not _own(el, p):
            continue
        par = el.getparent()
        if par is None or par.tag != _W + "r":
            continue                      # w:tab inside w:tabs (paragraph settings) etc.
        if el.tag == _W + "t":
            out.append((el, "t", el.text or ""))
        elif el.tag == _W + "tab":
            out.append((el, "x", "\t"))
        elif el.tag == _W + "noBreakHyphen":
            out.append((el, "x", "‑"))
        else:
            if el.get(_W + "type") == "page":
                continue                  # shown as a page break, not as text
            out.append((el, "x", "\n"))
    return out


def para_text(p) -> str:
    return "".join(t for _, _, t in _tokens(p))


def _norm(s: str) -> str:
    return (s or "").replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")


def _set_t(t_el, text: str) -> None:
    t_el.text = text
    if text != text.strip() or "  " in text:
        t_el.set(_XML_SPACE, "preserve")


def _new_run(p, text: str):
    """Append a run with *text* to p, formatted like the paragraph's last run."""
    import copy
    from lxml import etree
    runs = [r for r in p.iter(_W + "r") if _own(r, p)]
    r = etree.SubElement(p, _W + "r")
    if runs:
        rpr = runs[-1].find(_W + "rPr")
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
    t = etree.SubElement(r, _W + "t")
    _set_t(t, text)
    return t


def replace_text(p, old: str, new: str) -> bool:
    """Change paragraph p from *old* to *new* (both as shown). False when the
    paragraph no longer reads *old* (the file changed)."""
    toks = _tokens(p)
    cur = _norm("".join(t for _, _, t in toks))
    old, new = _norm(old), _norm(new)
    if old.strip() == "" and cur == "":
        old = ""
    if cur != old and cur.rstrip("\n") == old.rstrip("\n"):
        tail = cur[len(cur.rstrip("\n")):]          # line breaks at the very end
        old, new = cur, new.rstrip("\n") + tail
    if cur != old:
        return False
    if old == new:
        return True
    # each changed stretch on its own, last first (earlier positions stay valid),
    # so text between two edits keeps its own formatting
    import difflib
    ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in reversed(ops):
        if tag != "equal":
            _splice(p, i1, i2, new[j1:j2])
    return True


def _splice(p, d0: int, d1: int, mid: str) -> None:
    """Replace characters [d0, d1) of paragraph p's text with *mid*."""
    toks = _tokens(p)
    # where the new middle goes: the text piece holding the character just
    # before an insertion (at the very start, the first text piece)
    # (replacing text: the piece where the removed part starts)
    target, pos = None, 0
    for i, (_, kind, t) in enumerate(toks):
        s, e = pos, pos + len(t)
        hit = (s <= d0 < e) if d1 > d0 else ((s < d0 <= e) or (d0 == 0 and s == 0))
        if kind == "t" and hit:
            target = i
            break
        pos = e
    if target is None and mid and toks:
        # no text piece there (e.g. between two tabs): a new w:t in the run,
        # right before the piece that starts at d0 (or after the last one)
        from lxml import etree
        pos, j = 0, None
        for i, (_, _, t) in enumerate(toks):
            if pos >= d0:
                j = i
                break
            pos += len(t)
        t_new = etree.Element(_W + "t")
        _set_t(t_new, mid)
        if j is not None:
            toks[j][0].addprevious(t_new)
        else:
            toks[-1][0].addnext(t_new)
        mid = ""
    pos = 0
    for i, (el, kind, t) in enumerate(toks):
        s, e = pos, pos + len(t)
        pos = e
        if kind == "x":
            if d0 <= s and e <= d1 and d1 > d0:
                par = el.getparent()
                par.remove(el)
            continue
        # the piece minus the removed part [d0, d1); the middle at d0
        keep_a = t[:max(0, min(len(t), d0 - s))]
        keep_b = t[max(0, min(len(t), d1 - s)):]
        text = keep_a + (mid if i == target else "") + keep_b
        if text != t:
            _set_t(el, text)
    if mid and not toks:
        _new_run(p, mid)


def save(path: str, edits: list, out_path: str, sig: str = "") -> dict:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        raise DocxEditError("python-docx is not installed", "missing_dep") from exc
    if not path or not os.path.isfile(path):
        raise DocxEditError(f"File not found: {os.path.basename(str(path or ''))}", "not_found")
    if not out_path:
        raise DocxEditError("No output file given", "bad_args")
    if os.path.abspath(out_path).lower() == os.path.abspath(path).lower():
        raise DocxEditError("Save the edited document as a new file", "bad_args")
    if os.path.exists(out_path):
        raise DocxEditError(f"{os.path.basename(out_path)} already exists", "exists")
    if sig and sig != file_sig(path):
        raise DocxEditError("The document was changed by another program. Open it again to edit.", "changed")
    try:
        document = docx.Document(path)
    except Exception as exc:
        raise DocxEditError(f"Could not read the Word document: {exc}", "bad_file") from exc
    paras = list(document.element.body.iter(_W + "p"))
    changed, skipped = 0, []
    for ed in edits or []:
        try:
            i = int(ed.get("pi"))
        except (TypeError, ValueError):
            continue
        if not 0 <= i < len(paras) or not is_editable(paras[i]):
            skipped.append(i)
            continue
        old, new = str(ed.get("old") or ""), str(ed.get("new") or "")
        if _norm(old) == _norm(new):
            continue
        if replace_text(paras[i], old, new):
            changed += 1
        else:
            skipped.append(i)
    if skipped:
        raise DocxEditError("Some paragraphs could not be matched; nothing was saved. Open the document again.", "mismatch")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    document.save(out_path)
    return {"out_path": out_path, "changed": changed}
