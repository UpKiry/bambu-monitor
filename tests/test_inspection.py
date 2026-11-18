"""Synthetic inspection tests: secrecy, bounds and lifecycle, never real cloud."""
from datetime import datetime, timedelta, timezone
import json
import unittest

from bambu_monitor.auth import Secret
from bambu_monitor.config import Config
from bambu_monitor.inspection import TelemetryInspection, MAX_CACHE_BYTES
from bambu_monitor.session import Envelope, FakeMqtt, Session

NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)
CANARY = "PRIVATE-CANARY-9981"


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.inspection = TelemetryInspection()
        self.inspection.protect(CANARY)

    def capture(self, value, generation=1, second=0):
        return self.inspection.capture(json.dumps(value).encode(), generation=generation,
                                       received_at=NOW + timedelta(seconds=second))

    def test_redacts_nested_keys_credentials_urls_and_known_secret_values(self):
        self.capture({"print": {"command": "push_status", "nozzle_temper": 21.25,
                              "dev_access_code": CANARY, "accessToken": CANARY,
                              "nested": [{"password": CANARY, "tray_uuid": CANARY,
                                          "camera_url": "rtsps://user:pass@example.invalid/stream"}],
                              "unexpected": "prefix-" + CANARY,
                              "description": "https://example.invalid/file?token=private",
                              "subtask_name": "private-file.3mf", "user_id": CANARY}})
        text = json.dumps(self.inspection.snapshot(1))
        self.assertNotIn(CANARY, text)
        self.assertNotIn("private-file", text)
        self.assertNotIn("example.invalid", text)
        self.assertIn('21.25', text)
        self.assertIn('已隐藏', json.dumps(self.inspection.snapshot(1), ensure_ascii=False))

    def test_aliases_numeric_uid_and_sensitive_keys_are_filtered(self):
        self.inspection.protect(CANARY, '123456789')
        self.capture({'pwd': 'PRIVATE-PASSWORD', 'auth': 'PRIVATE-AUTH',
                      'debug': 123456789, 'other': 123456789.0,
                      'person@example.com': 1, 'https://example.invalid/private': 2})
        text = json.dumps(self.inspection.snapshot(1))
        for secret in ('PRIVATE-PASSWORD', 'PRIVATE-AUTH', '123456789',
                       'person@example.com', 'https://example.invalid/private'):
            self.assertNotIn(secret, text)

    def test_unknown_fields_and_versions_do_not_require_normalized_parser_support(self):
        self.capture({"info": {"module": [{"name": "ams", "sw_ver": "1.2.3"}]},
                      "device": {"experimental": {"temperature": 23}}})
        fields = {item['path']: item for item in self.inspection.snapshot(1)['fields']}
        self.assertEqual(fields['/device/experimental/temperature']['value'], 23)
        self.assertEqual(fields['/info/module']['value'][0]['sw_ver'], '1.2.3')

    def test_incremental_cache_has_observation_times_and_atomic_arrays(self):
        self.capture({"print": {"x": 0, "ams": [{"id": 0, "type": "PLA"}]}})
        self.capture({"print": {"y": 9}}, second=10)
        fields = {item['path']: item for item in self.inspection.snapshot(1)['fields']}
        self.assertEqual(fields['/print/x']['value'], 0)
        self.assertEqual(fields['/print/x']['updated_at'], NOW.isoformat())
        self.assertEqual(fields['/print/y']['updated_at'], (NOW + timedelta(seconds=10)).isoformat())
        self.capture({"print": {"ams": []}}, second=20)
        fields = {item['path']: item for item in self.inspection.snapshot(1)['fields']}
        self.assertEqual(fields['/print/ams']['value'], [])
        self.capture({"print": {"x": {"nested": 4}}}, second=30)
        fields = {item['path']: item for item in self.inspection.snapshot(1)['fields']}
        self.assertNotIn('/print/x', fields)
        self.assertIn('/print/x/nested', fields)
        self.capture({"print": {"x": {}}}, second=40)
        fields = {item['path']: item for item in self.inspection.snapshot(1)['fields']}
        self.assertNotIn('/print/x/nested', fields)
        self.assertEqual(fields['/print/x']['value'], {})

    def test_generation_revoke_reset_and_copy(self):
        self.capture({"print": {"x": 1}})
        self.assertIsNone(self.inspection.snapshot(2)['latest'])
        view = self.inspection.snapshot(1)
        view['latest']['data']['print']['x'] = 8
        self.assertEqual(self.inspection.snapshot(1)['latest']['data']['print']['x'], 1)
        self.capture({"print": {"y": 2}}, generation=2)
        self.assertEqual([field['path'] for field in self.inspection.snapshot(2)['fields']], ['/print/y'])

    def test_malformed_and_resource_bounds(self):
        for payload in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'[' * 40,
                        b'{' + b' ' * 65536 + b'}', b'{"a":1e999}'):
            self.assertFalse(self.inspection.capture(payload, generation=1, received_at=NOW))
        self.capture({"print": {str(i): "v" * 2000 for i in range(24)}})
        self.assertTrue(self.inspection.snapshot(1)['truncated'])
        self.capture({"print": {"k" + str(i): i for i in range(700)}})
        self.assertLessEqual(len(self.inspection.snapshot(1)['fields']), 512)
        for index in range(12):
            self.capture({"array" + str(index): ["v" * 900] * 50})
        self.assertLessEqual(self.inspection._cache_bytes, MAX_CACHE_BYTES)
        self.assertTrue(self.inspection.snapshot(1)['truncated'])


