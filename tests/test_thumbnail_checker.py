import io
import json
import struct
import sys
import unittest
import unittest.mock as mock
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from thumbnail_checker import (
    __version__,
    check,
    jpeg_dimensions,
    main,
    png_dimensions,
    validate_jpeg_structure,
    validate_png_structure,
)

try:
    from PIL import Image as _PILImage  # noqa: F401
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _chunk(typ: bytes, data: bytes) -> bytes:
    c = struct.pack(">I", len(data)) + typ + data
    c += struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    return c


def make_png(width: int, height: int, rgb=(200, 30, 30)) -> bytes:
    """Build a real, valid PNG with stdlib only (struct + zlib)."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw = b""
    row = bytes(rgb) * width
    for _ in range(height):
        raw += b"\x00" + row
    idat = _chunk(b"IDAT", zlib.compress(raw))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def make_png_header_only() -> bytes:
    """TC-1 repro (a): 29 bytes — PNG signature + IHDR (1280x720), no
    CRC, no IDAT, no IEND, no pixel data at all."""
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">IIBBBBB", 1280, 720, 8, 2, 0, 0, 0))


def make_png_mismatched_pixels() -> bytes:
    """TC-1 repro (c): valid chunks/CRCs, IHDR claims 1280x720 but the
    IDAT stream holds only a 10x10 image."""
    raw = b""
    for _ in range(10):
        raw += b"\x00" + bytes((200, 30, 30)) * 10
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1280, 720, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw))
            + _chunk(b"IEND", b""))


def make_png_invalid_color_type() -> bytes:
    """PNG whose chunks, CRCs and IDAT all validate structurally, but
    whose IHDR uses reserved color type 1 — only a real decoder (PIL)
    rejects it. Exercises the PIL layer specifically."""
    raw = b"\x00" + b"\x00" * 100
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 10, 10, 8, 1, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw))
            + _chunk(b"IEND", b""))


def make_jpeg_header_only() -> bytes:
    """TC-1 repro (d): 39 bytes — SOI + APP0 + SOF0 (1280x720), no SOS
    scan data, no EOI."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"\x00" * 14
    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 720, 1280) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + app0 + sof0


