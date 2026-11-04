---
name: thumbnail-checker
description: Pre-upload check of a YouTube thumbnail PNG/JPEG — real dimensions, data integrity, aspect ratio, file size, filename SEO — with PASS/WARN/FAIL verdicts; use to gate a thumbnail file before it is uploaded.
---

# thumbnail-checker — agent skill

## Purpose

Checks one thumbnail image file before upload. It parses the PNG/JPEG
dimensions from the file header with the standard library only (no
image library required), validates that the image data is actually
intact, and reports resolution, aspect ratio, file size, filename SEO
and extension/format match as PASS / WARN / FAIL checks with
plain-English reasons.

## When to use / not use

Use when:
- A thumbnail file (PNG or JPEG) is about to be uploaded and you need
  a gate verdict: upload it, fix it first, or reject it.
- You need the image's real dimensions/format as data, not from
  metadata claims or the filename.

Do not use when:
- The file is not a PNG/JPEG thumbnail (other formats FAIL as
  unparseable — that is a verdict about this tool's scope, not about
  the image).
- You want the image edited — this tool only reads; it never modifies
  the file.

## Commands

```bash
# human-readable report
python3 src/thumbnail_checker.py demo/best-goalkeeper-save-ever-thumbnail.png

# machine-readable (agents): one JSON object on stdout
python3 src/thumbnail_checker.py path/to/thumbnail.png --json
```

One positional argument: the image path. `--json` switches output to
JSON. `--version` prints the version.

## Output JSON schema

On a completed check, stdout is exactly one JSON object:

- `tool` — `"thumbnail-checker"`
- `version` — tool version string
- `file` — the path as given
- `exists` — bool
- `verdict` — `"PASS"` · `"WARN"` · `"FAIL"` (worst check wins)
- `width`, `height` — real pixel dimensions from the header (or null)
- `format` — `"PNG"` · `"JPEG"` (detected from content, or null)
- `size_bytes` — file size in bytes (or null)
- `checks` — list of `{name, status, message}` in report order;
  status is `"PASS"` · `"WARN"` · `"FAIL"` · `"SKIP"`
- `warnings` — the messages of all WARN checks
- `failures` — the messages of all FAIL checks

On a usage error (e.g. no file argument), stdout is one JSON object
`{"error": "<message>"}` with exit code 2. An unreadable file in
`--json` mode also yields that error object (exit 1) — never a
traceback. A missing file is not an error object: it is a normal FAIL
result (`exists: false`, verdict `FAIL`, exit 1), exactly as in human
mode.

## Exit codes

- `0` — verdict PASS or WARN (WARN still exits 0, as in human mode)
- `1` — verdict FAIL, or an unreadable-file error in `--json` mode
- `2` — usage error (JSON error object in `--json` mode)

## Limits & honesty rules

- A corrupt, truncated or header-only image is a **FAIL** — "image
  data corrupt or truncated". That verdict is final: never soften it,
  never re-run hoping for a different answer, and never upload a file
  this tool FAILs.
- Zero width or height is a FAIL, not a quirk to ignore.
- The optional brightness/contrast check reports `SKIP` when Pillow is
  not installed. A SKIP means "not measured" — it is not a PASS for
  that check, and the verdict is computed without it.
- Dimensions come from the actual file bytes; if the extension and the
  real format disagree, that is a WARN and the content format is the
  truth.
- Verdict is the worst check: one FAIL anywhere makes the whole file
  FAIL, no matter how many PASSes surround it.
