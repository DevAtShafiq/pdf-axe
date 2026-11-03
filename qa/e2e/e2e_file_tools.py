"""End-to-end UI test of the file tools in a running QA instance (see cdp.py).

    python qa/e2e/e2e_file_tools.py <empty scratch folder>

Drives the real dialogs (merge, split, extract, arrange, images->PDF,
PDF->images, compress with live preview + KB target, crop, ZIP with AES
password + extract, PDF protect / unlock, AI suit gate) and verifies every
output with PyMuPDF / pyzipper. Prints PASS/FAIL per step.
"""
import io
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymupdf  # noqa: E402
import pyzipper  # noqa: E402
from PIL import Image  # noqa: E402

from cdp_tools import app_page, open_folder, toasts  # noqa: E402

D = os.path.abspath(sys.argv[1])
os.makedirs(D, exist_ok=True)
RESULTS = []


def step(name):
    def deco(fn):
        def run(page):
            try:
                fn(page)
                RESULTS.append((name, "PASS", ""))
                print("PASS", name, flush=True)
            except Exception as e:
                RESULTS.append((name, "FAIL", repr(e)))
                print("FAIL", name, repr(e), flush=True)
                traceback.print_exc()
                try:
                    print("   toasts:", toasts(page)[:4])
                except Exception:
                    pass
            finally:
                try:   # close anything left open
                    page.keyboard.press("Escape")
                    page.evaluate("() => document.querySelectorAll('.modal-overlay').forEach(o => o._close ? o._close() : o.remove())")
                except Exception:
                    pass
        run.__name__ = fn.__name__
        return run
    return deco


def mkpdf(name, n, text="Page"):
    p = os.path.join(D, name)
    doc = pymupdf.open()
    for i in range(n):
        pg = doc.new_page()
        pg.insert_text((72, 72), f"{text} {i + 1} of {name}")
    doc.save(p)
    return p


def mkimg(name, size=(800, 600), color=(30, 120, 200), noise=False):
    p = os.path.join(D, name)
    im = Image.effect_noise(size, 60).convert("RGB") if noise else Image.new("RGB", size, color)
    im.save(p, quality=95)
    return p


def photo_pdf(name, n=3):
    p = os.path.join(D, name)
    doc = pymupdf.open()
    for i in range(n):
        pg = doc.new_page()
        pg.insert_text((72, 60), f"Scan {i + 1} keeps text")
        b = io.BytesIO()
        Image.effect_noise((1700, 2200), 70).convert("RGB").save(b, "JPEG", quality=95)
        pg.insert_image(pymupdf.Rect(36, 80, 560, 800), stream=b.getvalue())
    doc.save(p)
    return p


def wait_file(path, timeout=30):
    t = time.time()
    while time.time() - t < timeout:
        if os.path.exists(path):
            time.sleep(0.3)
            return path
        time.sleep(0.2)
    raise AssertionError(f"not created: {os.path.basename(path)}")


def btn(page, label):
    return page.locator(f'.modal-overlay [data-modal-btn="{label}"]').last


A = mkpdf("alpha.pdf", 4)
B = mkpdf("beta.pdf", 2)
IMG1 = mkimg("img1.jpg", (800, 600))
IMG2 = mkimg("img2.png", (400, 700), (200, 50, 50))
BIG = photo_pdf("scan.pdf", 3)


@step("merge dialog")
def t_merge(page):
    page.evaluate("([a, b, d]) => void PdfTools.openMerge([a, b], d)", [A, B, D])
    page.wait_for_function("() => !document.querySelector('#pt-m-go').disabled")
    page.fill("#pt-m-name", "merged_qa")
    page.click("#pt-m-go")
    out = wait_file(os.path.join(D, "merged_qa.pdf"))
    doc = pymupdf.open(out)
    assert doc.page_count == 6, doc.page_count
    assert "Page 1 of beta.pdf" in doc[4].get_text()
    assert [t[1] for t in doc.get_toc() if t[0] == 1] == ["alpha", "beta"]
    # same name again -> " (2)"
    page.evaluate("([a, b, d]) => void PdfTools.openMerge([a, b], d)", [A, B, D])
    page.wait_for_function("() => !document.querySelector('#pt-m-go').disabled")
    page.fill("#pt-m-name", "merged_qa")
    page.click("#pt-m-go")
    wait_file(os.path.join(D, "merged_qa (2).pdf"))


@step("split every 3")
def t_split(page):
    page.evaluate("a => void PdfTools.openSplit(a)", A)
    page.wait_for_selector("#pt-s-every")
    page.click('.pt-mode[data-mode="every"]')
    page.fill("#pt-s-every", "3")
    page.wait_for_function("() => document.querySelector('#pt-s-prev').textContent.includes('2 files')")
    page.click("#pt-s-go")
    f1 = wait_file(os.path.join(D, "alpha_split", "alpha_p1-3.pdf"))
    f2 = wait_file(os.path.join(D, "alpha_split", "alpha_p4.pdf"))
    assert pymupdf.open(f1).page_count == 3 and pymupdf.open(f2).page_count == 1


