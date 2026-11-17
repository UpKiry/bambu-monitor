#!/usr/bin/env python3
"""Explicitly regenerate a digit atlas in .build; normal builds reuse assets."""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

try:
    from .fonts import resolve_font
except ImportError:
    from fonts import resolve_font

ROOT = Path(__file__).resolve().parent.parent
CHARS = "0123456789:%"
CELL_W, CELL_H = 48, 64
COLS, ROWS = 4, 3
EM = 32.0
SCALE = CELL_H / EM


def build_atlas(atlas_path, metrics_path, font_path=None):
    atlas_path, metrics_path = Path(atlas_path), Path(metrics_path)
    resolved = resolve_font(font_path, kind="digit")
    font = ImageFont.truetype(str(resolved), CELL_H)
    image = Image.new("RGBA", (COLS * CELL_W, ROWS * CELL_H), (0, 0, 0, 0))
    metrics = []
    for index, char in enumerate(CHARS):
        cell = Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))
        ImageDraw.Draw(cell).text((CELL_W / 2, int(CELL_H * 0.80)), char, font=font,
                                  fill=(255, 255, 255, 255), anchor="ms")
        bounds = cell.getbbox()
        top = max(0, min(32, round(bounds[1] / SCALE))) if bounds else 0
        bottom = max(0, min(32, round(bounds[3] / SCALE))) if bounds else 0
        metrics.append([max(4, min(32, round(font.getlength(char) / SCALE))), top, bottom])
        image.alpha_composite(cell, ((index % COLS) * CELL_W, (index // COLS) * CELL_H))
    atlas_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(atlas_path)
    document = {"chars": CHARS, "metrics": metrics, "cell": [CELL_W, CELL_H]}
    metrics_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Explicit glyph regeneration: {atlas_path}, {metrics_path} | font: {resolved}")
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", type=Path, default=ROOT / ".build/glyphs/glyphs.png")
    parser.add_argument("--metrics", type=Path, default=ROOT / ".build/glyphs/glyph_metrics.json")
    parser.add_argument("--font", type=Path, help="Digit font, or set BAMBU_DIGIT_FONT")
    args = parser.parse_args(argv)
    build_atlas(args.atlas, args.metrics, args.font)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
