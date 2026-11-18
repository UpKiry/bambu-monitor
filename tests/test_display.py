"""Display-model vectors: formatting, quality, AMS mapping and layout bounds.

These are offline checks. They do not contact a printer, and a passing layout
does not mean the device can render it: the host projection and the dynamic
colour primitive are still missing (see docs/ARCHITECTURE.md).
"""
import json
import math
from pathlib import Path
import unittest

from bambu_monitor.display import (MATERIAL_CELLS, build_display, cell_width,
                                   format_count, format_minutes, format_percent,
                                   format_temperature)

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-11-18T04:00:00+00:00"


def metric(value, quality="valid"):
    return {"value": value, "quality": quality,
            "updated_at": STAMP if quality in {"valid", "stale"} else None}


def descriptor(value, quality="valid"):
    return {"value": value, "quality": quality,
            "updated_at": STAMP if quality in {"valid", "stale"} else None}


def tray(tray_id, material=None, color=None):
    return {"id": str(tray_id),
            "material": material or descriptor(None, "unknown"),
            "color": color or descriptor(None, "unknown")}


def unit(unit_id, *trays):
    return {"id": str(unit_id), "trays": list(trays)}


BASE_METRICS = {
    "remaining_minutes": metric(83),
    "progress_percent": metric(42),
    "current_layer": metric(120),
    "total_layers": metric(340),
    "nozzle_celsius": metric(21.28125),
    "bed_celsius": metric(21.46875),
    "chamber_celsius": metric(None, "unsupported"),
    "part_fan_percent": metric(0),
    "aux_fan_percent": metric(None, "unsupported"),
}


def snapshot(metrics=None, state="online", model="A1 mini"):
    body = dict(BASE_METRICS)
    body.update(metrics or {})
    return {
        "schema_version": 1, "source": "bambu-cloud",
        "printer": {"id": "SYNTHETIC-DEVICE", "model": model},
        "connection": {"state": state, "last_message_at": STAMP},
        "generated_at": STAMP,
        "job": {"state": "RUNNING", "name": None},
        "metrics": body,
        "ams": [],
    }


def make(metrics=None, state="online", model="A1 mini", materials=None):
    return build_display(snapshot(metrics, state, model), materials=materials)


class FormattingTests(unittest.TestCase):
    def test_minutes_boundaries(self):
        self.assertEqual(format_minutes(0), ("00:00", False))
        self.assertEqual(format_minutes(59), ("00:59", False))
        self.assertEqual(format_minutes(60), ("01:00", False))
        self.assertEqual(format_minutes(3599), ("59:59", False))
        self.assertEqual(format_minutes(5999), ("99:59", False))
        self.assertEqual(format_minutes(6000), ("99:59", True))
        self.assertEqual(format_minutes(2 ** 31 - 1), ("99:59", True))

    def test_minutes_reject_invalid(self):
        for value in (None, -1, True, 1.5, "83", math.inf, math.nan):
            self.assertEqual(format_minutes(value), ("—", False), value)

    def test_temperature_rounds_half_away_from_zero(self):
        self.assertEqual(format_temperature(21.28125), ("21", False))
        self.assertEqual(format_temperature(21.5), ("22", False))
        self.assertEqual(format_temperature(21.49), ("21", False))
        self.assertEqual(format_temperature(-0.5), ("-1", False))
        self.assertEqual(format_temperature(499.5), ("500", False))
        self.assertEqual(format_temperature(None), ("—", False))

    def test_percent_bounds(self):
        self.assertEqual(format_percent(0), ("0", False))
        self.assertEqual(format_percent(100), ("100", False))
        for value in (101, -1, 42.5, True, None, "42"):
            self.assertEqual(format_percent(value), ("—", False), value)

    def test_count_cap_is_explicit(self):
        self.assertEqual(format_count(999999), ("999999", False))
        self.assertEqual(format_count(1000000), ("999999", True))
        self.assertEqual(format_count(2 ** 31 - 1), ("999999", True))
        self.assertEqual(format_count(-3), ("—", False))


