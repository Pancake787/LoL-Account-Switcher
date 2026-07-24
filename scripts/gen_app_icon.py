"""Generate assets/icon.ico for the PyInstaller build (no external deps).

Draws the teal swap-arrow mark (matching the Stream Deck plugin icon) at 256px
and wraps it as a PNG-compressed ICO entry (Vista+ format).

Re-run any time:  py scripts/gen_app_icon.py
"""
import os
import struct
import zlib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Canvas:
    def __init__(self, size):
        self.size = size
        self.buf = bytearray(size * size * 4)

    def _put(self, x, y, color):
        x, y = int(round(x)), int(round(y))
        if 0 <= x < self.size and 0 <= y < self.size:
            i = (y * self.size + x) * 4
            self.buf[i:i + 4] = bytes(color)

    def rounded_rect(self, color, radius):
        s, r = self.size, radius
        for y in range(s):
            for x in range(s):
                cx = min(max(x, r), s - 1 - r)
                cy = min(max(y, r), s - 1 - r)
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self._put(x, y, color)

    def disc(self, cx, cy, rad, color):
        r2 = rad * rad
        for y in range(int(cy - rad - 1), int(cy + rad + 2)):
            for x in range(int(cx - rad - 1), int(cx + rad + 2)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                    self._put(x, y, color)

    def thick_line(self, x0, y0, x1, y1, thickness, color):
        steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 3) + 1
        rad = thickness / 2.0
        for i in range(steps + 1):
            t = i / steps
            self.disc(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, rad, color)

    def png_bytes(self):
        s = self.size
        raw = bytearray()
        stride = s * 4
        for y in range(s):
            raw.append(0)
            raw.extend(self.buf[y * stride:(y + 1) * stride])

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", s, s, 8, 6, 0, 0, 0)
        idat = zlib.compress(bytes(raw), 9)
        return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def draw(c, k):
    c.rounded_rect((31, 111, 120, 255), int(40 * k))  # teal, rounded
    w = (255, 255, 255, 255)
    th = 18 * k
    # top arrow -> right
    c.thick_line(64 * k, 100 * k, 178 * k, 100 * k, th, w)
    c.thick_line(178 * k, 100 * k, 150 * k, 78 * k, th, w)
    c.thick_line(178 * k, 100 * k, 150 * k, 122 * k, th, w)
    # bottom arrow -> left
    c.thick_line(192 * k, 156 * k, 78 * k, 156 * k, th, w)
    c.thick_line(78 * k, 156 * k, 106 * k, 134 * k, th, w)
    c.thick_line(78 * k, 156 * k, 106 * k, 178 * k, th, w)


SIZE = 256
c = Canvas(SIZE)
draw(c, SIZE / 256.0)
png = c.png_bytes()

# ICO container with one PNG-compressed entry
# ICONDIR (6) + ICONDIRENTRY (16) + PNG data
width = 0 if SIZE >= 256 else SIZE  # 0 means 256
icondir = struct.pack("<HHH", 0, 1, 1)
entry = struct.pack("<BBBBHHII", width, width, 0, 0, 1, 32, len(png), 22)
ico = icondir + entry + png

out_dir = os.path.join(REPO_ROOT, "assets")
os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, "icon.ico")
with open(out, "wb") as f:
    f.write(ico)
print(f"wrote assets/icon.ico ({SIZE}x{SIZE}, {len(ico)} bytes)")
