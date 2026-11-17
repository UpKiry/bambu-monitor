#!/usr/bin/env python3
"""Generate component JSON from the unchanged v0.1.6 layout.

Repo root is resolved from this script, so the build is reproducible anywhere.

Three device constraints shaped this layout, each learned from a photo of the
real panel:
  1. Body text is a compact 16 px CJK bitmap. 16 px is crisp; larger CJK gets
     upscaled and looks soft. So every Chinese label is 16 px.
  2. Digits come from a 64 px glyph atlas and stay on integer scales only
     (32 or 64). Fractional scales like 26/64 are what made them blurry.
  3. A meter paints only its filled portion. At 0 % that is zero pixels, so the
     track has to be a separate panel or the bar appears to be missing.

Prototype v0.1.6 -- readings are placeholder values until a data adapter exists.
"""
import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLYPH_METRICS = ROOT / "tools" / "glyph_metrics.json"

CID = "bambu-monitor"
VERSION = "0.1.6"


def rgb(r, g, b):
    """RGB888 -> RGB565."""
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


BG    = rgb(8, 8, 9)
CARD  = rgb(22, 22, 24)
CARD2 = rgb(34, 34, 37)
TRACK = rgb(58, 58, 62)
INK   = rgb(240, 240, 238)
MUTED = rgb(140, 140, 138)
DIM   = rgb(96, 96, 95)
ACCENT = rgb(72, 224, 122)

P = 26
R = 640 - P
W = R - P
BODY = 16

VARS = {
    "remain":   {"type": "string", "init": "00:00"},
    "prog":     {"type": "int", "init": 0},
    "layer":    {"type": "int", "init": 0},
    "layers":   {"type": "int", "init": 0},
    "nozzle":   {"type": "int", "init": 0},
    "bed":      {"type": "int", "init": 0},
    "chamber":  {"type": "int", "init": 0},
    "fan_part": {"type": "int", "init": 0},
    "fan_aux":  {"type": "int", "init": 0},
    "tray1":    {"type": "int", "init": 0},
    "tray2":    {"type": "int", "init": 0},
    "tray3":    {"type": "int", "init": 0},
    "tray4":    {"type": "int", "init": 0},
}

PAGES = [{"id": "task"}, {"id": "temps"}, {"id": "ams"}]


def panel(x, y, w, h, color=CARD, radius=16, page=None):
    n = {"type": "panel", "x": x, "y": y, "w": w, "h": h, "color": color, "radius": radius}
    if page is not None:
        n["page"] = page
    return n


def text(x, y, w, h, s, size=BODY, color=INK, weight=400, align="left", page=None):
    n = {"type": "text", "x": x, "y": y, "w": w, "h": h, "color": color, "text": s,
         "font_size": size, "weight": weight, "align": align, "valign": "top"}
    if page is not None:
        n["page"] = page
    return n


def value(x, y, w, h, var, size=BODY, color=INK, weight=400, align="left", page=None):
    n = {"type": "text", "x": x, "y": y, "w": w, "h": h, "color": color, "var_bind": var,
         "font_size": size, "weight": weight, "align": align, "valign": "top"}
    if page is not None:
        n["page"] = page
    return n


def meter(x, y, w, h, var, color=ACCENT, radius=6, page=None):
    n = {"type": "meter", "x": x, "y": y, "w": w, "h": h, "color": color,
         "var_bind": var, "radius": radius}
    if page is not None:
        n["page"] = page
    return n


