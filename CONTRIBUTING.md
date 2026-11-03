# Contributing

Thanks for helping improve thumbnail-checker!

## Rules
- Python 3 stdlib only for the core checker — no new dependencies. PIL, if present, is optional only.
- Dimension parsing must stay header-based (PNG/JPEG), no image library required.
- Add a test for every behaviour change (generate test images with stdlib `struct`/`zlib`).

## Dev
```bash
python3 -m unittest discover -s tests -v
```

Open an issue or PR with the image dimensions / format that failed.