def make_jpeg_garbage_scan() -> bytes:
    """JPEG with SOI + SOF + SOS + EOI all present (stdlib structure
    passes) but garbage where the entropy-coded scan should be — only
    a real decoder (PIL load) rejects it."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"\x00" * 14
    sof0 = (b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 720, 1280)
            + b"\x03" + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    sos = b"\xff\xda" + struct.pack(">H", 12) + b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00"
    return b"\xff\xd8" + app0 + sof0 + sos + b"\x11\x22\x33\x44" * 20 + b"\xff\xd9"


def no_pil():
    """Simulate PIL being absent: a None entry in sys.modules makes
    `from PIL import Image` raise ImportError."""
    return mock.patch.dict(sys.modules, {"PIL": None, "PIL.Image": None})


class TestThumbnailChecker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        p = self.dir / name
        p.write_bytes(data)
        return p

    def _assert_corrupt_fails(self, r: dict):
        self.assertEqual(r["verdict"], "FAIL", r["checks"])
        fails = [m for _, s, m in r["checks"] if s == "FAIL"]
        self.assertTrue(fails, r["checks"])
        self.assertIn("corrupt or truncated", " ".join(fails))

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
        img = self.dir / "no-pil-check-thumbnail.png"
        img.write_bytes(make_png(1280, 720))
        with no_pil():
            r = check(str(img))
        bright = [(s, m) for n, s, m in r["checks"] if "Brightness" in n]
        self.assertTrue(bright)
        self.assertEqual(bright[0][0], "SKIP", bright)

    def test_missing_file_error(self):
        r = check(str(self.dir / "does-not-exist.png"))
        self.assertFalse(r["exists"])
        self.assertEqual(r["verdict"], "FAIL")

    # ---- TC-1: corrupt / truncated / header-only images must FAIL ----

    def test_tc1_header_only_png_fails_both_modes(self):
        data = make_png_header_only()
        self.assertEqual(len(data), 29)
        p = self._write("header-only-repro-thumbnail.png", data)
        self._assert_corrupt_fails(check(str(p)))          # PIL-present path
        self.assertEqual(main([str(p)]), 1)
        with no_pil():                                      # stdlib path
            self._assert_corrupt_fails(check(str(p)))

    def test_tc1_truncated_png_fails_both_modes(self):
        p = self._write("truncated-repro-thumbnail.png", make_png(1280, 720)[:40])
        self._assert_corrupt_fails(check(str(p)))
        self.assertEqual(main([str(p)]), 1)
        with no_pil():
            self._assert_corrupt_fails(check(str(p)))

    def test_tc1_mismatched_pixel_stream_png_fails_both_modes(self):
        p = self._write("broken-pixels-repro-thumbnail.png", make_png_mismatched_pixels())
        self._assert_corrupt_fails(check(str(p)))
        self.assertEqual(main([str(p)]), 1)
        with no_pil():
            self._assert_corrupt_fails(check(str(p)))

    def test_tc1_header_only_jpeg_fails_both_modes(self):
        data = make_jpeg_header_only()
        self.assertEqual(len(data), 39)
        p = self._write("header-only-repro-thumbnail.jpg", data)
        self._assert_corrupt_fails(check(str(p)))
        self.assertEqual(main([str(p)]), 1)
        with no_pil():
            self._assert_corrupt_fails(check(str(p)))

    def test_tc1_png_bad_crc_fails_both_modes(self):
        data = bytearray(make_png(1280, 720))
        data[len(data) // 2] ^= 0xFF  # flip a byte inside IDAT; CRC now wrong
        p = self._write("bad-crc-repro-thumbnail.png", bytes(data))
        self._assert_corrupt_fails(check(str(p)))
        with no_pil():
            self._assert_corrupt_fails(check(str(p)))

    def test_tc1_stdlib_validators_directly(self):
        self.assertIsNone(validate_png_structure(make_png(1280, 720)))
        self.assertIsNotNone(validate_png_structure(make_png_header_only()))
        self.assertIsNotNone(validate_png_structure(make_png(1280, 720)[:40]))
        self.assertIsNotNone(validate_png_structure(make_png_mismatched_pixels()))
        self.assertIsNotNone(validate_jpeg_structure(make_jpeg_header_only()))
        # the garbage-scan JPEG is structurally complete — the PIL layer
        # (not the stdlib walk) is what must catch it
        self.assertIsNone(validate_jpeg_structure(make_jpeg_garbage_scan()))

    @unittest.skipUnless(HAS_PIL, "PIL not installed — PIL-layer test cannot run")
    def test_tc1_jpeg_garbage_scan_fails_via_pil(self):
        p = self._write("garbage-scan-repro-thumbnail.jpg", make_jpeg_garbage_scan())
        r = check(str(p))
        self._assert_corrupt_fails(r)
        self.assertIn("PIL", " ".join(m for _, s, m in r["checks"] if s == "FAIL"))
        self.assertEqual(main([str(p)]), 1)

    @unittest.skipUnless(HAS_PIL, "PIL not installed — PIL-layer test cannot run")
    def test_tc1_png_invalid_color_type_fails_via_pil(self):
        data = make_png_invalid_color_type()
        self.assertIsNone(validate_png_structure(data))  # stdlib layer passes it
        p = self._write("bad-color-type-repro-thumbnail.png", data)
        r = check(str(p))
        self._assert_corrupt_fails(r)
        self.assertIn("PIL", " ".join(m for _, s, m in r["checks"] if s == "FAIL"))

    # ---- TC-2: zero dimensions must FAIL ----

    def test_tc2_zero_dimensions_fail_both_modes(self):
        for w, h in [(0, 720), (1280, 0), (0, 0)]:
            with self.subTest(w=w, h=h):
                p = self._write(f"zero-{w}x{h}-thumbnail.png", make_png(w, h))
                r = check(str(p))
                self.assertEqual(r["verdict"], "FAIL", r["checks"])
                self.assertEqual(main([str(p)]), 1)
                with no_pil():
                    self.assertEqual(check(str(p))["verdict"], "FAIL")

    # ---- TC-4: extension/content mismatch must WARN ----

    @unittest.skipUnless(HAS_PIL, "PIL needed to write a real JPEG fixture")
    def test_tc4_real_jpeg_named_png_warns(self):
        jpg = self.dir / "real-photo-source-thumbnail.jpg"
        _PILImage.new("RGB", (1280, 720), (30, 120, 200)).save(jpg, "JPEG")
        p = self.dir / "real-photo-thumbnail.png"  # JPEG bytes, .png name
        p.write_bytes(jpg.read_bytes())
        r = check(str(p))
        self.assertEqual(r["format"], "JPEG")
        self.assertEqual(r["verdict"], "WARN", r["checks"])
        warns = [m for _, s, m in r["checks"] if s == "WARN"]
        self.assertIn("file extension does not match actual format", " ".join(warns))
        self.assertEqual(main([str(p)]), 0)  # WARN does not gate the exit code

    def test_tc4_png_named_jpg_warns_both_modes(self):
        p = self._write("real-graphic-thumbnail.jpg", make_png(1280, 720))  # PNG bytes, .jpg name
        r = check(str(p))
        self.assertEqual(r["format"], "PNG")
        self.assertEqual(r["verdict"], "WARN", r["checks"])
        warns = [m for _, s, m in r["checks"] if s == "WARN"]
        self.assertIn("file extension does not match actual format", " ".join(warns))
        with no_pil():
            r2 = check(str(p))
            self.assertEqual(r2["verdict"], "WARN", r2["checks"])
            warns2 = [m for _, s, m in r2["checks"] if s == "WARN"]
            self.assertIn("file extension does not match actual format", " ".join(warns2))

    def test_tc4_matching_extension_no_mismatch_warning(self):
        p = self._write("matching-name-thumbnail.png", make_png(1280, 720))
        r = check(str(p))
        self.assertNotIn("does not match actual format",
                         " ".join(m for _, _, m in r["checks"]))
        self.assertEqual(r["verdict"], "PASS", r["checks"])

    # ---- previously-clean FAIL cases must stay FAIL ----

    def test_zero_byte_file_fails(self):
        p = self._write("zero-byte-thumbnail.png", b"")
        r = check(str(p))
        self.assertEqual(r["verdict"], "FAIL")
        self.assertEqual(main([str(p)]), 1)

    def test_text_file_named_png_fails(self):
        p = self._write("text-file-thumbnail.png", b"this is plain text, not an image")
        self.assertEqual(check(str(p))["verdict"], "FAIL")

    def test_gif_named_png_fails(self):
        p = self._write("gif-file-thumbnail.png", b"GIF89a" + b"\x01\x00\x01\x00" + b"\x00" * 40)
        self.assertEqual(check(str(p))["verdict"], "FAIL")

    def test_version_is_020(self):
        self.assertEqual(__version__, "0.2.0")


class TestJsonMode(unittest.TestCase):
    """v0.2.0 agent-ready mode: --json emits one JSON object whose
    verdict agrees with human mode; corrupt files still FAIL."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        p = self.dir / name
        p.write_bytes(data)
        return p

    def _run_json(self, argv):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = main(argv)
        return code, json.loads(out.getvalue())

    def test_json_pass_parses_and_agrees(self):
        p = self._write("my-great-thumbnail.png", make_png(1280, 720))
        code, data = self._run_json([str(p), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(data["tool"], "thumbnail-checker")
        self.assertEqual(data["version"], __version__)
        self.assertEqual(data["verdict"], check(str(p))["verdict"])
        self.assertEqual(data["verdict"], "PASS")
        self.assertEqual(data["width"], 1280)
        self.assertEqual(data["height"], 720)
        self.assertEqual(data["format"], "PNG")
        self.assertTrue(data["checks"])
        for c in data["checks"]:
            self.assertIn("name", c)
            self.assertIn("status", c)
            self.assertIn("message", c)
        self.assertEqual(data["warnings"], [])
        self.assertEqual(data["failures"], [])

    def test_json_warn_agrees_and_lists_warnings(self):
        p = self._write("small thumb.png", make_png(320, 240))
        code, data = self._run_json([str(p), "--json"])
        self.assertEqual(code, 0)  # WARN still exits 0, as in human mode
        self.assertEqual(data["verdict"], "WARN")
        self.assertEqual(data["verdict"], check(str(p))["verdict"])
        warn_msgs = [c["message"] for c in data["checks"]
                     if c["status"] == "WARN"]
        self.assertTrue(warn_msgs)
        self.assertEqual(data["warnings"], warn_msgs)

    def test_json_corrupt_still_fails_exit1(self):
        p = self._write("header-only-thumbnail.png", make_png_header_only())
        code, data = self._run_json([str(p), "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(data["verdict"], "FAIL")
        self.assertTrue(data["failures"])
        self.assertIn("corrupt or truncated", " ".join(data["failures"]))

    def test_json_missing_file_is_json_fail_not_traceback(self):
        code, data = self._run_json([str(self.dir / "nope.png"), "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(data["verdict"], "FAIL")
        self.assertFalse(data["exists"])

    def test_json_usage_error_is_json_object_exit2(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                main(["--json"])  # missing required file argument
        self.assertEqual(cm.exception.code, 2)
        data = json.loads(out.getvalue())
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
