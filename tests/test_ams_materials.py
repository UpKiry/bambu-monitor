"""Synthetic AMS descriptor vectors; no cloud or hardware claims."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from bambu_monitor.telemetry import TelemetryState
from bambu_monitor.telemetry.parser import decode, PROFILE

T0 = datetime(2026, 11, 18, tzinfo=timezone.utc)


def payload(*units, **fields):
    return json.dumps({"print": {"command": "push_status", "ams": {"ams": list(units)}, **fields}}).encode()


def unit(uid, *trays):
    return {"id": str(uid), "tray": list(trays)}


def tray(tid, **fields):
    return {"id": str(tid), **fields}


class AmsMaterialTests(unittest.TestCase):
    def setUp(self):
        self.state = TelemetryState("SYNTHETIC", model="A1 mini", offline_test_markers=True)
        self.state.begin_generation(1)

    def apply(self, sec, *units, generation=1, topic="device/SYNTHETIC/report", **fields):
        return self.state.apply(payload(*units, **fields), generation=generation, topic=topic,
                                observed_at=T0 + timedelta(seconds=sec), monotonic=sec)

    def view(self, sec=0):
        snapshot = self.state.snapshot(now=T0 + timedelta(seconds=sec), monotonic=sec)
        schema = Path(__file__).resolve().parents[1] / "schemas/printer-snapshot.schema.json"
        Draft202012Validator(json.loads(schema.read_text()), format_checker=FormatChecker()).validate(snapshot)
        return self.state.materials_snapshot()

    def slot(self, sec=0, uid="0", tid="0"):
        return next(t for u in self.view(sec) if u["id"] == uid for t in u["trays"] if t["id"] == tid)

    def test_four_lite_slots_without_uuid_and_unchanged_snapshot_schema(self):
        self.assertTrue(self.apply(0, unit(0, *[tray(i, tray_type=m, tray_color=c) for i, m, c in
            [(0, "PLA", "aabbccdd"), (1, "PETG", "00000000"),
             (2, "ABS", "FFFFFFFF"), (3, "PLA-CF", "12345678")]])))
        slots = self.view()[0]["trays"]
        self.assertEqual(len(slots), 4)
        self.assertEqual(slots[0]["color"]["value"], "AABBCCDD")
        for slot in slots:
            self.assertEqual(slot["material"]["quality"], "valid")
            self.assertEqual(slot["color"]["quality"], "valid")
        old = self.state.snapshot(now=T0, monotonic=0)
        self.assertEqual(old["ams"][0]["trays"][0], {"id": "0", "remaining_percent":
            {"value": None, "quality": "unknown", "updated_at": None}})

    def test_decode_compatibility_and_descriptor_evidence(self):
        decoded = decode(payload(unit(0, tray(0, tray_type="PLA"))), PROFILE)
        self.assertTrue(decoded.evidence)
        self.assertEqual(decoded.trays[0].material, "PLA")
        self.assertTrue(self.apply(0, unit(0, tray(0, tray_color="00000000"))))
        self.assertEqual(self.state.snapshot(now=T0, monotonic=0)["connection"]["state"], "online")
        self.assertEqual(self.slot()["material"]["quality"], "unknown")

    def test_incremental_id_merge_independent_timestamps_absence_and_heartbeat(self):
        self.apply(0, unit(2, tray(3, tray_type="PETG")), unit(0, tray(0, tray_type="PLA")))
        self.apply(10, unit(0, tray(0, tray_color="AABBCCFF")))
        self.apply(20, unit(2, tray(3, tray_color="112233FF")))
        self.apply(50, nozzle_temper=21)
        self.apply(80, nozzle_temper=22)
        slot = self.slot(80)
        self.assertEqual(slot["material"], {"value": "PLA", "quality": "valid", "updated_at": T0.isoformat()})
        self.assertEqual(slot["color"]["updated_at"], (T0 + timedelta(seconds=10)).isoformat())
        self.assertEqual(self.slot(80, "2", "3")["color"]["value"], "112233FF")
        self.assertEqual([u["id"] for u in self.view(80)], ["0", "2"])

    def test_changed_material_or_color_clears_unobserved_counterpart(self):
        self.apply(0, unit(0, tray(0, tray_type="PLA", tray_color="AABBCCFF")))
        self.apply(10, unit(0, tray(0, tray_type="PETG")))
        self.assertEqual(self.slot(10)["color"]["quality"], "unknown")
        self.apply(20, unit(0, tray(0, tray_color="112233FF")))
        self.apply(30, unit(0, tray(0, tray_color="445566FF")))
        self.assertEqual(self.slot(30)["material"]["quality"], "unknown")
        self.apply(40, unit(0, tray(0, tray_type="ABS", tray_color="778899FF")))
        self.assertEqual(self.slot(40)["material"]["value"], "ABS")
        self.assertEqual(self.slot(40)["color"]["quality"], "valid")

    def test_identity_change_and_late_old_identity(self):
        self.apply(0, unit(0, tray(0, tray_uuid="spool-A", tray_type="PLA", tray_color="AABBCCFF")))
        self.apply(10, unit(0, tray(0, tray_uuid="spool-B", tray_type="PETG")))
        self.assertEqual(self.slot(10)["color"]["quality"], "unknown")
        self.assertFalse(self.apply(20, unit(0, tray(0, tray_uuid="spool-A", tray_color="112233FF"))))
        self.assertEqual(self.slot(20)["material"]["value"], "PETG")
        self.apply(30, unit(0, tray(0, tray_uuid="00000000", tray_type="ABS", tray_color="123456FF")))
        self.assertEqual(self.slot(30)["material"]["quality"], "valid")

    def test_invalid_values_revoke_only_their_own_basis(self):
        self.apply(0, unit(0, tray(0, tray_type="PLA", tray_color="AABBCCFF")))
        self.assertFalse(self.apply(10, unit(0, tray(0, tray_color="#AABBCC"))))
        self.assertEqual(self.slot(10)["color"]["quality"], "stale")
        self.assertEqual(self.slot(10)["material"]["quality"], "valid")
        for sec, bad in enumerate(("", "x" * 65, " PLA", "PLA\n", None, True, 5), 11):
            self.assertFalse(self.apply(sec, unit(0, tray(0, tray_type=bad))))
        self.assertEqual(self.slot(20)["material"]["quality"], "stale")
        self.assertEqual(self.slot(20)["material"]["updated_at"], T0.isoformat())

    def test_timeout_view_clock_and_new_heartbeat_do_not_revive_old_values(self):
        self.apply(0, unit(0, tray(0, tray_type="PLA", tray_color="AABBCCFF")))
        self.assertEqual(self.slot(61)["material"]["quality"], "stale")
        self.assertEqual(self.state.materials_snapshot()[0]["trays"][0]["color"]["quality"], "stale")
        self.apply(62, nozzle_temper=21)
        self.assertEqual(self.slot(62)["material"]["quality"], "stale")
        self.apply(63, unit(0, tray(0, tray_type="PLA")))
        self.assertEqual(self.slot(63)["material"]["quality"], "valid")
        self.assertEqual(self.slot(63)["color"]["quality"], "stale")

    def test_disconnect_and_generation_revoke_until_independent_refresh(self):
        self.apply(0, unit(0, tray(0, tray_type="PLA", tray_color="AABBCCFF")))
        self.state.set_connection("offline", "network_error")
        self.apply(10, nozzle_temper=21)
        self.assertEqual(self.slot(10)["material"]["quality"], "stale")
        self.state.begin_generation(2)
        self.assertFalse(self.apply(20, unit(0, tray(0, tray_type="ABS"))))
        self.apply(21, unit(0, tray(0, tray_type="PLA")), generation=2)
        self.assertEqual(self.slot(21)["material"]["quality"], "valid")
        self.assertEqual(self.slot(21)["color"]["quality"], "stale")
        self.assertFalse(self.apply(22, unit(0, tray(0, tray_type="ABS")), generation=2, topic="device/OTHER/report"))

    def test_duplicate_slot_unit_and_invalid_topology_revoke_affected_basis(self):
        self.apply(0, unit(0, tray(0, tray_type="PLA"), tray(1, tray_type="ABS")), unit(1, tray(0, tray_type="PETG")))
        self.apply(10, unit(0, tray(0, tray_type="X"), tray(0, tray_type="Y")), nozzle_temper=21)
        self.assertEqual(self.slot(10)["material"]["quality"], "stale")
        self.assertEqual(self.slot(10, tid="1")["material"]["quality"], "valid")
        self.apply(20, unit(0, tray(1, tray_type="X")), unit(0, tray(1, tray_type="Y")), nozzle_temper=21)
        self.assertEqual(self.slot(20, tid="1")["material"]["quality"], "stale")
        self.assertEqual(self.slot(20, uid="1")["material"]["quality"], "valid")
        self.apply(30, unit("bad", tray(0, tray_type="ABS")), unit(1, tray(0, tray_type="PETG")), nozzle_temper=21)
        self.assertEqual(self.slot(30, uid="1")["material"]["quality"], "stale")
        self.apply(40, unit(1, tray(0, tray_type="PETG")))
        self.assertEqual(self.slot(40, uid="1")["material"]["quality"], "valid")

    def test_bounds_normalization_and_invalid_colors_cannot_create_evidence(self):
        for bad in ("112233", "112233445", "GG2233FF", "#112233FF", None, True, 12):
            self.assertFalse(self.apply(0, unit(0, tray(0, tray_color=bad))))
        self.assertTrue(self.apply(1, unit(0, tray(0, tray_type="P" * 64, tray_color="abcdef00"))))
        self.assertEqual(self.slot(1)["color"]["value"], "ABCDEF00")
        self.assertFalse(self.apply(0, unit(0, tray(0, tray_type="ABS"))))
        self.assertEqual(self.slot(1)["material"]["value"], "P" * 64)
        self.state.profile = "future-v9"
        self.assertEqual(self.slot(2)["material"]["quality"], "stale")

    def test_malformed_topology_cannot_refresh_descriptors(self):
        self.apply(0, unit(0, tray(0, tray_type="PLA", tray_color="AABBCCFF")))
        self.apply(10, {"id": "0", "tray": "bad"}, nozzle_temper=21)
        self.assertEqual(self.slot(10)["material"]["quality"], "stale")
        self.apply(20, unit(0, tray(0, tray_type="PLA", tray_color="AABBCCFF")))
        self.apply(30, unit(0, tray("bad", tray_type="ABS"), tray(0, tray_type="PLA")), nozzle_temper=21)
        self.assertEqual(self.slot(30)["material"]["quality"], "stale")
        self.apply(40, unit(0, tray(0, tray_color="AABBCCFF")))
        self.assertEqual(self.slot(40)["material"]["quality"], "stale")
        self.assertEqual(self.slot(40)["color"]["quality"], "valid")

    def test_offline_empty_and_removal_clear_materials_without_inferred_deletion(self):
        self.apply(0, unit(0, tray(0, tray_type="PLA"), tray(1, tray_type="ABS")))
        self.apply(10)
        self.assertEqual(len(self.view(10)[0]["trays"]), 2)
        self.apply(20, unit(0, tray(0, empty=True)))
        self.assertEqual(self.slot(20)["material"]["quality"], "unknown")
        self.apply(30, unit(0, tray(1, removed=True)))
        self.assertEqual(len(self.view(30)[0]["trays"]), 1)


if __name__ == "__main__":
    unittest.main()
