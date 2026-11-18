"""Display model for the device component: formatting only, no I/O or transport.

The strict v1 snapshot and ``ams_materials`` are the inputs. This module owns the
presentation decisions the device cannot recompute: HH:MM conversion, display
rounding, per-field quality, AMS slot mapping and colour composition. It never
decides whether a value is valid; it only carries the quality it was given.
"""
from __future__ import annotations

import math
import re
import unicodedata

SCHEMA = "bambu-display-v1"
QUALITIES = frozenset({"valid", "stale", "unknown", "unsupported"})
UNKNOWN = "—"
UNSUPPORTED = "不支持"
STALE_BADGE = "旧值"
TRUNCATED_BADGE = "超过显示上限"

# The card colour the device composites a translucent filament swatch over.
CARD_RGB = (22, 22, 24)
MATERIAL_CELLS = 20          # half-width cells available in the AMS row
MAX_COUNT_DIGITS = 6
MAX_HOURS = 99
MAX_MODEL_CELLS = 20

CONNECTIONS = {
    "online": ("已连接", "normal"),
    "connecting": ("连接中", "dim"),
    "offline": ("已断开", "warn"),
    "auth_required": ("需要登录", "warn"),
    "unconfigured": ("未配置", "dim"),
    "error": ("连接错误", "warn"),
}

METRIC_KEYS = {
    "remaining_minutes": "remaining",
    "progress_percent": "progress",
    "current_layer": "current_layer",
    "total_layers": "total_layers",
    "nozzle_celsius": "nozzle",
    "bed_celsius": "bed",
    "chamber_celsius": "chamber",
    "part_fan_percent": "part_fan",
    "aux_fan_percent": "aux_fan",
}

_COLOR_RE = re.compile(r"^(?:#)?([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})$")


