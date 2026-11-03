# Changelog

## v0.1.0 — 2026-11-03
- Initial release: stdlib-only PNG/JPEG dimension parsing from file headers (no PIL required).
- Checks: resolution (warn <1280x720), aspect ratio 16:9 vs 9:16, file size, filename SEO.
- Optional brightness/contrast via PIL when available; otherwise a clear `SKIP` label.
- Renamed from thumbnail-checker-lite before first publish (owner decision 2026-11-03).
