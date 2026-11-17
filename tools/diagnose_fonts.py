#!/usr/bin/env python3
"""Explain the CJK-vs-digit size mismatch that made the panel look wrong.

The device draws Chinese from a ~16 px bitmap and digits from our glyph atlas.
If the two disagree in ink height the screen reads as two typefaces. This prints
the numbers used to decide the sizes, so a future change can be checked.
"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
metrics = json.loads((ROOT / "tools" / "glyph_metrics.json").read_text(encoding="utf-8"))
atlas = Image.open(ROOT / "package" / "assets" / "glyphs.png").convert("RGBA")
CW, CH = metrics["cell"]
COLS = atlas.width // CW
EM = 32.0

print("=== glyph atlas: cell %dx%d, metrics normalised to a %.0f-unit em ===" % (CW, CH, EM))
for i, ch in enumerate(metrics["chars"]):
    cell = atlas.crop(((i % COLS) * CW, (i // COLS) * CH,
                       (i % COLS) * CW + CW, (i // COLS) * CH + CH))
    bb = cell.getbbox()
    adv, top, bot = metrics["metrics"][i]
    ink = (bot - top)
    print("  %-2s advance=%2d ink=%2d em" % (repr(ch), adv, ink))

digit_ink_32 = (metrics["metrics"][0][2] - metrics["metrics"][0][1]) * 32 / EM
print()
print("digit ink height at font_size=32 : %.0f px" % digit_ink_32)

cjk = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", 16)
for sample in ("预计剩余", "层", "拓竹"):
    img = Image.new("L", (240, 40), 0)
    ImageDraw.Draw(img).text((2, 2), sample, font=cjk, fill=255)
    bb = img.getbbox()
    print("CJK 16px %-6s ink height     : %d px" % (sample, bb[3] - bb[1] if bb else 0))

print()
print("Rule: keep the ratio near 1.5-2.0x, and use only integer atlas scales.")
print("Cell height is %d px, so 32 and 64 are the sharp choices." % CH)
