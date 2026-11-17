#!/usr/bin/env python3
"""Render the declared surface the way the firmware draws it.

This is a local preview, not a device capture. It mirrors the element rules from
package/runtime/widget.json: page filter, top-anchored text, integer atlas
scaling, and -- importantly -- a meter painting only its filled portion.

Two bugs this preview previously had, both of which hid real defects:
  * it centred text vertically, while the device anchors to the top of the box;
  * it filled the whole meter, so the real "bar invisible at 0 %" never showed.
"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "package"
OUT = ROOT / "design"
CJK_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"

runtime = json.loads((PKG / "runtime" / "widget.json").read_text(encoding="utf-8"))
surface = runtime["surface"]
values = {k: v.get("init", 0) for k, v in runtime["vars"].items()}
atlas = Image.open(PKG / "assets" / "glyphs.png").convert("RGBA")
spec = surface["fonts"][0]
CW, CH = spec["cell"]
COLS = atlas.width // CW
CJK = ImageFont.truetype(CJK_PATH, 16)


def rgb565(v):
    return (((v >> 11) & 0x1F) * 255 // 31,
            ((v >> 5) & 0x3F) * 255 // 63,
            (v & 0x1F) * 255 // 31)


def atlas_text(img, x, y, s, px, color, align, box_w):
    adv_scale = px / 32.0
    bitmap_scale = px / float(CH)
    widths = [spec["metrics"][spec["chars"].index(c)][0] * adv_scale
              for c in s if c in spec["chars"]]
    total = sum(widths)
    cx = x if align == "left" else (x + box_w - total if align == "right"
                                    else x + (box_w - total) / 2)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    for ch in s:
        if ch not in spec["chars"]:
            cx += px * 0.3
            continue
        idx = spec["chars"].index(ch)
        adv = spec["metrics"][idx][0]
        cell = atlas.crop(((idx % COLS) * CW, (idx // COLS) * CH,
                           (idx % COLS) * CW + CW, (idx // COLS) * CH + CH))
        resized = cell.resize((max(1, round(CW * bitmap_scale)),
                               max(1, round(CH * bitmap_scale))), Image.LANCZOS)
        layer.alpha_composite(resized, (round(cx), round(y)))
        cx += adv * adv_scale
    tint = Image.new("RGBA", layer.size, color + (255,))
    tint.putalpha(layer.getchannel("A"))
    img.alpha_composite(tint)


def render(page_index, out_name):
    img = Image.new("RGBA", (640, 480), rgb565(surface["background"]) + (255,))
    d = ImageDraw.Draw(img)
    for n in surface["elements"]:
        if n.get("page", 4) not in (4, page_index):
            continue
        color = rgb565(n["color"])
        x, y, w, h = n["x"], n["y"], n["w"], n["h"]
        if n["type"] == "panel":
            d.rounded_rectangle((x, y, x + w - 1, y + h - 1),
                                radius=n.get("radius", 0), fill=color)
        elif n["type"] == "meter":
            # fill only; the track is a separate panel behind it
            pct = max(0, min(100, int(values.get(n["var_bind"], 0))))
            fw = int(w * pct / 100)
            if fw:
                d.rounded_rectangle((x, y, x + fw - 1, y + h - 1),
                                    radius=n.get("radius", 0), fill=color)
        elif n["type"] == "text":
            label = str(values.get(n["var_bind"], "")) if "var_bind" in n else n.get("text", "")
            size, align = n.get("font_size", 16), n.get("align", "left")
            if size > 16 and all(c in spec["chars"] or c == " " for c in label):
                atlas_text(img, x, y, label, size, color, align, w)
            else:
                if align == "right":
                    pos, anchor = (x + w, y), "ra"
                elif align == "center":
                    pos, anchor = (x + w / 2, y), "ma"
                else:
                    pos, anchor = (x, y), "la"
                d.text(pos, label, font=CJK, fill=color, anchor=anchor)
    img.convert("RGB").save(OUT / out_name)


OUT.mkdir(parents=True, exist_ok=True)
names = ["page1-task.png", "page2-environment.png", "page3-ams.png"]
for i, name in enumerate(names):
    render(i, name)
print("rendered %d pages -> %s" % (len(names), OUT.relative_to(ROOT)))
