"""Single-owner bounded MQTT session; callbacks only enqueue facts/control work."""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import random
from threading import RLock, get_ident
from typing import Protocol
import uuid

from .auth import Secret
from .config import Config, ENDPOINTS
from .telemetry import TelemetryState

MAX_MESSAGE_BYTES = 65536


class MqttTransport(Protocol):
    """Future adapters must bound connect/close and return without joining callbacks."""
    def connect(self, *, host, port, tls, client_id, username, token,
                on_connack, on_suback, on_message, on_disconnect): ...
    def subscribe(self, topic): ...
    def publish(self, topic, payload): ...
    def close(self): ...


@dataclass(frozen=True)
class Envelope:
    generation: int
    topic: str = field(repr=False)
    payload: bytes = field(repr=False)
    observed_at: datetime
    monotonic: float
    baseline: bool = False


class Session:
    """Owner calls start/drain/snapshot/stop; network callbacks never do I/O.

    Callback/control work is bounded independently of the raw delta queue.
    Missing a delta may hide an identity change, so overflow revokes the entire
    generation. Close is drained on the owner before any reconnect. There is no
    scheduler or worker thread here: a future runner must pump this owner.
    """
    def __init__(self, config: Config, transport: MqttTransport, *, profile="legacy-cloud-v1",
                 queue_size=64, random_value=random.random, offline_test_markers=False,
                 inspect_telemetry=False):
        if not config.identity:
            raise ValueError("explicit printer selection required")
        if type(queue_size) is not int or not 1 <= queue_size <= 4096:
            raise ValueError("invalid queue budget")
        self.config, self.identity, self.transport = config, config.identity, transport
        from .inspection import TelemetryInspection
        self.inspection = TelemetryInspection() if inspect_telemetry else None
        self.core = TelemetryState(config.printer_id, profile=profile, liveness_seconds=config.liveness_seconds,
                                   offline_test_markers=offline_test_markers)
        self._queue, self._actions = deque(), deque()
        self._queue_size = queue_size
        self._lock, self._owner = RLock(), get_ident()
        self._random = random_value
        self.generation, self.active = 0, False
        self.phase, self.reason = "unconfigured", "not_started"
        self._attempt, self.retry_at = 0, None
        self._last_initialization = self._initialization_generation = None
        self._last_monotonic = 0.0
        self._needs_close = self._core_dirty = False
        self._cleanup_failed = False
        self._stop_requested = lambda: False
        self.counters = {"dropped": 0, "late": 0, "oversized": 0, "accepted": 0}

    def _assert_owner(self):
        if get_ident() != self._owner:
            raise RuntimeError("session owner thread required")

    def set_stop_check(self, callback):
        self._assert_owner()
        if not callable(callback):
            raise ValueError("stop callback required")
        self._stop_requested = callback

    def _current(self, generation):
        return self.active and generation == self.generation

    def _maintenance(self):
        with self._lock:
            close, dirty = self._needs_close, self._core_dirty
            self._needs_close = self._core_dirty = False
            generation, phase, reason = self.generation, self.phase, self.reason
        if dirty:
            if self.inspection is not None:
                self.inspection.clear()
            self.core.begin_generation(generation)
            self.core.set_connection("offline" if phase == "stopped" else phase, reason)
        if close:
            try:
                self.transport.close()
                self._cleanup_failed = False
            except Exception:
                self.reason = "close_failed"
                self._cleanup_failed = True
        return not self._cleanup_failed

    def start(self, *, uid: Secret, token: Secret, monotonic: float):
        self._assert_owner()
        if self.active:
            raise RuntimeError("session already started")
        if self.inspection is not None:
            self.inspection.protect(uid.value, token.value, self.config.printer_id)
        if type(monotonic) not in (int, float) or not 0 <= monotonic <= 1e12 or not math.isfinite(monotonic) or monotonic < self._last_monotonic:
            raise ValueError("invalid monotonic time")
        if not self._maintenance():
            raise RuntimeError("previous transport cleanup failed")
        with self._lock:
            self.generation += 1
            generation = self.generation
            self.active, self.phase, self.reason = True, "connecting", "awaiting_connack"
            self._last_monotonic, self.retry_at = monotonic, None
        self.core.begin_generation(generation)
        self.core.set_connection("connecting", "awaiting_subscription")
        try:
            self.transport.connect(
                host=ENDPOINTS[self.config.region][1], port=8883, tls=True,
                client_id="bambu-monitor-" + uuid.uuid4().hex,
                username=Secret("u_" + uid.value), token=token,
                on_connack=lambda accepted, auth_rejected=False: self.connack(generation, accepted, auth_rejected),
                on_suback=lambda accepted: self.suback(generation, accepted),
                on_message=lambda topic, payload, observed_at, monotonic: self.enqueue(
                    Envelope(generation, topic, payload, observed_at, monotonic)),
                on_disconnect=lambda auth_rejected=False: self.disconnect(generation, auth_rejected=auth_rejected))
        except Exception:
            self.disconnect(generation)

    def connack(self, generation, accepted, auth_rejected=False):
        with self._lock:
            if not self._current(generation):
                return
            if not accepted:
                self.disconnect(generation, auth_rejected=auth_rejected)
            elif self.phase == "connecting":
                self.phase, self.reason = "subscribing", "awaiting_subscribe"
                self._actions.append((generation, "subscribe"))

    def suback(self, generation, accepted):
        with self._lock:
            if not self._current(generation) or self.phase != "awaiting_suback":
                return
            if not accepted:
                self.disconnect(generation, reason="subscription_rejected")
            else:
                self.phase, self.reason = "subscribed", "awaiting_target_telemetry"
                self._actions.append((generation, "initialize"))

    def _initialize(self, generation):
        if self._initialization_generation == generation:
            return
        self._initialization_generation = generation
        now = self._last_monotonic
        if self._last_initialization is not None and now - self._last_initialization < 300:
            return
        self._last_initialization = now
        for section, command in (("info", "get_version"), ("pushing", "pushall")):
            with self._lock:
                if not self._current(generation) or self._stop_requested():
                    return
            payload = json.dumps({section: {"sequence_id": "0", "command": command}}, separators=(",", ":")).encode()
            self.transport.publish(f"device/{self.config.printer_id}/request", payload)

    def enqueue(self, envelope: Envelope):
        with self._lock:
            if not self._current(envelope.generation) or self.phase != "subscribed":
                self.counters["late"] += 1
                return False
            if (not isinstance(envelope.payload, bytes) or len(envelope.payload) > MAX_MESSAGE_BYTES
                    or not isinstance(envelope.topic, str) or len(envelope.topic) > 256):
                self.counters["oversized"] += 1
                return False
            if (type(envelope.monotonic) not in (int, float) or not 0 <= envelope.monotonic <= 1e12
                    or not math.isfinite(envelope.monotonic)
                    or envelope.monotonic < self._last_monotonic
                    or not isinstance(envelope.observed_at, datetime) or envelope.observed_at.utcoffset() is None):
                self.counters["late"] += 1
                return False
            if len(self._queue) >= self._queue_size:
                self.counters["dropped"] += 1
                self.disconnect(envelope.generation, reason="queue_overflow")
                return False
            self._queue.append(envelope)
            return True

    def drain(self):
        self._assert_owner()
        self._maintenance()
        while True:
            with self._lock:
                if not self._actions or self._stop_requested():
                    break
                generation, action = self._actions.popleft()
                if not self._current(generation):
                    continue
                if action == "subscribe":
                    self.phase, self.reason = "awaiting_suback", "awaiting_suback"
            try:
                if action == "subscribe":
                    self.transport.subscribe(f"device/{self.config.printer_id}/report")
                else:
                    self._initialize(generation)
            except Exception:
                self.disconnect(generation, reason="subscription_or_initialization_failed")
            self._maintenance()
        count = 0
        # One owner pump has a fixed work budget, even under continuous input.
        for _ in range(self._queue_size):
            with self._lock:
                if not self.active or not self._queue or self._stop_requested():
                    break
                message = self._queue.popleft()
                if not self._current(message.generation):
                    continue
                # Inspect only the current, exact target after queue/time checks.
                # Unknown telemetry remains observable without establishing liveness.
                if (self.inspection is not None
                        and message.topic == f"device/{self.config.printer_id}/report"
                        and message.monotonic >= self._last_monotonic):
                    self.inspection.capture(message.payload, generation=message.generation,
                                            received_at=message.observed_at)
                # Serialize a core update against callback revocation; no transport I/O here.
                if self.core.apply(message.payload, topic=message.topic, generation=message.generation,
                                   observed_at=message.observed_at, monotonic=message.monotonic,
                                   baseline=message.baseline):
                    self.counters["accepted"] += 1
                    self._attempt = 0
                    count += 1
                self._last_monotonic = max(self._last_monotonic, message.monotonic)
        self._maintenance()
        return count

    def disconnect(self, generation, *, auth_rejected=False, reason="network_disconnected"):
        with self._lock:
            if not self._current(generation):
                return
            self.active = False
            self._queue.clear()
            self._actions.clear()
            self.generation += 1
            self.phase = "auth_required" if auth_rejected else "offline"
            self.reason = "credential_rejected" if auth_rejected else reason
            self._needs_close = self._core_dirty = True
            if auth_rejected:
                self.retry_at = None
            else:
                delay = min(self.config.max_delay_seconds,
                            self.config.initial_delay_seconds * 2 ** min(self._attempt, 16))
                self._attempt += 1
                factor = 0.5 + 0.5 * self._random() if self.config.jitter else 1
                self.retry_at = self._last_monotonic + delay * factor

    def advance_time(self, monotonic):
        """Anchor retries to the owner clock even during a silent network period."""
        self._assert_owner()
        if (type(monotonic) not in (int, float) or not 0 <= monotonic <= 1e12
                or not math.isfinite(monotonic) or monotonic < self._last_monotonic):
            raise ValueError("invalid monotonic time")
        with self._lock:
            self._last_monotonic = monotonic

    def snapshot(self, *, now, monotonic):
        self._assert_owner()
        self.drain()
        self._last_monotonic = max(self._last_monotonic, monotonic)
        with self._lock:
            if self.phase == "stopped":
                return None
            return self.core.snapshot(now=now, monotonic=monotonic)

    def stop(self):
        self._assert_owner()
        with self._lock:
            self.active = False
            self.generation += 1
            self._queue.clear()
            self._actions.clear()
            self.phase, self.reason, self.retry_at = "stopped", "stopped", None
            self._needs_close = self._core_dirty = True
        if self.inspection is not None:
            self.inspection.clear()
            self.inspection.protect()
        return self._maintenance()

    def inspection_view(self):
        self._assert_owner()
        if self.inspection is None:
            return {}
        materials, _ = self.inspection.sanitize_value(self.core.materials_snapshot())
        return {"telemetry": self.inspection.snapshot(self.generation),
                "ams_materials": materials}

    def diagnostics(self):
        self._assert_owner()
        with self._lock:
            return {"generation": self.generation, "phase": self.phase, "reason": self.reason,
                    "retry_at": self.retry_at, "cleanup_failed": self._cleanup_failed,
                    "queued": len(self._queue), "counters": dict(self.counters),
                    "telemetry": self.core.metadata()}


class FakeMqtt:
    """No sockets/threads; only bounded read requests and safe connection metadata."""
    def __init__(self):
        self.callbacks, self.connection = {}, {}
        self.subscriptions, self.requests = [], []
        self.closed = True

    def connect(self, **kwargs):
        self.closed = False
        self.callbacks = {key: value for key, value in kwargs.items() if key.startswith("on_")}
        self.connection = {key: kwargs[key] for key in ("host", "port", "tls", "client_id")}

    def subscribe(self, topic):
        if self.closed:
            raise RuntimeError("transport closed")
        if len(self.subscriptions) >= 1024:
            raise RuntimeError("fake subscription budget exceeded")
        self.subscriptions.append(topic)

    def publish(self, topic, payload):
        if self.closed:
            raise RuntimeError("transport closed")
        if len(self.requests) >= 1024:
            raise RuntimeError("fake request budget exceeded")
        self.requests.append((topic, payload))

    def close(self):
        self.closed = True
