"""Offline adapter tests. No DNS, sockets, threads or real broker connection."""
from collections import deque
from datetime import datetime, timezone
import importlib.util
import json
from types import SimpleNamespace
from threading import Thread
import traceback
import unittest
from unittest.mock import patch

from bambu_monitor.auth import Secret
from bambu_monitor.cloud_mqtt import PahoMqtt, SafeMqttError, _bounded_client_type
from bambu_monitor.config import Config
from bambu_monitor.session import Session

CANARY = "synthetic-MQTT-token-do-not-export"
WALL = datetime(2026, 11, 18, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.calls = []
        self.events = deque()
        self._out_packet = deque()
        self._username = self._password = None
        self.connect_rc = self.loop_rc = self.subscribe_rc = self.publish_rc = 0
        self.mid = 7
        self.early_suback = None
        self.closed = False
        self.fail_close = False

    def disable_logger(self):
        self.logger = None

    def max_inflight_messages_set(self, value):
        self.inflight = value

    def max_queued_messages_set(self, value):
        self.queue_size = value

    def tls_set_context(self, context):
        self.context = context

    def tls_insecure_set(self, flag):
        self.insecure = flag

    def username_pw_set(self, username, password=None):
        self._username, self._password = username, password

    def connect(self, host, port, keepalive):
        self.calls.append(("connect", host, port, keepalive))
        self._out_packet.append({"packet": CANARY.encode()})
        return self.connect_rc

    def subscribe(self, topic, qos):
        self.calls.append(("subscribe", topic, qos))
        if self.early_suback is not None:
            mid, codes = self.early_suback
            self.on_subscribe(self, None, mid, codes, None)
        return self.subscribe_rc, self.mid

    def publish(self, topic, payload, qos, retain):
        self.calls.append(("publish", topic, payload, qos, retain))
        return SimpleNamespace(rc=self.publish_rc)

    def loop(self, timeout):
        self.calls.append(("loop", timeout))
        if self.events:
            event = self.events.popleft()
            event()
        return self.loop_rc

    def _reset_sockets(self):
        self.closed = True
        if self.fail_close:
            raise OSError(CANARY)

    def loop_start(self):
        raise AssertionError("must not start threads")

    def loop_stop(self):
        raise AssertionError("must not join threads")

    def loop_forever(self):
        raise AssertionError("must not own retry loop")

    def connect_async(self, *args, **kwargs):
        raise AssertionError("async config is not driven by manual loop")


class MqttAdapterTests(unittest.TestCase):
    def make(self):
        self.clients = []
        def factory(**kwargs):
            client = FakeClient(**kwargs)
            self.clients.append(client)
            return client
        module = SimpleNamespace(CallbackAPIVersion=SimpleNamespace(VERSION2=2), MQTTv311=4,
                                 MQTT_ERR_SUCCESS=0)
        adapter = PahoMqtt("test-printer", client_factory=factory, mqtt_module=module,
                           wall_clock=lambda: WALL, monotonic=lambda: 12.5)
        callbacks = {name: [] for name in ("connack", "suback", "message", "disconnect")}
        self.args = dict(host="cn.mqtt.bambulab.com", port=8883, tls=True,
                         client_id="bambu-monitor-" + "a" * 32,
                         username=Secret("u_12345"), token=Secret(CANARY),
                         on_connack=lambda value, **kwargs: callbacks["connack"].append((value, kwargs)),
                         on_suback=lambda value: callbacks["suback"].append(value),
                         on_message=lambda *args: callbacks["message"].append(args),
                         on_disconnect=lambda **kwargs: callbacks["disconnect"].append(kwargs))
        return adapter, callbacks

    def connected(self):
        adapter, callbacks = self.make()
        adapter.connect(**self.args)
        client = self.clients[-1]
        client.on_connect(client, None, None, SimpleNamespace(value=0), None)
        return adapter, callbacks, client

    def subscribed(self):
        adapter, callbacks, client = self.connected()
        adapter.subscribe("device/test-printer/report")
        client.on_subscribe(client, None, 7, [SimpleNamespace(value=1)], None)
        return adapter, callbacks, client

    def test_connection_defaults_tls_owner_and_manual_loop_only(self):
        adapter, callbacks = self.make()
        adapter.connect(**self.args)
        client = self.clients[-1]
        self.assertEqual(client.options["callback_api_version"], 2)
        self.assertEqual(client.options["protocol"], 4)
        self.assertTrue(client.options["clean_session"])
        self.assertFalse(client.options["reconnect_on_failure"])
        self.assertEqual(client.connect_timeout, 2)
        self.assertEqual((client.inflight, client.queue_size), (1, 2))
        self.assertTrue(client.context.check_hostname)
        self.assertNotEqual(client.context.verify_mode, 0)
        self.assertFalse(client.insecure)
        self.assertEqual(client.calls, [("connect", "cn.mqtt.bambulab.com", 8883, 30)])
        self.assertEqual(callbacks["connack"], [])
        client.events.append(lambda: client.on_connect(client, None, None, SimpleNamespace(value=0), None))
        self.assertIsNone(adapter.pump())
        self.assertEqual(callbacks["connack"], [(True, {"auth_rejected": False})])
        self.assertEqual(client.calls[-1], ("loop", .1))
        adapter.close()

    def test_broker_topic_tls_identity_and_secret_parameters_rejected(self):
        for change in ({"host": "localhost"}, {"host": []}, {"port": 1883}, {"tls": False},
                       {"client_id": "static-id"}, {"username": Secret("bblp")}, {"token": CANARY}):
            adapter, callbacks = self.make()
            with self.assertRaises(SafeMqttError) as caught:
                adapter.connect(**(self.args | change))
            self.assertNotIn(CANARY, str(caught.exception))
            self.assertEqual(self.clients, [])
        adapter, callbacks, client = self.connected()
        with self.assertRaises(SafeMqttError):
            adapter.subscribe("device/other/report")
        adapter.close()

    def test_matching_suback_mid_and_failure_code_are_required(self):
        for codes, expected in (([SimpleNamespace(value=0)], True), ([SimpleNamespace(value=1)], True),
                                ([SimpleNamespace(value=128)], False), ([], False), ([0, 1], False), ([2], False)):
            adapter, callbacks, client = self.connected()
            adapter.subscribe("device/test-printer/report")
            client.on_subscribe(client, None, 99, codes, None)
            self.assertEqual(callbacks["suback"], [])
            client.on_subscribe(client, None, 7, codes, None)
            self.assertEqual(callbacks["suback"], [expected])
            client.on_subscribe(client, None, 7, codes, None)
            self.assertEqual(len(callbacks["suback"]), 1)
            adapter.close()

    def test_reentrant_suback_is_checked_after_returned_mid(self):
        adapter, callbacks, client = self.connected()
        client.early_suback = (7, [0])
        adapter.subscribe("device/test-printer/report")
        self.assertEqual(callbacks["suback"], [True])
        adapter.close()
        adapter.connect(**self.args)
        client = self.clients[-1]
        client.on_connect(client, None, None, 0, None)
        client.early_suback = (99, [0])
        adapter.subscribe("device/test-printer/report")
        self.assertEqual(callbacks["suback"], [True])
        client.on_subscribe(client, None, 7, [0], None)
        self.assertEqual(callbacks["suback"], [True, True])
        adapter.close()

    def test_publish_exact_allowlist_deduplicates_and_never_replays_unknown_outcome(self):
        adapter, callbacks, client = self.subscribed()
        commands = [{"info": {"sequence_id": "0", "command": "get_version"}},
                    {"pushing": {"sequence_id": "0", "command": "pushall"}}]
        for command in commands:
            payload = json.dumps(command).encode()
            adapter.publish("device/test-printer/request", payload)
            adapter.publish("device/test-printer/request", payload)
        published = [call for call in client.calls if call[0] == "publish"]
        self.assertEqual(len(published), 2)
        self.assertTrue(all(call[-2:] == (0, False) for call in published))
        for payload in (b'{"print":{"command":"pause"}}', b'{"pushing":{"sequence_id":"1","command":"pushall"}}',
                        b'{"info":{"command":"get_version","sequence_id":"0"},"extra":0}',
                        b'{"info":{"command":"pause","command":"get_version","sequence_id":"0"}}',
                        b"x" * 257):
            with self.assertRaises(SafeMqttError):
                adapter.publish("device/test-printer/request", payload)
        adapter.close()
        adapter, callbacks, client = self.subscribed()
        client.publish_rc = 15
        payload = json.dumps(commands[0]).encode()
        with self.assertRaises(SafeMqttError):
            adapter.publish("device/test-printer/request", payload)
        adapter.publish("device/test-printer/request", payload)
        self.assertEqual(len([call for call in client.calls if call[0] == "publish"]), 1)
        adapter.close()

    def test_incoming_message_scope_size_retained_and_receive_timestamp(self):
        adapter, callbacks, client = self.subscribed()
        for topic, payload, retain in (("device/other/report", b"{}", False),
                                       ("device/test-printer/report", b"x" * 65537, False),
                                       ("device/test-printer/report", b"{}", True)):
            client.on_message(client, None, SimpleNamespace(topic=topic, payload=payload, retain=retain))
        self.assertEqual(callbacks["message"], [])
        client.on_message(client, None, SimpleNamespace(topic="device/test-printer/report", payload=b"{}", retain=False))
        self.assertEqual(callbacks["message"], [("device/test-printer/report", b"{}", WALL, 12.5)])
        adapter.close()

    def test_auth_connack_and_disconnect_stop_pump_without_auto_retry(self):
        for code in (134, 135):
            adapter, callbacks = self.make()
            adapter.connect(**self.args)
            client = self.clients[-1]
            client.on_connect(client, None, None, SimpleNamespace(value=code), None)
            self.assertEqual(callbacks["connack"], [(False, {"auth_rejected": True})])
            adapter.pump()
            self.assertFalse(any(call[0] == "loop" for call in client.calls))
            adapter.close()
        adapter, callbacks, client = self.subscribed()
        client.loop_rc = 7
        adapter.pump()
        self.assertEqual(callbacks["disconnect"], [{"auth_rejected": False}])
        adapter.pump()
        self.assertEqual(len([call for call in client.calls if call[0] == "loop"]), 1)
        adapter.close()

    def test_close_erases_credentials_connect_packet_and_ignores_old_client_callbacks(self):
        adapter, callbacks, client = self.subscribed()
        old = (client.on_connect, client.on_disconnect, client.on_message, client.on_subscribe)
        adapter.close()
        self.assertTrue(client.closed)
        self.assertIsNone(client._username)
        self.assertIsNone(client._password)
        self.assertEqual(list(client._out_packet), [])
        self.assertIsNone(adapter._client)
        self.assertEqual(adapter._callbacks, {})
        self.assertNotIn(CANARY, repr(adapter.__dict__))
        adapter.connect(**self.args)
        current = self.clients[-1]
        old[0](client, None, None, 0, None)
        old[1](client, None, None, 135, None)
        old[2](client, None, SimpleNamespace(topic="device/test-printer/report", payload=b"old", retain=False))
        old[3](client, None, 7, [0], None)
        self.assertFalse(current.closed)
        self.assertEqual(callbacks["disconnect"], [])
        self.assertEqual(callbacks["message"], [])
        adapter.close()

    def test_owner_thread_and_timeout_budgets(self):
        adapter, callbacks, client = self.subscribed()
        results = []
        def wrong_thread():
            for method in (adapter.pump, adapter.close):
                try:
                    method()
                except SafeMqttError as error:
                    results.append(str(error))
        worker = Thread(target=wrong_thread)
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, ["mqtt_owner_thread_required"] * 2)
        for invalid in (-1, 2, True, float("nan"), 10**1000):
            with self.assertRaises(SafeMqttError):
                adapter.pump(invalid)
        adapter.close()

    def test_library_exception_text_is_not_exported_and_cleanup_still_runs(self):
        adapter, callbacks, client = self.subscribed()
        def failing_loop(timeout):
            raise OSError(CANARY)
        client.loop = failing_loop
        with self.assertRaises(SafeMqttError) as caught:
            adapter.pump()
        formatted = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn(CANARY, formatted)
        client.fail_close = True
        with self.assertRaises(SafeMqttError) as caught:
            adapter.close()
        self.assertEqual(str(caught.exception), "mqtt_close_failed")
        self.assertIsNone(client._password)
        self.assertEqual(list(client._out_packet), [])
        self.assertIsNone(adapter._client)

    def test_failed_cleanup_is_retained_and_cannot_be_bypassed_by_second_close(self):
        adapter, callbacks, client = self.subscribed()
        client.fail_close = True
        for _ in range(2):
            with self.assertRaisesRegex(SafeMqttError, "mqtt_close_failed"):
                adapter.close()
            self.assertIs(adapter._closing_client, client)
            self.assertIsNone(adapter._client)
            self.assertIsNone(client._password)
            with self.assertRaisesRegex(SafeMqttError, "mqtt_cleanup_pending"):
                adapter.connect(**self.args)
        self.assertEqual(len(self.clients), 1)
        self.assertNotIn(CANARY, repr(adapter))
        client.fail_close = False
        adapter.close()
        self.assertIsNone(adapter._closing_client)
        adapter.connect(**self.args)
        self.assertEqual(len(self.clients), 2)
        adapter.close()

    def test_connect_subscribe_and_pump_failure_paths_remain_value_free(self):
        adapter, callbacks = self.make()
        factory = adapter._factory
        def failing_factory(**kwargs):
            client = factory(**kwargs)
            def fail(*args, **kwargs):
                raise OSError(CANARY)
            client.connect = fail
            return client
        adapter._factory = failing_factory
        with self.assertRaisesRegex(SafeMqttError, "mqtt_connect_failed") as caught:
            adapter.connect(**self.args)
        self.assertNotIn(CANARY, "".join(traceback.format_exception(caught.exception)))
        client = self.clients[-1]
        self.assertTrue(client.closed)
        self.assertIsNone(client._password)
        self.assertIsNone(adapter._client)
        adapter, callbacks, client = self.connected()
        client.subscribe_rc = 4
        with self.assertRaisesRegex(SafeMqttError, "mqtt_subscribe_failed"):
            adapter.subscribe("device/test-printer/report")
        self.assertFalse(adapter._subscribed)
        client.on_subscribe(client, None, 7, [0], None)
        self.assertEqual(callbacks["suback"], [])
        adapter.close()

    def test_session_real_adapter_integration_uses_common_core(self):
        adapter, callbacks = self.make()
        session = Session(Config("cn", "test-printer"), adapter)
        session.start(uid=Secret("12345"), token=Secret(CANARY), monotonic=0)
        client = self.clients[-1]
        client.events.append(lambda: client.on_connect(client, None, None, 0, None))
        adapter.pump()
        session.drain()
        client.events.append(lambda: client.on_subscribe(client, None, 7, [1], None))
        adapter.pump()
        session.drain()
        self.assertEqual(len([call for call in client.calls if call[0] == "publish"]), 2)
        payload = b'{"print":{"command":"push_status","nozzle_temper":219.5}}'
        client.events.append(lambda: client.on_message(client, None, SimpleNamespace(
            topic="device/test-printer/report", payload=payload, retain=False)))
        adapter.pump()
        snapshot = session.snapshot(now=WALL, monotonic=12.5)
        self.assertEqual(snapshot["metrics"]["nozzle_celsius"]["value"], 219.5)
        self.assertEqual(snapshot["connection"]["state"], "online")
        self.assertTrue(session.stop())
        self.assertTrue(client.closed)


