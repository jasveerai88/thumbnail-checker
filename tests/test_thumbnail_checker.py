import struct
import sys
import unittest
import zlib
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from thumbnail_checker import check, png_dimensions, jpeg_dimensions


def make_png(width: int, height: int, rgb=(200, 30, 30)) -> bytes:
    """Build a real, valid PNG with stdlib only (struct + zlib)."""
    def chunk(typ: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + typ + data
        c += struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        return c
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw = b""
    row = bytes(rgb) * width
    for _ in range(height):
        raw += b"\x00" + row
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


class TestThumbnailChecker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_png_dimensions_parser(self):
        data = make_png(1280, 720)
        self.assertEqual(png_dimensions(data), (1280, 720))

    def test_pass_path_good_thumbnail(self):
        p = self.dir / "best-goalkeeper-save-ever-thumbnail.png"
        p.write_bytes(make_png(1280, 720))
        r = check(str(p))
        self.assertEqual(r["width"], 1280)
        self.assertEqual(r["height"], 720)
        statuses = {name: s for name, s, _ in r["checks"]}
        self.assertEqual(statuses["Resolution"], "PASS")
        self.assertEqual(statuses["Aspect ratio"], "PASS")
        # Brightness check must be PASS (if PIL present) or SKIP, never crash
        bright = [s for n, s, _ in r["checks"] if "Brightness" in n]
        self.assertTrue(bright and bright[0] in ("PASS", "SKIP"))

    def test_warn_path_small_image(self):
        p = self.dir / "small thumb.png"
        p.write_bytes(make_png(320, 240))
        r = check(str(p))
        self.assertEqual(r["verdict"], "WARN")
        statuses = {name: s for name, s, _ in r["checks"]}
        self.assertEqual(statuses["Resolution"], "WARN")
        self.assertEqual(statuses["Aspect ratio"], "WARN")  # 4:3 is neither 16:9 nor 9:16

    def test_vertical_shorts_aspect_passes(self):
        p = self.dir / "vertical-shorts-thumbnail-cover.png"
        p.write_bytes(make_png(720, 1280))
        r = check(str(p))
        statuses = {name: s for name, s, _ in r["checks"]}
        self.assertEqual(statuses["Aspect ratio"], "PASS")
        self.assertEqual(statuses["Resolution"], "PASS")

    def test_jpeg_dimensions_parser(self):
        # Minimal hand-built JPEG: SOI + APP0 + SOF0 (1280x720), stdlib bytes only
        app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"\x00" * 14
        sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 720, 1280) + b"\x03" + b"\x00" * 9
        data = b"\xff\xd8" + app0 + sof0
        self.assertEqual(jpeg_dimensions(data), (1280, 720))

    def test_brightness_skip_without_pil(self):
        # Simulate PIL being absent: a None entry in sys.modules makes
        # `from PIL import Image` raise ImportError, which must yield SKIP.
        import unittest.mock as mock
        img = self.dir / "no-pil-check-thumbnail.png"
        img.write_bytes(make_png(1280, 720))
        with mock.patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
            r = check(str(img))
        bright = [(s, m) for n, s, m in r["checks"] if "Brightness" in n]
        self.assertTrue(bright)
        self.assertEqual(bright[0][0], "SKIP", bright)

    def test_missing_file_error(self):
        r = check(str(self.dir / "does-not-exist.png"))
        self.assertFalse(r["exists"])
        self.assertEqual(r["verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main()
