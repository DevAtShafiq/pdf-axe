"""Edge-case probe for the file-tool engines (PDF / archive / convert / crop).

Usage:  PYTHONUTF8=1 python qa/e2e/probe_engines.py <empty scratch dir>
Prints one line per call: result or exception. Used during QA to hunt bugs;
real regressions are covered by tests/.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402

import archive_tools as at  # noqa: E402
import image_crop as ic  # noqa: E402
import media_convert as mc  # noqa: E402
import pdf_tools as pt  # noqa: E402

D = sys.argv[1]
os.makedirs(D, exist_ok=True)


def P(*a):
    print(*a, flush=True)


def tryit(label, fn):
    try:
        P(label, "->", fn())
    except Exception as e:
        P(label, "EXC", type(e).__name__, e)


def mkpdf(name, n=3, enc=None):
    path = os.path.join(D, name)
    if os.path.exists(path):
        return path
    d = pymupdf.open()
    for i in range(n):
        p = d.new_page()
        p.insert_text((72, 72), f"Page {i + 1} hello")
    if enc == "user":
        d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u1", owner_pw="o1")
    elif enc == "owner":
        d.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="o1",
               permissions=pymupdf.PDF_PERM_PRINT)
    else:
        d.save(path)
    return path


plain = mkpdf("plain.pdf")
user = mkpdf("user.pdf", enc="user")
owner = mkpdf("owner.pdf", enc="owner")
bad = os.path.join(D, "bad.pdf")
open(bad, "wb").write(b"not a pdf at all")

tryit("merge owner", lambda: pt.merge([plain, owner], os.path.join(D, "m.pdf")))
tryit("merge user", lambda: pt.merge([plain, user], os.path.join(D, "m.pdf")))
tryit("merge bad", lambda: pt.merge([plain, bad], os.path.join(D, "m.pdf")))
tryit("split owner", lambda: pt.split(owner)["count"])
tryit("extract owner", lambda: pt.extract(owner, "2"))
tryit("info user", lambda: pt.pdf_info(user))
tryit("info bad", lambda: pt.pdf_info(bad))
tryit("thumb missing", lambda: pt.thumbnails(os.path.join(D, "nope.pdf"), [0]))
tryit("compress owner", lambda: mc.compress_pdf(owner, "screen"))
tryit("compress owner target", lambda: mc.compress_pdf(owner, target_kb=1))
tryit("compress user", lambda: mc.compress_pdf(user, "screen"))
tryit("compress bad", lambda: mc.compress_pdf(bad, "screen"))
tryit("preview owner", lambda: mc.preview_pdf(owner, ["screen", "level-5"], True))
tryit("p2i owner", lambda: mc.pdf_to_images(owner, "png", 72)["pages"])
tryit("p2i user", lambda: mc.pdf_to_images(user, "png", 72))
tryit("p2i bad range", lambda: mc.pdf_to_images(plain, "png", 72, "9"))
tryit("p2i bad", lambda: mc.pdf_to_images(bad, "png", 72))
tryit("pdf_is_enc owner", lambda: at.pdf_is_encrypted(owner))
tryit("pdf_check wrong owner", lambda: at.pdf_check_password(owner, "WRONG"))
tryit("pdf_remove wrong owner", lambda: at.pdf_remove_password(owner, "WRONG"))
tryit("pdf_remove right owner", lambda: at.pdf_remove_password(owner, "o1"))
tryit("pdf_set on owner", lambda: at.pdf_set_password(owner, "new1"))
tryit("pdf_set on user nocur", lambda: at.pdf_set_password(user, "new1"))
tryit("pdf_set on user cur", lambda: at.pdf_set_password(user, "new1", current_password="u1"))
tryit("pdf_set bad", lambda: at.pdf_set_password(bad, "x"))
tryit("pdf_remove plain", lambda: at.pdf_remove_password(plain, "x"))
tryit("pdf_remove user wrong", lambda: at.pdf_remove_password(user, "zz"))
tryit("pdf_remove user owner pw", lambda: at.pdf_remove_password(user, "o1"))

cmyk = os.path.join(D, "cmyk.jpg")
Image.new("CMYK", (300, 200), (0, 100, 50, 0)).save(cmyk)
p16 = os.path.join(D, "g16.png")
Image.new("I;16", (300, 200)).save(p16)
ppal = os.path.join(D, "pal.png")
Image.new("P", (100, 100)).save(ppal, transparency=0)
fr = [Image.new("RGB", (50, 50), (i * 80, 0, 0)) for i in range(3)]
gif = os.path.join(D, "anim.gif")
fr[0].save(gif, save_all=True, append_images=fr[1:])
tif = os.path.join(D, "multi.tif")
fr[0].save(tif, save_all=True, append_images=fr[1:])
for p in (cmyk, p16, ppal, gif, tif):
    b = os.path.basename(p)
    tryit("i2p " + b, lambda: mc.images_to_pdf([p])["pages"])
    tryit("i2p q100 " + b, lambda: mc.images_to_pdf([p], quality=100)["pages"])
    tryit("pt.img " + b, lambda: pt.image_to_pdf_doc(p).page_count)
    tryit("crop " + b, lambda: ic.crop_image(p, 1, 1, 20, 20))
    tryit("cmpimg " + b, lambda: mc.compress_image(p, 50)["out"])
    tryit("cmpimg tgt " + b, lambda: mc.compress_image(p, 50, target_bytes=500)["out"])
    tryit("conv webp " + b, lambda: mc.convert_image(p, "webp")["out"])
    tryit("crop_src " + b, lambda: ic.crop_source(p)["format"])
