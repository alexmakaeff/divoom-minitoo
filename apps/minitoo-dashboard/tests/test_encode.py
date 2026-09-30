import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from minitoo_dashboard import encode


def pages(n):
    return [Image.new("RGB", (160, 128), (i * 40, 0, 0)) for i in range(n)]


class BlobTest(unittest.TestCase):
    def test_header(self):
        blob = encode.build_blob(pages(3), 8000)
        self.assertEqual(blob[:6], bytes([0x23, 3, 0x1F, 0x40, 8, 10]))

    def test_frames_are_flagged_jpegs(self):
        blob = encode.build_blob(pages(2), 1000)
        offset = 6
        for _ in range(2):
            self.assertEqual(blob[offset], 0x01)
            length = struct.unpack(">I", blob[offset + 1:offset + 5])[0]
            self.assertEqual(blob[offset + 5:offset + 7], b"\xff\xd8")
            offset += 5 + length
        self.assertEqual(offset, len(blob))

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            encode.build_blob([], 1000)
        with self.assertRaises(ValueError):
            encode.build_blob(pages(1), 70000)
        with self.assertRaises(ValueError):
            encode.build_blob([Image.new("RGB", (10, 10))], 1000)

    def test_deterministic(self):
        self.assertEqual(encode.build_blob(pages(2), 8000), encode.build_blob(pages(2), 8000))


class RawfileTest(unittest.TestCase):
    def test_lines(self):
        payload = bytes(range(256)) * 2 + b"\x01\x02"
        lines = encode.rawfile_lines(payload)
        self.assertEqual(lines[0], "8b 00 02 02 00 00")
        self.assertEqual(len(lines), 1 + 3)
        second = bytes.fromhex(lines[2])
        self.assertEqual(second[:8], b"\x8b\x01\x02\x02\x00\x00\x01\x00")
        self.assertEqual(len(second), 8 + 256)
        self.assertEqual(bytes.fromhex(lines[3])[8:], b"\x01\x02")

    def test_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "frame.raw"
            self.assertEqual(encode.write_rawfile(b"ALERT", path), 1)
            self.assertTrue(path.read_text().startswith("8b 00 05 00 00 00\n"))


if __name__ == "__main__":
    unittest.main()
