#!/usr/bin/env python3
"""Render offline previews with the checked-in atlas and an installed CJK font.

This approximates the declared surface; it is not a firmware/device capture.
"""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

try:
    from .fonts import resolve_font
except ImportError:
    from fonts import resolve_font

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("page1-task.png", "page2-environment.png", "page3-ams.png")


def rgb565(value):
    return (((value >> 11) & 0x1F) * 255 // 31,
            ((value >> 5) & 0x3F) * 255 // 63,
            (value & 0x1F) * 255 // 31)


def atlas_text(img, x, y, text, px, color, align, box_width, atlas, spec):
    cell_width, cell_height = spec["cell"]
    columns = atlas.width // cell_width
    advance_scale = px / 32.0
    bitmap_scale = px / float(cell_height)
    widths = [spec["metrics"][spec["chars"].index(char)][0] * advance_scale
              for char in text if char in spec["chars"]]
    total = sum(widths)
    cursor = x if align == "left" else (x + box_width - total if align == "right"
                                       else x + (box_width - total) / 2)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    for char in text:
        if char not in spec["chars"]:
            cursor += px * 0.3
            continue
        index = spec["chars"].index(char)
        advance = spec["metrics"][index][0]
        left, top = (index % columns) * cell_width, (index // columns) * cell_height
        cell = atlas.crop((left, top, left + cell_width, top + cell_height))
        resized = cell.resize((max(1, round(cell_width * bitmap_scale)),
                               max(1, round(cell_height * bitmap_scale))), Image.Resampling.LANCZOS)
        layer.alpha_composite(resized, (round(cursor), round(y)))
        cursor += advance * advance_scale
    tint = Image.new("RGBA", layer.size, color + (255,))
    tint.putalpha(layer.getchannel("A"))
    img.alpha_composite(tint)


def render_page(runtime, atlas, font, page_index):
    surface = runtime["surface"]
    values = {key: value.get("init", 0) for key, value in runtime["vars"].items()}
    spec = surface["fonts"][0]
    img = Image.new("RGBA", (640, 480), rgb565(surface["background"]) + (255,))
    draw = ImageDraw.Draw(img)
    for node in surface["elements"]:
        if node.get("page", 4) not in (4, page_index):
            continue
        color = rgb565(node["color"])
        x, y, width, height = (node[key] for key in ("x", "y", "w", "h"))
        if node["type"] == "panel":
            draw.rounded_rectangle((x, y, x + width - 1, y + height - 1),
                                   radius=node.get("radius", 0), fill=color)
        elif node["type"] == "meter":
            percent = max(0, min(100, int(values.get(node["var_bind"], 0))))
            filled = int(width * percent / 100)
            if filled:
                draw.rounded_rectangle((x, y, x + filled - 1, y + height - 1),
                                       radius=node.get("radius", 0), fill=color)
        elif node["type"] == "text":
            label = str(values.get(node["var_bind"], "")) if "var_bind" in node else node.get("text", "")
            size, align = node.get("font_size", 16), node.get("align", "left")
            if size > 16 and all(char in spec["chars"] or char == " " for char in label):
                atlas_text(img, x, y, label, size, color, align, width, atlas, spec)
            else:
                if align == "right":
                    position, anchor = (x + width, y), "ra"
                elif align == "center":
                    position, anchor = (x + width / 2, y), "ma"
                else:
                    position, anchor = (x, y), "la"
                draw.text(position, label, font=font, fill=color, anchor=anchor)
    return img.convert("RGB")


def render_all(package_dir, output_dir, font_path=None):
    package_dir, output_dir = Path(package_dir), Path(output_dir)
    runtime = json.loads((package_dir / "runtime/widget.json").read_text(encoding="utf-8"))
    resolved = resolve_font(font_path)
    font = ImageFont.truetype(str(resolved), 16)
    with Image.open(package_dir / "assets/glyphs.png") as source:
        atlas = source.convert("RGBA")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, name in enumerate(NAMES):
        target = output_dir / name
        render_page(runtime, atlas, font, index).save(target)
        paths.append(target)
    print(f"Offline preview: {len(paths)} pages -> {output_dir} | CJK font: {resolved}")
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, default=ROOT / ".build/package")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".build/preview")
    parser.add_argument("--font", type=Path, help="CJK font, or set BAMBU_CJK_FONT")
    args = parser.parse_args(argv)
    render_all(args.package_dir, args.output_dir, args.font)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
