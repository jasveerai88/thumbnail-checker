#!/usr/bin/env python3
"""thumbnail-checker — check a thumbnail image with stdlib first.

Parses PNG / JPEG dimensions straight from file headers (no PIL needed).
Checks: resolution, aspect ratio (16:9 vs 9:16), file size, filename SEO,
extension/format match, and image-data integrity.

Integrity is validated in both modes:
- stdlib (always): PNG chunk walk (IHDR + IDAT + IEND, CRCs, IDAT
  decompression + expected pixel-data size/filter bytes); JPEG marker
  walk (SOI + SOF + SOS + EOI). A header-only file FAILs here.
- PIL (when installed): ``verify()`` plus a full ``load()``; a PIL
  failure is a FAIL ("image data corrupt or truncated"), never a SKIP.

If PIL happens to be installed it is also used *optionally* for average
brightness / contrast; otherwise that check is reported as SKIP.

Stdlib only for the core. No network, no uploads.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
import zlib
from pathlib import Path

__version__ = "0.2.0"

YT_MIN_W, YT_MIN_H = 1280, 720
MAX_SIZE_BYTES = 10 * 1024 * 1024   # YouTube thumbnail limit
WARN_SIZE_BYTES = 2 * 1024 * 1024

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
JPEG_STANDALONE_MARKERS = {0xD8, 0xD9, 0x01} | set(range(0xD0, 0xD8))
EXTENSION_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    # IHDR is the first chunk: width/height are big-endian uint32 at 16/20
    if data[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    i = 2
    # Markers without a length payload
    standalone = {0xD8, 0xD9, 0x01} | set(range(0xD0, 0xD8))
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while i + 1 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in standalone:
            i += 2
            continue
        if i + 4 > len(data):
            return None
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if length < 2:
            return None
        if marker in sof_markers:
            if i + 9 > len(data):
                return None
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + length
    return None


def image_dimensions(path: Path) -> tuple[int, int, str] | None:
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        dims = png_dimensions(data)
        return (*dims, "PNG") if dims else None
    if data[:2] == b"\xff\xd8":
        dims = jpeg_dimensions(data)
        return (*dims, "JPEG") if dims else None
    return None


def validate_png_structure(data: bytes) -> str | None:
    """Stdlib PNG integrity check. Returns an error string, or None if sound.

    Walks every chunk: requires IHDR first, at least one IDAT and a
    closing IEND, validates every chunk CRC, then decompresses the
    joined IDAT stream and (for non-interlaced images) checks the
    pixel data is exactly the size the IHDR promises, with a legal
    filter byte (0-4) on every row. A header-only or truncated file
    fails here even though its IHDR parses fine.
    """
    if len(data) < 8 or data[:8] != PNG_SIGNATURE:
        return "not a PNG file (bad signature)"
    offset = 8
    seen_ihdr = seen_idat = seen_iend = False
    ihdr_data: bytes | None = None
    idat_data = bytearray()
    first = True
    while offset < len(data):
        if offset + 8 > len(data):
            return "truncated PNG chunk header — image data corrupt or truncated"
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        if not all(65 <= b <= 90 or 97 <= b <= 122 for b in chunk_type):
            return "invalid PNG chunk type — image data corrupt or truncated"
        chunk_end = offset + 8 + length
        crc_end = chunk_end + 4
        if crc_end > len(data):
            return "truncated PNG chunk (missing data or CRC) — image data corrupt or truncated"
        chunk_data = data[offset + 8:chunk_end]
        stored_crc = struct.unpack(">I", data[chunk_end:crc_end])[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != stored_crc:
            return (f"PNG chunk {chunk_type.decode('ascii', 'replace')} has an invalid CRC "
                    "— image data corrupt or truncated")
        if first and chunk_type != b"IHDR":
            return "first PNG chunk is not IHDR — image data corrupt or truncated"
        first = False
        if chunk_type == b"IHDR":
            if seen_ihdr:
                return "multiple IHDR chunks — image data corrupt or truncated"
            if length != 13:
                return "invalid IHDR length — image data corrupt or truncated"
            seen_ihdr = True
            ihdr_data = chunk_data
        elif chunk_type == b"IDAT":
            seen_idat = True
            idat_data.extend(chunk_data)
        elif chunk_type == b"IEND":
            if length != 0:
                return "invalid IEND length — image data corrupt or truncated"
            seen_iend = True
            break
        offset = crc_end
    if not seen_ihdr:
        return "missing IHDR chunk — image data corrupt or truncated"
    if not seen_idat:
        return "missing IDAT chunk (no pixel data) — image data corrupt or truncated"
    if not seen_iend:
        return "missing IEND chunk — image data corrupt or truncated"
    if ihdr_data is not None:
        try:
            raw = zlib.decompress(bytes(idat_data))
        except zlib.error as exc:
            return f"IDAT pixel stream cannot be decompressed ({exc}) — image data corrupt or truncated"
        w, h, bit_depth, color_type, _comp, _filt, interlace = struct.unpack(">IIBBBBB", ihdr_data)
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
        if w > 0 and h > 0 and interlace == 0 and channels is not None:
            row_bytes = (w * channels * bit_depth + 7) // 8
            expected = h * (1 + row_bytes)
            if len(raw) != expected:
                return (f"PNG pixel data is {len(raw)} bytes but {w}x{h} needs {expected} "
                        "— image data corrupt or truncated")
            for row in range(h):
                if raw[row * (1 + row_bytes)] > 4:
                    return "PNG pixel data has an invalid row filter byte — image data corrupt or truncated"
    return None


def validate_jpeg_structure(data: bytes) -> str | None:
    """Stdlib JPEG integrity check. Returns an error string, or None if sound.

    Requires SOI at the start, a SOF marker (dimensions), an SOS
    marker (actual scan data follows it) and an EOI marker after the
    scan. A header that never reaches scan data fails here.
    """
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return "not a JPEG file (missing SOI marker)"
    sof_found = False
    i = 2
    while i + 1 < len(data):
        if data[i] != 0xFF:
            return "corrupt JPEG marker — image data corrupt or truncated"
        marker = data[i + 1]
        if marker == 0xDA:  # SOS: scan data follows; EOI must appear in it
            if not sof_found:
                return "JPEG scan data appears before any SOF marker — image data corrupt or truncated"
            if b"\xff\xd9" not in data[i + 2:]:
                return "missing EOI marker after scan data — image data corrupt or truncated"
            return None
        if marker == 0xD9:  # EOI before any SOS
            return "missing SOS marker (no scan data) — image data corrupt or truncated"
        if marker in JPEG_STANDALONE_MARKERS:
            i += 2
            continue
        if i + 4 > len(data):
            return "truncated JPEG segment — image data corrupt or truncated"
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if length < 2:
            return "invalid JPEG segment length — image data corrupt or truncated"
        if marker in JPEG_SOF_MARKERS:
            sof_found = True
        i += 2 + length
    if not sof_found:
        return "missing SOF marker — image data corrupt or truncated"
    return "missing SOS marker (no scan data) — image data corrupt or truncated"


def validate_image_structure(data: bytes, fmt: str) -> str | None:
    """Dispatch to the stdlib structural validator for *fmt*."""
    if fmt == "PNG":
        return validate_png_structure(data)
    if fmt == "JPEG":
        return validate_jpeg_structure(data)
    return f"unsupported format {fmt}"


def _pil_integrity_error(path: Path) -> str | None:
    """Full PIL verify + load. None if PIL is absent or the image is sound.

    ``verify()`` alone does not decode pixel data, so a second open
    with ``load()`` follows it — a broken pixel stream must fail here,
    never fall through as a SKIP.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            im.load()
    except Exception as exc:
        return str(exc)
    return None