def _number(value):
    """Return a finite float, or None. Booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def format_minutes(value):
    """Return (text, truncated). Hours above 99 are capped, never wrapped."""
    number = _number(value)
    if number is None or number < 0 or not number.is_integer():
        return UNKNOWN, False
    minutes = int(number)
    hours, rest = divmod(minutes, 60)
    if hours > MAX_HOURS:
        return "%02d:59" % MAX_HOURS, True
    return "%02d:%02d" % (hours, rest), False


def format_temperature(value):
    """Round half away from zero; the model keeps full precision."""
    number = _number(value)
    if number is None:
        return UNKNOWN, False
    rounded = int(math.floor(number + 0.5)) if number >= 0 else int(math.ceil(number - 0.5))
    return str(rounded), False


def format_percent(value):
    number = _number(value)
    if number is None or not 0 <= number <= 100 or not number.is_integer():
        return UNKNOWN, False
    return str(int(number)), False


def format_count(value):
    """Integer counters are capped so a wide value cannot overflow the row."""
    number = _number(value)
    if number is None or number < 0 or not number.is_integer():
        return UNKNOWN, False
    count = int(number)
    if len(str(count)) > MAX_COUNT_DIGITS:
        return "9" * MAX_COUNT_DIGITS, True
    return str(count), False


def cell_width(text):
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def truncate_cells(text, limit):
    """Truncate by display cells and mark it; the original record is untouched."""
    if cell_width(text) <= limit:
        return text, False
    kept, used = [], 0
    for char in text:
        width = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if used + width > limit - 1:
            break
        kept.append(char)
        used += width
    return "".join(kept) + "…", True


def sanitize_material(text):
    """Drop control characters; text with nothing left is not a material name."""
    if not isinstance(text, str):
        return None
    cleaned = "".join(char for char in text
                      if not (ord(char) < 32 or 0x7F <= ord(char) <= 0x9F)).strip()
    return cleaned or None


def parse_color(text):
    """Parse canonical 8-digit RGBA. Six-digit input is invalid, not guessed."""
    if not isinstance(text, str):
        return None
    match = _COLOR_RE.match(text.strip())
    if not match:
        return None
    rgb_hex, alpha_hex = match.group(1).upper(), match.group(2).upper()
    rgb = tuple(int(rgb_hex[index:index + 2], 16) for index in (0, 2, 4))
    return rgb_hex, rgb, int(alpha_hex, 16)


def composite(rgb, alpha, background=CARD_RGB):
    ratio = alpha / 255
    return tuple(int(round(base * (1 - ratio) + channel * ratio))
                 for base, channel in zip(background, rgb))


def needs_border(rgb, alpha, background=CARD_RGB):
    """Dark, near-white and translucent swatches all need a visible outline."""
    if alpha < 200:
        return True
    blended = composite(rgb, alpha, background)
    return max(blended) < 40 or min(blended) > 235


def _hex(rgb):
    return "#%02X%02X%02X" % rgb


def _field(key, metric, formatter):
    """Build one display field from a {value, quality, updated_at} record."""
    if not isinstance(metric, dict):
        raise ValueError("metric record must be an object: %s" % key)
    quality = metric.get("quality")
    if quality not in QUALITIES:
        raise ValueError("unknown quality for %s: %r" % (key, quality))
    value = metric.get("value")
    if quality == "unsupported":
        return {"key": key, "value": UNSUPPORTED, "quality": quality,
                "tone": "muted", "badge": "", "truncated": False, "raw": None}
    if quality == "unknown" or value is None:
        return {"key": key, "value": UNKNOWN, "quality": "unknown",
                "tone": "muted", "badge": "", "truncated": False, "raw": None}
    text, truncated = formatter(value)
    badges = [TRUNCATED_BADGE] if truncated else []
    if quality == "stale":
        badges.append(STALE_BADGE)
    return {"key": key, "value": text, "quality": quality,
            "tone": "warn" if quality == "stale" else "normal",
            "badge": " · ".join(badges), "truncated": truncated, "raw": value}


def _descriptor_field(key, descriptor):
    """AMS descriptors use their own quality labels and no unsupported state."""
    if not isinstance(descriptor, dict):
        raise ValueError("AMS descriptor must be an object: %s" % key)
    quality = descriptor.get("quality")
    if quality not in {"valid", "stale", "unknown"}:
        raise ValueError("unexpected AMS quality for %s: %r" % (key, quality))
    value = descriptor.get("value")
    if quality == "unknown" or value is None:
        return {"key": key, "value": UNKNOWN, "quality": "unknown",
                "tone": "muted", "badge": "", "truncated": False, "raw": None}
    if quality == "stale":
        return {"key": key, "value": value, "quality": quality, "tone": "warn",
                "badge": STALE_BADGE, "truncated": False, "raw": value}
    return {"key": key, "value": value, "quality": quality, "tone": "normal",
            "badge": "", "truncated": False, "raw": value}


def _material_field(slot_key, descriptor):
    field = _descriptor_field(slot_key, descriptor)
    name = sanitize_material(field["raw"] if isinstance(field["raw"], str) else None)
    if name is None:
        # An empty or control-only name has nothing to show, even if it was reported.
        return {"key": slot_key, "value": UNKNOWN, "quality": "unknown",
                "tone": "muted", "badge": "", "truncated": False, "raw": field["raw"]}
    text, truncated = truncate_cells(name, MATERIAL_CELLS)
    badges = []
    if truncated:
        badges.append(TRUNCATED_BADGE)
    if field["badge"]:
        badges.append(field["badge"])
    return {"key": slot_key, "value": text, "quality": field["quality"],
            "tone": field["tone"], "badge": " · ".join(badges),
            "truncated": truncated, "raw": field["raw"]}


def _swatch_field(slot_key, descriptor):
    field = _descriptor_field(slot_key + "_color", descriptor)
    if field["quality"] == "unknown":
        return {"key": slot_key + "_color", "kind": "unknown", "value": None, "quality": "unknown",
                "tone": "muted", "badge": "", "rgba": None, "swatch": None,
                "border": True, "truncated": False}
    parsed = parse_color(field["raw"] if isinstance(field["raw"], str) else None)
    if parsed is None:
        # A reported colour that cannot be decoded is not a valid reading.
        return {"key": slot_key + "_color", "kind": "unknown", "value": None, "quality": "unknown",
                "tone": "muted", "badge": STALE_BADGE if field["badge"] else "",
                "rgba": None, "swatch": None, "border": True, "truncated": False}
    rgb_hex, rgb, alpha = parsed
    return {"key": slot_key + "_color", "kind": "swatch", "value": "#" + rgb_hex, "quality": field["quality"],
            "tone": field["tone"], "badge": field["badge"], "rgba": rgb_hex + "%02X" % alpha,
            "swatch": _hex(composite(rgb, alpha)), "border": needs_border(rgb, alpha),
            "truncated": False}


def _page_note(fields, connection_state, empty_text, unsupported_text):
    qualities = [field["quality"] for field in fields]
    if qualities and all(quality == "unsupported" for quality in qualities):
        return unsupported_text
    if all(quality in {"unknown", "unsupported"} for quality in qualities):
        return empty_text
    if any(quality == "stale" for quality in qualities):
        return "部分读数已过期，连接恢复后会重新确认。"
    if connection_state != "online":
        return "连接不是当前状态，显示的读数可能已过时。"
    return ""


def build_ams(materials):
    """Map AMS units/trays by explicit ID; never by array position."""
    units = []
    if isinstance(materials, list):
        for unit in materials[:16]:
            if not isinstance(unit, dict) or not isinstance(unit.get("id"), str):
                continue
            trays = {}
            for tray in (unit.get("trays") or [])[:4]:
                if not isinstance(tray, dict):
                    continue
                tray_id = tray.get("id")
                if isinstance(tray_id, str) and tray_id in {"0", "1", "2", "3"}:
                    trays[tray_id] = tray
            units.append({"id": unit["id"], "trays": trays})

    if not units:
        status, unit_id, note = "none", None, "未收到 AMS 数据。"
    elif len(units) == 1:
        status, unit_id, note = "single", units[0]["id"], ""
    else:
        status, unit_id, note = "multiple", None, "检测到 %d 个 AMS，尚未选择要显示的单元。" % len(units)
    slots = []
    for index in range(4):
        tray_id = str(index)
        slot_key = "tray%d" % (index + 1)
        tray = units[0]["trays"].get(tray_id) if status == "single" else None
        if tray is None:
            missing = {"value": None, "quality": "unknown", "updated_at": None}
            material = _material_field(slot_key, missing)
            swatch = _swatch_field(slot_key, missing)
        else:
            material = _material_field(slot_key, tray.get("material"))
            swatch = _swatch_field(slot_key, tray.get("color"))
        slots.append({"slot": index + 1, "tray_id": tray_id,
                      "material": material, "color": swatch})

    if status == "single":
        if all(slot["material"]["quality"] in {"unknown", "unsupported"} and
               slot["color"]["quality"] in {"unknown", "unsupported"} for slot in slots):
            note = "该 AMS 尚未上报耗材类型与颜色。"
        elif any(slot["material"]["quality"] == "stale" or slot["color"]["quality"] == "stale"
                 for slot in slots):
            note = "部分耗材描述已过期，连接恢复后会重新确认。"
    labels = {"none": "未检测到 AMS", "single": "AMS %s",
              "multiple": "多个 AMS"}
    label = labels[status] % unit_id if status == "single" else labels[status]
    return {"status": status, "unit": unit_id, "status_label": label,
            "note": note, "trays": slots}


def _model_label(model):
    if not isinstance(model, str) or not model.strip():
        return "拓竹打印机"
    label = "拓竹 " + model.strip()
    text, _ = truncate_cells(label, MAX_MODEL_CELLS)
    return text


def build_display(snapshot, *, materials=None):
    """Return the JSON-serialisable display model for all three pages."""
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    printer = snapshot.get("printer")
    connection = snapshot.get("connection")
    metrics = snapshot.get("metrics")
    if not isinstance(printer, dict) or not isinstance(connection, dict) or not isinstance(metrics, dict):
        raise ValueError("snapshot is missing printer, connection or metrics")
    missing = set(METRIC_KEYS) - set(metrics)
    if missing:
        raise ValueError("snapshot is missing metrics: %s" % ", ".join(sorted(missing)))

    state = connection.get("state")
    if state not in CONNECTIONS:
        raise ValueError("unknown connection state: %r" % state)
    label, tone = CONNECTIONS[state]

    remaining = _field("remaining", metrics["remaining_minutes"], format_minutes)
    progress = _field("progress", metrics["progress_percent"], format_percent)
    current_layer = _field("current_layer", metrics["current_layer"], format_count)
    total_layers = _field("total_layers", metrics["total_layers"], format_count)
    nozzle = _field("nozzle", metrics["nozzle_celsius"], format_temperature)
    bed = _field("bed", metrics["bed_celsius"], format_temperature)
    chamber = _field("chamber", metrics["chamber_celsius"], format_temperature)
    part_fan = _field("part_fan", metrics["part_fan_percent"], format_percent)
    aux_fan = _field("aux_fan", metrics["aux_fan_percent"], format_percent)

    task_fields = {field["key"]: field for field in
                   (remaining, progress, current_layer, total_layers, nozzle, bed)}
    env_fields = {field["key"]: field for field in (chamber, part_fan, aux_fan)}

    meter_visible = progress["quality"] in {"valid", "stale"} and progress["raw"] is not None
    percent = int(progress["raw"]) if meter_visible else None
    if percent is not None:
        percent = max(0, min(100, percent))

    ams = build_ams(materials)
    ams_note = ams["note"]
    if ams["status"] == "single" and ams_note and state == "offline":
        ams_note = "%s 连接已断开。" % ams_note

    task_note = _page_note(list(task_fields.values()), state,
                           "尚未收到打印机读数，收到后自动更新。",
                           "当前型号不提供这些任务读数。")
    if current_layer["truncated"] or total_layers["truncated"]:
        # The row cannot widen, so the cap must be stated rather than implied.
        task_note = (task_note + " 层数按显示上限标注。" if task_note
                     else "层数超过 6 位，按显示上限标注。")
    pages = [
        {"id": "task", "title": "打印任务", "fields": task_fields,
         "meter": {"visible": meter_visible, "percent": percent},
         "note": task_note},
        {"id": "environment", "title": "环境与散热", "fields": env_fields,
         "note": _page_note(list(env_fields.values()), state,
                            "尚未收到环境读数，收到后自动更新。",
                            "当前型号不提供这些环境读数。")},
        {"id": "ams", "title": "AMS 耗材", "trays": ams["trays"], "note": ams_note},
    ]

    field_count = len(task_fields) + len(env_fields) + sum(
        2 for _ in ams["trays"])
    if field_count != 17:
        raise ValueError("display contract must cover 17 fields, got %d" % field_count)

    return {
        "schema": SCHEMA,
        "model": {"id": printer.get("model"), "label": _model_label(printer.get("model"))},
        "connection": {"state": state, "label": label, "tone": tone},
        "generated_at": snapshot.get("generated_at"),
        "pages": pages,
        "ams": {"status": ams["status"], "unit": ams["unit"],
                "status_label": ams["status_label"]},
        "field_count": field_count,
    }
