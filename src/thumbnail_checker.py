#!/usr/bin/env python3
"""thumbnail-checker — check a thumbnail image with stdlib only.

Parses PNG / JPEG dimensions straight from file headers (no PIL needed).
Checks: resolution, aspect ratio (16:9 vs 9:16), file size, filename SEO.
If PIL happens to be installed it is used *optionally* for average
brightness / contrast; otherwise that check is reported as SKIP.

Stdlib only for the core. No network, no uploads.
"""
from __future__ import annotations

import argparse
import math
import re
import struct
import sys
from pathlib import Path

YT_MIN_W, YT_MIN_H = 1280, 720
MAX_SIZE_BYTES = 10 * 1024 * 1024   # YouTube thumbnail limit
WARN_SIZE_BYTES = 2 * 1024 * 1024


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
    checks.append(("Format", "PASS", f"{fmt} image, {w}x{h}"))

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
    except Exception as exc:  # PIL present but image unreadable by it
        checks.append(("Brightness/contrast (PIL)", "SKIP", f"PIL could not read image: {exc}"))

    statuses = [s for _, s, _ in checks]
    verdict = "FAIL" if "FAIL" in statuses else ("WARN" if "WARN" in statuses else "PASS")
    return {"path": str(path), "exists": True, "verdict": verdict, "checks": checks,
            "width": w, "height": h, "format": fmt, "size_bytes": size}


def format_result(r: dict) -> str:
    lines = [f"File: {r['path']}"]
    if r.get("width"):
        lines.append(f"Dimensions: {r['width']}x{r['height']} ({r['format']}), {r['size_bytes']} bytes")
    for name, status, msg in r["checks"]:
        lines.append(f"[{status}] {name}: {msg}")
    lines.append(f"Verdict: {r['verdict']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check a thumbnail image (stdlib only).")
    ap.add_argument("file", help="Path to a PNG or JPEG thumbnail")
    args = ap.parse_args(argv)
    result = check(args.file)
    print(format_result(result))
    return 1 if result["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
