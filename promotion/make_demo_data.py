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


def make_simple_pdf(path, title, subtitle, paras, pages=1):
    import pymupdf as fitz
    doc = fitz.open()
    for n in range(pages):
        pg = doc.new_page(width=595, height=842)
        _page_header(pg, title, f"{subtitle}  ·  page {n + 1} of {pages}" if pages > 1 else subtitle)
        pg.insert_textbox(fitz.Rect(48, 220, 547, 760), "\n\n".join(paras), fontsize=12, color=(0.15, 0.15, 0.2))
    doc.save(path)
    doc.close()


def make_form(path):
    """A fillable application form (text fields + a checkbox) with a signature line."""
    import pymupdf as fitz
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    _page_header(pg, "Application Form", "Graduate admissions 2026  ·  please fill in and sign")
    fields = [("full_name", "Full name"), ("passport_no", "Passport number"),
              ("date_of_birth", "Date of birth"), ("programme", "Programme applied for")]
    y = 230
    for name, label in fields:
        pg.insert_text((48, y), label, fontsize=11, color=GREY)
        w = fitz.Widget()
        w.field_name, w.field_type = name, fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(220, y - 15, 547, y + 7)
        w.text_fontsize = 11
        w.border_color, w.fill_color = (0.75, 0.78, 0.84), (0.97, 0.98, 1.0)
        pg.add_widget(w)
        y += 46
    pg.insert_text((48, y), "I confirm the information is correct", fontsize=11, color=GREY)
    cb = fitz.Widget()
    cb.field_name, cb.field_type = "confirm", fitz.PDF_WIDGET_TYPE_CHECKBOX
    cb.rect = fitz.Rect(290, y - 13, 304, y + 1)
    pg.add_widget(cb)
    pg.insert_text((48, 640), "Applicant signature", fontsize=11, color=GREY)
    pg.draw_line((48, 700), (300, 700), color=(0.3, 0.3, 0.35), width=0.8)
    pg.insert_text((340, 640), "Photo", fontsize=11, color=GREY)
    pg.draw_rect(fitz.Rect(340, 650, 450, 790), color=(0.6, 0.62, 0.68), width=0.8, dashes="[3] 0")
    doc.save(path)
    doc.close()


def make_xlsx(path):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fees 2026"
    rows = [("Item", "Due date", "Amount (KRW)", "Status"),
            ("Application fee", "2026-03-01", 150000, "Paid"),
            ("Tuition — semester 1", "2026-08-15", 4200000, "Due"),
            ("Dormitory deposit", "2026-08-01", 500000, "Due"),
            ("Health insurance", "2026-09-01", 180000, "Due"),
            ("Orientation", "2026-08-25", 50000, "Due")]
    for r in rows:
        ws.append(r)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F335C")
    for col, w in zip("ABCD", (26, 14, 16, 10)):
        ws.column_dimensions[col].width = w
    wb.save(path)


def page_photo(src_pdf, path, page=0, angle=-2.5, size=(2480, 3300)):
    """A 'phone photo' of a printed page: slight tilt, paper shadow, desk colour."""
    import pymupdf as fitz
    from PIL import Image, ImageFilter
    with fitz.open(src_pdf) as d:
        pix = d[page].get_pixmap(dpi=220)
    pg = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    pg = pg.resize((int(size[0] * 0.82), int(size[0] * 0.82 * pix.height / pix.width)))
    desk = Image.new("RGB", size, (176, 150, 120))
    noise = Image.effect_noise(size, 18).convert("RGB")
    desk = Image.blend(desk, noise, 0.12)
    sh = Image.new("L", size, 0)
    from PIL import ImageDraw
    ox, oy = (size[0] - pg.width) // 2, (size[1] - pg.height) // 2
    ImageDraw.Draw(sh).rectangle([ox + 30, oy + 40, ox + pg.width + 30, oy + pg.height + 40], fill=150)
    sh = sh.filter(ImageFilter.GaussianBlur(40))
    desk.paste((60, 45, 30), (0, 0), sh)
    desk.paste(pg, (ox, oy))
    desk = desk.rotate(angle, resample=Image.BICUBIC, fillcolor=(176, 150, 120))
    desk.save(path, quality=92)


def make_portrait(path, size=(2400, 3200), outfit="suit"):
    """An illustrated (not real) passport-style portrait: flat shapes, soft light."""
    from PIL import Image, ImageDraw, ImageFilter
    W_, H_ = size
    im = Image.new("RGB", size, (232, 236, 242))
    d = ImageDraw.Draw(im)
    for i in range(H_):                                   # soft vertical light
        v = int(244 - 22 * i / H_)
        d.line([(0, i), (W_, i)], fill=(v - 6, v - 2, v + 4))
    cx = W_ // 2
    if outfit == "suit":
        d.ellipse([cx - 1050, 2150, cx + 1050, 4300], fill=(36, 52, 88))                 # jacket
        d.polygon([(cx - 260, 2180), (cx, 2650), (cx + 260, 2180)], fill=(245, 245, 248))   # shirt
        d.polygon([(cx - 70, 2260), (cx + 70, 2260), (cx + 40, 2700), (cx - 40, 2700)], fill=(150, 40, 55))  # tie
    else:
        d.ellipse([cx - 1050, 2150, cx + 1050, 4300], fill=(94, 140, 120))               # casual t-shirt
        d.chord([cx - 230, 2050, cx + 230, 2420], 0, 180, fill=(214, 168, 132))          # round neckline
    d.rounded_rectangle([cx - 190, 1700, cx + 190, 2300], radius=80, fill=(214, 168, 132))   # neck
    d.ellipse([cx - 560, 620, cx + 560, 1950], fill=(40, 30, 28))                    # hair back
    d.ellipse([cx - 470, 760, cx + 470, 2000], fill=(226, 182, 146))                 # face
    d.chord([cx - 520, 560, cx + 520, 1350], 180, 360, fill=(40, 30, 28))           # hair top
    d.ellipse([cx - 540, 1250, cx - 430, 1500], fill=(214, 168, 132))                # ears
    d.ellipse([cx + 430, 1250, cx + 540, 1500], fill=(214, 168, 132))
    for ex in (cx - 190, cx + 190):
        d.ellipse([ex - 70, 1300, ex + 70, 1390], fill=(250, 250, 250))
        d.ellipse([ex - 34, 1310, ex + 34, 1380], fill=(52, 38, 30))
        d.rounded_rectangle([ex - 95, 1220, ex + 95, 1250], radius=14, fill=(52, 38, 30))  # brows
    d.polygon([(cx, 1400), (cx - 45, 1590), (cx + 45, 1590)], fill=(206, 158, 124))  # nose
    d.arc([cx - 160, 1600, cx + 160, 1780], 15, 165, fill=(150, 70, 70), width=22)   # smile
    im = im.filter(ImageFilter.GaussianBlur(2))
    im.save(path, quality=95, dpi=(300, 300))


