#!/usr/bin/env python3
"""Build package/assets/glyphs.png plus tools/glyph_metrics.json.

Why an atlas at all: the device renders CJK body text from a compact 16 px
bitmap. Drawing 32-40 px digits through that bitmap means upscaling it, which
looks soft. Large numerals are shipped as an alpha atlas instead.

Cell height is one em (64 px) while metrics are normalised to a 32-unit em, so
consumers must scale bitmaps by px/CELL_H and advances by px/32. Using a single
factor for both made every glyph claim a full cell width and overlap.
"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ATLAS = ROOT / "package" / "assets" / "glyphs.png"
METRICS = ROOT / "tools" / "glyph_metrics.json"

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
CHARS = "0123456789:%"
CELL_W, CELL_H = 48, 64
COLS, ROWS = 4, 3
EM = 32.0
SCALE = CELL_H / EM

ATLAS.parent.mkdir(parents=True, exist_ok=True)

img = Image.new("RGBA", (COLS * CELL_W, ROWS * CELL_H), (0, 0, 0, 0))
font = ImageFont.truetype(FONT, CELL_H)

metrics = []
for i, ch in enumerate(CHARS):
    cx, cy = (i % COLS) * CELL_W, (i // COLS) * CELL_H
    cell = Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(cell)
    cd.text((CELL_W / 2, int(CELL_H * 0.80)), ch, font=font,
            fill=(255, 255, 255, 255), anchor="ms")
    bbox = cell.getbbox()
    top = max(0, min(32, round(bbox[1] / SCALE))) if bbox else 0
    bottom = max(0, min(32, round(bbox[3] / SCALE))) if bbox else 0
    metrics.append([max(4, min(32, round(font.getlength(ch) / SCALE))), top, bottom])
    img.alpha_composite(cell, (cx, cy))

img.save(ATLAS)
METRICS.write_text(json.dumps(
    {"chars": CHARS, "metrics": metrics, "cell": [CELL_W, CELL_H]},
    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print("atlas %dx%d -> %s" % (img.width, img.height, ATLAS.relative_to(ROOT)))
print("metrics -> %s" % METRICS.relative_to(ROOT))