class PrivateHookTests(unittest.TestCase):
    def test_dependency_preflight_is_safe_and_does_not_construct_clients(self):
        with patch.object(PahoMqtt, "_production_module", return_value=object()) as checked:
            self.assertIsNone(PahoMqtt.check_available())
            checked.assert_called_once_with()
        with patch("importlib.metadata.version", return_value="2.2.0"):
            try:
                import paho.mqtt.client
            except ImportError:
                # Dependency-missing behavior is tested separately; no install is needed.
                pass
            else:
                with self.assertRaisesRegex(SafeMqttError, "version_unsupported"):
                    PahoMqtt.check_available()
        import builtins
        original = builtins.__import__
        def unavailable(name, *args, **kwargs):
            if name == "paho.mqtt.client":
                raise ImportError(CANARY)
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=unavailable):
            with self.assertRaisesRegex(SafeMqttError, "dependency_unavailable") as caught:
                PahoMqtt.check_available()
        self.assertNotIn(CANARY, "".join(traceback.format_exception(caught.exception)))

    def test_tls_handshake_timeout_is_not_keepalive_and_failure_closes_socket(self):
        class Base:
            pass
        wrapped = SimpleNamespace(settimeout=lambda value: setattr(wrapped, "timeout", value),
                                  do_handshake=lambda: None, close=lambda: setattr(wrapped, "closed", True))
        observed = {}
        def wrap(sock, **kwargs):
            observed.update(kwargs)
            return wrapped
        cls = _bounded_client_type(SimpleNamespace(Client=Base), 2)
        client = cls()
        client._ssl_context = SimpleNamespace(wrap_socket=wrap)
        client._host = "cn.mqtt.bambulab.com"
        self.assertIs(client._ssl_wrap_socket(object()), wrapped)
        self.assertEqual(wrapped.timeout, 2)
        self.assertEqual(observed, {"server_hostname": "cn.mqtt.bambulab.com", "do_handshake_on_connect": False})
        def fail():
            raise OSError(CANARY)
        wrapped.do_handshake = fail
        with self.assertRaises(SafeMqttError):
            client._ssl_wrap_socket(object())
        self.assertTrue(wrapped.closed)

    def test_packet_bound_precedes_reading_allocating_declared_body(self):
        class Base:
            def _sock_recv(self, count):
                raise AssertionError("must reject before body read")
        client = _bounded_client_type(SimpleNamespace(Client=Base), 2)()
        client._in_packet = {"have_remaining": 1, "remaining_length": 65536 + 513}
        with self.assertRaisesRegex(SafeMqttError, "packet_too_large"):
            client._sock_recv(65536 + 513)

    def test_pinned_real_paho_construct_and_socket_teardown_without_network(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.skipTest("optional paho-mqtt==2.1.0 not installed")
        adapter = PahoMqtt("test-printer", mqtt_module=mqtt)
        callbacks = []
        with patch.object(mqtt.Client, "connect", return_value=0):
            adapter.connect(host="cn.mqtt.bambulab.com", port=8883, tls=True,
                            client_id="bambu-monitor-" + "b" * 32, username=Secret("u_123"), token=Secret(CANARY),
                            on_connack=lambda *args, **kwargs: callbacks.append(args), on_suback=lambda *args: None,
                            on_message=lambda *args: None, on_disconnect=lambda **kwargs: None)
        client = adapter._client
        self.assertIsNone(client._thread)
        self.assertEqual(client.connect_timeout, 2)
        self.assertIsNotNone(client._password)
        adapter.close()
        self.assertIsNone(client._password)
        self.assertIsNone(client._username)
        self.assertIsNone(client.socket())
        self.assertIsNone(client._thread)
        # Exercise the actual Paho 2.1 packet parser with an in-memory fake socket.
        # The large declared Remaining Length must be rejected before body recv.
        remaining = 65536 + 513
        header = bytearray([0x30])
        while True:
            part, remaining = remaining % 128, remaining // 128
            header.append(part | (128 if remaining else 0))
            if not remaining:
                break
        class HeaderSocket:
            def __init__(self):
                self.data = bytes(header)
                self.counts = []
            def recv(self, count):
                self.counts.append(count)
                if count != 1:
                    raise AssertionError("large packet body must not be read")
                value, self.data = self.data[:count], self.data[count:]
                return value
            def close(self):
                pass
        sock = HeaderSocket()
        client._sock = sock
        with self.assertRaisesRegex(SafeMqttError, "mqtt_packet_too_large"):
            client._packet_read()
        self.assertTrue(sock.counts)
        self.assertTrue(all(count == 1 for count in sock.counts))
        client._reset_sockets()


if __name__ == "__main__":
    unittest.main()
