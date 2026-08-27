#!/usr/bin/env python3
"""Generate an opaque, dependency-free SC.AI PNG icon."""
import argparse
import struct
import zlib
from pathlib import Path

SIZE = 512
BG = (20, 87, 84, 255)
PANEL = (31, 111, 107, 255)
WHITE = (251, 250, 246, 255)
ACCENT = (108, 224, 207, 255)

GLYPHS = {
    "S": ("01110", "10001", "10000", "01110", "00001", "10001", "01110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
}


def inside_round_rect(x, y, x0, y0, x1, y1, radius):
    cx = min(max(x, x0 + radius), x1 - radius)
    cy = min(max(y, y0 + radius), y1 - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 or (
        x0 + radius <= x <= x1 - radius or y0 + radius <= y <= y1 - radius
    ) and x0 <= x <= x1 and y0 <= y <= y1


def render():
    pixels = bytearray(SIZE * SIZE * 4)
    for y in range(SIZE):
        for x in range(SIZE):
            # Solid rounded-square background. No transparent pixels.
            if inside_round_rect(x, y, 8, 8, SIZE - 9, SIZE - 9, 92):
                color = PANEL if 24 <= x < SIZE - 24 and 24 <= y < SIZE - 24 else BG
            else:
                color = BG
            # Teal accent ring.
            d = ((x - 256) ** 2 + (y - 256) ** 2) ** 0.5
            if 184 <= d <= 190:
                color = ACCENT
            # Bitmap SC wordmark.
            scale = 25
            start_x, start_y = 99, 169
            gx, gy = (x - start_x) // scale, (y - start_y) // scale
            if 0 <= gy < 7 and 0 <= gx < 11:
                if gx < 5:
                    cell = GLYPHS["S"][gy][gx]
                elif gx == 5:
                    cell = "0"
                else:
                    cell = GLYPHS["C"][gy][gx - 6]
                if cell == "1":
                    color = WHITE
            # Accent dot.
            if (x - 402) ** 2 + (y - 256) ** 2 <= 18 ** 2:
                color = ACCENT
            i = (y * SIZE + x) * 4
            pixels[i:i + 4] = bytes(color)
    return bytes(pixels)


def write_png(path, rgba):
    raw = bytearray()
    for y in range(SIZE):
        raw.append(0)
        raw.extend(rgba[y * SIZE * 4:(y + 1) * SIZE * 4])

    def chunk(tag, data):
        payload = tag + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xffffffff)

    data = b"\x89PNG\r\n\x1a\n"
    data += chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    data += chunk(b"IEND", b"")
    path.write_bytes(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="launcher/icon.png")
    args = parser.parse_args()
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_png(output, render())
    print(f"wrote {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
