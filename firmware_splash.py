"""Build a SH1107 power-on image for Raspberry Pi's firmware splash language.

Format v2: https://github.com/raspberrypi/utils/tree/master/splashasm
This runs at build/install time, never during firmware execution.
"""
import argparse
from pathlib import Path
import struct

from boot_display import SH1107_INIT, static_frame, centered_text, pack_pages

MAGIC = b"SPLASH ASM" + b"\0" * 5 + b"\x02"


def power_on_frame():
    frame = bytearray(static_frame(0, "POWER ON"))
    # Firmware execution is synchronous: a stationary percentage would suggest
    # a stalled operation. Real stage progress and motion begin in the helper.
    frame[38 * 128:] = bytes((64 - 38) * 128)
    centered_text(frame, "BOOTING", 48)
    return frame


def transactions():
    yield bytes((0x00, *SH1107_INIT))
    for page, data in enumerate(pack_pages(power_on_frame())):
        yield bytes((0x00, 0x10, 0x00, 0xb0 | page))
        yield b"\x40" + data
    yield b"\x00\xaf"


def build_binary():
    # v2 selects the physical controller: GPIO2/3 are BSC1, not BSC0.
    # The upstream assembler at b57c48e rewrites [port 1] to 0; that
    # output NACKs on this Pi. Encode port 1 explicitly in the wire format.
    setup = struct.pack("<B4sBBHBBBBI", 1, b"I2C ", 8, 1, 0,
                        2, 3, 0x3c, 0, 100000)
    commands = b"".join(struct.pack("<BBBH", 0x10, 0, 0, len(data)) + data
                        for data in transactions())
    return MAGIC + setup + commands


def source_text():
    lines = ["# SH1107 64x128, rotated to 128x64. No artificial boot delay.",
             "# Reference only: firmware_splash.py preserves v2 port 1; see FIRMWARE_SPLASH.txt.",
             "define oled i2c [sda 2] [scl 3] [addr 0x3c] [freq 100000] [port 1]"]
    lines.extend("oled [WRITE] " + " ".join("0x%02x" % value for value in data)
                 for data in transactions())
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    args.output.write_bytes(build_binary())
    if args.source:
        args.source.write_text(source_text())


if __name__ == "__main__":
    main()
