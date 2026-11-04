# Changelog

## v0.2.0 — 2026-11-03
Agent-ready release (no behaviour change to the existing human output or verdicts):
- `--json` flag: prints one JSON object to stdout capturing the same verdict and checks as the human report (tool, version, file, exists, verdict, real width/height/format, size_bytes, a checks list of {name, status, message}, plus warnings and failures lists). Exit codes are unchanged in both modes (0 PASS/WARN, 1 FAIL).
- Error paths in `--json` mode emit `{"error": "<message>"}` as JSON — a usage error with exit 2, an unreadable file with exit 1 — never plain text, never a traceback. A missing file remains a normal FAIL result object, exactly as in human mode.
- `SKILL.md` added at repo root: an agent-facing brief (purpose, exact commands incl. `--json`, the full JSON schema, exit codes, honesty rules — a corrupt file's FAIL is final and a SKIP is not a PASS).
- README gained a "Use with AI agents" section pointing at `--json` + `SKILL.md`.
- 5 tests added (28 total, all passing): JSON parses and agrees with `check()` on PASS and WARN fixtures, corrupt header-only PNG still FAILs with exit 1 in JSON mode, missing file is a JSON FAIL result, and a usage error yields the JSON error object with exit 2. (The version-pin test moved to 0.2.0 with this bump.)

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
