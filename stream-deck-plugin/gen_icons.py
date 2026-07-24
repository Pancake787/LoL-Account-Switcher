"""Generate placeholder PNG icons for the Stream Deck plugin (no external deps).

Produces the 6 PNGs referenced by manifest.json:
  imgs/plugin/icon.png (+@2x)            -> teal background, white swap arrows
  imgs/actions/switch-inactive.png (+@2x)-> dark gray, hollow ring  (State 0)
  imgs/actions/switch-active.png (+@2x)  -> green, white checkmark   (State 1)

Re-run any time:  py gen_icons.py
"""
import os
import struct
import zlib


class Canvas:
    def __init__(self, size):
        self.size = size
        # RGBA buffer, transparent by default
        self.buf = bytearray(size * size * 4)

    def _put(self, x, y, color):
        x = int(round(x))
        y = int(round(y))
        if 0 <= x < self.size and 0 <= y < self.size:
            i = (y * self.size + x) * 4
            self.buf[i:i + 4] = bytes(color)

    def rounded_rect(self, color, radius):
        s, r = self.size, radius
        for y in range(s):
            for x in range(s):
                # corner check
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

    def to_png(self, path):
        s = self.size
        raw = bytearray()
        stride = s * 4
        for y in range(s):
            raw.append(0)  # filter type 0
            raw.extend(self.buf[y * stride:(y + 1) * stride])

        def chunk(tag, data):
            c = struct.pack(">I", len(data)) + tag + data
            return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">IIBBBBB", s, s, 8, 6, 0, 0, 0)  # 8-bit RGBA
        idat = zlib.compress(bytes(raw), 9)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def draw_plugin(c, k):
    c.rounded_rect((31, 111, 120, 255), int(14 * k))  # teal
    w = (255, 255, 255, 255)
    th = 5 * k
    # top arrow pointing right
    c.thick_line(18 * k, 28 * k, 50 * k, 28 * k, th, w)
    c.thick_line(50 * k, 28 * k, 42 * k, 22 * k, th, w)
    c.thick_line(50 * k, 28 * k, 42 * k, 34 * k, th, w)
    # bottom arrow pointing left
    c.thick_line(54 * k, 44 * k, 22 * k, 44 * k, th, w)
    c.thick_line(22 * k, 44 * k, 30 * k, 38 * k, th, w)
    c.thick_line(22 * k, 44 * k, 30 * k, 50 * k, th, w)


def draw_inactive(c, k):
    bg = (58, 63, 75, 255)  # dark gray
    c.rounded_rect(bg, int(14 * k))
    ring = (150, 156, 168, 255)
    c.disc(36 * k, 36 * k, 18 * k, ring)
    c.disc(36 * k, 36 * k, 12 * k, bg)  # punch hole -> ring


def draw_active(c, k):
    c.rounded_rect((46, 158, 68, 255), int(14 * k))  # green
    w = (255, 255, 255, 255)
    th = 7 * k
    # checkmark
    c.thick_line(20 * k, 38 * k, 31 * k, 50 * k, th, w)
    c.thick_line(31 * k, 50 * k, 54 * k, 23 * k, th, w)


BASE = os.path.join(os.path.dirname(__file__), "com.lolswitcher.plugin.sdPlugin", "imgs")
TARGETS = [
    ("plugin/icon.png", 72, draw_plugin),
    ("plugin/icon@2x.png", 144, draw_plugin),
    ("actions/switch-inactive.png", 72, draw_inactive),
    ("actions/switch-inactive@2x.png", 144, draw_inactive),
    ("actions/switch-active.png", 72, draw_active),
    ("actions/switch-active@2x.png", 144, draw_active),
]

for rel, size, fn in TARGETS:
    c = Canvas(size)
    fn(c, size / 72.0)
    out = os.path.join(BASE, rel)
    c.to_png(out)
    print(f"wrote {rel} ({size}x{size})")