def make_statement(path, pages=4):
    """A large scanned-looking statement (big images) — something worth compressing."""
    import pymupdf as fitz
    from PIL import Image, ImageDraw
    import io as _io
    doc = fitz.open()
    for n in range(pages):
        img = Image.effect_noise((1654, 2339), 22).convert("RGB")
        img = Image.blend(Image.new("RGB", img.size, (248, 247, 243)), img, 0.18)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 1654, 200], fill=(31, 51, 92))
        for i in range(28):
            y = 350 + i * 66
            d.rectangle([120, y, 1530, y + 3], fill=(205, 208, 214))
            d.rectangle([120, y - 34, 120 + (i * 91) % 600 + 330, y - 15], fill=(120, 124, 132))
        buf = _io.BytesIO()
        img.save(buf, "JPEG", quality=97)
        pg = doc.new_page(width=595, height=842)
        pg.insert_image(pg.rect, stream=buf.getvalue())
        pg.insert_text((40, 40), f"Hanbit Bank (fictional) · Statement · page {n + 1}", fontsize=12, color=(1, 1, 1))
        pg.insert_text((40, 120), f"Account holder: Minji Park   ·   Closing balance: 18,420,000 KRW", fontsize=11)
    doc.save(path)
    doc.close()


def make_qr_receipt(path):
    """Payment receipt with a QR code (needs the 'qrcode' package; skipped without it)."""
    try:
        import qrcode
    except ImportError:
        return False
    import io as _io
    import pymupdf as fitz
    q = qrcode.make("Office Axe demo — receipt NB-2026-00417, application fee 150,000 KRW (fictional)")
    buf = _io.BytesIO()
    q.save(buf, "PNG")
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    _page_header(pg, "Payment Receipt", "Application fee  ·  fictional")
    _rows(pg, 230, [("Receipt no.", "NB-2026-00417"), ("Student", "Minji Park"),
                    ("Amount", "150,000 KRW"), ("Paid on", "1 March 2026")])
    pg.insert_image(fitz.Rect(48, 400, 248, 600), stream=buf.getvalue())
    pg.insert_text((260, 500), "Scan to check this receipt", fontsize=12, color=GREY)
    doc.save(path)
    doc.close()
    return True


def build(out: str) -> dict:
    app = os.path.join(out, "Applications 2026")
    inbox = os.path.join(app, "Documents received")
    os.makedirs(inbox, exist_ok=True)
    make_xlsx(os.path.join(inbox, "Fee schedule.xlsx"))
    make_form(os.path.join(inbox, "Application form.pdf"))
    make_simple_pdf(os.path.join(inbox, "Offer letter.pdf"), "Offer of Admission", "Master of International Business",
                    ["Dear Minji Park,", "We are delighted to offer you a place on the Master of International "
                     "Business programme starting in September 2026.", "Please confirm your acceptance by 30 June 2026.",
                     "(Fictional letter for a product demonstration.)"], pages=2)
    make_simple_pdf(os.path.join(inbox, "Visa checklist.pdf"), "Student Visa Checklist", "Documents to prepare",
                    ["1. Valid passport (6+ months)", "2. Offer letter", "3. Bank statement",
                     "4. Passport photo 35 × 45 mm", "5. Academic certificates with apostille"], pages=2)
    make_statement(os.path.join(inbox, "Bank statement.pdf"))
    make_portrait(os.path.join(inbox, "Passport photo.jpg"))
    make_portrait(os.path.join(inbox, "Casual photo.jpg"), outfit="casual")
    have_qr = make_qr_receipt(os.path.join(inbox, "Payment receipt.pdf"))
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
    page_photo(os.path.join(inbox, "scan_0012.pdf"), os.path.join(inbox, "Page photo 1.jpg"), angle=-2.0)
    page_photo(os.path.join(inbox, "Offer letter.pdf"), os.path.join(inbox, "Page photo 2.jpg"), angle=1.6)
    # Stable, readable order and believable dates.
    now = time.time()
    for i, name in enumerate(sorted(os.listdir(inbox))):
        t = now - 3600 * (i + 1)
        os.utime(os.path.join(inbox, name), (t, t))
    return {"root": out, "app": app, "inbox": inbox, "students": STUDENTS, "qr": have_qr}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "work", time.strftime("run-%Y%m%d-%H%M%S"), "demo-data"))
    a = ap.parse_args(argv)
    info = build(os.path.abspath(a.out))
    print(info["app"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
