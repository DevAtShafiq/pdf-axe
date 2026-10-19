"""Tiny QR code generator for tests (byte mode, ECC level L, versions 1-5).

No third-party QR library is installed in CI, so the QR tests draw their own
codes. Follows the ISO 18004 layout (finder/timing/alignment patterns, mask 0,
BCH format bits); versions 1-5 at level L use a single Reed-Solomon block.
"""
from __future__ import annotations

from PIL import Image

# version -> (total data codewords, ec codewords per block, alignment centres)
_SPEC = {
    1: (19, 7, []),
    2: (34, 10, [6, 18]),
    3: (55, 15, [6, 22]),
    4: (80, 20, [6, 26]),
    5: (108, 26, [6, 30]),
}


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(len(result)):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < len(result):
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list, divisor: list) -> list:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def qr_matrix(text: str) -> list:
    data = text.encode("utf-8")
    for ver, (ndata, nec, align) in _SPEC.items():
        if len(data) + 2 <= ndata:
            break
    else:
        raise ValueError("text too long for this tiny encoder")
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in reversed(range(8))]
    for b in data:
        bits += [(b >> i) & 1 for i in reversed(range(8))]
    cap = ndata * 8
    bits += [0] * min(4, cap - len(bits))
    bits += [0] * (-len(bits) % 8)
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(codewords) < ndata:
        codewords.append(pad)
        pad ^= 0xEC ^ 0x11
    allcw = codewords + _rs_remainder(codewords, _rs_divisor(nec))

    size = ver * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mod[y][x] = dark
        fn[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    n = len(align)
    for i in range(n):
        for j in range(n):
            if (i == 0 and j == 0) or (i == 0 and j == n - 1) or (i == n - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(align[i] + dx, align[j] + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask: int):
        d = (1 << 3) | mask          # ECC L = 01
        rem = d
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        fbits = ((d << 10) | rem) ^ 0x5412
        g = lambda i: (fbits >> i) & 1 != 0  # noqa: E731
        for i in range(6):
            setf(8, i, g(i))
        setf(8, 7, g(6))
        setf(8, 8, g(7))
        setf(7, 8, g(8))
        for i in range(9, 15):
            setf(14 - i, 8, g(i))
        for i in range(8):
            setf(size - 1 - i, 8, g(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, g(i))
        setf(8, size - 8, True)

    draw_format(0)
    i = 0
    total = len(allcw) * 8
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not fn[y][x] and i < total:
                    mod[y][x] = (allcw[i >> 3] >> (7 - (i & 7))) & 1 != 0
                    i += 1
        right -= 2
    for y in range(size):
        for x in range(size):
            if not fn[y][x] and (x + y) % 2 == 0:
                mod[y][x] = not mod[y][x]
    return mod


def qr_image(text: str, scale: int = 8, border: int = 4) -> Image.Image:
    m = qr_matrix(text)
    n = len(m)
    img = Image.new("L", ((n + 2 * border) * scale,) * 2, 255)
    px = img.load()
    for y in range(n):
        for x in range(n):
            if m[y][x]:
                for yy in range((y + border) * scale, (y + border + 1) * scale):
                    for xx in range((x + border) * scale, (x + border + 1) * scale):
                        px[xx, yy] = 0
    return img.convert("RGB")
