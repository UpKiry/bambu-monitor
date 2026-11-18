"""Paho 2.1 cloud MQTT adapter; one owner, manually pumped, no network threads.

No arbitrary broker, topic or publish API is exposed. Diagnostics/exceptions use
constant categories. Importing this module neither imports Paho nor opens sockets.
"""
from datetime import datetime, timezone
import json
import math
import re
import ssl
from threading import get_ident
import time

from .auth import Secret

BROKERS = frozenset(("cn.mqtt.bambulab.com", "us.mqtt.bambulab.com"))
MAX_PAYLOAD = 65536


class SafeMqttError(RuntimeError):
    """Constant diagnostics only; never interpolate a library exception."""


def _reason_code(reason):
    value = getattr(reason, "value", reason)
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate command key")
        result[key] = value
    return result


def _bounded_client_type(mqtt, connect_timeout):
    """Small version-pinned hooks; no upstream implementation copied.

    Paho 2.1 uses keepalive for TLS handshake and allocates incoming packet
    bodies before on_message. Bound both at the actual I/O boundary.
    """
    class BoundedClient(mqtt.Client):
        def _ssl_wrap_socket(self, tcp_sock):
            wrapped = None
            try:
                wrapped = self._ssl_context.wrap_socket(
                    tcp_sock, server_hostname=self._host, do_handshake_on_connect=False)
                wrapped.settimeout(connect_timeout)
                wrapped.do_handshake()
                return wrapped
            except Exception:
                (wrapped if wrapped is not None else tcp_sock).close()
                raise SafeMqttError("mqtt_tls_failed") from None

        def _sock_recv(self, count):
            packet = self._in_packet
            if packet.get("have_remaining") and packet.get("remaining_length", 0) > MAX_PAYLOAD + 512:
                raise SafeMqttError("mqtt_packet_too_large")
            return super()._sock_recv(count)

    return BoundedClient


