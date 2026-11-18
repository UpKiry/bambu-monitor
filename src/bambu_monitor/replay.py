"""Bounded offline replay through the production session/parser/state path."""
from datetime import datetime
import json
import math

from .auth import Secret
from .config import Config, identifier, ConfigError
from .session import Session, FakeMqtt, Envelope


class ReplayError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate replay key")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("invalid replay number")


def _object(line):
    try:
        value = json.loads(line, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ReplayError("invalid replay JSON") from None
    if not isinstance(value, dict):
        raise ReplayError("replay object required")
    return value


def replay(stream):
    """Yield tagged export envelopes, never raw input; caller owns output stream.

    NDJSON header plus <=10000 events, <=64KiB per line. The baseline flag is
    trusted *only* as an explicit offline assertion, never a cloud wire flag.
    """
    line = stream.readline(65538)
    if len(line) > 65536:
        raise ReplayError("replay header too large")
    header = _object(line)
    if (set(header) != {"kind", "input_class", "profile", "region", "printer_id"}
            or header.get("kind") != "fixture"
            or header.get("input_class") not in ("synthetic", "redacted")):
        raise ReplayError("explicit fixture header required")
    try:
        profile = identifier(header["profile"])
        config = Config(region=header["region"], printer_id=identifier(header["printer_id"]))
        config.identity  # Validate region before starting the fake transport.
    except (ConfigError, TypeError):
        raise ReplayError("invalid replay identity") from None
    mqtt = FakeMqtt()
    session = Session(config, mqtt, profile=profile, offline_test_markers=True)
    previous = -1.0
    started = False
    try:
        for index in range(10001):
            line = stream.readline(65538)
            if not line:
                return
            if len(line) > 65536 or index == 10000:
                raise ReplayError("replay input budget exceeded")
            event = _object(line)
            if set(event) - {"kind", "at", "monotonic", "payload", "baseline", "topic", "generation"}:
                raise ReplayError("unknown replay field")
            mono = event.get("monotonic")
            if type(mono) not in (int, float) or not 0 <= mono <= 1e12 or not math.isfinite(mono) or mono < previous:
                raise ReplayError("replay monotonic time must not decrease")
            previous = mono
            try:
                wall = datetime.fromisoformat(event["at"])
                if wall.tzinfo is None or wall.utcoffset() is None:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise ReplayError("replay wall time requires offset") from None
            kind = event.get("kind")
            if kind == "start":
                if started:
                    raise ReplayError("use reconnect after first start")
                started = True
                session.start(uid=Secret("synthetic-uid"), token=Secret("synthetic-token"), monotonic=mono)
            elif kind == "connack":
                mqtt.callbacks.get("on_connack", lambda *a: None)(True)
            elif kind == "suback":
                mqtt.callbacks.get("on_suback", lambda *a: None)(True)
            elif kind == "message":
                baseline = event.get("baseline", False)
                if type(baseline) is not bool or not isinstance(event.get("payload"), dict):
                    raise ReplayError("invalid replay telemetry")
                generation = event.get("generation", session.generation)
                if type(generation) is not int:
                    raise ReplayError("invalid replay generation")
                try:
                    payload = json.dumps(event["payload"], allow_nan=False).encode()
                except (ValueError, RecursionError):
                    raise ReplayError("invalid replay telemetry") from None
                session.enqueue(Envelope(generation, event.get("topic", f"device/{config.printer_id}/report"),
                                         payload, wall, mono, baseline))
            elif kind in ("disconnect", "auth_rejected"):
                session.snapshot(now=wall, monotonic=mono)
                session.disconnect(session.generation, auth_rejected=kind == "auth_rejected")
            elif kind == "reconnect":
                if session.phase != "offline" or session.retry_at is None or mono < session.retry_at:
                    raise ReplayError("reconnect requires elapsed recoverable backoff")
                session.start(uid=Secret("synthetic-uid"), token=Secret("synthetic-token"), monotonic=mono)
            elif kind == "stop":
                if not session.stop():
                    raise ReplayError("transport cleanup failed")
            elif kind != "tick":
                raise ReplayError("unknown replay event")
            snapshot = session.snapshot(now=wall, monotonic=mono)
            if snapshot is not None:
                yield {"input_class": header["input_class"], "snapshot": snapshot,
                       "diagnostics": session.diagnostics()}
    finally:
        session.stop()
