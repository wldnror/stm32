"""Minimal 128x64 splash renderer and the installed SH1107's I2C protocol.

No image/font or application imports: the first frame can precede userspace
networking, font loading and normal application initialization.
"""
import fcntl
import os
from functools import lru_cache

WIDTH, HEIGHT = 128, 64
FONT = {
    " ": (0, 0, 0, 0, 0),
    "A": (0x7e, 0x11, 0x11, 0x11, 0x7e), "B": (0x7f, 0x49, 0x49, 0x49, 0x36),
    "C": (0x3e, 0x41, 0x41, 0x41, 0x22), "D": (0x7f, 0x41, 0x41, 0x22, 0x1c),
    "E": (0x7f, 0x49, 0x49, 0x49, 0x41), "F": (0x7f, 0x09, 0x09, 0x09, 0x01),
    "G": (0x3e, 0x41, 0x49, 0x49, 0x7a), "H": (0x7f, 0x08, 0x08, 0x08, 0x7f),
    "I": (0, 0x41, 0x7f, 0x41, 0), "J": (0x20, 0x40, 0x41, 0x3f, 0x01),
    "K": (0x7f, 0x08, 0x14, 0x22, 0x41), "L": (0x7f, 0x40, 0x40, 0x40, 0x40),
    "M": (0x7f, 0x02, 0x0c, 0x02, 0x7f), "N": (0x7f, 0x04, 0x08, 0x10, 0x7f),
    "O": (0x3e, 0x41, 0x41, 0x41, 0x3e), "P": (0x7f, 0x09, 0x09, 0x09, 0x06),
    "Q": (0x3e, 0x41, 0x51, 0x21, 0x5e), "R": (0x7f, 0x09, 0x19, 0x29, 0x46),
    "S": (0x46, 0x49, 0x49, 0x49, 0x31), "T": (0x01, 0x01, 0x7f, 0x01, 0x01),
    "U": (0x3f, 0x40, 0x40, 0x40, 0x3f), "V": (0x1f, 0x20, 0x40, 0x20, 0x1f),
    "W": (0x3f, 0x40, 0x38, 0x40, 0x3f), "X": (0x63, 0x14, 0x08, 0x14, 0x63),
    "Y": (0x07, 0x08, 0x70, 0x08, 0x07), "Z": (0x61, 0x51, 0x49, 0x45, 0x43),
    "0": (0x3e, 0x51, 0x49, 0x45, 0x3e), "1": (0, 0x42, 0x7f, 0x40, 0),
    "2": (0x42, 0x61, 0x51, 0x49, 0x46), "3": (0x21, 0x41, 0x45, 0x4b, 0x31),
    "4": (0x18, 0x14, 0x12, 0x7f, 0x10), "5": (0x27, 0x45, 0x45, 0x45, 0x39),
    "6": (0x3c, 0x4a, 0x49, 0x49, 0x30), "7": (0x01, 0x71, 0x09, 0x05, 0x03),
    "8": (0x36, 0x49, 0x49, 0x49, 0x36), "9": (0x06, 0x49, 0x49, 0x29, 0x1e),
    ":": (0, 0x36, 0x36, 0, 0), "-": (8, 8, 8, 8, 8),
}


def rectangle(frame, left, top, right, bottom, fill=False):
    for y in range(max(0, top), min(HEIGHT, bottom + 1)):
        for x in range(max(0, left), min(WIDTH, right + 1)):
            if fill or x in (left, right) or y in (top, bottom):
                frame[y * WIDTH + x] = 1


def centered_text(frame, text, y, scale=1):
    text = text.upper()[:WIDTH // (6 * scale)]
    x0 = (WIDTH - (len(text) * 6 - 1) * scale) // 2
    for index, char in enumerate(text):
        for column, bits in enumerate(FONT.get(char, FONT[" "])):
            for row in range(7):
                if bits & (1 << row):
                    x = x0 + (index * 6 + column) * scale
                    rectangle(frame, x, y + row * scale, x + scale - 1,
                              y + (row + 1) * scale - 1, fill=True)


@lru_cache(maxsize=16)
def static_frame(progress, stage):
    frame = bytearray(WIDTH * HEIGHT)
    centered_text(frame, "GDSENG", 7, scale=2)
    centered_text(frame, stage, 28)
    rectangle(frame, 12, 44, 115, 51)
    filled = int(100 * max(0, min(100, progress)) / 100)
    if filled:
        rectangle(frame, 14, 46, 13 + filled, 49, fill=True)
    return bytes(frame)


def render(progress, stage, elapsed, tick=0):
    frame = bytearray(static_frame(progress, stage))
    for index in range((tick % 4) + 1):
        rectangle(frame, 57 + index * 4, 38, 58 + index * 4, 38, fill=True)
    centered_text(frame, str(int(max(0, elapsed))) + "S", 56)
    return frame


def pack_pages(frame):
    """Clockwise rotation plus SH1107 pages, without per-pixel generators."""
    pages = [bytearray(64) for _ in range(16)]
    for y in range(64):
        row = frame[y * WIDTH:(y + 1) * WIDTH]
        x = 63 - y
        for page in range(16):
            start = page * 8
            pages[page][x] = (row[start] | row[start + 1] << 1 | row[start + 2] << 2 |
                              row[start + 3] << 3 | row[start + 4] << 4 | row[start + 5] << 5 |
                              row[start + 6] << 6 | row[start + 7] << 7)
    return pages


class SH1107:
    def __init__(self, path="/dev/i2c-1", address=0x3c):
        self.descriptor = os.open(path, os.O_RDWR)
        try:
            fcntl.ioctl(self.descriptor, 0x0703, address)
            self.command(0xae, 0x20, 0xa6, 0xa8, 0x7f, 0xa4, 0xd3, 0x60,
                         0xd5, 0x80, 0xd9, 0x22, 0xda, 0x12, 0xdb, 0x35,
                         0x81, 0x7f)
        except BaseException:
            os.close(self.descriptor)
            raise

    def command(self, *values):
        os.write(self.descriptor, bytes((0x00, *values)))

    def display(self, frame):
        for page, data in enumerate(pack_pages(frame)):
            self.command(0x10, 0x00, 0xb0 | page)
            os.write(self.descriptor, b"\x40" + data)
        self.command(0xaf)

    def close(self):
        # Preserve the last frame until the application's first frame replaces it.
        os.close(self.descriptor)
