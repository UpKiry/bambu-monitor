"""Fake HTTP/MQTT integration; no accounts, sockets, USB or screen writes."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from bambu_monitor.auth import CloudAuth, FakeHttp, Response, Secret, MemoryCredentials
from bambu_monitor.cli import main
from bambu_monitor.config import Config, ConfigError, Identity, ENDPOINTS, load_config
from bambu_monitor.replay import replay, ReplayError
from bambu_monitor.session import Session, FakeMqtt, Envelope

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime.fromisoformat("2026-11-18T08:00:00+08:00")
CANARY = "CANARY-do-not-export-4d1806"


def message(generation, at=0, **fields):
    return Envelope(generation, "device/test-printer/report",
                    json.dumps({"print": {"command": "push_status", **fields}}).encode(),
                    T0 + timedelta(seconds=at), at)


class ConfigAuthTests(unittest.TestCase):
    def test_config_example_is_loadable_but_does_not_select_a_device(self):
        config = load_config(ROOT / "examples/provider.example.toml")
        self.assertIsNone(config.identity)
        self.assertEqual(config.region, "cn")

    def test_config_rejects_secrets_unknown_region_and_nonfinite_budgets(self):
        for addition in ('password="' + CANARY + '"', 'region="auto"', 'liveness_seconds=nan'):
            text = {'password': '[provider]\nkind="bambu-cloud"\nregion="cn"\n',
                    'region': '[provider]\nkind="bambu-cloud"\n',
                    'liveness': '[provider]\nkind="bambu-cloud"\nregion="cn"\n[freshness]\n'}
            key = next(key for key in text if addition.startswith(key))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.toml"
                path.write_text(text[key] + addition)
                with self.assertRaises(ConfigError) as caught:
                    load_config(path)
                self.assertNotIn(CANARY, str(caught.exception))

    def test_identity_namespaces_separate_account_region_and_device(self):
        identities = [Identity("account-a", "cn", "p"), Identity("account-b", "cn", "p"),
                      Identity("account-a", "global", "p"), Identity("account-a", "cn", "q")]
        self.assertEqual(len({item.namespace for item in identities}), 4)
        self.assertEqual(ENDPOINTS["cn"][1], "cn.mqtt.bambulab.com")
        self.assertEqual(ENDPOINTS["global"][0], "https://api.bambulab.com")

    def auth(self, *responses):
        transport = FakeHttp(responses)
        return CloudAuth("cn", transport), transport

    def test_opaque_token_and_explicit_id_selection_no_access_code_export(self):
        auth, transport = self.auth(Response(200, {"accessToken": CANARY}), Response(200, {"devices": [
            {"dev_id": "B", "name": "same", "dev_access_code": CANARY},
            {"dev_id": "A", "name": "same", "dev_access_code": CANARY}]}))
        self.assertTrue(auth.login(Secret(CANARY), Secret(CANARY)))
        devices = auth.discover()
        self.assertIsNone(auth.select(devices, ""))
        self.assertEqual(auth.select(tuple(reversed(devices)), "B").id, "B")
        self.assertIsNone(auth.select(devices, "missing"))
        self.assertEqual(auth.reason, "selected_device_unavailable")
        self.assertNotIn(CANARY, repr(devices) + repr(transport.requests))
        self.assertTrue(auth.close())
        self.assertIsNone(auth.credentials.get(auth.reference))

    def test_code_requires_explicit_send_and_mfa_is_diagnosed(self):
        auth, transport = self.auth(Response(200, {"loginType": "verifyCode"}),
                                    Response(200, {"success": True}),
                                    Response(200, {"loginType": "tfa", "tfaKey": CANARY}))
        self.assertFalse(auth.login(Secret(CANARY), Secret(CANARY)))
        self.assertEqual(auth.reason, "code_required")
        self.assertEqual(len(transport.requests), 1)
        self.assertTrue(auth.send_email_code(Secret(CANARY)))
        self.assertFalse(auth.login(Secret(CANARY), Secret("wrong"), method="code"))
        self.assertEqual(auth.reason, "mfa_unsupported")
        self.assertEqual(auth.state, "auth_required")
        self.assertNotIn(CANARY, repr(auth.__dict__))

    def test_auth_errors_are_value_free_and_do_not_retry(self):
        for response, reason in ((Response(401, {"error": CANARY}), "credential_rejected"),
                                 (Response(403, {"error": CANARY}), "permission_denied"),
                                 (Response(429, {"error": CANARY}), "rate_limited"),
                                 (TimeoutError(CANARY), "transport_error"),
                                 (Response(200, {"code": CANARY}), "credential_rejected")):
            with self.subTest(reason=reason):
                auth, transport = self.auth(response)
                self.assertFalse(auth.login(Secret(CANARY), Secret(CANARY)))
                self.assertEqual(auth.reason, reason)
                self.assertEqual(len(transport.requests), 1)
                self.assertEqual(auth.state, "auth_required")

    def test_401_discovery_revokes_token_and_empty_discovery_is_not_first_device(self):
        auth, transport = self.auth(Response(200, {"accessToken": CANARY}), Response(401, {}))
        auth.login(Secret(CANARY), Secret(CANARY))
        self.assertEqual(auth.discover(), ())
        self.assertEqual(auth.state, "auth_required")
        self.assertEqual(auth.discover(), ())
        self.assertEqual(len(transport.requests), 2)
        self.assertIsNone(auth.select((), "A"))

    def test_close_during_http_request_discards_late_token(self):
        entered, release = Event(), Event()

        class BlockingHttp(FakeHttp):
            def request(self, *args, **kwargs):
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test release missing")
                return Response(200, {"accessToken": CANARY})

        auth = CloudAuth("cn", BlockingHttp())
        results = []
        worker = Thread(target=lambda: results.append(auth.login(Secret(CANARY), Secret(CANARY))))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(auth.close())
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])
        self.assertEqual(auth.state, "auth_required")
        self.assertIsNone(auth.credentials.get(auth.reference))

    def test_old_login_cannot_rebind_to_new_epoch_before_request(self):
        auth = CloudAuth("cn", FakeHttp([Response(200, {"accessToken": CANARY})]))
        entered, release = Event(), Event()
        original = auth._request
        results = []

        def delayed_request(method, path, *, epoch, **kwargs):
            if kwargs.get("body", {}).get("password") == "old-password":
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test release missing")
            return original(method, path, epoch=epoch, **kwargs)

        auth._request = delayed_request
        worker = Thread(target=lambda: results.append(auth.login(Secret("account"), Secret("old-password"))))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(auth.login(Secret("account"), Secret("new-password")))
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])
        self.assertEqual(auth.state, "authenticated")
        self.assertIsNotNone(auth.credentials.get(auth.reference))
        self.assertEqual(len(auth.transport.requests), 1)

    def test_credential_store_exceptions_fall_back_to_memory_without_secret_error(self):
        class BrokenStore:
            def clear(self):
                pass
            def put(self, *args):
                raise OSError(CANARY)

        auth = CloudAuth("cn", FakeHttp([Response(200, {"accessToken": CANARY})]), BrokenStore())
        self.assertTrue(auth.login(Secret(CANARY), Secret(CANARY)))
        self.assertFalse(auth.credentials.persistent)
        self.assertNotIn(CANARY, auth.reason)

    def test_session_memory_is_private_and_has_no_file_fallback(self):
        store = MemoryCredentials()
        store.put("x", Secret(CANARY))
        self.assertFalse(store.persistent)
        self.assertNotIn(CANARY, repr(store.get("x")))
        store.clear()
        self.assertIsNone(store.get("x"))


class SessionTests(unittest.TestCase):
    def make(self, *, queue_size=64):
        mqtt = FakeMqtt()
        session = Session(Config("cn", "test-printer"), mqtt, queue_size=queue_size, random_value=lambda: 0.5)
        session.start(uid=Secret(CANARY), token=Secret(CANARY), monotonic=0)
        return session, mqtt

    def ready(self, **kwargs):
        session, mqtt = self.make(**kwargs)
        mqtt.callbacks["on_connack"](True)
        session.drain()
        mqtt.callbacks["on_suback"](True)
        session.drain()
        return session, mqtt

    def test_tls_unique_id_subscription_then_only_two_read_commands(self):
        session, mqtt = self.make()
        self.assertTrue(mqtt.connection["tls"])
        self.assertEqual(mqtt.connection["port"], 8883)
        self.assertEqual(mqtt.requests, [])
        mqtt.callbacks["on_connack"](True)
        session.drain()
        self.assertEqual(mqtt.subscriptions, ["device/test-printer/report"])
        self.assertEqual(mqtt.requests, [])
        mqtt.callbacks["on_suback"](True)
        session.drain()
        mqtt.callbacks["on_suback"](True)
        session.drain()
        self.assertEqual([json.loads(payload) for _, payload in mqtt.requests], [
            {"info": {"sequence_id": "0", "command": "get_version"}},
            {"pushing": {"sequence_id": "0", "command": "pushall"}}])
        self.assertNotIn(CANARY, repr(mqtt.__dict__) + repr(session.diagnostics()))
        snapshot = session.snapshot(now=T0, monotonic=0)
        self.assertEqual(snapshot["connection"]["state"], "connecting")
        self.assertIsNone(snapshot["connection"]["last_message_at"])
        previous_id = mqtt.connection["client_id"]
        session.disconnect(session.generation)
        session.start(uid=Secret(CANARY), token=Secret(CANARY), monotonic=1)
        self.assertNotEqual(mqtt.connection["client_id"], previous_id)
        mqtt.callbacks["on_connack"](True)
        session.drain()
        mqtt.callbacks["on_suback"](True)
        session.drain()
        self.assertEqual(len(mqtt.requests), 2)  # global initialization throttle

    def test_failed_suback_does_not_request_or_become_online(self):
        session, mqtt = self.make()
        mqtt.callbacks["on_connack"](True)
        session.drain()
        mqtt.callbacks["on_suback"](False)
        self.assertEqual(mqtt.requests, [])
        self.assertEqual(session.reason, "subscription_rejected")
        self.assertFalse(session.active)

    def test_disconnect_revokes_queued_and_late_callbacks_before_restart(self):
        session, mqtt = self.ready()
        previous_callbacks = dict(mqtt.callbacks)
        old = session.generation
        session.enqueue(message(old, nozzle_temper=220))
        session.disconnect(old)
        session.start(uid=Secret(CANARY), token=Secret(CANARY), monotonic=1)
        mqtt.callbacks["on_connack"](True)
        session.drain()
        mqtt.callbacks["on_suback"](True)
        session.drain()
        previous_callbacks["on_message"]("device/test-printer/report", message(old).payload, T0, 0)
        self.assertEqual(session.drain(), 0)
        snapshot = session.snapshot(now=T0 + timedelta(seconds=1), monotonic=1)
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["quality"], "unknown")
        self.assertGreater(session.counters["late"], 0)

    def test_overflow_revokes_generation_instead_of_hiding_identity_changes(self):
        session, mqtt = self.ready(queue_size=2)
        for index in range(2):
            self.assertTrue(session.enqueue(message(session.generation, index, nozzle_temper=220)))
        self.assertFalse(session.enqueue(message(session.generation, 2, task_id="2")))
        self.assertFalse(session.active)
        self.assertEqual(session.reason, "queue_overflow")
        self.assertEqual(session.counters["dropped"], 1)
        self.assertEqual(session.diagnostics()["queued"], 0)

    def test_auth_rejection_stops_retry_backoff_is_capped_and_jittered(self):
        session, mqtt = self.ready()
        delays = []
        for _ in range(12):
            session.disconnect(session.generation)
            delays.append(session.retry_at - session._last_monotonic)
            session.start(uid=Secret(CANARY), token=Secret(CANARY), monotonic=100)
        self.assertEqual(delays[0], 0.75)
        self.assertEqual(max(delays), 45)
        session.disconnect(session.generation, auth_rejected=True)
        self.assertEqual(session.phase, "auth_required")
        self.assertIsNone(session.retry_at)

    def test_message_bound_and_stop_clear_resources_no_further_snapshot(self):
        session, mqtt = self.ready()
        self.assertFalse(session.enqueue(Envelope(session.generation, "topic", b"x" * 65537, T0, 0)))
        self.assertTrue(session.stop())
        self.assertTrue(mqtt.closed)
        self.assertIsNone(session.snapshot(now=T0, monotonic=0))
        self.assertFalse(session.enqueue(message(session.generation, nozzle_temper=123)))

    def test_callbacks_defer_io_and_old_disconnect_cannot_close_new_transport(self):
        session, mqtt = self.make()
        calls = []
        original_publish, original_close = mqtt.publish, mqtt.close

        def checked_publish(*args):
            calls.append(("publish", session._lock._is_owned()))
            original_publish(*args)

        def checked_close():
            calls.append(("close", session._lock._is_owned()))
            original_close()

        mqtt.publish, mqtt.close = checked_publish, checked_close
        old = dict(mqtt.callbacks)
        old["on_connack"](True)
        self.assertEqual(mqtt.subscriptions, [])
        session.drain()
        old["on_suback"](True)
        self.assertEqual(mqtt.requests, [])
        session.drain()
        # Network callback revokes immediately, defers close to owner.
        old["on_disconnect"]()
        self.assertFalse(mqtt.closed)
        session.start(uid=Secret(CANARY), token=Secret(CANARY), monotonic=1)
        self.assertTrue(session.active)
        self.assertFalse(mqtt.closed)
        old["on_disconnect"]()
        session.drain()
        self.assertFalse(mqtt.closed)
        self.assertEqual(calls, [("publish", False), ("publish", False), ("close", False)])
        session.stop()

    def test_close_failure_is_reported_and_prevents_reconnect(self):
        session, mqtt = self.ready()
        def broken_close():
            raise TimeoutError(CANARY)
        mqtt.close = broken_close
        session.disconnect(session.generation)
        session.drain()
        self.assertTrue(session.diagnostics()["cleanup_failed"])
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                session.start(uid=Secret(CANARY), token=Secret(CANARY), monotonic=1)
        self.assertFalse(session.stop())
        self.assertNotIn(CANARY, repr(session.diagnostics()))

    def test_new_identity_session_never_accepts_stopped_device_callbacks(self):
        previous, transport = self.ready()
        previous.enqueue(message(previous.generation, nozzle_temper=220))
        previous.drain()
        old_callback = transport.callbacks["on_message"]
        previous.stop()
        fresh_transport = FakeMqtt()
        fresh = Session(Config("global", "other-printer", "account-b"), fresh_transport)
        fresh.start(uid=Secret(CANARY), token=Secret(CANARY), monotonic=1)
        old_callback("device/test-printer/report", message(1, nozzle_temper=999).payload, T0, 1)
        snapshot = fresh.snapshot(now=T0, monotonic=1)
        self.assertEqual(snapshot["printer"]["id"], "other-printer")
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["quality"], "unknown")
        self.assertIsNone(previous.snapshot(now=T0, monotonic=1))
        self.assertNotEqual(previous.identity.namespace, fresh.identity.namespace)
        self.assertEqual(fresh_transport.connection["host"], "us.mqtt.bambulab.com")
        fresh.stop()

    def test_local_invalid_cannot_make_broker_evidence_online(self):
        session, mqtt = self.ready()
        for topic, payload in (("device/other/report", b'{"print":{"command":"push_status","nozzle_temper":10}}'),
                               ("device/test-printer/report", b'{"info":{"command":"get_version"}}'),
                               ("device/test-printer/report", b"invalid")):
            session.enqueue(Envelope(session.generation, topic, payload, T0, 0))
        snapshot = session.snapshot(now=T0, monotonic=0)
        self.assertIsNone(snapshot["connection"]["last_message_at"])
        self.assertNotEqual(snapshot["connection"]["state"], "online")


class ReplayTests(unittest.TestCase):
    def test_end_to_end_replay_outputs_valid_schema_retention_timeout_and_generation(self):
        validator = Draft202012Validator(json.loads((ROOT / "schemas/printer-snapshot.schema.json").read_text()),
                                        format_checker=FormatChecker())
        with (ROOT / "examples/replay.synthetic.ndjson").open("rb") as stream:
            outputs = list(replay(stream))
        for output in outputs:
            self.assertEqual(output["input_class"], "synthetic")
            validator.validate(output["snapshot"])
        retained = next(output["snapshot"] for output in outputs
                        if output["snapshot"]["generated_at"].startswith("2026-11-18T08:03:00"))
        self.assertEqual(retained["metrics"]["total_layers"]["quality"], "valid")
        self.assertEqual(retained["metrics"]["total_layers"]["updated_at"], T0.isoformat())
        timed_out = next(output["snapshot"] for output in outputs
                         if output["snapshot"]["generated_at"].startswith("2026-11-18T08:04:01"))
        self.assertEqual(timed_out["connection"]["state"], "offline")
        self.assertEqual(timed_out["metrics"]["nozzle_celsius"]["quality"], "stale")
        final = outputs[-1]["snapshot"]
        self.assertEqual(final["metrics"]["nozzle_celsius"]["value"], 0)
        self.assertEqual(final["metrics"]["nozzle_celsius"]["quality"], "valid")
        self.assertEqual(final["metrics"]["total_layers"]["quality"], "stale")

    def test_replay_budgets_and_missing_fixture_label_are_rejected(self):
        for data in (b'{"kind":"start"}\n', b'x' * 65537, b'{}\n', b'[]\n'):
            with self.assertRaises(ReplayError):
                list(replay(io.BytesIO(data)))

    def test_replay_rejects_duplicate_keys_before_reserializing_payload(self):
        header = b'{"kind":"fixture","input_class":"synthetic","profile":"legacy-cloud-v1","region":"cn","printer_id":"test"}\n'
        for event in (b'{"kind":"message","at":"2026-11-18T08:00:00+08:00","monotonic":0,"payload":{"print":{"nozzle_temper":1,"nozzle_temper":2}}}\n',
                      b'{"kind":"tick","at":"2026-11-18T08:00:00+08:00","monotonic":NaN}\n'):
            with self.assertRaises(ReplayError):
                list(replay(io.BytesIO(header + event)))

    def test_huge_numeric_input_has_a_bounded_safe_cli_error(self):
        config = '[provider]\nkind="bambu-cloud"\nregion="cn"\n[freshness]\nliveness_seconds=' + '9' * 1000
        header = '{"kind":"fixture","input_class":"synthetic","profile":"legacy-cloud-v1","region":"cn","printer_id":"test"}\n'
        event = '{"kind":"tick","at":"2026-11-18T08:00:00+08:00","monotonic":' + '9' * 1000 + '}\n'
        with tempfile.TemporaryDirectory() as directory:
            for command, text in (("check-config", config), ("replay", header + event)):
                path = Path(directory) / "input.txt"
                path.write_text(text)
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    self.assertEqual(main([command, str(path)]), 2)
                self.assertEqual(out.getvalue(), "")
                self.assertIn("error", json.loads(err.getvalue()))
                self.assertNotIn("Traceback", err.getvalue())

    def test_cli_machine_output_and_error_diagnostics_exclude_canary(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = main(["replay", str(ROOT / "examples/replay.synthetic.ndjson")])
        self.assertEqual(result, 0)
        self.assertEqual(err.getvalue(), "")
        for line in out.getvalue().splitlines():
            self.assertEqual(json.loads(line)["input_class"], "synthetic")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.ndjson"
            path.write_text(CANARY)
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(["replay", str(path)]), 2)
        self.assertNotIn(CANARY, out.getvalue() + err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