@step("split bad range shows error")
def t_split_bad(page):
    page.evaluate("a => void PdfTools.openSplit(a)", A)
    page.wait_for_selector("#pt-s-ranges")
    page.click('.pt-mode[data-mode="ranges"]')
    page.fill("#pt-s-ranges", "1-2, 9")
    page.wait_for_function("() => document.querySelector('#pt-s-err').textContent.includes('out of range')")
    assert page.is_disabled("#pt-s-go")


@step("extract 3,1")
def t_extract(page):
    page.evaluate("a => void PdfTools.openExtract(a)", A)
    page.wait_for_selector("#pt-e-spec")
    page.fill("#pt-e-spec", "3,1")
    page.wait_for_function("() => !document.querySelector('#pt-e-go').disabled")
    page.click("#pt-e-go")
    out = wait_file(os.path.join(D, "alpha_p3_1.pdf"))
    doc = pymupdf.open(out)
    assert doc.page_count == 2 and "Page 3 " in doc[0].get_text() and "Page 1 " in doc[1].get_text()


@step("arrange rotate+delete+save new")
def t_arrange(page):
    page.evaluate("a => void PdfTools.openArrange([a])", A)
    page.wait_for_selector(".pt-tile")
    tiles = page.locator(".pt-tile")
    assert tiles.count() == 4
    tiles.nth(0).click()
    page.keyboard.press("r")                      # rotate page 1 right
    tiles.nth(1).click()
    page.keyboard.press("Delete")                 # delete page 2
    assert page.locator(".pt-tile").count() == 3
    page.keyboard.press("Control+z")
    assert page.locator(".pt-tile").count() == 4
    page.keyboard.press("Control+y")
    assert page.locator(".pt-tile").count() == 3
    page.fill("#pt-a-name", "alpha_arr_qa")
    page.click("#pt-a-save")
    out = wait_file(os.path.join(D, "alpha_arr_qa.pdf"))
    doc = pymupdf.open(out)
    assert doc.page_count == 3 and doc[0].rotation == 90
    assert "Page 3 " in doc[1].get_text()


@step("arrange overwrite original keeps backup")
def t_arrange_over(page):
    src = mkpdf("over.pdf", 3)
    page.evaluate("a => void PdfTools.openArrange([a])", src)
    page.wait_for_selector(".pt-tile")
    page.locator(".pt-tile").nth(2).click()
    page.keyboard.press("Delete")
    page.check('input[name="pt-a-out"][value="over"]', force=True)
    page.click("#pt-a-save")
    page.locator('.modal-overlay.dlg-top .btn-primary, .modal-overlay .btn-danger, .modal-overlay [data-modal-btn="Overwrite"]').last.click()
    t = time.time()
    while pymupdf.open(src).page_count != 2 and time.time() - t < 20:
        time.sleep(0.3)
    assert pymupdf.open(src).page_count == 2
    rv = os.path.join(D, "_to_review")
    backs = [f for f in os.listdir(rv) if f.startswith("over.backup-")]
    assert backs and pymupdf.open(os.path.join(rv, backs[0])).page_count == 3


@step("images to pdf (combine, A4)")
def t_i2p(page):
    page.evaluate("([a, b]) => void ConvertTools.openImagesToPdf([a, b])", [IMG1, IMG2])
    page.wait_for_selector("#i2p-size")
    page.select_option("#i2p-size", "a4")
    btn(page, "Convert").click()
    out = wait_file(os.path.join(D, "img1_combined.pdf"))
    doc = pymupdf.open(out)
    assert doc.page_count == 2
    assert round(doc[0].rect.width) == 842 and round(doc[1].rect.width) == 595   # auto orientation


@step("pdf to images (jpg, range)")
def t_p2i(page):
    page.evaluate("a => void ConvertTools.openPdfToImages(a)", A)
    page.wait_for_selector('[data-seg-for="p2i-which"]')
    page.click('[data-seg-for="p2i-fmt"] [data-v="jpg"]')
    page.click('[data-seg-for="p2i-which"] [data-v="range"]')
    page.wait_for_selector("#p2i-range")
    page.fill("#p2i-range", "2-3")
    btn(page, "Convert").click()
    d = os.path.join(D, "alpha_images")
    wait_file(os.path.join(d, "alpha_p03.jpg"))
    assert sorted(os.listdir(d)) == ["alpha_p02.jpg", "alpha_p03.jpg"]