def _close(a: float, b: float, tol: float = 0.06) -> bool:
    return abs(a - b) <= tol


def check_filename(name: str) -> list[tuple[str, str]]:
    """Return [(status, message)] — status in PASS/WARN."""
    out = []
    stem = Path(name).stem
    if " " in name:
        out.append(("WARN", "Filename contains spaces — use hyphens instead (e.g. my-thumbnail.jpg)"))
    else:
        out.append(("PASS", "Filename has no spaces"))
    if re.fullmatch(r"(?i)(img|image|thumbnail|thumb|screenshot|dsc|pxl)[-_ ]?\d*", stem.strip()):
        out.append(("WARN", "Filename looks generic (IMG_/thumbnail) — use descriptive SEO words"))
    elif len(stem) < 10:
        out.append(("WARN", "Filename is very short — add descriptive keywords for SEO"))
    elif len(stem) > 80:
        out.append(("WARN", "Filename is very long (>80 chars) — shorten it"))
    else:
        out.append(("PASS", "Filename is descriptive length"))
    if stem != stem.lower() and stem != stem.upper():
        pass  # mixed case is fine, no warning
    return out


def check(path_str: str) -> dict:
    path = Path(path_str)
    checks: list[tuple[str, str, str]] = []  # (name, status, message)
    if not path.exists():
        return {"path": str(path), "exists": False, "verdict": "FAIL",
                "checks": [("File", "FAIL", f"File not found: {path}")],
                "width": None, "height": None, "format": None, "size_bytes": None}
    if not path.is_file():
        return {"path": str(path), "exists": False, "verdict": "FAIL",
                "checks": [("File", "FAIL", f"Not a file: {path}")],
                "width": None, "height": None, "format": None, "size_bytes": None}

    size = path.stat().st_size
    dims = image_dimensions(path)
    if dims is None:
        checks.append(("Format", "FAIL", "Could not parse PNG/JPEG dimensions from header (unsupported or corrupt file)"))
        return {"path": str(path), "exists": True, "verdict": "FAIL", "checks": checks,
                "width": None, "height": None, "format": None, "size_bytes": size}
    w, h, fmt = dims

    def _fail(checks_so_far: list[tuple[str, str, str]]) -> dict:
        return {"path": str(path), "exists": True, "verdict": "FAIL", "checks": checks_so_far,
                "width": w, "height": h, "format": fmt, "size_bytes": size}

    # TC-2: zero dimensions are invalid, never a pass/warn
    if w == 0 or h == 0:
        checks.append(("Format", "FAIL",
                       f"{fmt} image has a zero dimension ({w}x{h}) — width and height must be greater than zero"))
        return _fail(checks)

    # TC-1 layer 2 (always on): stdlib structural validation
    data = path.read_bytes()
    struct_err = validate_image_structure(data, fmt)
    if struct_err is not None:
        checks.append(("Format", "FAIL", f"image data corrupt or truncated: {struct_err}"))
        return _fail(checks)

    # TC-1 layer 1: when PIL is installed it must fully verify + load
    pil_err = _pil_integrity_error(path)
    if pil_err is not None:
        checks.append(("Format", "FAIL",
                       f"image data corrupt or truncated: PIL could not fully load/verify the image: {pil_err}"))
        return _fail(checks)

    checks.append(("Format", "PASS", f"{fmt} image, {w}x{h}"))

    # TC-4: extension/content mismatch
    expected_fmt = EXTENSION_FORMATS.get(path.suffix.lower())
    if expected_fmt is not None and expected_fmt != fmt:
        checks.append(("Extension", "WARN",
                       f"file extension does not match actual format: "
                       f"'{path.suffix.lower()}' implies {expected_fmt} but the file contains {fmt} data"))

    # Resolution: warn <1280x720, orientation-aware (portrait swaps the pair)
    long_side, short_side = max(w, h), min(w, h)
    if long_side >= YT_MIN_W and short_side >= YT_MIN_H:
        checks.append(("Resolution", "PASS", f"{w}x{h} meets the 1280x720 minimum"))
    else:
        checks.append(("Resolution", "WARN", f"{w}x{h} is below the recommended 1280x720 minimum for YouTube"))

    # Aspect ratio
    ratio = w / h if h else 0
    if _close(ratio, 16 / 9):
        checks.append(("Aspect ratio", "PASS", f"{ratio:.2f} is 16:9 (landscape / YouTube)"))
    elif _close(ratio, 9 / 16):
        checks.append(("Aspect ratio", "PASS", f"{ratio:.2f} is 9:16 (vertical / Shorts)"))
    else:
        checks.append(("Aspect ratio", "WARN", f"{ratio:.2f} is neither 16:9 nor 9:16 — it may be cropped or letterboxed"))

    # File size
    kb = size / 1024
    if size > MAX_SIZE_BYTES:
        checks.append(("File size", "FAIL", f"{kb:.0f} KB exceeds the 10 MB YouTube thumbnail limit"))
    elif size > WARN_SIZE_BYTES:
        checks.append(("File size", "WARN", f"{kb:.0f} KB is large (>2 MB) — compress if possible"))
    else:
        checks.append(("File size", "PASS", f"{kb:.1f} KB"))

    # Filename SEO
    for status, msg in check_filename(path.name):
        checks.append(("Filename SEO", status, msg))

    # Optional brightness / contrast via PIL
    try:
        from PIL import Image  # type: ignore
        with Image.open(path) as im:
            gray = im.convert("L")
            pixels = list(gray.getdata())
            if pixels:
                mean = sum(pixels) / len(pixels)
                var = sum((p - mean) ** 2 for p in pixels) / len(pixels)
                checks.append(("Brightness/contrast (PIL)", "PASS",
                               f"avg brightness {mean:.0f}/255, contrast (std) {math.sqrt(var):.0f}"))
            else:
                checks.append(("Brightness/contrast (PIL)", "SKIP", "No pixel data"))
    except ImportError:
        checks.append(("Brightness/contrast (PIL)", "SKIP", "PIL not installed — brightness/contrast skipped (optional)"))
    except Exception as exc:  # PIL present but image unreadable by it — corrupt, never a SKIP
        checks.append(("Brightness/contrast (PIL)", "FAIL",
                       f"image data corrupt or truncated: PIL could not read image: {exc}"))

    statuses = [s for _, s, _ in checks]
    verdict = "FAIL" if "FAIL" in statuses else ("WARN" if "WARN" in statuses else "PASS")
    return {"path": str(path), "exists": True, "verdict": verdict, "checks": checks,
            "width": w, "height": h, "format": fmt, "size_bytes": size}