class FieldQualityTests(unittest.TestCase):
    def test_valid_zero_is_a_reading(self):
        display = make(metrics={"progress_percent": metric(0), "remaining_minutes": metric(0)})
        task = display["pages"][0]
        self.assertEqual(task["fields"]["progress"]["value"], "0")
        self.assertEqual(task["fields"]["progress"]["quality"], "valid")
        self.assertEqual(task["fields"]["remaining"]["value"], "00:00")
        self.assertTrue(task["meter"]["visible"])
        self.assertEqual(task["meter"]["percent"], 0)

    def test_unknown_hides_the_meter(self):
        display = make(metrics={"progress_percent": metric(None, "unknown")})
        task = display["pages"][0]
        self.assertEqual(task["fields"]["progress"]["value"], "—")
        self.assertEqual(task["fields"]["progress"]["tone"], "muted")
        self.assertFalse(task["meter"]["visible"])
        self.assertIsNone(task["meter"]["percent"])

    def test_unsupported_is_distinct_from_unknown(self):
        display = make()
        environment = display["pages"][1]["fields"]
        self.assertEqual(environment["chamber"]["value"], "不支持")
        self.assertEqual(environment["chamber"]["quality"], "unsupported")
        self.assertEqual(environment["part_fan"]["value"], "0")

    def test_stale_keeps_the_value_and_marks_it(self):
        display = make(metrics={"nozzle_celsius": metric(21.28125, "stale")})
        nozzle = display["pages"][0]["fields"]["nozzle"]
        self.assertEqual(nozzle["value"], "21")
        self.assertEqual(nozzle["quality"], "stale")
        self.assertEqual(nozzle["tone"], "warn")
        self.assertEqual(nozzle["badge"], "旧值")

    def test_truncated_reading_carries_a_badge(self):
        display = make(metrics={"remaining_minutes": metric(12345),
                                "current_layer": metric(1234567)})
        fields = display["pages"][0]["fields"]
        self.assertEqual(fields["remaining"]["value"], "99:59")
        self.assertTrue(fields["remaining"]["truncated"])
        self.assertIn("超过显示上限", fields["remaining"]["badge"])
        self.assertEqual(fields["current_layer"]["value"], "999999")
        self.assertTrue(fields["current_layer"]["truncated"])
        self.assertIn("显示上限", display["pages"][0]["note"])

    def test_seventeen_fields_are_always_present(self):
        display = make(materials=[unit(0)])
        self.assertEqual(display["field_count"], 17)
        fields = set(display["pages"][0]["fields"]) | set(display["pages"][1]["fields"])
        for slot in display["pages"][2]["trays"]:
            fields |= {slot["material"]["key"], slot["color"]["key"]}
        self.assertEqual(len(fields), 17)

    def test_model_and_connection_labels(self):
        self.assertEqual(make(model="A1 mini")["model"]["label"], "拓竹 A1 mini")
        self.assertEqual(make(model=None)["model"]["label"], "拓竹打印机")
        self.assertEqual(make(model="")["model"]["label"], "拓竹打印机")
        self.assertEqual(make(model="X1C")["model"]["label"], "拓竹 X1C")
        self.assertLessEqual(cell_width(make(model="MODEL-" + "X" * 40)["model"]["label"]),
                             MATERIAL_CELLS + 4)
        labels = {state: make(state=state)["connection"] for state in
                  ("online", "connecting", "offline", "auth_required", "unconfigured", "error")}
        self.assertEqual(labels["online"]["label"], "已连接")
        self.assertEqual(labels["offline"]["tone"], "warn")
        with self.assertRaises(ValueError):
            make(state="unknown")

    def test_malformed_snapshot_is_rejected(self):
        with self.assertRaises(ValueError):
            build_display({"metrics": BASE_METRICS})
        broken = snapshot()
        del broken["metrics"]["bed_celsius"]
        with self.assertRaises(ValueError):
            build_display(broken)
        bad_quality = {"nozzle_celsius": metric(21, "maybe")}
        with self.assertRaises(ValueError):
            make(metrics=bad_quality)

    def test_display_is_json_serialisable(self):
        model = make(materials=[unit(0, tray(0, descriptor("PLA"), descriptor("F0F0EEFF")))])
        round_trip = json.loads(json.dumps(model, ensure_ascii=False))
        self.assertEqual(round_trip["field_count"], 17)


