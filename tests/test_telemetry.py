"""Independent synthetic AC06/07/08 acceptance vectors; no real cloud claims."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from bambu_monitor.telemetry import TelemetryState

T0 = datetime(2026, 11, 18, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def report(**fields):
    return json.dumps({"print": {"command": "push_status", **fields}}).encode()


def ams(*units):
    return {"ams": list(units)}


def unit(uid=0, *trays):
    return {"id": str(uid), "tray": list(trays)}


def tray(tid=0, spool="spool-A", **fields):
    return {"id": str(tid), "tray_uuid": spool, **fields}


class TelemetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = Draft202012Validator(json.loads((ROOT / "schemas/printer-snapshot.schema.json").read_text()), format_checker=FormatChecker())

    def setUp(self):
        self.state = TelemetryState("SYNTHETIC-PRINTER", offline_test_markers=True)
        self.state.begin_generation(1)

    def apply(self, sec=0, *, raw=None, state=None, baseline=False, generation=1, topic=None, wall=None, **fields):
        state = state or self.state
        return state.apply(raw if raw is not None else report(**fields),
                           topic=topic or "device/SYNTHETIC-PRINTER/report", generation=generation,
                           observed_at=wall or T0 + timedelta(seconds=sec), monotonic=sec, baseline=baseline)

    def snapshot(self, sec=0, *, state=None, wall=None):
        result = (state or self.state).snapshot(now=wall or T0 + timedelta(seconds=sec), monotonic=sec)
        self.validator.validate(result)
        generated = datetime.fromisoformat(result["generated_at"])
        metrics = list(result["metrics"].values()) + [t["remaining_percent"] for u in result["ams"] for t in u["trays"]]
        for metric in metrics:
            if metric["updated_at"] is not None:
                self.assertLessEqual(datetime.fromisoformat(metric["updated_at"]), generated)
        if result["connection"]["last_message_at"]:
            self.assertLessEqual(datetime.fromisoformat(result["connection"]["last_message_at"]), generated)
        return result

    def baseline(self):
        return self.apply(baseline=True, task_id="101", gcode_state="RUNNING", total_layer_num=300,
                          layer_num=1, mc_percent=0, mc_remaining_time=120,
                          nozzle_temper=220, bed_temper=60.5, cooling_fan_speed=0,
                          ams=ams(unit(0, tray(remain=75))))

    def remaining(self, snapshot, uid="0", tid="0"):
        return next(t["remaining_percent"] for u in snapshot["ams"] if u["id"] == uid for t in u["trays"] if t["id"] == tid)

    def test_ac08a_no_evidence_from_ping_other_target_invalid_or_info(self):
        state = TelemetryState("SYNTHETIC-PRINTER", model="A1 mini")
        state.begin_generation(1)
        for raw in (b"PINGRESP", b"not json", b'{"info":{"command":"get_version"}}', b'{"print":{"command":"push_status","accessToken":"canary"}}'):
            self.assertFalse(self.apply(raw=raw, state=state))
        self.assertFalse(self.apply(state=state, topic="device/OTHER/report", nozzle_temper=200))
        snapshot = self.snapshot(180, state=state)
        self.assertIsNone(snapshot["connection"]["last_message_at"])
        self.assertEqual(snapshot["connection"]["state"], "connecting")
        self.assertEqual(snapshot["metrics"]["chamber_celsius"]["quality"], "unsupported")
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["quality"], "unknown")
        self.assertFalse(state.metadata()["baseline_complete"])
        self.assertIsNone(state.metadata()["last_target_evidence_monotonic"])

    def test_ac08bc_retained_for_180_seconds_without_forged_observation(self):
        self.assertTrue(self.baseline())
        first = self.snapshot()
        for sec in range(10, 191, 10):
            self.apply(sec, mc_percent=sec // 10, **({"nozzle_temper": 220} if sec == 70 else {}))
            current = self.snapshot(sec)
            self.assertEqual(current["metrics"]["total_layers"], first["metrics"]["total_layers"])
            self.assertEqual(self.remaining(current), self.remaining(first))
            self.assertEqual(current["metrics"]["nozzle_celsius"]["quality"], "valid")
        meta = self.state.metadata()
        self.assertEqual(current["metrics"]["nozzle_celsius"]["updated_at"], (T0 + timedelta(seconds=70)).isoformat())
        self.assertEqual(meta["field_basis"]["nozzle_celsius"], "delta-retained")
        self.assertEqual(meta["field_basis"]["total_layers"], "task-scoped")
        self.assertEqual(meta["field_basis"]["ams.0.trays.0.remaining_percent"], "tray-scoped")

    def test_ac08d_timeout_ignores_ping_and_requires_new_basis(self):
        self.baseline()
        self.assertFalse(self.apply(50, raw=b"PINGRESP"))
        snapshot = self.snapshot(61)
        self.assertEqual(snapshot["connection"]["state"], "offline")
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["quality"], "stale")
        self.assertEqual(self.state.metadata()["connection_reason"], "telemetry_timeout")
        self.apply(62, mc_percent=50)
        snapshot = self.snapshot(62)
        self.assertEqual(snapshot["metrics"]["progress_percent"]["quality"], "valid")
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["quality"], "stale")

    def test_timeout_without_snapshot_cannot_restore_retention(self):
        self.baseline()
        self.apply(100, mc_percent=10)
        self.assertEqual(self.snapshot(100)["metrics"]["total_layers"]["quality"], "stale")

    def test_ac08e_generation_delta_is_not_complete_baseline(self):
        self.baseline()
        self.state.begin_generation(2)
        self.assertFalse(self.apply(5, generation=1, nozzle_temper=999))
        self.assertTrue(self.apply(10, generation=2, nozzle_temper=221))
        snapshot = self.snapshot(10)
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["quality"], "valid")
        self.assertEqual(snapshot["metrics"]["total_layers"]["quality"], "stale")
        self.assertFalse(self.state.metadata()["baseline_complete"])
        self.apply(20, generation=2, mc_percent=10)
        self.assertEqual(self.snapshot(20)["metrics"]["nozzle_celsius"]["quality"], "stale")
        self.apply(30, generation=2, baseline=True, task_id="101", total_layer_num=300, nozzle_temper=222)
        self.assertTrue(self.state.metadata()["baseline_complete"])
        self.assertEqual(self.snapshot(30)["metrics"]["total_layers"]["quality"], "valid")
        self.assertEqual(self.remaining(self.snapshot(30))["quality"], "stale")

    def test_ac08f_job_switch_unknowns_and_late_old_job_rejected(self):
        self.baseline()
        self.apply(10, task_id="202", nozzle_temper=222)
        snapshot = self.snapshot(10)
        self.assertEqual(snapshot["metrics"]["total_layers"]["quality"], "unknown")
        self.assertEqual(snapshot["metrics"]["progress_percent"]["quality"], "unknown")
        self.assertEqual(snapshot["metrics"]["bed_celsius"]["quality"], "valid")
        epoch = self.state.metadata()["job_epoch"]
        self.assertFalse(self.apply(20, task_id="101", total_layer_num=999))
        self.assertEqual(self.state.metadata()["job_epoch"], epoch)
        self.assertEqual(self.snapshot(20)["metrics"]["total_layers"]["quality"], "unknown")

    def test_new_prepare_without_identity_revokes_task(self):
        self.baseline()
        self.apply(10, gcode_state="PREPARE", nozzle_temper=210)
        self.assertEqual(self.snapshot(10)["metrics"]["total_layers"]["quality"], "unknown")

    def test_ac08g_spool_replacement_empty_and_old_spool(self):
        self.baseline()
        self.apply(10, ams=ams(unit(0, tray(spool="spool-B"))))
        self.assertEqual(self.remaining(self.snapshot(10))["quality"], "unknown")
        self.assertFalse(self.apply(20, ams=ams(unit(0, tray(remain=99)))))
        self.assertEqual(self.remaining(self.snapshot(20))["quality"], "unknown")
        self.apply(30, ams=ams(unit(0, tray(spool="spool-B", remain=0))))
        self.assertEqual(self.remaining(self.snapshot(30))["value"], 0)
        self.apply(40, ams=ams(unit(0, {"id": "0", "empty": True})))
        self.assertEqual(self.remaining(self.snapshot(40)), {"value": None, "quality": "unknown", "updated_at": None})

    def test_material_type_alone_cannot_confirm_spool_identity(self):
        self.apply(baseline=True, ams=ams(unit(0, {"id": "0", "tray_info_idx": "GFA00", "remain": 75})))
        self.assertNotEqual(self.remaining(self.snapshot())["quality"], "valid")

    def test_ac08h_ams_reordering_absence_and_explicit_removal(self):
        self.apply(baseline=True, ams=ams(unit(2, tray(3, "spool-D", remain=90)), unit(0, tray(1, "spool-B", remain=50), tray(0, remain=75))))
        self.apply(10, ams=ams(unit(0, tray(1, "spool-B", remain=49))))
        snapshot = self.snapshot(10)
        self.assertEqual(self.remaining(snapshot, "2", "3")["value"], 90)
        self.assertEqual(self.remaining(snapshot)["value"], 75)
        self.assertEqual(self.remaining(snapshot, "0", "1")["value"], 49)
        self.apply(20, ams=ams({"id": "2", "removed": True}))
        self.assertEqual([u["id"] for u in self.snapshot(20)["ams"]], ["0"])
        self.assertFalse(self.apply(30, ams=ams(unit(2, tray(3, "spool-D", remain=99)))))
        self.assertEqual([u["id"] for u in self.snapshot(30)["ams"]], ["0"])

    def test_ac08i_unknown_profile_and_previous_cache(self):
        unknown = TelemetryState("SYNTHETIC-PRINTER", profile="future-v9", model="unknown-device")
        self.assertFalse(self.apply(state=unknown, generation=0, baseline=True, nozzle_temper=220))
        snapshot = self.snapshot(state=unknown)
        self.assertTrue(all(m["quality"] == "unknown" for m in snapshot["metrics"].values()))
        self.assertIn("unsupported_protocol_profile", unknown.metadata()["diagnostics"])
        self.baseline()
        self.state.profile = "future-v9"
        self.assertEqual(self.snapshot(10)["metrics"]["nozzle_celsius"]["quality"], "stale")

    def test_ac08j_invalid_local_and_real_zero(self):
        self.baseline()
        self.apply(10, nozzle_temper="secret-not-a-number", bed_temper="0", ams=ams(unit(0, tray(remain=-1))))
        snapshot = self.snapshot(10)
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["quality"], "stale")
        self.assertEqual(snapshot["metrics"]["bed_celsius"]["value"], 0)
        self.assertEqual(snapshot["metrics"]["bed_celsius"]["quality"], "valid")
        self.assertEqual(snapshot["metrics"]["total_layers"]["quality"], "valid")
        self.assertEqual(self.remaining(snapshot)["quality"], "stale")
        self.apply(20, mc_percent=1)
        self.assertEqual(self.remaining(self.snapshot(20))["quality"], "stale")
        self.assertEqual(snapshot["metrics"]["chamber_celsius"]["quality"], "unknown")

    def test_ac08k_wall_jumps_use_monotonic_and_logical_generated_time(self):
        self.baseline()
        future = T0 + timedelta(days=20)
        self.apply(10, wall=future, nozzle_temper=221)
        self.apply(20, wall=T0 - timedelta(days=20), mc_percent=20)
        snapshot = self.snapshot(20, wall=T0 - timedelta(days=20))
        self.assertEqual(snapshot["connection"]["state"], "online")
        self.assertEqual(snapshot["generated_at"], future.isoformat())
        self.assertEqual(self.state.metadata()["generated_at_clock"], "logical-wall-max-observed")
        self.assertEqual(self.snapshot(81, wall=T0)["connection"]["state"], "offline")

    def test_ac08l_other_instance_and_out_of_order_monotonic_cannot_mutate(self):
        self.baseline()
        first = self.snapshot()
        self.assertFalse(self.apply(10, topic="device/SAME-NAME-OTHER-ID/report", nozzle_temper=10))
        self.apply(20, nozzle_temper=222)
        self.assertFalse(self.apply(19, nozzle_temper=123))
        self.assertEqual(self.snapshot(20)["metrics"]["nozzle_celsius"]["value"], 222)
        self.assertEqual(first["metrics"]["nozzle_celsius"]["value"], 220)

    def test_fan_units_temperature_precision_numeric_strings_and_bad_types(self):
        self.apply(nozzle_temper="219.75", cooling_fan_speed="7", big_fan1_speed=15, big_fan2_speed=0)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["value"], 219.75)
        self.assertEqual(snapshot["metrics"]["part_fan_percent"]["value"], 47)
        self.assertEqual(snapshot["metrics"]["aux_fan_percent"]["value"], 100)
        self.apply(10, cooling_fan_speed=16, big_fan1_speed=True, nozzle_temper="1e999")
        snapshot = self.snapshot(10)
        for name in ("part_fan_percent", "aux_fan_percent", "nozzle_celsius"):
            self.assertEqual(snapshot["metrics"][name]["quality"], "stale")

    def test_untrusted_strings_and_account_data_never_exported(self):
        secret = "SYNTHETIC-CANARY-DO-NOT-EXPORT"
        self.apply(task_id=secret, gcode_state=secret, gcode_file=secret, subtask_name=secret, accessToken=secret, nozzle_temper=220,
                   ams=ams(unit(0, tray(spool=secret, remain=75))))
        self.state.set_connection("online", secret)
        output = json.dumps({"snapshot": self.snapshot(), "metadata": self.state.metadata()})
        self.assertNotIn(secret, output)
        self.assertIsNone(self.snapshot()["job"]["name"])
        self.assertIsNone(self.snapshot()["job"]["state"])
        self.assertIsNone(TelemetryState("SYNTHETIC-PRINTER", model=secret).snapshot(now=T0, monotonic=0)["printer"]["model"])

    def test_bounded_payload_nesting_ids_duplicate_keys_and_topology(self):
        for payload in (b" " * (256 * 1024 + 1), b"[" * 40 + b"0" + b"]" * 40,
                        b'{"print":{"command":"push_status","nozzle_temper":1,"nozzle_temper":2}}', b"\xff"):
            with self.subTest(payload_length=len(payload)):
                self.assertFalse(self.apply(raw=payload))
        self.apply(nozzle_temper=220, ams=ams(unit(0, tray(remain=75), tray(remain=60))))
        self.assertEqual(self.snapshot()["ams"], [])
        self.apply(10, nozzle_temper=221, ams=ams(*[unit(i, tray(remain=75)) for i in range(17)]))
        self.assertEqual(self.snapshot(10)["ams"], [])
        self.apply(20, nozzle_temper=222, ams=ams(unit(0, *[tray(i, remain=75) for i in range(5)])))
        self.assertEqual(self.snapshot(20)["ams"], [])
        self.apply(30, nozzle_temper=223, ams=ams(unit("not-safe-account", tray(remain=75))))
        self.assertEqual(self.snapshot(30)["ams"], [])

    def test_explicit_disconnect_revokes_without_clearing_cache(self):
        self.baseline()
        self.assertTrue(self.apply(10, raw=b'{"event":{"event":"client.disconnected"}}'))
        snapshot = self.snapshot(10)
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["value"], 220)
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["quality"], "stale")
        self.assertEqual(self.state.metadata()["connection_reason"], "target_disconnected")

    def test_removed_slot_can_be_reconfirmed_in_new_generation(self):
        self.baseline()
        self.apply(10, ams=ams(unit(0, {"id": "0", "removed": True})))
        self.assertEqual(self.snapshot(10)["ams"], [])
        self.state.begin_generation(2)
        self.apply(20, generation=2, ams=ams(unit(0, tray(remain=70))))
        self.assertEqual(self.remaining(self.snapshot(20))["quality"], "valid")
        self.assertEqual(self.remaining(self.snapshot(20))["value"], 70)

    def test_non_utf8_and_non_json_constants_cannot_renew_evidence(self):
        self.baseline()
        self.assertFalse(self.apply(50, raw=report(nozzle_temper=221).decode().encode("utf-16")))
        self.assertFalse(self.apply(50, raw=b'{"print":{"command":"push_status","nozzle_temper":NaN,"bed_temper":60}}'))
        self.assertEqual(self.snapshot(61)["connection"]["state"], "offline")

    def test_zero_uuid_revokes_existing_spool_scope(self):
        self.baseline()
        self.apply(10, ams=ams(unit(0, tray(spool="spool-B", remain=65))))
        self.apply(20, ams=ams(unit(0, tray(spool="00000000000000000000000000000000"))))
        self.assertEqual(self.remaining(self.snapshot(20))["quality"], "unknown")

    def test_default_production_state_ignores_unverified_empty_and_removed(self):
        production = TelemetryState("SYNTHETIC-PRINTER")
        self.apply(state=production, generation=0, baseline=True, ams=ams(unit(0, tray(remain=75))))
        for sec, marker in enumerate((ams(unit(0, {"id": "0", "empty": True})),
                                      ams(unit(0, {"id": "0", "removed": True})),
                                      ams({"id": "0", "removed": True})), 1):
            self.assertFalse(self.apply(sec, state=production, generation=0, ams=marker))
            self.assertEqual(self.remaining(self.snapshot(sec, state=production))["value"], 75)
        self.assertFalse(production.metadata()["offline_test_markers_enabled"])
        self.assertTrue(self.state.metadata()["offline_test_markers_enabled"])

    def test_partial_incomparable_task_identity_and_reused_prepare_clear_scope(self):
        self.baseline()
        self.apply(10, subtask_id="202", nozzle_temper=221)
        self.assertEqual(self.snapshot(10)["metrics"]["total_layers"]["quality"], "unknown")
        other = TelemetryState("SYNTHETIC-PRINTER")
        self.apply(state=other, generation=0, baseline=True, task_id="101", total_layer_num=300, gcode_state="RUNNING")
        self.apply(10, state=other, generation=0, task_id="101", gcode_state="PREPARE")
        self.assertEqual(self.snapshot(10, state=other)["metrics"]["total_layers"]["quality"], "unknown")

    def test_ambiguous_topology_revokes_only_affected_cached_scope(self):
        self.apply(baseline=True, ams=ams(unit(0, tray(remain=75), tray(1, "spool-B", remain=50)), unit(1, tray(spool="spool-C", remain=60))), nozzle_temper=220)
        self.apply(10, nozzle_temper=221, ams=ams(unit(0, tray(remain=74), tray(spool="spool-D", remain=99))))
        snapshot = self.snapshot(10)
        self.assertEqual(self.remaining(snapshot)["quality"], "stale")
        self.assertEqual(self.remaining(snapshot, "0", "1")["quality"], "valid")
        self.assertEqual(self.remaining(snapshot, "1")["quality"], "valid")
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["quality"], "valid")
        self.apply(20, nozzle_temper=222, ams=ams(unit(0, {"id": "bad-id", "remain": 5})))
        self.assertEqual(self.remaining(self.snapshot(20), "0", "1")["quality"], "stale")
        self.assertEqual(self.remaining(self.snapshot(20), "1")["quality"], "valid")
        self.apply(30, nozzle_temper=223, ams={"ams": []})
        self.assertEqual(self.remaining(self.snapshot(30))["quality"], "stale")

    def test_weak_new_generation_identities_cannot_reuse_old_scope(self):
        self.apply(baseline=True, task_id="101", project_id="5", total_layer_num=300,
                   ams=ams(unit(0, {"id": "0", "tray_uuid": "spool-A", "tray_info_idx": "GFA00", "remain": 75})))
        self.state.begin_generation(2)
        self.apply(10, generation=2, project_id="5", total_layer_num=250,
                   ams=ams(unit(0, {"id": "0", "tray_info_idx": "GFA00", "remain": 30})))
        snapshot = self.snapshot(10)
        self.assertEqual(self.remaining(snapshot)["quality"], "stale")
        self.assertEqual(snapshot["metrics"]["total_layers"]["quality"], "stale")
        self.apply(20, generation=2, task_id="101", total_layer_num=250,
                   ams=ams(unit(0, tray(remain=30))))
        snapshot = self.snapshot(20)
        self.assertEqual(self.remaining(snapshot)["quality"], "valid")
        self.assertEqual(snapshot["metrics"]["total_layers"]["quality"], "valid")

    def test_constructor_generation_and_timestamp_validation(self):
        for bad in ("", "account@example.com", "../printer", "secret\n"):
            with self.assertRaises(ValueError):
                TelemetryState(bad)
        for bad_generation in (True, 1.0, "1", 0):
            self.assertFalse(self.apply(generation=bad_generation, nozzle_temper=220))
        with self.assertRaises(ValueError):
            self.state.begin_generation(1)
        with self.assertRaises(ValueError):
            self.state.snapshot(now=datetime(2026, 1, 1), monotonic=0)
        for invalid_mono in (float("nan"), float("inf"), -(10**1000), 10**1000, -1):
            with self.assertRaises(ValueError):
                self.state.apply(report(nozzle_temper=220), topic="device/SYNTHETIC-PRINTER/report", generation=1, observed_at=T0, monotonic=invalid_mono)


if __name__ == "__main__":
    unittest.main()