class SessionInspectionTests(unittest.TestCase):
    def session(self, inspect=True):
        transport = FakeMqtt()
        session = Session(Config('cn', 'PRINTER'), transport, inspect_telemetry=inspect)
        session.start(uid=Secret('123456789'), token=Secret(CANARY), monotonic=0)
        session.connack(session.generation, True)
        session.drain()
        session.suback(session.generation, True)
        session.drain()
        return session

    def test_current_exact_target_only_no_liveness_from_unknown_fields(self):
        session = self.session()
        generation = session.generation
        for topic in ('device/OTHER/report', 'device/PRINTER/report'):
            session.enqueue(Envelope(generation, topic,
                json.dumps({'info': {'module': [], 'unexpected': CANARY}}).encode(), NOW, 1))
        session.drain()
        snapshot = session.snapshot(now=NOW, monotonic=1)
        self.assertNotEqual(snapshot['connection']['state'], 'online')
        view = session.inspection_view()
        self.assertIsNotNone(view['telemetry']['latest'])
        self.assertNotIn(CANARY, json.dumps(view))
        session.disconnect(generation)
        session.drain()
        self.assertIsNone(session.inspection_view()['telemetry']['latest'])
        session.stop()
        self.assertEqual(session.inspection._secrets, ())

    def test_opt_in_only_and_materials_through_session(self):
        ordinary = self.session(False)
        self.assertEqual(ordinary.inspection_view(), {})
        ordinary.stop()
        session = self.session()
        payload = {'print': {'command': 'push_status', 'ams': {'ams': [
            {'id': 0, 'tray': [{'id': 2, 'tray_type': 'PETG', 'tray_color': '00ff00ff'}]}]}}}
        session.enqueue(Envelope(session.generation, 'device/PRINTER/report',
                                 json.dumps(payload).encode(), NOW, 1))
        session.drain()
        session.snapshot(now=NOW, monotonic=1)
        tray = session.inspection_view()['ams_materials'][0]['trays'][0]
        self.assertEqual(tray['id'], '2')
        self.assertEqual(tray['material']['value'], 'PETG')
        self.assertEqual(tray['color']['value'], '00FF00FF')
        payload['print']['ams']['ams'][0]['tray'][0]['tray_type'] = CANARY
        session.enqueue(Envelope(session.generation, 'device/PRINTER/report',
                                 json.dumps(payload).encode(), NOW + timedelta(seconds=1), 2))
        session.drain()
        session.snapshot(now=NOW + timedelta(seconds=1), monotonic=2)
        self.assertNotIn(CANARY, json.dumps(session.inspection_view()))
        session.stop()


if __name__ == '__main__':
    unittest.main()
