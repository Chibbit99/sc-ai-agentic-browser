#!/usr/bin/env python3
"""Generate the SC.AI launcher icon (512x512 PNG) with zero dependencies.

The icon is a teal rounded square with the "SC" wordmark and an accent dot.
It is generated with pure stdlib so the build never needs Pillow or a font.

Usage:
    python3 build/make_icon.py [--out launcher/icon.png]
"""

import argparse
import math
import struct
import zlib
from pathlib import Path

SIZE = 512

# 5x7 bitmap glyphs ("1" = filled cell).
GLYPHS = {
    "S": (
        "01110",
        "10001",
        "10000",
        "01110",
        "00001",
        "10001",
        "01110",
    ),
    "C": (
        "01111",
        "10000",
        "10000",
        "10000",
        "10000",
        "10000",
        "01111",
    ),
}

BG_TOP = (31, 111, 107)    # #1f6f6b
BG_BOTTOM = (18, 74, 71)   # darker teal
INK = (251, 250, 246)      # #fbfaf6
DOT = (95, 189, 182)       # #5fbdb6


def lerp(a, b, t):
    return a + (b - a) * t


def rounded_rect_sdf(x, y, cx, cy, w, h, r):
    dx = abs(x - cx) - (w / 2 - r)
    dy = abs(y - cy) - (h / 2 - r)
    ax, ay = max(dx, 0.0), max(dy, 0.0)
    return math.hypot(ax, ay) + min(max(dx, dy), 0.0) - r


def rect_sdf(x, y, x0, y0, x1, y1):
    dx = max(x0 - x, x - x1, 0.0)
    dy = max(y0 - y, y - y1, 0.0)
    return math.hypot(dx, dy)


def coverage(sdf):
    return max(0.0, min(1.0, 0.5 - sdf))


def render() -> bytes:
    rgba = bytearray(SIZE * SIZE * 4)

    scale = 13
    gap_cells = 1
    text_w = (5 * 2 + gap_cells) * scale
    text_h = 7 * scale
    text_x = (SIZE - text_w) / 2.0
    text_y = (SIZE - text_h) / 2.0
    dot_cx = text_x + text_w + 40
    dot_cy = SIZE / 2.0
    dot_r = 14.0

    for y in range(SIZE):
        fy = y + 0.5
        t = fy / SIZE
        bg_r = lerp(BG_TOP[0], BG_BOTTOM[0], t)
        bg_g = lerp(BG_TOP[1], BG_BOTTOM[1], t)
        bg_b = lerp(BG_TOP[2], BG_BOTTOM[2], t)
        for x in range(SIZE):
            fx = x + 0.5
            bg_cov = coverage(rounded_rect_sdf(fx, fy, SIZE / 2, SIZE / 2, SIZE, SIZE, 112))
            if bg_cov <= 0.0:
                continue
            r, g, b = bg_r, bg_g, bg_b

            # Wordmark: find which glyph cell this pixel falls in.
            gx = int((fx - text_x) / scale)
            gy = int((fy - text_y) / scale)
            cell = "0"
            if 0 <= gy < 7:
                if 0 <= gx <= 4:
                    cell = GLYPHS["S"][gy][gx]
                elif 6 <= gx <= 10:
                    cell = GLYPHS["C"][gy][gx - 6]
            if cell == "1":
                x0 = text_x + gx * scale
                y0 = text_y + gy * scale
                ink = coverage(rect_sdf(fx, fy, x0, y0, x0 + scale, y0 + scale))
                if ink > 0.0:
                    r = lerp(r, INK[0], ink)
                    g = lerp(g, INK[1], ink)
                    b = lerp(b, INK[2], ink)

            # Accent dot.
            dist = math.hypot(fx - dot_cx, fy - dot_cy)
            dot = coverage(dist - dot_r)
            if dot > 0.0:
                r = lerp(r, DOT[0], dot)
                g = lerp(g, DOT[1], dot)
                b = lerp(b, DOT[2], dot)

            idx = (y * SIZE + x) * 4
            rgba[idx] = int(r)
            rgba[idx + 1] = int(g)
            rgba[idx + 2] = int(b)
            rgba[idx + 3] = 255
    return bytes(rgba)


def write_png(path: Path, size: int, rgba: bytes) -> None:
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter: none
        start = y * size * 4
        raw.extend(rgba[start : start + size * 4])
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA

    def chunk(tag: bytes, data: bytes) -> bytes:
        payload = tag + data
        return struct.pack(">I", len(data)) + payload + struct.pack(
            ">I", zlib.crc32(payload) & 0xFFFFFFFF
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", ihdr)
    png += chunk(b"IDAT", zlib.compress(rgba, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the SC.AI launcher icon")
    parser.add_argument("--out", default="launcher/icon.png", help="output PNG path")
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_png(out, SIZE, render())
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()