@step("compress: live preview + custom level + 300 KB target")
def t_compress(page):
    before = os.path.getsize(BIG)
    page.evaluate("a => void ConvertTools.openCompress([a])", BIG)
    # live estimates fill in for every preset
    page.wait_for_function("() => ['screen','ebook','printer','lossless'].every(k => /≈/.test(document.querySelector('#pc-est-' + k).textContent))", timeout=60000)
    page.check('input[name="pc-preset"][value="level"]', force=True)
    page.wait_for_selector("#pc-level-row:not(.hidden)")
    page.fill("#pc-level", "3")
    page.dispatch_event("#pc-level", "input")
    page.wait_for_function("() => /≈/.test(document.querySelector('#pc-level-est').textContent)", timeout=60000)
    est_txt = page.text_content("#pc-level-est")
    btn(page, "Compress").click()
    out = wait_file(os.path.join(D, "scan_compressed.pdf"), 120)
    assert os.path.getsize(out) < before
    print("   level-3 estimate:", est_txt.strip(), "actual:", os.path.getsize(out) // 1024, "KB")
    # target size
    page.check('input[name="cmp-target"][value="custom"]', force=True)
    page.fill("#cmp-custom", "300")
    page.dispatch_event("#cmp-custom", "input")
    btn(page, "Compress").click()   # label now reads "Compress again"
    out2 = wait_file(os.path.join(D, "scan_compressed (2).pdf"), 180)
    assert os.path.getsize(out2) <= 300 * 1024, os.path.getsize(out2)
    assert "Scan 2 keeps text" in pymupdf.open(out2)[1].get_text()


@step("crop (Enter saves full-res crop)")
def t_crop(page):
    page.evaluate("a => void Cropper.open(a)", IMG1)
    page.wait_for_selector(".crop-presets [data-preset='1:1']")
    page.click(".crop-presets [data-preset='1:1']")
    page.locator(".crop-stage canvas").focus()
    page.keyboard.press("Enter")
    out = wait_file(os.path.join(D, "img1_cropped.jpg"))
    w, h = Image.open(out).size
    assert w == h and w >= 500, (w, h)


@step("zip with AES password + extract with prompt")
def t_zip(page):
    page.evaluate("p => { Archive.openZip([p]); }", A)
    page.wait_for_selector("#ax-zip-name")
    page.fill("#ax-zip-name", "alpha_pw")
    page.evaluate("() => { const c = document.querySelector('#ax-pw-on'); c.checked = true; c.dispatchEvent(new Event('change')); }")
    page.fill("#ax-zip-pw", "Secret#123")
    page.fill("#ax-zip-pw2", "Secret#123")
    btn(page, "Create ZIP").click()
    z = wait_file(os.path.join(D, "alpha_pw.zip"))
    time.sleep(1)
    with pyzipper.AESZipFile(z) as zf:
        zf.setpassword(b"Secret#123")
        assert zf.read("alpha.pdf") == open(A, "rb").read()
        assert zf.infolist()[0].flag_bits & 1
    page.evaluate("p => { window.__x = Archive.extract(p); }", z)
    page.wait_for_selector(".ax-ask input[type=password]")
    page.fill(".ax-ask input[type=password]", "wrong")
    btn(page, "Extract").click()
    page.wait_for_function("() => /Wrong password/.test(document.querySelector('.ax-ask .field-error').textContent)")
    page.fill(".ax-ask input[type=password]", "Secret#123")
    btn(page, "Extract").click()
    out = wait_file(os.path.join(D, "alpha_pw", "alpha.pdf"))
    assert open(out, "rb").read() == open(A, "rb").read()


@step("pdf protect + remove password")
def t_pdfpw(page):
    page.evaluate("p => { Archive.pdfProtect(p); }", B)
    page.wait_for_selector("#ax-pp-user")
    page.fill("#ax-pp-user", "open123")
    page.fill("#ax-pp-user2", "open123")
    page.uncheck('[data-perm="copy"]')
    btn(page, "Protect PDF").click()
    out = wait_file(os.path.join(D, "beta_protected.pdf"))
    doc = pymupdf.open(out)
    assert doc.needs_pass and doc.authenticate("open123")
    page.evaluate("p => { Archive.pdfRemovePassword(p); }", out)
    page.wait_for_selector(".ax-ask input[type=password]")
    page.fill(".ax-ask input[type=password]", "open123")
    btn(page, "Remove password").click()
    un = wait_file(os.path.join(D, "beta_unlocked.pdf"))
    d2 = pymupdf.open(un)
    assert not d2.needs_pass and "Page 2 of beta.pdf" in d2[1].get_text()


@step("AI suit: no key -> clear message, nothing written")
def t_ai(page):
    r = page.evaluate("p => SFM.runAiPhoto(p, 'wear_suit', {})", IMG1)
    print("   ai result:", r)
    assert r["ok"] is False
    assert r.get("need_api_key") or r.get("need_subscription") or r.get("need_login"), r
    assert not os.path.exists(os.path.join(D, "img1_suit.jpg"))


with app_page() as page:
    open_folder(page, D)
    only = set(sys.argv[2:])
    for t in (t_merge, t_split, t_split_bad, t_extract, t_arrange, t_arrange_over, t_i2p,
              t_p2i, t_compress, t_crop, t_zip, t_pdfpw, t_ai):
        if not only or t.__name__ in only:
            t(page)

print()
print(f"{sum(1 for r in RESULTS if r[1] == 'PASS')}/{len(RESULTS)} passed")
for r in RESULTS:
    if r[1] != "PASS":
        print("  FAIL", r[0], r[2])
