#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate icon.ico: folder + list motif in Windows accent blue (Pillow required).
Run: pip install pillow && python build_icon.py
"""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError as e:
    raise SystemExit("Install Pillow first: pip install pillow") from e

OUT_PATH = Path(__file__).resolve().parent / "icon.ico"


def draw_folder_icon(size: int) -> Image.Image:
    """Draw a crisp folder-with-lines icon at the given square size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = float(size)
    m = max(2.0, s * 0.06)

    # Rounded app tile background
    bg = (0, 120, 212, 255)
    r_bg = max(3, int(s * 0.18))
    d.rounded_rectangle([m, m, s - m, s - m], radius=r_bg, fill=bg)

    # Inner folder metrics (classic tab folder)
    ix0 = m + s * 0.14
    iy_tab = m + s * 0.22
    ix1 = s - m - s * 0.14
    iy0 = m + s * 0.36
    iy1 = s - m - s * 0.14

    tab_left = ix0
    tab_right = ix0 + s * 0.42
    tab_top = iy_tab
    tab_bot = iy0

    light = (210, 232, 255, 255)
    mid = (90, 168, 235, 255)
    shadow = (40, 120, 190, 255)

    # Folder body
    d.rounded_rectangle(
        [ix0, iy0, ix1, iy1],
        radius=max(2, int(s * 0.04)),
        fill=mid,
        outline=shadow,
        width=max(1, int(s * 0.02)),
    )

    # Tab (trapezoid feel with polygon)
    d.polygon(
        [
            (tab_left, tab_bot),
            (tab_left + s * 0.02, tab_top + s * 0.06),
            (tab_left + s * 0.12, tab_top),
            (tab_right - s * 0.08, tab_top),
            (tab_right, tab_top + s * 0.05),
            (tab_right, tab_bot),
        ],
        fill=light,
        outline=shadow,
    )

    # “Student list” lines on folder front
    pad = s * 0.12
    lx0 = ix0 + pad
    lx1 = ix1 - pad
    base_y = iy0 + (iy1 - iy0) * 0.28
    gap = (iy1 - iy0) * 0.16
    line_w = max(1, int(s * 0.018))
    paper = (255, 255, 255, 230)
    for i in range(3):
        y = base_y + i * gap
        cut = s * (0.08 + 0.06 * (i % 2))
        d.line([(lx0, y), (lx1 - cut, y)], fill=paper, width=line_w)

    return img


def main() -> None:
    dims = [256, 128, 64, 48, 32, 16]
    images = [draw_folder_icon(n) for n in dims]
    first, *rest = images
    first.save(
        OUT_PATH,
        format="ICO",
        append_images=rest,
    )
    print(f"Wrote {OUT_PATH} ({', '.join(map(str, dims))} px)")


if __name__ == "__main__":
    main()
