"""Private interaction, native-store boundary and foreground scheduling tests."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import json
import os
from threading import Event
import unittest
from unittest.mock import patch

from bambu_monitor.auth import CloudAuth, FakeHttp, Response, Secret
from bambu_monitor.cli import main
from bambu_monitor.collector import collect, cloud_command, CollectorError
from bambu_monitor.config import Config
from bambu_monitor.credentials import Credentials, SERVICE, native_backend
from bambu_monitor.interaction import PrivateTTY, InteractionError, authenticate, select_device
from bambu_monitor.session import Session, FakeMqtt

CANARY = "SYNTHETIC-CANARY-SECRET-6d086"
T0 = datetime(2026, 11, 18, tzinfo=timezone.utc)


class NativeStore:
    def __init__(self):
        self.values = {}
    def get_password(self, service, key):
        return self.values.get((service, key))
    def set_password(self, service, key, value):
        self.values[service, key] = value
    def delete_password(self, service, key):
        del self.values[service, key]


class UI:
    def __init__(self, *, secrets=(), choices=()):
        self.secrets = iter(secrets)
        self.choices = iter(choices)
        self.notices = []
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def tell(self, value):
        self.notices.append(value)
    def secret(self, prompt):
        return Secret(next(self.secrets))
    def choice(self, prompt, allowed):
        value = next(self.choices)
        if value not in allowed:
            raise InteractionError("invalid_selection")
        return value


class Clock:
    def __init__(self):
        self.now = 0.0
    def mono(self):
        return self.now
    def wall(self):
        return T0 + timedelta(seconds=self.now)
    def wait(self, seconds):
        self.now += seconds


class PumpMqtt(FakeMqtt):
    def __init__(self, clock, actions=None):
        super().__init__()
        self.clock = clock
        self.actions = list(actions) if actions is not None else ["connack", "suback", "message"]
        self.connects = 0
    def connect(self, **kwargs):
        super().connect(**kwargs)
        self.connects += 1
    def pump(self, timeout=.1):
        if not self.actions:
            return
        action = self.actions.pop(0)
        if action == "connack":
            self.callbacks["on_connack"](True)
        elif action == "suback":
            self.callbacks["on_suback"](True)
        elif action == "message":
            self.callbacks["on_message"]("device/P1/report", b'{"print":{"command":"push_status","nozzle_temper":219.5}}', self.clock.wall(), self.clock.mono())
        elif action == "auth":
            self.callbacks["on_connack"](False, auth_rejected=True)
        elif action == "disconnect":
            self.callbacks["on_disconnect"]()
        elif action == "fail":
            raise OSError(CANARY)


class CredentialTests(unittest.TestCase):
    def test_os_slot_is_region_and_reference_scoped_and_close_preserves_entry(self):
        backend = NativeStore()
        store = Credentials("cn", "account-one", backend_factory=lambda: backend)
        store.put("account-one", Secret(CANARY))
        self.assertTrue(store.persistent)
        self.assertNotIn(CANARY, repr(store))
        store.close()
        self.assertEqual(len(backend.values), 1)
        restored = Credentials("cn", "account-one", backend_factory=lambda: backend)
        self.assertEqual(restored.get("account-one").value, CANARY)
        for region, reference in (("global", "account-one"), ("cn", "account-two")):
            self.assertIsNone(Credentials(region, reference, backend_factory=lambda: backend).get(reference))
        restored.clear()
        self.assertEqual(backend.values, {})

    def test_native_selection_never_uses_automatic_third_party_keyring(self):
        with patch("bambu_monitor.credentials.importlib.import_module", side_effect=ImportError(CANARY)) as load:
            self.assertIsNone(native_backend())
            self.assertEqual(load.call_count, 1 if os.sys.platform in ("darwin", "win32", "linux") else 0)
            if load.call_count:
                self.assertIn(load.call_args.args[0], ("keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService"))

    def test_keyring_failure_falls_back_to_memory_and_has_safe_reason(self):
        class Broken(NativeStore):
            def set_password(self, *_):
                raise OSError(CANARY)
        store = Credentials("cn", "a", backend_factory=Broken)
        store.put("a", Secret(CANARY))
        self.assertFalse(store.persistent)
        self.assertEqual(store.get("a").value, CANARY)
        self.assertNotIn(CANARY, store.reason + repr(store))
        store.close()
        with self.assertRaises(ValueError):
            store.get("a")

    def test_auth_close_retains_os_token_but_401_revokes_only_own_slot(self):
        backend = NativeStore()
        def store():
            return Credentials("cn", "a", backend_factory=lambda: backend)
        auth = CloudAuth("cn", FakeHttp([Response(200, {"accessToken": CANARY})]), store(), "a")
        self.assertTrue(auth.login(Secret("account"), Secret(CANARY)))
        auth.close()
        auth = CloudAuth("cn", FakeHttp([Response(401, {})]), store(), "a")
        self.assertTrue(auth.restore())
        self.assertIsNone(auth.mqtt_credentials())
        self.assertEqual(auth.state, "auth_required")
        self.assertEqual(backend.values, {})

    def test_failed_restore_revokes_previous_memory_scope_without_reusing_token(self):
        from bambu_monitor.auth import MemoryCredentials
        store = MemoryCredentials()
        auth = CloudAuth("cn", FakeHttp([Response(200, {"accessToken": CANARY})]), store)
        auth.login(Secret("person@example.test"), Secret(CANARY))
        store.clear()
        self.assertFalse(auth.restore())
        self.assertEqual(auth.state, "auth_required")
        self.assertIsNone(auth.mqtt_credentials())
        self.assertIsNone(auth._token)
        self.assertEqual(auth._devices, ())

    def test_stored_token_requires_preference_uid_not_jwt_decode(self):
        backend = NativeStore()
        store = Credentials("global", "a", backend_factory=lambda: backend)
        store.put("a", Secret("opaque-token"))
        http = FakeHttp([Response(200, {"uid": 12345})])
        auth = CloudAuth("global", http, store, "a")
        self.assertTrue(auth.restore())
        uid, token = auth.mqtt_credentials()
        self.assertEqual(uid.value, "12345")
        self.assertEqual(token.value, "opaque-token")
        self.assertEqual(http.requests, [("GET", "https://api.bambulab.com/v1/design-user-service/my/preference")])
        auth.close()


class InteractionTests(unittest.TestCase):
    def test_no_controlling_tty_refuses_without_falling_back_to_stdin(self):
        with patch("bambu_monitor.interaction.os.open", side_effect=OSError(CANARY)), patch("sys.stdin", io.StringIO(CANARY)) as stdin:
            with self.assertRaisesRegex(InteractionError, "private_tty_required"):
                with PrivateTTY():
                    pass
            self.assertEqual(stdin.tell(), 0)

    @unittest.skipUnless(os.name == "posix", "POSIX private tty only")
    def test_private_tty_wraps_real_nonseekable_terminal(self):
        try:
            master, slave = os.openpty()
        except PermissionError:
            self.skipTest("sandbox denies pseudoterminal allocation; real TTY verification pending")
        try:
            with patch("bambu_monitor.interaction.os.open", return_value=os.dup(slave)):
                with PrivateTTY() as tty:
                    self.assertTrue(tty._stream.isatty())
                    tty.tell("synthetic terminal message")
                    self.assertIn(b"synthetic terminal message", os.read(master, 1024))
        finally:
            os.close(master)
            os.close(slave)

    def test_getpass_warning_aborts_before_echo_fallback(self):
        import getpass
        import warnings
        def insecure(*args, **kwargs):
            warnings.warn(CANARY, getpass.GetPassWarning)
            self.fail("warning must stop before an echoed read")
        tty = PrivateTTY()
        tty._stream = io.StringIO()
        with patch("bambu_monitor.interaction.getpass.getpass", side_effect=insecure):
            with self.assertRaisesRegex(InteractionError, "private_input_unavailable"):
                tty.secret("Password: ")

    def test_password_then_code_only_sends_on_explicit_selection(self):
        http = FakeHttp([Response(200, {"loginType": "verifyCode"}), Response(200, {"accessToken": CANARY})])
        auth = CloudAuth("cn", http)
        ui = UI(secrets=("account", CANARY, "123456"), choices=("received",))
        self.assertTrue(authenticate(auth, ui, allow_restore=False))
        self.assertEqual(len(http.requests), 2)
        self.assertTrue(all("sendemail" not in path for _, path in http.requests))
        self.assertNotIn(CANARY, repr(ui.notices))

    def test_phone_code_explicit_send_cancel_and_password_challenge(self):
        phone = "13800000000"
        http = FakeHttp([Response(200, {}), Response(200, {"accessToken": CANARY})])
        auth = CloudAuth("cn", http)
        self.assertTrue(authenticate(auth, UI(secrets=(phone, "123456"), choices=("send",)),
                                     method="sms-code", allow_restore=False))
        self.assertEqual(http.requests[0][1], "https://bambulab.cn/api/v1/user-service/user/sendsmscode")
        http = FakeHttp([Response(200, {"loginType": "verifyCode"}), Response(200, {}),
                         Response(200, {"accessToken": CANARY})])
        auth = CloudAuth("cn", http)
        self.assertTrue(authenticate(auth, UI(secrets=(phone, "pw", "123456"), choices=("send",)),
                                     allow_restore=False))
        self.assertIn("sendsmscode", http.requests[1][1])
        for region, account in (("global", phone), ("cn", "a@b.invalid")):
            http = FakeHttp([])
            auth = CloudAuth(region, http)
            with self.assertRaisesRegex(InteractionError, "code_send_failed"):
                authenticate(auth, UI(secrets=(account,), choices=("send",)), method="sms-code", allow_restore=False)
            self.assertEqual(http.requests, [])
        http = FakeHttp([])
        with self.assertRaisesRegex(InteractionError, "login_cancelled"):
            authenticate(CloudAuth("cn", http), UI(secrets=(phone,), choices=("cancel",)),
                         method="sms-code", allow_restore=False)
        self.assertEqual(http.requests, [])

    def test_email_code_200_without_success_flag_and_mfa_are_explicit(self):
        http = FakeHttp([Response(200, {}), Response(200, {"accessToken": CANARY})])
        auth = CloudAuth("cn", http)
        self.assertTrue(authenticate(auth, UI(secrets=("account", "123456"), choices=("send",)), method="email-code", allow_restore=False))
        self.assertEqual(len(http.requests), 2)
        mfa = CloudAuth("cn", FakeHttp([Response(200, {"loginType": "tfa", "tfaKey": CANARY})]))
        self.assertFalse(authenticate(mfa, UI(secrets=("account", CANARY)), allow_restore=False))
        self.assertEqual(mfa.reason, "mfa_unsupported")

    def test_product_model_fallback_uses_only_known_aliases(self):
        http = FakeHttp([Response(200, {"accessToken": CANARY}), Response(200, {"devices": [
            {"dev_id": "P1", "dev_model_name": "N1", "dev_product_name": "A1 mini"},
            {"dev_id": "P2", "dev_model_name": "X1 Carbon"},
            {"dev_id": "P3", "dev_product_name": CANARY}]} )])
        auth = CloudAuth("cn", http)
        auth.login(Secret("person@example.test"), Secret(CANARY))
        self.assertEqual([item.model for item in auth.discover()], ["A1 mini", "X1C", None])

    def test_device_selection_masking_reordering_and_stale_list(self):
        auth = CloudAuth("cn", FakeHttp([Response(200, {"accessToken": CANARY}), Response(200, {"devices": [
            {"dev_id": "DEVICE-A123", "name": CANARY, "dev_model_name": "A1"}, {"dev_id": "DEVICE-B456", "name": CANARY}]})]))
        auth.login(Secret("account"), Secret(CANARY))
        devices = auth.discover()
        ui = UI(choices=("2",))
        self.assertEqual(select_device(auth, devices, "", ui).id, "DEVICE-B456")
        self.assertNotIn(CANARY, repr(ui.notices))
        self.assertNotIn("DEVICE-A123", repr(ui.notices))
        auth.invalidate()
        self.assertIsNone(auth.select(devices, "DEVICE-A123"))


class RunnerTests(unittest.TestCase):
    def run_collect(self, actions=None, *, duration=1, liveness=60, emit=None):
        clock = Clock()
        mqtt = PumpMqtt(clock, actions)
        session = Session(Config("cn", "P1", liveness_seconds=liveness), mqtt, random_value=lambda: 1)
        output = []
        result = collect(session, Secret("synthetic-uid"), Secret(CANARY), emit=emit or output.append,
                         stop_event=Event(), duration=duration, monotonic=clock.mono,
                         wall_clock=clock.wall, wait=clock.wait)
        return result, output, session, mqtt

    def test_foreground_pumps_actual_session_and_publishes_typed_snapshots(self):
        result, outputs, session, mqtt = self.run_collect()
        self.assertEqual(result, 0)
        self.assertEqual(session.phase, "stopped")
        self.assertTrue(mqtt.closed)
        self.assertTrue(any(item["snapshot"]["metrics"]["nozzle_celsius"]["value"] == 219.5 for item in outputs))
        self.assertTrue(all(item["input_class"] == "live-cloud" for item in outputs))
        self.assertTrue(all(not item["diagnostics"]["telemetry"]["baseline_complete"] for item in outputs))
        self.assertNotIn(CANARY, json.dumps(outputs))

    def test_auth_rejection_stops_without_any_reconnect(self):
        result, outputs, session, mqtt = self.run_collect(["auth"])
        self.assertEqual(result, 3)
        self.assertEqual(mqtt.connects, 1)
        self.assertEqual(outputs[-1]["snapshot"]["connection"]["state"], "auth_required")
        self.assertTrue(mqtt.closed)

    def test_network_failure_recovers_on_bounded_retry_not_busy_loop(self):
        result, outputs, session, mqtt = self.run_collect(["connack", "suback", "fail", "connack", "suback", "message"], duration=3)
        self.assertEqual(result, 0)
        self.assertEqual(mqtt.connects, 2)
        self.assertTrue(any(item["snapshot"]["metrics"]["nozzle_celsius"]["value"] == 219.5 for item in outputs))
        self.assertNotIn(CANARY, json.dumps(outputs))

    def test_timer_marks_silent_target_offline_while_broker_remains_connected(self):
        result, outputs, session, mqtt = self.run_collect(["connack", "suback"], duration=2, liveness=.5)
        self.assertEqual(result, 0)
        self.assertEqual(mqtt.connects, 1)
        self.assertTrue(any(item["snapshot"]["connection"]["state"] == "offline" for item in outputs))
        self.assertTrue(all(item["snapshot"]["connection"]["last_message_at"] is None for item in outputs))

    def test_handshake_timeout_cannot_wait_forever(self):
        _, outputs, _, mqtt = self.run_collect([], duration=18)
        self.assertGreaterEqual(mqtt.connects, 2)
        self.assertLess(mqtt.connects, 4)
        self.assertTrue(any(item["diagnostics"]["reason"] == "handshake_timeout" for item in outputs))

    def test_stop_during_suback_pump_prevents_initialization_publish(self):
        clock, stop, output = Clock(), Event(), []
        class StoppingMqtt(PumpMqtt):
            def pump(self, timeout=.1):
                next_action = self.actions[0] if self.actions else None
                super().pump(timeout)
                if next_action == "suback":
                    stop.set()
        mqtt = StoppingMqtt(clock)
        session = Session(Config("cn", "P1"), mqtt)
        self.assertEqual(collect(session, Secret("uid"), Secret(CANARY), emit=output.append,
                                 stop_event=stop, monotonic=clock.mono, wall_clock=clock.wall,
                                 wait=clock.wait), 0)
        self.assertEqual(mqtt.requests, [])
        self.assertTrue(mqtt.closed)

    def test_stop_during_first_initialize_prevents_second_request(self):
        clock, stop = Clock(), Event()
        mqtt = PumpMqtt(clock)
        original = mqtt.publish
        def publish(*args):
            original(*args)
            stop.set()
        mqtt.publish = publish
        session = Session(Config("cn", "P1"), mqtt)
        collect(session, Secret("uid"), Secret(CANARY), emit=lambda _: None,
                stop_event=stop, monotonic=clock.mono, wall_clock=clock.wall, wait=clock.wait)
        self.assertEqual(len(mqtt.requests), 1)
        self.assertEqual(json.loads(mqtt.requests[0][1])["info"]["command"], "get_version")
        self.assertTrue(mqtt.closed)

    def test_stop_request_prevents_further_output_and_closes_transport(self):
        clock, stop, output = Clock(), Event(), []
        mqtt = PumpMqtt(clock)
        session = Session(Config("cn", "P1"), mqtt)
        def emit(item):
            output.append(item)
            stop.set()
        self.assertEqual(collect(session, Secret("uid"), Secret(CANARY), emit=emit, stop_event=stop,
                                 monotonic=clock.mono, wall_clock=clock.wall, wait=clock.wait), 0)
        self.assertEqual(len(output), 1)
        self.assertTrue(mqtt.closed)
        self.assertEqual(session.phase, "stopped")

    def test_machine_output_failure_still_closes_session(self):
        def failing(_):
            raise BrokenPipeError(CANARY)
        with self.assertRaises(BrokenPipeError):
            self.run_collect(emit=failing)

    def test_cloud_orchestration_private_auth_selection_and_stdout_separation(self):
        clock, stop, output, diagnostics = Clock(), Event(), io.StringIO(), io.StringIO()
        http = FakeHttp([Response(200, {"accessToken": CANARY}), Response(200, {"uid": "123"}), Response(200, {"devices": [{"dev_id": "P1", "name": CANARY}]})])
        ui = UI(secrets=("account", CANARY), choices=("1",))
        mqtt = PumpMqtt(clock)
        result = cloud_command("collect", Config("cn"), duration=1, memory_only=True,
                               tty_factory=lambda: ui, store_factory=lambda region, ref, **kw: Credentials(region, ref, memory_only=True),
                               http_factory=lambda _: http, mqtt_factory=lambda _: mqtt, output=output,
                               diagnostics=diagnostics, stop_event=stop, monotonic=clock.mono,
                               wall_clock=clock.wall, wait=clock.wait)
        self.assertEqual(result, 0)
        self.assertNotIn(CANARY, output.getvalue() + diagnostics.getvalue() + repr(ui.notices))
        for line in output.getvalue().splitlines():
            self.assertIn("snapshot", json.loads(line))
        self.assertIn("collector_started", diagnostics.getvalue())
        self.assertTrue(http.closed)
        self.assertTrue(mqtt.closed)

    def test_auth_close_failure_is_not_reported_as_success(self):
        class BrokenClose(FakeHttp):
            def close(self):
                raise OSError(CANARY)
        http = BrokenClose([Response(200, {"accessToken": CANARY}), Response(200, {"uid": "123"})])
        ui = UI(secrets=("person@example.test", CANARY))
        with self.assertRaisesRegex(CollectorError, "http_cleanup_failed"):
            cloud_command("login", Config("cn"), tty_factory=lambda: ui,
                          store_factory=lambda region, ref, **kw: Credentials(region, ref, memory_only=True),
                          http_factory=lambda _: http, mqtt_factory=lambda _: None,
                          diagnostics=io.StringIO())

    def test_missing_tty_happens_before_any_cloud_request(self):
        http = FakeHttp()
        def missing():
            raise InteractionError("private_tty_required")
        with self.assertRaisesRegex(InteractionError, "private_tty_required"):
            cloud_command("login", Config("cn"), tty_factory=missing,
                          store_factory=lambda region, ref, **kw: Credentials(region, ref, memory_only=True),
                          http_factory=lambda _: http, mqtt_factory=lambda _: None)
        self.assertEqual(http.requests, [])
        self.assertTrue(http.closed)

    def test_local_logout_never_uses_cloud_and_reports_store_failure(self):
        backend = NativeStore()
        store = Credentials("cn", "a", backend_factory=lambda: backend)
        store.put("a", Secret(CANARY))
        self.assertEqual(cloud_command("logout", Config("cn", credential_ref="a"),
                                      store_factory=lambda *a, **kw: store,
                                      http_factory=lambda _: self.fail("logout is local only"),
                                      mqtt_factory=lambda _: self.fail("logout is local only"),
                                      diagnostics=io.StringIO()), 0)
        self.assertEqual(backend.values, {})
        with self.assertRaisesRegex(CollectorError, "credential_store_unavailable"):
            cloud_command("logout", Config("cn"),
                          store_factory=lambda region, ref, **kw: Credentials(region, ref, memory_only=True),
                          http_factory=lambda _: None, mqtt_factory=lambda _: None)

    def test_cli_help_and_bad_duration_never_open_tty_or_network(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(["collect", "unused-config.toml", "--duration", "nan"]), 2)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(json.loads(err.getvalue()), {"error": "invalid_duration"})


if __name__ == "__main__":
    unittest.main()