class NoteTests(unittest.TestCase):
    def test_empty_state_explains_why(self):
        unknown = {name: metric(None, "unknown") for name in BASE_METRICS}
        display = make(metrics=unknown, state="connecting")
        self.assertIn("尚未收到", display["pages"][0]["note"])
        self.assertTrue(display["pages"][1]["note"])
        self.assertIn("未收到 AMS", display["pages"][2]["note"])

    def test_all_unsupported_environment_note(self):
        display = make(metrics={"part_fan_percent": metric(None, "unsupported")})
        self.assertIn("不提供", display["pages"][1]["note"])

    def test_stale_note_and_quiet_when_fine(self):
        self.assertEqual(make()["pages"][0]["note"], "")
        stale = make(metrics={"bed_celsius": metric(21.4, "stale")})
        self.assertIn("过期", stale["pages"][0]["note"])

    def test_offline_keeps_values_visible(self):
        display = make(state="offline", metrics={"nozzle_celsius": metric(21.0, "stale")})
        self.assertEqual(display["pages"][0]["fields"]["nozzle"]["value"], "21")
        self.assertEqual(display["connection"]["label"], "已断开")


class AmsTests(unittest.TestCase):
    def test_slots_follow_explicit_ids_not_positions(self):
        materials = [unit(0, tray(3, descriptor("ABS"), descriptor("112233FF")),
                          tray(1, descriptor("PETG"), descriptor("48BFA1FF")),
                          tray(0, descriptor("PLA"), descriptor("F0F0EEFF")),
                          tray(2, descriptor("PLA-CF"), descriptor("DF8958FF")))]
        slots = make(materials=materials)["pages"][2]["trays"]
        self.assertEqual([slot["material"]["value"] for slot in slots],
                         ["PLA", "PETG", "PLA-CF", "ABS"])
        self.assertEqual([slot["slot"] for slot in slots], [1, 2, 3, 4])

    def test_missing_slot_is_unknown(self):
        slots = make(materials=[unit(0, tray(0, descriptor("PLA"), descriptor("F0F0EEFF")))])["pages"][2]["trays"]
        self.assertEqual(slots[0]["material"]["quality"], "valid")
        for slot in slots[1:]:
            self.assertEqual(slot["material"]["quality"], "unknown")
            self.assertEqual(slot["color"]["quality"], "unknown")

    def test_multiple_units_are_not_guessed(self):
        display = make(materials=[unit(0, tray(0, descriptor("PLA"))),
                                  unit(1, tray(0, descriptor("PETG")))])
        page = display["pages"][2]
        self.assertEqual(display["ams"]["status"], "multiple")
        self.assertIn("2 个 AMS", page["note"])
        for slot in page["trays"]:
            self.assertEqual(slot["material"]["quality"], "unknown")

    def test_no_units(self):
        display = make(materials=[])
        self.assertEqual(display["ams"]["status"], "none")
        self.assertEqual(display["ams"]["status_label"], "未检测到 AMS")

    def test_malformed_units_are_ignored(self):
        display = make(materials=[{"trays": []}, {"id": "0", "trays": [{"id": "9"}]}, "x"])
        self.assertEqual(display["ams"]["status"], "single")
        self.assertEqual(display["ams"]["unit"], "0")

    def test_material_is_sanitized_and_truncated(self):
        long_name = "PLA Silk " + "彩虹渐变多色混色耗材" * 3
        slots = make(materials=[unit(0, tray(0, descriptor(long_name), descriptor("7F4FD8FF")))])["pages"][2]["trays"]
        material = slots[0]["material"]
        self.assertTrue(material["truncated"])
        self.assertTrue(material["value"].endswith("…"))
        self.assertLessEqual(cell_width(material["value"]), MATERIAL_CELLS)
        self.assertIn("超过显示上限", material["badge"])

        dirty = make(materials=[unit(0, tray(0, descriptor("PLA\n")))] )["pages"][2]["trays"][0]
        self.assertEqual(dirty["material"]["value"], "PLA")

    def test_empty_or_control_only_material_is_unknown(self):
        for value in ("", "   ", "\x00\x01"):
            slot = make(materials=[unit(0, tray(0, descriptor(value), descriptor("F0F0EEFF")))])["pages"][2]["trays"][0]
            self.assertEqual(slot["material"]["quality"], "unknown", value)
            self.assertEqual(slot["material"]["value"], "—")

    def test_material_and_colour_quality_are_independent(self):
        slots = make(materials=[unit(0,
            tray(0, descriptor("PLA"), descriptor(None, "unknown")),
            tray(1, descriptor(None, "unknown"), descriptor("112233FF")))])["pages"][2]["trays"]
        self.assertEqual(slots[0]["material"]["value"], "PLA")
        self.assertEqual(slots[0]["color"]["quality"], "unknown")
        self.assertEqual(slots[1]["color"]["value"], "#112233")
        self.assertEqual(slots[1]["material"]["quality"], "unknown")


