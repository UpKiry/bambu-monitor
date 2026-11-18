"""Generation and identity scoped telemetry cache, independent of transport/UI."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import math
import re

from .parser import FIELDS, JOB_FIELDS, MODELS, PROFILE, decode

CONNECTION_STATES = {"unconfigured", "auth_required", "connecting", "online", "offline", "error"}
REASONS = {"initial", "connecting", "subscribed", "target_telemetry", "telemetry_timeout", "target_disconnected", "network_error", "auth_required", "stopped", "unconfigured", "subscription_failed", "rate_limited"}


def _time(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value


def _mono(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 <= value <= 1e12 or not math.isfinite(value):
        raise ValueError("finite monotonic time required")
    return float(value)


@dataclass
class Metric:
    value: int | float | str | None = None
    observed: datetime | None = None
    generation: int | None = None
    serial: int = -1
    invalid: bool = False


@dataclass
class Tray:
    metric: Metric = field(default_factory=Metric)
    material: Metric = field(default_factory=Metric)
    color: Metric = field(default_factory=Metric)
    identity: dict = field(default_factory=dict)
    confirmed: bool = False
    epoch: int = 0
    retired: deque = field(default_factory=lambda: deque(maxlen=128))
    removed: bool = False
    removal_generation: int = -1


class TelemetryState:
    """Single-owner mutable state. Caller serializes all operations.

    baseline=True is an OFFLINE manual evidence assertion, not wire detection.
    Unknown profiles deliberately decode no fields. No raw payload is retained.
    """

    def __init__(self, printer_id, model=None, profile=PROFILE, liveness_seconds=60,
                 offline_test_markers=False):
        if not isinstance(offline_test_markers, bool):
            raise ValueError("offline test markers must be bool")
        self._offline_test_markers = offline_test_markers
        if not isinstance(printer_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", printer_id):
            raise ValueError("invalid selected printer identifier")
        if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile):
            raise ValueError("invalid profile identifier")
        self.printer_id = printer_id
        self.model = model if isinstance(model, str) and model in MODELS else None
        self.profile = profile
        self.liveness_seconds = _mono(liveness_seconds)
        if self.liveness_seconds <= 0:
            raise ValueError("positive liveness threshold required")
        self.generation = 0
        self.baseline_generation = None
        self._baseline_fields = set()
        self._metrics = {spec[0]: Metric() for spec in FIELDS.values()}
        self._trays = {}
        self._job_identity = {}
        self._job_confirmed = False
        self._job_current_fields = set()
        self._job_retired = deque(maxlen=128)
        self._job_epoch = 0
        self._job_state = None
        self._serial = 0
        self._last_mono = None
        self._last_frame_mono = None
        self._last_wall = None
        self._latest_wall = None
        self._connection = "connecting"
        self._reason = "initial"
        self._errors = deque(maxlen=32)
        self._clock_adjusted = False
        self._view_mono = None

    def _error(self, reason):
        if reason not in self._errors:
            self._errors.append(reason)

    def begin_generation(self, generation: int):
        if isinstance(generation, bool) or not isinstance(generation, int) or generation <= self.generation:
            raise ValueError("generation must increase")
        self.generation = generation
        self.baseline_generation = None
        self._baseline_fields.clear()
        self._job_confirmed = False
        self._job_current_fields.clear()
        self._job_state = None
        for slots in self._trays.values():
            for tray in slots.values():
                tray.confirmed = False
                tray.retired.clear()
        self._job_retired.clear()
        self._last_mono = None
        self._last_frame_mono = None
        self._connection, self._reason = "connecting", "connecting"

    def set_connection(self, state: str, reason: str):
        if state not in CONNECTION_STATES:
            raise ValueError("invalid connection state")
        self._connection = state
        self._reason = reason if reason in REASONS else "transport_state"
        if state != "online":
            # A later broker/online notification cannot revive old evidence.
            self._last_mono = None
            self.baseline_generation = None
            self._invalidate_materials()

    def _invalidate_materials(self):
        for slots in self._trays.values():
            for tray in slots.values():
                tray.material.invalid = tray.color.invalid = True

    @staticmethod
    def _changed(old, incoming):
        return any(key in old and old[key] != value for key, value in incoming.items())

    @staticmethod
    def _retired(retired, incoming):
        for key in ("task_id", "subtask_id", "project_id", "tray_uuid", "tray_info_idx"):
            if key in incoming and incoming[key] is not None:
                return any(past.get(key) == incoming[key] for past in retired)
        return False

    def _observe(self, metric, value, wall):
        if value is None:
            metric.invalid = True
            return
        metric.value, metric.observed = value, wall
        metric.generation, metric.serial = self.generation, self._serial
        metric.invalid = False

    def _reset_job(self):
        self._job_epoch += 1
        for name in JOB_FIELDS:
            self._metrics[name] = Metric()
        self._job_identity = {}
        self._job_confirmed = False
        self._job_current_fields.clear()
        self._job_state = None

    def apply(self, payload: bytes, *, topic: str, generation: int, observed_at: datetime,
              monotonic: float, baseline: bool = False) -> bool:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation != self.generation or topic != f"device/{self.printer_id}/report":
            return False
        mono, wall = _mono(monotonic), _time(observed_at)
        if self._last_frame_mono is not None and mono < self._last_frame_mono:
            self._error("out_of_order_frame")
            return False
        if not isinstance(baseline, bool):
            raise ValueError("baseline must be a bool evidence assertion")
        report = decode(payload, self.profile, offline_test_markers=self._offline_test_markers)
        if report is None:
            self._error("unsupported_protocol_profile" if self.profile != PROFILE else "invalid_or_unrecognized_report")
            return False
        if report.offline:
            self._last_frame_mono = mono
            self.set_connection("offline", "target_disconnected")
            return True
        if self._retired(self._job_retired, report.job_identity):
            self._error("retired_job_epoch")
            return False
        if self._last_mono is not None and mono - self._last_mono > self.liveness_seconds:
            self.baseline_generation = None
            self._invalidate_materials()
        self._serial += 1
        self._view_mono = mono
        self._last_frame_mono = mono
        for error in report.errors:
            self._error(error)
        if report.job_identity_invalid:
            if self._job_identity:
                self._job_retired.append(self._job_identity.copy())
            self._reset_job()
            self._error("ambiguous_job_identity")
        elif report.job_identity:
            disjoint = bool(self._job_identity) and not (self._job_identity.keys() & report.job_identity.keys())
            if disjoint or self._changed(self._job_identity, report.job_identity):
                self._job_retired.append(self._job_identity.copy())
                self._reset_job()
            self._job_identity.update(report.job_identity)
            self._job_current_fields.update(report.job_identity)
            self._job_confirmed = bool(self._job_current_fields & {"task_id", "subtask_id"})
        if report.job_state_present:
            if report.job_state in {"PREPARE", "INIT"} and self._job_state not in {None, "PREPARE", "INIT"}:
                if self._job_identity and not report.job_identity:
                    self._job_retired.append(self._job_identity.copy())
                self._reset_job()
                if report.job_identity and not report.job_identity_invalid:
                    self._job_identity.update(report.job_identity)
                    self._job_current_fields.update(report.job_identity)
                    self._job_confirmed = bool(self._job_current_fields & {"task_id", "subtask_id"})
            self._job_state = report.job_state
        accepted = report.job_state is not None
        for name, value in report.metrics.items():
            self._observe(self._metrics[name], value, wall)
            accepted |= value is not None
        for uid, slots in self._trays.items():
            for tid, tray in slots.items():
                if report.invalid_ams or uid in report.invalid_units or tid in report.invalid_trays.get(uid, set()):
                    tray.confirmed = False
                    tray.metric.invalid = True
                    tray.material.invalid = tray.color.invalid = True
        for uid in report.removed_units:
            for tray in self._trays.get(uid, {}).values():
                tray.removed = True
                tray.removal_generation = self.generation
                tray.confirmed = False
                tray.metric = Metric()
                tray.material, tray.color = Metric(), Metric()
                tray.epoch += 1
            accepted = True
        for fact in report.trays:
            slots = self._trays.setdefault(fact.unit, {})
            tray = slots.setdefault(fact.tray, Tray())
            if fact.removed:
                tray.removed = True
                tray.removal_generation = self.generation
                tray.confirmed = False
                tray.metric = Metric()
                tray.material, tray.color = Metric(), Metric()
                tray.epoch += 1
                accepted = True
                continue
            if self._retired(tray.retired, fact.identity):
                self._error("retired_tray_epoch")
                continue
            if tray.removed:
                if tray.removal_generation == self.generation and (not fact.identity or not self._changed(tray.identity, fact.identity)):
                    self._error("removed_tray_epoch")
                    continue
                tray.removed = False
            if fact.empty or self._changed(tray.identity, fact.identity):
                if tray.identity:
                    tray.retired.append(tray.identity.copy())
                tray.metric = Metric()
                tray.material, tray.color = Metric(), Metric()
                tray.identity = {}
                tray.confirmed = False
                tray.epoch += 1
            if fact.empty:
                accepted = True
                continue
            if any(value is None for value in fact.identity.values()):
                tray.metric = Metric()
                tray.material, tray.color = Metric(), Metric()
                tray.identity = {}
                tray.confirmed = False
                tray.epoch += 1
            elif fact.identity:
                tray.identity.update(fact.identity)
                tray.confirmed = tray.confirmed or "tray_uuid" in fact.identity
            # Explicit type/color observations need no RFID UUID (AMS lite).
            # A changed descriptor starts a new pair: do not present the other
            # cached descriptor as belonging to the newly observed material.
            topology_valid = not (report.invalid_ams or fact.unit in report.invalid_units
                                  or fact.tray in report.invalid_trays.get(fact.unit, set()))
            if topology_valid:
                changed = any(present and value is not None and metric.value is not None
                              and metric.value != value
                              for present, value, metric in (
                                  (fact.material_present, fact.material, tray.material),
                                  (fact.color_present, fact.color, tray.color)))
                if changed:
                    tray.material, tray.color = Metric(), Metric()
                for present, value, metric in (
                        (fact.material_present, fact.material, tray.material),
                        (fact.color_present, fact.color, tray.color)):
                    if present:
                        self._observe(metric, value, wall)
                        accepted |= value is not None
            if fact.remaining_present:
                self._observe(tray.metric, fact.remaining, wall)
                accepted |= fact.remaining is not None
        if accepted:
            self._last_mono, self._last_wall = mono, wall
            self._latest_wall = max(self._latest_wall, wall) if self._latest_wall else wall
            self._connection, self._reason = "online", "target_telemetry"
            if baseline:
                self.baseline_generation = self.generation
                self._baseline_fields = {name for name, metric in self._metrics.items() if metric.generation == self.generation and not metric.invalid}
        return accepted

    def _live(self, mono):
        return (self.profile == PROFILE and self._connection == "online" and self._last_mono is not None
                and 0 <= mono - self._last_mono <= self.liveness_seconds)

    def _quality(self, metric, live, scope=True, unsupported=False):
        if unsupported:
            return "unsupported"
        if metric.value is None:
            return "unknown"
        retained = self.baseline_generation == self.generation
        if (live and scope and not metric.invalid and metric.generation == self.generation
                and (metric.serial == self._serial or retained)):
            return "valid"
        return "stale"

    def _unsupported(self, name):
        return self.model in {"A1", "A1 mini"} and name in {"chamber_celsius", "aux_fan_percent"}

    def _metric_snapshot(self, metric, live, scope=True, unsupported=False):
        quality = self._quality(metric, live, scope, unsupported)
        if quality in {"unknown", "unsupported"}:
            return {"value": None, "quality": quality, "updated_at": None}
        return {"value": metric.value, "quality": quality, "updated_at": metric.observed.isoformat()}

    def snapshot(self, *, now: datetime, monotonic: float) -> dict:
        wall, mono = _time(now), _mono(monotonic)
        self._view_mono = mono
        live = self._live(mono)
        state = self._connection
        if state == "online" and not live:
            state = "offline"
            self.baseline_generation = None
            self._reason = "telemetry_timeout" if self.profile == PROFILE else "unsupported_protocol_profile"
        generated = max(wall, self._latest_wall) if self._latest_wall else wall
        if generated != wall:
            self._clock_adjusted = True
            self._error("wall_clock_rollback")
        return {
            "schema_version": 1, "source": "bambu-cloud",
            "printer": {"id": self.printer_id, "model": self.model},
            "connection": {"state": state, "last_message_at": self._last_wall.isoformat() if self._last_wall else None},
            "generated_at": generated.isoformat(),
            "job": {"state": self._job_state if live and self._job_confirmed else None, "name": None},
            "metrics": {name: self._metric_snapshot(metric, live, name not in JOB_FIELDS or self._job_confirmed, self._unsupported(name)) for name, metric in self._metrics.items()},
            "ams": [{"id": uid, "trays": [{"id": tid, "remaining_percent": self._metric_snapshot(tray.metric, live, tray.confirmed)} for tid, tray in sorted(slots.items(), key=lambda item: int(item[0])) if not tray.removed]} for uid, slots in sorted(self._trays.items(), key=lambda item: int(item[0])) if any(not tray.removed for tray in slots.values())],
        }

    def materials_snapshot(self) -> list:
        """AMS descriptors at the last snapshot/apply clock; no numeric contract changes.

        Explicit observations retain their own timestamps while the target is live.
        Reconnect, topology ambiguity and changed descriptors revoke that basis.
        Values are reported observations, not RFID identity or inventory evidence.
        """
        live = self._view_mono is not None and self._live(self._view_mono)

        def descriptor(metric):
            quality = ("unknown" if metric.value is None else
                       "valid" if live and not metric.invalid and metric.generation == self.generation
                       else "stale")
            return {"value": metric.value, "quality": quality,
                    "updated_at": metric.observed.isoformat() if metric.observed else None}

        return [{"id": uid, "trays": [
            {"id": tid, "material": descriptor(tray.material), "color": descriptor(tray.color)}
            for tid, tray in sorted(slots.items(), key=lambda item: int(item[0])) if not tray.removed]}
            for uid, slots in sorted(self._trays.items(), key=lambda item: int(item[0]))
            if any(not tray.removed for tray in slots.values())]

    def metadata(self) -> dict:
        mono = self._view_mono if self._view_mono is not None else self._last_mono
        live = mono is not None and self._live(mono)
        fields = {}
        for name, metric in self._metrics.items():
            quality = self._quality(metric, live, name not in JOB_FIELDS or self._job_confirmed, self._unsupported(name))
            fields[name] = {
                "last_observed": metric.observed.isoformat() if metric.observed else None,
                "basis": ("explicit" if metric.serial == self._serial else "task-scoped" if name in JOB_FIELDS else "delta-retained") if quality == "valid" else quality,
            }
        for uid, slots in self._trays.items():
            for tid, tray in slots.items():
                metric = tray.metric
                quality = self._quality(metric, live, tray.confirmed)
                fields[f"ams.{uid}.trays.{tid}.remaining_percent"] = {
                    "last_observed": metric.observed.isoformat() if metric.observed else None,
                    "basis": ("explicit" if metric.serial == self._serial else "tray-scoped") if quality == "valid" else quality,
                }
        return {
            "identity_scope": {"printer_id": self.printer_id},
            "session_generation": self.generation,
            "baseline_generation": self.baseline_generation,
            "baseline_complete": self.baseline_generation == self.generation and self.profile == PROFILE,
            "baseline_fields": sorted(self._baseline_fields),
            "baseline_evidence": "offline-manual-assertion" if self.baseline_generation == self.generation else None,
            "last_target_evidence_monotonic": self._last_mono,
            "job_epoch": self._job_epoch,
            "tray_epoch": {f"{uid}.{tid}": tray.epoch for uid, slots in self._trays.items() for tid, tray in slots.items()},
            "protocol_profile": PROFILE if self.profile == PROFILE else "unknown",
            "profile_verified_on_real_cloud": False,
            "offline_test_markers_enabled": self._offline_test_markers,
            "timestamp_source": "receive-observation",
            "generated_at_clock": "logical-wall-max-observed" if self._clock_adjusted else "wall",
            "field_basis": {name: value["basis"] for name, value in fields.items()},
            "field_last_observed": {name: value["last_observed"] for name, value in fields.items()},
            "connection_reason": self._reason,
            "diagnostics": list(self._errors) + (["unsupported_protocol_profile"] if self.profile != PROFILE else []),
        }
