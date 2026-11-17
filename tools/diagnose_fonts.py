#!/usr/bin/env python3
"""Report atlas metrics and installed CJK font ink heights without modifying files."""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

try:
    from .fonts import resolve_font
except ImportError:
    from fonts import resolve_font

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path)
    parser.add_argument("--metrics", type=Path, default=ROOT / "tools/glyph_metrics.json")
    args = parser.parse_args(argv)
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    print(f"Glyph cells: {metrics['cell']}; metrics normalized to a 32-unit em")
    for char, (advance, top, bottom) in zip(metrics["chars"], metrics["metrics"]):
        print(f"{char!r}: advance={advance}, ink={bottom - top}")
    font = ImageFont.truetype(str(resolve_font(args.font)), 16)
    for sample in ("预计剩余", "层", "拓竹"):
        image = Image.new("L", (240, 40), 0)
        ImageDraw.Draw(image).text((2, 2), sample, font=font, fill=255)
        bounds = image.getbbox()
        print(f"CJK 16px {sample}: ink height={bounds[3] - bounds[1] if bounds else 0}")
    print("Device font metrics still require actual device verification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
