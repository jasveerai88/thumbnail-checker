# Changelog

## v0.1.1 — 2026-11-03
- TC-1 (critical): corrupt/truncated/header-only images no longer PASS. Integrity is now validated in both modes — PIL (when installed) must fully `verify()` + `load()` the image, and any PIL failure is a FAIL ("image data corrupt or truncated"), never a SKIP; with PIL absent, stdlib structural validation runs instead: PNG chunk walk (IHDR + at least one IDAT + IEND, chunk CRCs, IDAT decompression with expected pixel-data size and row-filter checks) and JPEG marker walk (SOI + SOF + SOS + EOI required).
- TC-2: an image declaring zero width or height now FAILs instead of passing the format check.
- TC-4: a file whose extension disagrees with its actual format (e.g. a real JPEG named `.png`) now gets a WARN — "file extension does not match actual format".
- Regression tests for all of the above, including hand-built corrupt fixtures, in both PIL-present and simulated PIL-absent modes (suite: 23 tests).

## v0.1.0 — 2026-11-03
- Initial release: stdlib-only PNG/JPEG dimension parsing from file headers (no PIL required).
- Checks: resolution (warn <1280x720), aspect ratio 16:9 vs 9:16, file size, filename SEO.
- Optional brightness/contrast via PIL when available; otherwise a clear `SKIP` label.
- Renamed from thumbnail-checker-lite before first publish (owner decision 2026-11-03).