def format_result(r: dict) -> str:
    lines = [f"File: {r['path']}"]
    if r.get("width") is not None:
        lines.append(f"Dimensions: {r['width']}x{r['height']} ({r['format']}), {r['size_bytes']} bytes")
    for name, status, msg in r["checks"]:
        lines.append(f"[{status}] {name}: {msg}")
    lines.append(f"Verdict: {r['verdict']}")
    return "\n".join(lines)


def result_as_json(r: dict) -> dict:
    """The check() result as one JSON-ready object for --json mode.

    Mirrors exactly what format_result() shows a human: the same
    verdict, every check with its status and message, and the WARN /
    FAIL messages lifted into warnings / failures lists. A FAIL is
    never softened and a SKIP is never dressed up as a PASS.
    """
    return {
        "tool": "thumbnail-checker",
        "version": __version__,
        "file": r["path"],
        "exists": r["exists"],
        "verdict": r["verdict"],
        "width": r["width"],
        "height": r["height"],
        "format": r["format"],
        "size_bytes": r["size_bytes"],
        "checks": [
            {"name": name, "status": status, "message": msg}
            for name, status, msg in r["checks"]
        ],
        "warnings": [msg for _, s, msg in r["checks"] if s == "WARN"],
        "failures": [msg for _, s, msg in r["checks"] if s == "FAIL"],
    }


class _JsonAwareParser(argparse.ArgumentParser):
    """ArgumentParser that reports usage errors as JSON in --json mode
    ({"error": ...} on stdout, same exit code 2) — an agent driving
    this tool with --json never gets plain text or a traceback."""

    json_mode = False

    def error(self, message):
        if self.json_mode:
            print(json.dumps({"error": message}))
            self.exit(2)
        super().error(message)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = _JsonAwareParser(description="Check a thumbnail image (stdlib first, PIL optional).")
    ap.json_mode = "--json" in argv
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true",
                    help="Print one JSON object (verdict, checks, warnings) "
                         "instead of the human-readable report")
    ap.add_argument("file", help="Path to a PNG or JPEG thumbnail")
    args = ap.parse_args(argv)
    if args.json:
        try:
            result = check(args.file)
        except Exception as exc:  # unreadable file etc. — JSON, never a traceback
            print(json.dumps({"error": f"could not check file: {exc}"}))
            return 1
        print(json.dumps(result_as_json(result), indent=2))
        return 1 if result["verdict"] == "FAIL" else 0
    result = check(args.file)
    print(format_result(result))
    return 1 if result["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
