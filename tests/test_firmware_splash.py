from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import firmware_splash as splash
from boot_display import pack_pages, SH1107_INIT


class FirmwareSplashTests(unittest.TestCase):
    def decode(self):
        binary = splash.build_binary()
        self.assertEqual(binary[:16], b"SPLASH ASM" + b"\0" * 5 + b"\x02")
        setup = struct.unpack_from("<B4sBBHBBBBI", binary, 16)
        self.assertEqual(setup, (1, b"I2C ", 8, 1, 0, 2, 3, 0x3c, 0, 100000))
        position, packets = 33, []
        while position < len(binary):
            opcode, output, flags, length = struct.unpack_from("<BBBH", binary, position)
            # No blocking delays, reads, other output devices or unknown opcodes.
            self.assertEqual((opcode, output, flags), (0x10, 0, 0))
            position += 5
            packets.append(binary[position:position + length])
            self.assertEqual(len(packets[-1]), length)
            self.assertLessEqual(length, 65)
            position += length
        self.assertEqual(position, len(binary))
        return packets

    def test_format_only_writes_to_the_installed_oled(self):
        packets = self.decode()
        self.assertEqual(packets[0], bytes((0, *SH1107_INIT)))
        self.assertEqual(packets[-1], b"\x00\xaf")
        self.assertEqual(len(packets), 34)

    def test_all_controller_pages_match_the_power_on_image(self):
        packets = self.decode()
        pages = pack_pages(splash.power_on_frame())
        for page in range(16):
            self.assertEqual(packets[1 + page * 2], bytes((0, 0x10, 0, 0xb0 | page)))
            self.assertEqual(packets[2 + page * 2], b"\x40" + pages[page])

    def test_native_assets_are_reproducible(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("native_assets", ROOT / "native/build_assets.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual((ROOT / "native/boot_assets.h").read_text(), module.build())

    def test_committed_assets_are_reproducible(self):
        self.assertEqual((ROOT / "deploy/stm32_power_on.bin").read_bytes(), splash.build_binary())
        self.assertEqual((ROOT / "deploy/stm32_power_on.splash").read_text(), splash.source_text())


if __name__ == "__main__":
    unittest.main()
