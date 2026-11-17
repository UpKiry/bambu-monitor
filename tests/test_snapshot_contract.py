"""Offline contract checks; no accounts, MQTT, HTTP, USB or firmware execution."""
import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


class SnapshotContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / "schemas/printer-snapshot.schema.json").read_text())
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema, format_checker=FormatChecker())
        cls.sample = json.loads((ROOT / "examples/snapshot.cloud.example.json").read_text())

    def snapshot(self):
        return copy.deepcopy(self.sample)

    def assert_invalid(self, snapshot):
        self.assertTrue(list(self.validator.iter_errors(snapshot)))

    def test_documented_example_satisfies_contract(self):
        self.validator.validate(self.sample)

    def test_real_zero_and_unknown_are_distinct(self):
        snapshot = self.snapshot()
        metric = snapshot["metrics"]["progress_percent"]
        metric["value"] = 0
        self.validator.validate(snapshot)
        metric["quality"] = "unknown"
        self.assert_invalid(snapshot)
        metric.update(value=None, updated_at=None)
        self.validator.validate(snapshot)

    def test_unsupported_cannot_be_reported_as_zero(self):
        snapshot = self.snapshot()
        snapshot["metrics"]["chamber_celsius"]["value"] = 0
        self.assert_invalid(snapshot)

    def test_cached_stale_reading_retains_timestamp(self):
        snapshot = self.snapshot()
        snapshot["connection"]["state"] = "offline"
        metric = snapshot["metrics"]["nozzle_celsius"]
        metric["quality"] = "stale"
        self.validator.validate(snapshot)
        metric["updated_at"] = None
        self.assert_invalid(snapshot)

    def test_percent_range_and_integer_units(self):
        for value in (-1, 101, 42.5):
            with self.subTest(value=value):
                snapshot = self.snapshot()
                snapshot["metrics"]["progress_percent"]["value"] = value
                self.assert_invalid(snapshot)

    def test_missing_field_does_not_mean_zero(self):
        snapshot = self.snapshot()
        del snapshot["metrics"]["bed_celsius"]
        self.assert_invalid(snapshot)

    def test_transport_sources_share_one_contract(self):
        for source in ("bambu-cloud", "bambu-lan", "home-assistant"):
            with self.subTest(source=source):
                snapshot = self.snapshot()
                snapshot["source"] = source
                self.validator.validate(snapshot)

    def test_timestamps_require_timezone(self):
        snapshot = self.snapshot()
        snapshot["metrics"]["nozzle_celsius"]["updated_at"] = "2026-11-18T06:00:00"
        self.assert_invalid(snapshot)

    def test_credential_fields_are_not_part_of_export_contract(self):
        for field in ("accessToken", "password", "dev_access_code"):
            with self.subTest(field=field):
                snapshot = self.snapshot()
                snapshot["printer"][field] = "synthetic-forbidden-field"
                self.assert_invalid(snapshot)

    def test_all_unknown_before_first_telemetry_is_representable(self):
        snapshot = self.snapshot()
        snapshot["connection"].update(state="connecting", last_message_at=None)
        snapshot["job"] = {"state": None, "name": None}
        snapshot["ams"] = []
        for metric in snapshot["metrics"].values():
            metric.update(value=None, quality="unknown", updated_at=None)
        self.validator.validate(snapshot)


if __name__ == "__main__":
    unittest.main()
