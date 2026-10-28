# -*- coding: utf-8 -*-
"""
make_demo_data.py — fictional demo files for the Office Axe product video.

    python promotion/make_demo_data.py [--out promotion/work/run-<stamp>]

Creates (all names and contents are made up):

    <out>/Applications 2026/
        students.txt                     the list pasted in shot 1
        Documents received/
            scan_0012.pdf                a "bachelor certificate" scan (renamed in shot 2)
            Study plan.docx              Word files converted in shot 3
            Recommendation letter.docx
            Transcript.pdf               6 pages for Arrange (shot 4):
                                         p3 scanned sideways, p5 cover letter, p6 blank

A fresh folder is used for every run, so nothing from an earlier take is
ever overwritten or removed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

STUDENTS = ["Minji Park", "Arif Hossain", "Sofia Rahman",
            "Daniel Kim", "Nusrat Jahan", "Haruto Sato"]

NAVY = (0.12, 0.2, 0.36)
GREY = (0.42, 0.45, 0.5)
LINE = (0.82, 0.85, 0.89)


def _page_header(page, title, subtitle):
    import pymupdf as fitz
    w = page.rect.width
    page.draw_rect(fitz.Rect(0, 0, w, 86), color=None, fill=NAVY)
    page.insert_text((48, 44), "NORTHBRIDGE UNIVERSITY", fontsize=11, color=(1, 1, 1))
    page.insert_text((48, 66), "Office of the Registrar  ·  Demo document", fontsize=9, color=(0.8, 0.85, 0.95))
    page.insert_text((48, 140), title, fontsize=22, color=NAVY)
    page.insert_text((48, 164), subtitle, fontsize=11, color=GREY)
    page.draw_line((48, 180), (w - 48, 180), color=LINE, width=1)


def _rows(page, y, rows):
    for k, v in rows:
        page.insert_text((48, y), k, fontsize=11, color=GREY)
        page.insert_text((210, y), v, fontsize=11, color=(0.1, 0.1, 0.12))
        y += 26
    return y


def make_certificate(path):
    import pymupdf as fitz
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    _page_header(pg, "Certificate of Graduation", "Bachelor of Business Administration")
    y = _rows(pg, 230, [("Student", "Minji Park"), ("Student ID", "NB-2022-04817"),
                        ("Degree", "Bachelor of Business Administration"),
                        ("Date of award", "20 February 2026"), ("Honours", "Second class, upper")])
    pg.insert_textbox(fitz.Rect(48, y + 20, 547, y + 120),
                      "This is to certify that the student named above has completed all requirements "
                      "of the programme and has been awarded the degree stated. This is a fictional "
                      "document created for a product demonstration.", fontsize=11, color=(0.2, 0.2, 0.25))
    pg.draw_circle((470, 700), 46, color=NAVY, width=2)
    pg.insert_text((437, 704), "SEAL", fontsize=14, color=NAVY)
    doc.save(path)
    doc.close()


def make_transcript(path):
    import pymupdf as fitz
    doc = fitz.open()
    courses = [("BUS101", "Principles of Management", "A"), ("ECO110", "Microeconomics", "B+"),
               ("ACC120", "Financial Accounting", "A-"), ("MKT210", "Marketing", "A"),
               ("STA150", "Business Statistics", "B+"), ("FIN220", "Corporate Finance", "A-")]
    for n in range(1, 5):
        pg = doc.new_page(width=595, height=842)
        _page_header(pg, "Academic Transcript", f"Minji Park  ·  Year {n}  ·  page {n}")
        y = 220
        pg.insert_text((48, y), "Code", fontsize=10, color=GREY)
        pg.insert_text((130, y), "Course", fontsize=10, color=GREY)
        pg.insert_text((480, y), "Grade", fontsize=10, color=GREY)
        y += 12
        for code, name, grade in courses:
            y += 26
            pg.draw_line((48, y + 8), (547, y + 8), color=LINE, width=0.5)
            pg.insert_text((48, y), f"{code[:3]}{n}{code[4:]}", fontsize=11)
            pg.insert_text((130, y), name, fontsize=11)
            pg.insert_text((480, y), grade, fontsize=11)
        pg.insert_text((48, y + 50), f"Year {n} GPA: 3.{70 + n}", fontsize=12, color=NAVY)
        if n == 3:
            pg.set_rotation(90)            # scanned sideways — fixed with Rotate in the demo
    pg = doc.new_page(width=595, height=842)
    _page_header(pg, "Cover Letter", "To the admissions office")
    pg.insert_textbox(fitz.Rect(48, 220, 547, 520),
                      "Dear Admissions Committee,\n\nPlease find enclosed my academic transcript for "
                      "four years of study at Northbridge University. I am applying for the Master's "
                      "programme starting in September 2026.\n\nKind regards,\nMinji Park\n\n"
                      "(Fictional letter for a product demonstration.)", fontsize=12, color=(0.15, 0.15, 0.2))
    doc.new_page(width=595, height=842)    # blank page left by the scanner
    doc.save(path)
    doc.close()


def make_docx(path, title, paras):
    import docx
    from docx.shared import Pt, RGBColor
    d = docx.Document()
    h = d.add_heading(title, 0)
    for r in h.runs:
        r.font.color.rgb = RGBColor(0x1F, 0x33, 0x5C)
    d.add_paragraph("Minji Park  ·  Student ID NB-2022-04817  ·  Fictional demo document").runs[0].font.size = Pt(10)
    for p in paras:
        d.add_paragraph(p)
    d.save(path)


def build(out: str) -> dict:
    app = os.path.join(out, "Applications 2026")
    inbox = os.path.join(app, "Documents received")
    os.makedirs(inbox, exist_ok=True)
    with open(os.path.join(app, "students.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(STUDENTS) + "\n")
    make_certificate(os.path.join(inbox, "scan_0012.pdf"))
    make_transcript(os.path.join(inbox, "Transcript.pdf"))
    make_docx(os.path.join(inbox, "Study plan.docx"), "Study Plan",
              ["I plan to study international business with a focus on sustainable supply chains.",
               "Semester 1: core courses in strategy, data analysis and research methods.",
               "Semester 2: electives in logistics and an internship with a partner company.",
               "After graduating I want to work on responsible sourcing for consumer brands."])
    make_docx(os.path.join(inbox, "Recommendation letter.docx"), "Letter of Recommendation",
              ["I am pleased to recommend Minji Park, who attended my courses for three years.",
               "She is a careful, curious student who works well in teams and leads by example.",
               "I recommend her without reservation for graduate study.",
               "Prof. A. Example, Department of Management (fictional)"])
    # Files sort newest first in the app: give them a stable, readable order.
    now = time.time()
    order = ["Transcript.pdf", "Recommendation letter.docx", "Study plan.docx", "scan_0012.pdf"]
    for i, name in enumerate(order):
        t = now - 3600 * (i + 1)
        os.utime(os.path.join(inbox, name), (t, t))
    return {"root": out, "app": app, "inbox": inbox, "students": STUDENTS}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "work", time.strftime("run-%Y%m%d-%H%M%S"), "demo-data"))
    a = ap.parse_args(argv)
    info = build(os.path.abspath(a.out))
    print(info["app"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