class PahoMqtt:
    """Compatible with Session.MqttTransport, with explicit owner pump().

    connect() is synchronous until the TCP/TLS socket and CONNECT packet exist;
    CONNACK/SUBACK arrive only while the owner pumps. connect_timeout (<=2s) is
    Paho's socket-operation timeout, NOT a total deadline across DNS/addresses.
    The runner must enforce its own CONNACK/SUBACK deadlines and call Session
    drain between pumps. No loop_start/loop_forever/reconnect is ever invoked.
    """
    def __init__(self, printer_id, *, client_factory=None, mqtt_module=None,
                 monotonic=time.monotonic, wall_clock=None, connect_timeout=2.0):
        if not isinstance(printer_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", printer_id):
            raise SafeMqttError("mqtt_invalid_printer")
        if type(connect_timeout) not in (int, float) or not 0 < connect_timeout <= 2 or not math.isfinite(connect_timeout):
            raise SafeMqttError("mqtt_invalid_connect_timeout")
        self._printer_id = printer_id
        self._factory, self._module = client_factory, mqtt_module
        self._clock = monotonic
        self._wall = wall_clock or (lambda: datetime.now(timezone.utc))
        self._connect_timeout = connect_timeout
        self._owner = get_ident()
        self._client = None
        self._closing_client = None
        self._callbacks = {}
        self._epoch = 0
        self._connected = self._subscribed = False
        self._subscription_mid = None
        self._subscribing = False
        self._early_suback = None
        self._published = set()
        self._terminal = False

    def __repr__(self):
        return f"PahoMqtt(active={self._client is not None}, cleanup_pending={self._closing_client is not None})"

    def _assert_owner(self):
        if get_ident() != self._owner:
            raise SafeMqttError("mqtt_owner_thread_required")

    def _current(self, client, epoch):
        return (get_ident() == self._owner and client is self._client
                and epoch == self._epoch and not self._terminal)

    @staticmethod
    def _production_module():
        failed = False
        try:
            import paho.mqtt.client as mqtt
            from importlib.metadata import version
            supported = version("paho-mqtt") == "2.1.0"
        except Exception:
            failed = True
        if failed:
            raise SafeMqttError("mqtt_dependency_unavailable")
        if not supported:
            raise SafeMqttError("mqtt_dependency_version_unsupported")
        return mqtt

    @staticmethod
    def check_available():
        """Preflight import/version without constructing clients or opening sockets."""
        PahoMqtt._production_module()
        return None

    def _load(self):
        if self._module is None:
            self._module = self._production_module()
        return self._module

    def connect(self, *, host, port, tls, client_id, username, token,
                on_connack, on_suback, on_message, on_disconnect):
        self._assert_owner()
        if self._closing_client is not None:
            raise SafeMqttError("mqtt_cleanup_pending")
        if self._client is not None:
            raise SafeMqttError("mqtt_already_active")
        if (not isinstance(host, str) or host not in BROKERS or type(port) is not int or port != 8883 or tls is not True
                or not isinstance(client_id, str)
                or not re.fullmatch(r"bambu-monitor-[0-9a-f]{32}", client_id)
                or not isinstance(username, Secret) or not isinstance(token, Secret)
                or not isinstance(username.value, str) or not re.fullmatch(r"u_[A-Za-z0-9_-]{1,128}", username.value)
                or not isinstance(token.value, str) or not 0 < len(token.value) <= 16384
                or not all(callable(fn) for fn in (on_connack, on_suback, on_message, on_disconnect))):
            raise SafeMqttError("mqtt_invalid_connection")
        mqtt = self._load()
        self._epoch += 1
        epoch = self._epoch
        self._terminal = False
        self._published.clear()
        self._callbacks = {"connack": on_connack, "suback": on_suback,
                           "message": on_message, "disconnect": on_disconnect}
        failed = False
        try:
            factory = self._factory or _bounded_client_type(mqtt, self._connect_timeout)
            client = factory(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                             client_id=client_id, clean_session=True, protocol=mqtt.MQTTv311,
                             transport="tcp", reconnect_on_failure=False)
            self._client = client
            client.disable_logger()
            client.on_log = None
            client.connect_timeout = self._connect_timeout
            client.max_inflight_messages_set(1)
            client.max_queued_messages_set(2)
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            client.tls_set_context(context)
            client.tls_insecure_set(False)
            client.username_pw_set(username.value, password=token.value)
            client.on_connect = lambda c, u, f, rc, p: self._on_connect(c, epoch, rc)
            client.on_subscribe = lambda c, u, mid, codes, p: self._on_subscribe(c, epoch, mid, codes)
            client.on_message = lambda c, u, message: self._on_message(c, epoch, message)
            client.on_disconnect = lambda c, u, f, rc, p: self._on_disconnect(c, epoch, rc)
            if client.connect(host, port, keepalive=30) != mqtt.MQTT_ERR_SUCCESS:
                failed = True
        except Exception:
            failed = True
        if failed:
            try:
                self.close()
            except SafeMqttError:
                pass
            raise SafeMqttError("mqtt_connect_failed")

    def _on_connect(self, client, epoch, reason):
        if not self._current(client, epoch):
            return
        code = _reason_code(reason)
        self._connected = code == 0
        if not self._connected:
            self._terminal = True
        self._callbacks["connack"](self._connected, auth_rejected=code in (134, 135))

    def _complete_suback(self, mid, codes):
        if type(mid) is not int or self._subscription_mid is None or self._subscription_mid != mid:
            return
        self._subscription_mid = None
        accepted = isinstance(codes, (list, tuple)) and len(codes) == 1 and _reason_code(codes[0]) in (0, 1)
        self._subscribed = accepted
        self._callbacks["suback"](accepted)

    def _on_subscribe(self, client, epoch, mid, codes):
        if not self._current(client, epoch):
            return
        if self._subscribing:
            # A reentrant injectable client may deliver SUBACK before subscribe
            # returns its MID; retain one bounded result, verify MID afterwards.
            if self._early_suback is None:
                self._early_suback = (mid, codes)
            return
        self._complete_suback(mid, codes)

    def _on_message(self, client, epoch, message):
        if not self._current(client, epoch) or not self._subscribed:
            return
        try:
            topic, payload = message.topic, message.payload
            if (topic != f"device/{self._printer_id}/report" or not isinstance(payload, bytes)
                    or len(payload) > MAX_PAYLOAD or getattr(message, "retain", False)):
                return
            observed_at, mono = self._wall(), self._clock()
        except Exception:
            raise SafeMqttError("mqtt_invalid_message") from None
        self._callbacks["message"](topic, payload, observed_at, mono)

    def _on_disconnect(self, client, epoch, reason):
        if not self._current(client, epoch):
            return
        self._terminal = True
        self._connected = self._subscribed = False
        self._callbacks["disconnect"](auth_rejected=_reason_code(reason) in (134, 135))

    def subscribe(self, topic):
        self._assert_owner()
        if (topic != f"device/{self._printer_id}/report" or self._client is None
                or self._terminal or not self._connected or self._subscription_mid is not None
                or self._subscribed or self._subscribing):
            raise SafeMqttError("mqtt_subscription_rejected")
        failed = False
        self._subscribing = True
        self._early_suback = None
        try:
            result, mid = self._client.subscribe(topic, qos=1)
            if result != self._module.MQTT_ERR_SUCCESS or type(mid) is not int or not 1 <= mid <= 65535:
                failed = True
            else:
                self._subscription_mid = mid
        except Exception:
            failed = True
        finally:
            self._subscribing = False
        early, self._early_suback = self._early_suback, None
        if failed:
            raise SafeMqttError("mqtt_subscribe_failed")
        if early is not None:
            self._complete_suback(*early)

    def publish(self, topic, payload):
        self._assert_owner()
        if (topic != f"device/{self._printer_id}/request" or self._client is None
                or self._terminal or not self._subscribed or not isinstance(payload, bytes)
                or len(payload) > 256):
            raise SafeMqttError("mqtt_publish_rejected")
        try:
            value = json.loads(payload, object_pairs_hook=_json_pairs)
        except (ValueError, UnicodeError, RecursionError):
            raise SafeMqttError("mqtt_publish_rejected") from None
        command = None
        for section, name in (("info", "get_version"), ("pushing", "pushall")):
            if value == {section: {"sequence_id": "0", "command": name}}:
                command = name
        if command is None:
            raise SafeMqttError("mqtt_publish_rejected")
        if command in self._published:
            return
        # Attempt once: an unknown outcome must not be replayed automatically.
        self._published.add(command)
        failed = False
        try:
            info = self._client.publish(topic, payload, qos=0, retain=False)
            failed = info.rc != self._module.MQTT_ERR_SUCCESS
        except Exception:
            failed = True
        if failed:
            raise SafeMqttError("mqtt_publish_failed")

    def pump(self, timeout=0.1):
        self._assert_owner()
        if type(timeout) not in (int, float) or not 0 <= timeout <= 1 or not math.isfinite(timeout):
            raise SafeMqttError("mqtt_invalid_pump_timeout")
        if self._client is None or self._terminal:
            return None
        failed = False
        try:
            result = self._client.loop(timeout=timeout)
            if result != self._module.MQTT_ERR_SUCCESS and not self._terminal:
                self._on_disconnect(self._client, self._epoch, result)
        except Exception:
            failed = True
        if failed:
            self._terminal = True
            raise SafeMqttError("mqtt_pump_failed")
        return None

    def close(self):
        self._assert_owner()
        client = self._client if self._client is not None else self._closing_client
        self._client = None
        # Retain failed cleanup resources for the next close; never permit a new
        # connection merely because the first failed close dropped its reference.
        self._closing_client = client
        self._epoch += 1
        self._callbacks.clear()
        self._connected = self._subscribed = False
        self._subscription_mid = self._early_suback = None
        self._subscribing = False
        self._published.clear()
        self._terminal = True
        if client is None:
            return
        failed = False
        for name in ("on_connect", "on_subscribe", "on_message", "on_disconnect", "on_log"):
            try:
                setattr(client, name, None)
            except Exception:
                failed = True
        # No disconnect packet/loop_stop/join: immediate local socket teardown.
        # Paho 2.1 private reset also closes its socketpair. Clear queued CONNECT
        # bytes as well as username/password so stale client references lack tokens.
        for operation in (lambda: client._reset_sockets(),
                          lambda: client.username_pw_set(None, None),
                          lambda: client._out_packet.clear()):
            try:
                operation()
            except Exception:
                failed = True
        if failed:
            raise SafeMqttError("mqtt_close_failed")
        self._closing_client = None