def generate_package(glyph_metrics):
    """Return fresh JSON objects; do not read or write files."""
    nodes = []
    # ---------------- shared header ----------------
    nodes.append(text(P, 10, 300, 22, "拓竹 A1 mini", BODY, INK, 600))
    nodes.append(panel(R - 94, 8, 94, 24, CARD2, 12))
    nodes.append(text(R - 82, 12, 72, 20, "等待数据", BODY, MUTED))

    # ---------------- page 1: print task ----------------
    nodes.append(panel(P, 44, W, 152, CARD, 16, page=0))
    nodes.append(text(P + 18, 60, 200, 20, "预计剩余", BODY, MUTED, page=0))
    nodes.append(value(P + 18, 82, 320, 40, "remain", 32, INK, 600, page=0))
    nodes.append(text(P + 18, 128, 18, 20, "层", BODY, MUTED, page=0))
    nodes.append(value(P + 40, 128, 40, 20, "layer", BODY, INK, 600, "right", page=0))
    nodes.append(text(P + 84, 128, 11, 20, "/", BODY, DIM, 400, "center", page=0))
    nodes.append(value(P + 99, 128, 40, 20, "layers", BODY, INK, 600, page=0))
    nodes.append(text(R - 100, 128, 42, 20, "进度", BODY, MUTED, page=0))
    nodes.append(value(R - 56, 128, 26, 20, "prog", BODY, INK, 600, "right", page=0))
    nodes.append(text(R - 26, 128, 20, 20, "%", BODY, MUTED, page=0))
    # visible track + fill: a meter alone shows nothing at 0 %
    nodes.append(panel(P + 18, 160, W - 36, 12, TRACK, 6, page=0))
    nodes.append(meter(P + 18, 160, W - 36, 12, "prog", ACCENT, 6, page=0))

    nodes.append(panel(P, 208, 284, 100, CARD, 16, page=0))
    nodes.append(text(P + 18, 224, 220, 20, "喷嘴温度 °C", BODY, MUTED, page=0))
    nodes.append(value(P + 18, 252, 120, 38, "nozzle", 32, INK, 600, page=0))

    nodes.append(panel(R - 284, 208, 284, 100, CARD, 16, page=0))
    nodes.append(text(R - 266, 224, 220, 20, "热床温度 °C", BODY, MUTED, page=0))
    nodes.append(value(R - 266, 252, 120, 38, "bed", 32, INK, 600, page=0))

    nodes.append(panel(P, 320, W, 80, CARD, 16, page=0))
    nodes.append(text(P + 18, 336, 240, 20, "AMS 槽 1 余量 %", BODY, MUTED, page=0))
    nodes.append(value(P + 18, 360, 60, 34, "tray1", 32, INK, 600, page=0))
    nodes.append(text(P, 420, W, 20, "摇杆翻页   SW1 前进   SW2 返回", BODY, DIM, page=0))

    # ---------------- page 2: environment ----------------
    env = [("腔体温度 °C", "chamber"), ("部件风扇 %", "fan_part"), ("辅助风扇 %", "fan_aux")]
    for i, (label, var) in enumerate(env):
        y = 46 + i * 104
        nodes.append(panel(P, y, W, 92, CARD, 16, page=1))
        nodes.append(text(P + 18, y + 16, 300, 20, label, BODY, MUTED, page=1))
        nodes.append(value(P + 18, y + 46, 110, 38, var, 32, INK, 600, page=1))
    nodes.append(text(P, 420, W, 20, "摇杆翻页   SW1 前进   SW2 返回", BODY, DIM, page=1))

    # ---------------- page 3: AMS trays ----------------
    for i in range(4):
        y = 46 + i * 88
        nodes.append(panel(P, y, W, 80, CARD, 16, page=2))
        nodes.append(text(P + 18, y + 12, 200, 20, "槽 %d 余量 %%" % (i + 1), BODY, MUTED, page=2))
        nodes.append(value(P + 18, y + 38, 90, 36, "tray%d" % (i + 1), 32, INK, 600, page=2))
    nodes.append(text(P, 420, W, 20, "摇杆翻页   SW1 前进   SW2 返回", BODY, DIM, page=2))

    # The hint line at y=420 is the only element allowed into the risky band: if the
    # panel clips it, no reading is lost. Everything else must end by y=400.
    hint = "摇杆翻页   SW1 前进   SW2 返回"
    critical = [n for n in nodes if n.get("text") != hint]
    lowest = max(n["y"] + n["h"] for n in critical)
    assert lowest <= 400, "critical content must end by y=400, got %d" % lowest
    assert len(nodes) <= 48, "node budget exceeded: %d" % len(nodes)

    glyphs = glyph_metrics
    surface = {
        "engine": "p4-app-surface-v1",
        "graphics": "p4-app-graphics-v1",
        "bindings": "p4-variable-bindings-v1",
        "typography": "p4-app-type-v1",
        "background": BG,
        "images": [{"id": "glyphs", "asset": "assets/glyphs.png", "frame_width": 192,
                    "frame_height": 192, "frames": 1, "fps": 1}],
        "fonts": [{"image": "glyphs", "chars": glyphs["chars"], "metrics": glyphs["metrics"],
                   "cell": glyphs["cell"]}],
        "elements": nodes,
    }

    dashboard = {
        "title": "拓竹打印机",
        "headline": {"switch_page": {
            "task": "打印任务 · 等待打印机数据",
            "temps": "环境温度 · 等待打印机数据",
            "ams": "AMS 耗材 · 等待打印机数据",
        }},
        "note": "打印机数据接入后显示实时读数",
        "footer": "摇杆翻页   SW1 前进   SW2 返回",
        "badge": "—",
        "visualStyle": "clean",
        "visualPalette": "mono",
        "visualLayout": "tool",
        "visualSprite": "gauge",
    }

    runtime = {
        "schema_version": 1,
        "engine": "p4-bounded-runtime-v4",
        "quota": "p4-quota-v2",
        "vars": VARS,
        "states": ["standby"],
        "initial_state": "standby",
        "pages": PAGES,
        "initial_page": "task",
        "transitions": [
            {"on": "view_next", "from": "*", "page": "next"},
            {"on": "view_prev", "from": "*", "page": "prev"},
            {"on": "stick_next", "from": "*", "page": "next"},
            {"on": "stick_prev", "from": "*", "page": "prev"},
            {"on": "stick_right", "from": "*", "page": "next"},
            {"on": "stick_left", "from": "*", "page": "prev"},
        ],
        "tick": [],
        "dashboard": dashboard,
        "surface": surface,
    }

    component = {
        "id": CID,
        "name": "拓竹打印机监控",
        "version": VERSION,
        "kind": "tool",
        "description": "查看拓竹 3D 打印机的剩余时间、进度、层数、喷嘴与热床温度、风扇状态和 AMS 各槽余量。摇杆上下左右均可翻页，确认键向前、返回键向后，在打印任务、环境温度、AMS 耗材三个视图之间切换；打印机数据源接入前读数保持占位值。",
        "engine": "p4-bounded-runtime-v4",
    }

    negative = {"dashboard": dict(dashboard, headline="打印任务 · 等待打印机数据")}

    buttons = [
        {"action": "view_next", "control": "SW1", "event": "button.sw1.short_press", "label": "确认 / 下一页"},
        {"action": "view_prev", "control": "SW2", "event": "button.sw2.short_press", "label": "返回 / 上一页"},
        {"action": "stick_next", "control": "前方摇杆", "event": "joystick.up", "label": "向上翻页"},
        {"action": "stick_prev", "control": "前方摇杆", "event": "joystick.down", "label": "向下翻页"},
        {"action": "stick_right", "control": "前方摇杆", "event": "knob.rotate_cw", "label": "向右翻页"},
        {"action": "stick_left", "control": "前方摇杆", "event": "knob.rotate_ccw", "label": "向左翻页"},
    ]

    share = {"title": "拓竹 3D 打印机状态监控"}


    return copy.deepcopy({
        "component.json": component,
        "negative-screen.json": negative,
        "buttons.json": buttons,
        "share.json": share,
        "runtime/widget.json": runtime,
    })


def write_package(output_dir, glyph_metrics):
    """Write only generated JSON to an explicit build directory."""
    output_dir = Path(output_dir)
    documents = generate_package(glyph_metrics)
    for relative, document in documents.items():
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return documents


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".build" / "package")
    parser.add_argument("--metrics", type=Path, default=GLYPH_METRICS)
    args = parser.parse_args(argv)
    documents = write_package(args.output_dir, json.loads(args.metrics.read_text(encoding="utf-8")))
    runtime = documents["runtime/widget.json"]
    print(f"version {VERSION} | nodes {len(runtime['surface']['elements'])} | JSON -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
