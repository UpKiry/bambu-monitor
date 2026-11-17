#!/usr/bin/env python3
"""Copy the first-screen render into package/pc/cover.png (the card image).

The cover must come from an actual rendered frame, not a hand-drawn mock.
"""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "design" / "page1-task.png"
DST = ROOT / "package" / "pc" / "cover.png"

img = Image.open(SRC)
if img.size != (640, 480):
    raise SystemExit("cover must be 640x480, got %s" % (img.size,))
DST.parent.mkdir(parents=True, exist_ok=True)
img.convert("RGB").save(DST, "PNG", optimize=True)
print("cover %dx%d -> %s (%d bytes)"
      % (img.width, img.height, DST.relative_to(ROOT), DST.stat().st_size))
