"""Debug: drag-select one word on page 1 with / without a prior Ctrl+A."""
import sys
import time

from playwright.sync_api import sync_playwright

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from pv_cdp import page_of  # noqa: E402


def drag(pg, word):
    sp = pg.locator('#pdf-canvas-wrap .pdf-page[data-page="0"] .pdf-text-layer span', has_text=word).first
    sb = sp.bounding_box()
    pg.mouse.move(sb["x"] + 1, sb["y"] + sb["height"] / 2)
    pg.mouse.down()
    pg.mouse.move(sb["x"] + sb["width"] - 1, sb["y"] + sb["height"] / 2, steps=5)
    pg.mouse.up()
    return pg.evaluate("[PdfText.selectedText(), window.getSelection().toString().length]")


with sync_playwright() as pw:
    br, pg = page_of(pw)
    pg.evaluate("window.getSelection().removeAllRanges(); Preview.goToPage(1)")
    time.sleep(0.5)
    print("fresh:", drag(pg, sys.argv[1] if len(sys.argv) > 1 else "Hello"))
    bb = pg.locator('#pdf-canvas-wrap .pdf-page[data-page="0"]').bounding_box()
    pg.mouse.click(bb["x"] + 8, bb["y"] + 15)
    pg.keyboard.press("Control+a")
    print("after ctrl+a:", pg.evaluate("PdfText.selectedText().length"))
    print("drag after ctrl+a:", drag(pg, sys.argv[1] if len(sys.argv) > 1 else "Hello"))
    pg.mouse.click(bb["x"] + 8, bb["y"] + 15)
    print("after click empty:", pg.evaluate("window.getSelection().isCollapsed"))
    print("drag after click:", drag(pg, sys.argv[1] if len(sys.argv) > 1 else "Hello"))
