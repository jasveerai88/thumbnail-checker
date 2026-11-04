# thumbnail-checker

**Check a thumbnail before YouTube crops, blurs or rejects it.** thumbnail-checker reads a PNG or JPEG's real dimensions straight from the file header — no image library, no install — and reports: resolution, aspect ratio, file size, and filename SEO, each as PASS / WARN / FAIL with a plain-English reason.

100% local: no accounts, no logins, no tracking, your image never leaves your machine.

## Run it

```bash
python3 src/thumbnail_checker.py demo/best-goalkeeper-save-ever-thumbnail.png
```

## Real run (verbatim)

Passing thumbnail (1280×720 PNG, included in `demo/`):

```
$ python3 src/thumbnail_checker.py demo/best-goalkeeper-save-ever-thumbnail.png
File: demo/best-goalkeeper-save-ever-thumbnail.png
Dimensions: 1280x720 (PNG), 4319 bytes
[PASS] Format: PNG image, 1280x720
[PASS] Resolution: 1280x720 meets the 1280x720 minimum
[PASS] Aspect ratio: 1.78 is 16:9 (landscape / YouTube)
[PASS] File size: 4.2 KB
[PASS] Filename SEO: Filename has no spaces
[PASS] Filename SEO: Filename is descriptive length
[PASS] Brightness/contrast (PIL): avg brightness 81/255, contrast (std) 0
Verdict: PASS
```

Failing-size thumbnail (320×240, spaces in the filename — also in `demo/`):

```
$ python3 src/thumbnail_checker.py "demo/small thumb.png"
File: demo/small thumb.png
Dimensions: 320x240 (PNG), 780 bytes
[PASS] Format: PNG image, 320x240
[WARN] Resolution: 320x240 is below the recommended 1280x720 minimum for YouTube
[WARN] Aspect ratio: 1.33 is neither 16:9 nor 9:16 — it may be cropped or letterboxed
[PASS] File size: 0.8 KB
[WARN] Filename SEO: Filename contains spaces — use hyphens instead (e.g. my-thumbnail.jpg)
[PASS] Filename SEO: Filename is descriptive length
[PASS] Brightness/contrast (PIL): avg brightness 81/255, contrast (std) 0
Verdict: WARN
```

A missing file is a clear `FAIL` (`File not found: ...`, exit code 1) — never a traceback.

## What it checks

- **Format + dimensions** — parsed from PNG (`IHDR`) or JPEG (`SOF0/SOF2`) headers using only `struct`; works with no PIL installed.
- **Image integrity** — a header alone never passes: the file's data must actually be there and decode. With PIL installed it must fully verify + load; without PIL, a stdlib structural walk runs instead (PNG: IHDR + IDAT + IEND with valid chunk CRCs and a pixel stream that decompresses to the size the header promises; JPEG: SOI + SOF + SOS + EOI). Corrupt or truncated data is a `FAIL` ("image data corrupt or truncated"), and zero width/height is a `FAIL` too.
- **Extension match** — WARN when the file extension disagrees with the actual format (e.g. JPEG data in a `.png` file).
- **Resolution** — PASS at or above 1280×720 (orientation-aware: 720×1280 portrait passes too), WARN below it.
- **Aspect ratio** — PASS for 16:9 (landscape / YouTube) or 9:16 (vertical / Shorts), WARN for anything else.
- **File size** — FAIL over the 10 MB YouTube limit, WARN over 2 MB.
- **Filename SEO** — WARN on spaces, generic names (`IMG_1234`, `thumbnail`), or very short / very long names.
- **Brightness / contrast (optional)** — if Pillow happens to be installed it reports average brightness + contrast; if not, that one check prints `SKIP` and everything else still runs. In the runs above Pillow was installed, so the line shows real values (the demo PNGs are solid colour, hence contrast 0).

Verdict is the worst check: any FAIL → `FAIL`, else any WARN → `WARN`, else `PASS`. Exit code is 1 only on FAIL, so it can gate an upload script.

## Use with AI agents

Driving this from a coding agent (Claude Code, Codex, …)? Add `--json`:
stdout becomes one JSON object with the verdict, the real dimensions
and format, every check as `{name, status, message}`, and the WARN /
FAIL messages lifted into `warnings` / `failures` lists. Errors in
`--json` mode are JSON too — `{"error": "..."}` — never a traceback.
Full brief for agents, including the JSON schema and exit codes:
[SKILL.md](SKILL.md).

## Tests

```bash
python3 -m unittest discover -s tests -v
# 28 tests: tests generate real PNGs with stdlib struct/zlib (no fixtures,
# no PIL), check the PASS path, the WARN path, vertical 9:16, a hand-built
# JPEG header parse, and the missing-file error — plus corrupt/truncated/
# header-only fixtures, zero dimensions, and extension mismatches, each in
# both PIL-present and simulated PIL-absent modes
```

## Privacy

What it touches: only the image file you point it at (read-only). What it stores: nothing. What it sends: nothing — there is no network code in this tool.

## Why it's free / sponsor

I build free, local-first tools for small and faceless creators from measured failure data on real channels. GitHub Sponsors is live: **https://github.com/sponsors/jasveerai88** — sponsors keep the tools free and fund the next ones.

## Internal use

This is the pre-upload thumbnail gate used on three real YouTube channels — a thumbnail that WARNs here gets fixed before upload, not after a bad first impression.

MIT licensed. Python 3 stdlib only (Pillow optional, never required).
