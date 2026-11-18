#!/usr/bin/env python3
"""Render the confirmed three-page layout from the display model.

Every frame here is built from ``bambu_monitor.display.build_display`` fed by the
production telemetry state machine and synthetic push_status payloads. Nothing
in this file contacts a cloud account, and the images are design previews, not
device captures. The shipped 0.1.6 package is untouched.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

try:
    from .fonts import resolve_font
    from .render_frames import atlas_text
except ImportError:
    from fonts import resolve_font
    from render_frames import atlas_text

ROOT = Path(__file__).resolve().parent.parent
try:
    from bambu_monitor.display import build_display
    from bambu_monitor.telemetry import TelemetryState
except ImportError:  # running with tools/ only on sys.path
    sys.path.insert(0, str(ROOT / "src"))
    from bambu_monitor.display import build_display
    from bambu_monitor.telemetry import TelemetryState

T0 = datetime(2026, 11, 18, 4, 0, tzinfo=timezone.utc)
WATERMARK = "离线预览 · 合成数据 · 未上屏"

BG = (8, 8, 9)
CARD = (22, 22, 24)
TRACK = (58, 58, 62)
INK = (240, 240, 238)
MUTED = (140, 140, 138)
DIM = (96, 96, 95)
ACCENT = (72, 224, 122)
WARN = (223, 178, 103)

TONE = {"normal": INK, "warn": WARN, "muted": MUTED, "dim": DIM}
FOOTER = "摇杆翻页   SW1 下一页   SW2 上一页"
PAGE_COUNT = 3
PAGE_IDS = ("task", "environment", "ams")
SCENARIO_NAMES = ("live", "unknown", "stale", "partial", "multiple", "extremes")

# Mirrors the shipped buttons.json actions; the test asserts they stay in sync.
PAGE_STEP = {"view_next": 1, "stick_next": 1, "stick_right": 1,
             "view_prev": -1, "stick_prev": -1, "stick_left": -1}


def step_page(page, action):
    return (page + PAGE_STEP[action]) % PAGE_COUNT


def _panel(x, y, w, h, color=CARD, radius=14):
    return {"type": "panel", "x": x, "y": y, "w": w, "h": h, "color": color, "radius": radius}


def _text(x, y, w, h, text, size, color, align="left", font="cjk"):
    return {"type": "text", "x": x, "y": y, "w": w, "h": h, "text": text,
            "size": size, "color": color, "align": align, "font": font}


def _meter(x, y, w, h, percent, color=ACCENT, radius=5):
    return {"type": "meter", "x": x, "y": y, "w": w, "h": h,
            "percent": percent, "color": color, "radius": radius}


def _swatch(x, y, w, h, fill, border):
    return {"type": "swatch", "x": x, "y": y, "w": w, "h": h,
            "fill": fill, "border": border}


def _status_text(field):
    if field["badge"]:
        return field["badge"], WARN
    if field["quality"] == "unsupported":
        return "机型不支持", MUTED
    if field["quality"] == "unknown":
        return "等待数据", MUTED
    return "", MUTED


def _value_element(x, y, w, field, size):
    """Numeric readings use digit cells; words and dashes use the CJK face."""
    if field["quality"] in {"valid", "stale"} and field["raw"] is not None:
        return _text(x, y, w, size + 8, field["value"], size,
                     TONE.get(field["tone"], INK), "left", "digits")
    return _text(x, y + (size - 16) // 2, w, 22, field["value"], 16, MUTED)


def _header(display, page_index, title):
    elements = [
        _text(26, 10, 400, 22, display["model"]["label"], 16, INK),
        _text(400, 10, 214, 22, display["connection"]["label"], 16,
              TONE.get(display["connection"]["tone"], MUTED), "right"),
        _text(26, 46, 420, 22, title, 16, ACCENT),
        _text(506, 46, 108, 22, "%d / %d" % (page_index + 1, PAGE_COUNT), 16, MUTED, "right"),
    ]
    return elements


def _note_element(note):
    if not note:
        return []
    tone = WARN if ("过期" in note or "断开" in note or "中断" in note) else MUTED
    return [_text(26, 366, 588, 22, note, 16, tone)]


def _task_page(display):
    page = display["pages"][0]
    fields = page["fields"]
    elements = [_panel(26, 76, 588, 170, CARD, 14),
                _text(44, 90, 240, 22, "预计剩余", 16, MUTED)]
    elements.append(_value_element(42, 110, 430, fields["remaining"], 48))
    remaining = fields["remaining"]
    if remaining["badge"]:
        elements.append(_text(44, 162, 540, 20, remaining["badge"], 16, WARN))
    elements += [
        _text(44, 194, 18, 22, "层", 16, MUTED),
        _text(64, 194, 86, 22, fields["current_layer"]["value"], 16,
              TONE.get(fields["current_layer"]["tone"], INK), "right"),
        _text(154, 194, 14, 22, "/", 16, DIM, "center"),
        _text(172, 194, 96, 22, fields["total_layers"]["value"], 16,
              TONE.get(fields["total_layers"]["tone"], INK), "left"),
        _text(430, 194, 64, 22, "进度", 16, MUTED, "right"),
    ]
    progress = fields["progress"]
    if page["meter"]["visible"]:
        elements.append(_text(500, 180, 96, 40, progress["value"] + "%", 32,
                              TONE.get(progress["tone"], INK), "right", "digits"))
    else:
        elements.append(_text(500, 188, 96, 24, "—", 32, MUTED, "right", "cjk"))
    elements.append(_panel(44, 220, 552, 10, TRACK, 5))
    if page["meter"]["visible"]:
        elements.append(_meter(44, 220, 552, 10, page["meter"]["percent"],
                               WARN if progress["quality"] == "stale" else ACCENT))
    for index, (key, label) in enumerate((("nozzle", "喷嘴温度 ℃"), ("bed", "热床温度 ℃"))):
        x = 26 + index * 300
        elements.append(_panel(x, 258, 288, 96, CARD, 14))
        elements.append(_text(x + 18, 270, 250, 22, label, 16, MUTED))
        field = fields[key]
        if field["quality"] in {"valid", "stale"} and field["raw"] is not None:
            elements.append(_text(x + 16, 294, 120, 40, field["value"], 32,
                                  TONE.get(field["tone"], INK), "left", "digits"))
        else:
            elements.append(_text(x + 18, 302, 240, 24, field["value"], 16, MUTED))
        if field["badge"]:
            elements.append(_text(x + 150, 306, 120, 22, field["badge"], 16, WARN))
    elements += _note_element(page["note"])
    return elements


ENVIRONMENT_ROWS = (("chamber", "腔体温度 ℃"), ("part_fan", "零件冷却风扇 %"),
                    ("aux_fan", "辅助风扇 %"))


def _environment_page(display):
    page = display["pages"][1]
    elements = []
    for index, (key, label) in enumerate(ENVIRONMENT_ROWS):
        y = 76 + index * 92
        field = page["fields"][key]
        elements.append(_panel(26, y, 588, 84, CARD, 14))
        elements.append(_text(44, y + 12, 300, 22, label, 16, MUTED))
        if field["quality"] in {"valid", "stale"} and field["raw"] is not None:
            elements.append(_text(42, y + 36, 180, 40, field["value"], 32,
                                  TONE.get(field["tone"], INK), "left", "digits"))
        else:
            elements.append(_text(44, y + 46, 300, 24, field["value"], 16, MUTED))
        status, color = _status_text(field)
        if status:
            elements.append(_text(392, y + 46, 204, 22, status, 16, color, "right"))
    elements += _note_element(page["note"])
    return elements


def _slot_status(slot):
    material, color = slot["material"], slot["color"]
    if material["badge"] or color["badge"]:
        return material["badge"] or color["badge"], WARN
    material_known = material["quality"] in {"valid", "stale"}
    color_known = color["quality"] in {"valid", "stale"}
    if not material_known and not color_known:
        return "未收到", MUTED
    if not material_known:
        return "类型未知", MUTED
    if not color_known:
        return "颜色未知", MUTED
    return "", MUTED


def _ams_page(display):
    page = display["pages"][2]
    elements = []
    for index, slot in enumerate(page["trays"]):
        y = 70 + index * 74
        material, color = slot["material"], slot["color"]
        elements.append(_panel(26, y, 588, 68, CARD, 14))
        elements.append(_text(44, y + 24, 24, 22, str(slot["slot"]), 16, DIM))
        elements.append(_swatch(84, y + 14, 40, 40, color["swatch"], color["border"]))
        known = material["quality"] in {"valid", "stale"}
        elements.append(_text(140, y + 10, 300, 22, material["value"], 16,
                              TONE.get(material["tone"], MUTED) if known else MUTED))
        if color["value"]:
            elements.append(_text(140, y + 36, 220, 18, color["value"], 12, MUTED))
        status, tone = _slot_status(slot)
        if status:
            elements.append(_text(420, y + 24, 176, 22, status, 16, tone, "right"))
    elements += _note_element(page["note"])
    return elements


def layout(display, page_index):
    page = display["pages"][page_index]
    elements = _header(display, page_index, page["title"])
    if page["id"] == "task":
        elements += _task_page(display)
    elif page["id"] == "environment":
        elements += _environment_page(display)
    elif page["id"] == "ams":
        elements += _ams_page(display)
    else:
        raise ValueError("unknown display page: %r" % page["id"])
    elements.append(_text(26, 414, 588, 22, FOOTER, 16, DIM))
    elements.append(_text(26, 446, 588, 20, WATERMARK, 12, DIM))
    check_layout(elements)
    return elements


def check_layout(elements):
    """Reject anything that leaves the canvas or the verified content band."""
    for element in elements:
        if element["x"] < 0 or element["y"] < 0:
            raise ValueError("element outside canvas: %r" % element)
        if element["x"] + element["w"] > 640 or element["y"] + element["h"] > 480:
            raise ValueError("element outside canvas: %r" % element)
        if element["type"] == "text" and element["text"] is None:
            raise ValueError("text element without content: %r" % element)
    critical = [element for element in elements if element["y"] < 410]
    lowest = max(element["y"] + element["h"] for element in critical)
    if lowest > 400:
        raise ValueError("critical content ends at %d, must stop by 400" % lowest)
    return len(elements)


def render_frame(display, page_index, atlas, font_small, font_body):
    surface = Image.new("RGBA", (640, 480), BG + (255,))
    draw = ImageDraw.Draw(surface)
    for element in layout(display, page_index):
        x, y, w, h = (element[key] for key in ("x", "y", "w", "h"))
        if element["type"] == "panel":
            draw.rounded_rectangle((x, y, x + w - 1, y + h - 1),
                                   radius=element["radius"], fill=element["color"])
        elif element["type"] == "meter":
            filled = int(w * max(0, min(100, element["percent"])) / 100)
            if filled:
                draw.rounded_rectangle((x, y, x + filled - 1, y + h - 1),
                                       radius=element["radius"], fill=element["color"])
        elif element["type"] == "swatch":
            box = (x, y, x + w - 1, y + h - 1)
            if element["fill"] is None:
                draw.rounded_rectangle(box, radius=7, outline=DIM, width=2)
                draw.text((x + w / 2, y + h / 2), "?", font=font_small,
                          fill=MUTED, anchor="mm")
            else:
                draw.rounded_rectangle(box, radius=7, fill=element["fill"],
                                       outline=(90, 90, 92) if element["border"] else None,
                                       width=2 if element["border"] else 0)
        elif element["type"] == "text":
            text, size = element["text"], element["size"]
            if element["font"] == "digits" and all(char in DIGITS for char in text):
                atlas_text(surface, x, y, text, size, element["color"],
                           element["align"], w, atlas, GLYPHS)
            else:
                font = font_body if size >= 16 else font_small
                if element["align"] == "right":
                    position, anchor = (x + w, y), "ra"
                elif element["align"] == "center":
                    position, anchor = (x + w / 2, y), "ma"
                else:
                    position, anchor = (x, y), "la"
                draw.text(position, text, font=font, fill=element["color"], anchor=anchor)
        else:
            raise ValueError("unknown element type: %r" % element["type"])
    return surface.convert("RGB")


DIGITS = set("0123456789:%")
GLYPHS = json.loads((ROOT / "tools" / "glyph_metrics.json").read_text(encoding="utf-8"))


def _apply(state, seconds, payload):
    state.apply(payload, generation=1, topic="device/SYNTHETIC-DEVICE/report",
                observed_at=T0 + timedelta(seconds=seconds), monotonic=seconds)


def _push(**fields):
    return json.dumps({"print": dict(command="push_status", **fields)}).encode("utf-8")


def _tray(tray_id, **fields):
    return {"id": str(tray_id), **fields}


def _unit(unit_id, *trays):
    return {"id": str(unit_id), "tray": list(trays)}


def _snapshot(state, seconds):
    return state.snapshot(now=T0 + timedelta(seconds=seconds), monotonic=seconds)


def _new_state(model="A1 mini"):
    state = TelemetryState("SYNTHETIC-DEVICE", model=model, offline_test_markers=True)
    state.begin_generation(1)
    return state


LIVE_AMS = {"ams": {"ams": [_unit(0,
    _tray(0, tray_type="PLA", tray_color="F0F0EEFF"),
    _tray(1, tray_type="PETG", tray_color="48BFA1FF"),
    _tray(2, tray_type="PLA-CF", tray_color="DF8958FF"),
    _tray(3, tray_type="ABS", tray_color="2B2B2FFF"))]}}


def output_filenames():
    return tuple("%s-%s.png" % (name, page) for name in SCENARIO_NAMES
                 for page in PAGE_IDS) + ("review.png",)


def scenarios():
    """Build display models from the production pipeline with synthetic payloads."""
    built = []

    live = _new_state()
    _apply(live, 0, _push(task_id="TASK-SYNTH-001", gcode_state="RUNNING",
                          mc_remaining_time=83, mc_percent=42, layer_num=120,
                          total_layer_num=340, nozzle_temper=214.3, bed_temper=60.5,
                          cooling_fan_speed=7, **LIVE_AMS))
    built.append(("live", _snapshot(live, 20), live.materials_snapshot()))

    fresh = _new_state()
    built.append(("unknown", _snapshot(fresh, 0), fresh.materials_snapshot()))

    stale = _new_state()
    _apply(stale, 0, _push(task_id="TASK-SYNTH-001", gcode_state="RUNNING",
                           mc_remaining_time=83, mc_percent=42, layer_num=120,
                           total_layer_num=340, nozzle_temper=21.28125, bed_temper=21.46875,
                           cooling_fan_speed=0, **LIVE_AMS))
    built.append(("stale", _snapshot(stale, 61), stale.materials_snapshot()))

    partial = _new_state()
    _apply(partial, 0, _push(nozzle_temper=22.6, ams={"ams": [_unit(0,
        _tray(0, tray_type="PLA"),
        _tray(1, tray_color="112233FF"),
        _tray(2, tray_type="PLA Silk 彩虹渐变多色混色耗材（示例名称）",
              tray_color="7F4FD8FF"))]}))
    built.append(("partial", _snapshot(partial, 10), partial.materials_snapshot()))

    multiple = _new_state()
    _apply(multiple, 0, _push(nozzle_temper=40.0, bed_temper=55.0,
                              ams={"ams": [_unit(0, _tray(0, tray_type="PLA", tray_color="F0F0EEFF")),
                                           _unit(1, _tray(0, tray_type="PETG", tray_color="48BFA1FF"))]}))
    built.append(("multiple", _snapshot(multiple, 10), multiple.materials_snapshot()))

    extremes = _new_state()
    _apply(extremes, 0, _push(task_id="TASK-SYNTH-002", gcode_state="RUNNING",
                              mc_remaining_time=12345, mc_percent=100,
                              layer_num=1234567, total_layer_num=9876543,
                              nozzle_temper=499.6, bed_temper=120,
                              ams={"ams": [_unit(0,
                                  _tray(0, tray_type="PLA-CF", tray_color="FFFFFF00"))]}))
    built.append(("extremes", _snapshot(extremes, 10), extremes.materials_snapshot()))

    return [(name, build_display(snapshot, materials=materials))
            for name, snapshot, materials in built]


def render_scenarios(output_dir, font_path=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved = resolve_font(font_path)
    font_small = ImageFont.truetype(str(resolved), 12)
    font_body = ImageFont.truetype(str(resolved), 16)
    with Image.open(ROOT / "package" / "assets" / "glyphs.png") as source:
        atlas = source.convert("RGBA")

    frames = {}
    built = scenarios()
    if tuple(name for name, _ in built) != SCENARIO_NAMES:
        raise ValueError("scenario list changed; update SCENARIO_NAMES and the preview whitelist")
    for name, display in built:
        for page_index, page in enumerate(display["pages"]):
            frame = render_frame(display, page_index, atlas, font_small, font_body)
            target = output_dir / ("%s-%s.png" % (name, page["id"]))
            frame.save(target)
            frames[(name, page["id"])] = frame
    print("Display preview: %d scenarios x %d pages -> %s | CJK font: %s"
          % (len(frames) // PAGE_COUNT, PAGE_COUNT, output_dir, resolved))
    return frames


def render_contact(frames, output):
    """Two by three review sheet: the approved flow plus the key degraded states."""
    picks = [("live", "task"), ("live", "environment"), ("live", "ams"),
             ("stale", "task"), ("partial", "ams"), ("unknown", "task")]
    sheet = Image.new("RGB", (1280, 1440), BG)
    for index, key in enumerate(picks):
        if key not in frames:
            raise ValueError("missing frame for contact sheet: %r" % (key,))
        sheet.paste(frames[key], ((index % 2) * 640, (index // 2) * 480))
    sheet.save(output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".build/display-preview")
    parser.add_argument("--font", type=Path, help="CJK font, or BAMBU_CJK_FONT")
    args = parser.parse_args(argv)
    frames = render_scenarios(args.output_dir, args.font)
    contact = render_contact(frames, args.output_dir / "review.png")
    print("Review sheet: %s" % contact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