class ColourTests(unittest.TestCase):
    def swatch(self, rgba):
        return make(materials=[unit(0, tray(0, descriptor("PLA"), descriptor(rgba)))])["pages"][2]["trays"][0]["color"]

    def test_white_and_black_need_borders(self):
        white = self.swatch("F0F0EEFF")
        self.assertEqual(white["swatch"], "#F0F0EE")
        self.assertTrue(white["border"])
        black = self.swatch("000000FF")
        self.assertEqual(black["swatch"], "#000000")
        self.assertTrue(black["border"])

    def test_translucent_colour_is_composited_over_the_card(self):
        clear = self.swatch("11223300")
        self.assertEqual(clear["swatch"], "#161618")
        self.assertTrue(clear["border"])
        half = self.swatch("FFFFFF80")
        self.assertNotIn(half["swatch"], {"#FFFFFF", "#161618"})
        self.assertTrue(half["border"])

    def test_mid_colour_needs_no_border(self):
        mid = self.swatch("112233FF")
        self.assertFalse(mid["border"])

    def test_malformed_colour_is_unknown(self):
        for value in ("AABBCC", "#AABBCC", "ZZZZZZFF", 12345, None):
            colour = self.swatch(value)
            self.assertEqual(colour["quality"], "unknown", value)
            self.assertIsNone(colour["swatch"])

    def test_stale_colour_keeps_the_swatch(self):
        colour = make(materials=[unit(0, tray(0, descriptor("PLA"),
                                             descriptor("48BFA1FF", "stale")))])["pages"][2]["trays"][0]["color"]
        self.assertEqual(colour["swatch"], "#48BFA1")
        self.assertEqual(colour["badge"], "旧值")


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tools import render_display
        cls.render = render_display

    def test_every_scenario_stays_on_canvas(self):
        checked = 0
        for _name, display in self.render.scenarios():
            for index in range(3):
                elements = self.render.layout(display, index)
                self.assertGreater(len(elements), 8)
                for element in elements:
                    self.assertGreaterEqual(element["x"], 0)
                    self.assertGreaterEqual(element["y"], 0)
                    self.assertLessEqual(element["x"] + element["w"], 640)
                    self.assertLessEqual(element["y"] + element["h"], 480)
                critical = [e for e in elements if e["y"] < 410]
                self.assertLessEqual(max(e["y"] + e["h"] for e in critical), 400)
                checked += 1
        self.assertEqual(checked, 18)

    def test_footer_stays_inside_the_hint_band(self):
        display = self.render.scenarios()[0][1]
        elements = self.render.layout(display, 0)
        footer = [element for element in elements if element.get("text") == self.render.FOOTER]
        self.assertEqual(len(footer), 1)
        self.assertGreaterEqual(footer[0]["y"], 410)

    def test_page_flip_visits_every_page_and_returns(self):
        for action in self.render.PAGE_STEP:
            for start in range(3):
                page = start
                seen = {page}
                for _ in range(3):
                    page = self.render.step_page(page, action)
                    seen.add(page)
                self.assertEqual(seen, {0, 1, 2}, action)
                self.assertEqual(page, start, action)

    def test_page_actions_match_the_shipped_package(self):
        buttons = json.loads((ROOT / "package" / "buttons.json").read_text(encoding="utf-8"))
        runtime = json.loads((ROOT / "package" / "runtime" / "widget.json").read_text(encoding="utf-8"))
        actions = {button["action"] for button in buttons}
        self.assertEqual(actions, set(self.render.PAGE_STEP))
        transitions = {(t["on"], t.get("page")) for t in runtime["transitions"]}
        for action, step in self.render.PAGE_STEP.items():
            self.assertIn((action, "next" if step > 0 else "prev"), transitions)

    def test_render_frame_size_when_a_font_exists(self):
        from PIL import Image, ImageFont
        try:
            font_path = self.render.resolve_font(None)
        except RuntimeError:
            self.skipTest("no CJK font available")
        with Image.open(ROOT / "package" / "assets" / "glyphs.png") as source:
            atlas = source.convert("RGBA")
        small = ImageFont.truetype(str(font_path), 12)
        body = ImageFont.truetype(str(font_path), 16)
        display = self.render.scenarios()[0][1]
        task = self.render.render_frame(display, 0, atlas, small, body)
        ams = self.render.render_frame(display, 2, atlas, small, body)
        self.assertEqual(task.size, (640, 480))
        self.assertNotEqual(task.tobytes(), ams.tobytes())


if __name__ == "__main__":
    unittest.main()
