"""Bounded legacy report decoding; never retain raw account or message objects."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re

MAX_PAYLOAD_BYTES = 256 * 1024
PROFILE = "legacy-cloud-v1"
FIELDS = {
    "mc_remaining_time": ("remaining_minutes", "integer", 0, 2**31 - 1),
    "mc_percent": ("progress_percent", "integer", 0, 100),
    "layer_num": ("current_layer", "integer", 0, 2**31 - 1),
    "total_layer_num": ("total_layers", "integer", 0, 2**31 - 1),
    "nozzle_temper": ("nozzle_celsius", "number", -50, 500),
    "bed_temper": ("bed_celsius", "number", -50, 200),
    "chamber_temper": ("chamber_celsius", "number", -50, 200),
    "cooling_fan_speed": ("part_fan_percent", "fan", 0, 15),
    "big_fan1_speed": ("aux_fan_percent", "fan", 0, 15),
}
JOB_FIELDS = {"remaining_minutes", "progress_percent", "current_layer", "total_layers"}
JOB_STATES = {"IDLE", "PREPARE", "RUNNING", "PAUSE", "FINISH", "FAILED", "SLICING", "INIT"}
MODELS = {"A1", "A1 mini", "P1P", "P1S", "X1", "X1C", "X1E"}


def number(value, kind, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, str) and not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(result) or not minimum <= result <= maximum:
        return None
    if kind in {"integer", "fan"}:
        if not result.is_integer():
            return None
        result = int(result)
    if kind == "fan":
        return round(result * 100 / 15)
    return result


def identifier(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value)
    if not re.fullmatch(r"\d{1,3}", text):
        return None
    return str(int(text)) if int(text) <= maximum else None


def identity(value):
    """Private identity only: bounded tokens never appear in output or diagnostics."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", text) or not text.strip("0-"):
        return None
    return text


@dataclass
class TrayFact:
    unit: str
    tray: str
    identity: dict = field(default_factory=dict)
    remaining_present: bool = False
    remaining: int | None = None
    empty: bool = False
    removed: bool = False
    material_present: bool = False
    material: str | None = None
    color_present: bool = False
    color: str | None = None


@dataclass
class Report:
    metrics: dict = field(default_factory=dict)
    job_identity: dict = field(default_factory=dict)
    job_identity_invalid: bool = False
    job_state: str | None = None
    job_state_present: bool = False
    trays: list = field(default_factory=list)
    removed_units: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    invalid_ams: bool = False
    invalid_units: set = field(default_factory=set)
    invalid_trays: dict = field(default_factory=dict)
    offline: bool = False
    evidence: bool = False


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _bounded_json(payload):
    # Check nesting before json.loads (including unknown fields); work is bounded
    # by MAX_PAYLOAD_BYTES. Brackets inside strings do not count.
    depth = 0
    quoted = escaped = False
    for byte in payload:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 32:
                return False
        elif byte in (93, 125):
            depth -= 1
    return True


def _reject_constant(_):
    raise ValueError("non-JSON numeric constant")


def decode(payload: bytes, profile: str, offline_test_markers: bool = False) -> Report | None:
    if profile != PROFILE or not isinstance(payload, bytes) or len(payload) > MAX_PAYLOAD_BYTES or not _bounded_json(payload):
        return None
    try:
        root = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(root, dict):
        return None
    report = Report()
    # These event names come from community records; only explicit disconnect
    # invalidates. Connected events are not sufficient live telemetry evidence.
    event = root.get("event")
    if isinstance(event, dict) and event.get("event") == "client.disconnected":
        report.offline = True
    data = root.get("print")
    if not isinstance(data, dict) or data.get("command") != "push_status":
        return report if report.offline else None
    for raw, (name, kind, low, high) in FIELDS.items():
        if raw in data:
            value = number(data[raw], kind, low, high)
            report.metrics[name] = value
            report.evidence |= value is not None
            if value is None:
                report.errors.append("invalid_field:" + name)
    for key in ("task_id", "project_id", "subtask_id"):
        if key in data:
            token = identity(data[key])
            if token is not None:
                report.job_identity[key] = token
            else:
                report.job_identity_invalid = True
    if "gcode_state" in data:
        report.job_state_present = True
        value = data["gcode_state"]
        if isinstance(value, str) and value in JOB_STATES:
            report.job_state = value
            report.evidence = True
        else:
            report.errors.append("invalid_job_state")
    ams = data.get("ams")
    if "ams" in data and not isinstance(ams, dict):
        report.invalid_ams = True
        report.errors.append("invalid_ams_topology")
    if isinstance(ams, dict) and "ams" in ams:
        units = ams["ams"]
        if not isinstance(units, list) or len(units) > 16:
            report.invalid_ams = True
            report.errors.append("invalid_ams_topology")
        else:
            ids = [identifier(u.get("id"), 15) if isinstance(u, dict) else None for u in units]
            duplicates = {uid for uid in ids if uid is not None and ids.count(uid) > 1}
            for unit, uid in zip(units, ids):
                if uid is None or uid in duplicates:
                    if uid is None:
                        report.invalid_ams = True
                    else:
                        report.invalid_units.add(uid)
                    report.errors.append("invalid_ams_id")
                    continue
                if offline_test_markers and unit.get("removed") is True:
                    report.removed_units.append(uid)
                    report.evidence = True
                    continue
                trays = unit.get("tray", [])
                if not isinstance(trays, list) or len(trays) > 4:
                    report.invalid_units.add(uid)
                    report.errors.append("invalid_tray_topology")
                    continue
                tids = [identifier(t.get("id"), 3) if isinstance(t, dict) else None for t in trays]
                duplicates = {tid for tid in tids if tid is not None and tids.count(tid) > 1}
                for tray, tid in zip(trays, tids):
                    if tid is None or tid in duplicates:
                        if tid is None:
                            report.invalid_units.add(uid)
                        else:
                            report.invalid_trays.setdefault(uid, set()).add(tid)
                        report.errors.append("invalid_tray_id")
                        continue
                    fact = TrayFact(uid, tid)
                    for key in ("tray_uuid", "tray_info_idx"):
                        if key in tray:
                            token = identity(tray[key])
                            if token is not None:
                                fact.identity[key] = token
                            else:
                                report.errors.append("invalid_tray_identity")
                                # Explicitly ambiguous identity revokes prior identity.
                                fact.identity[key] = None
                    fact.empty = offline_test_markers and tray.get("empty") is True
                    fact.removed = offline_test_markers and tray.get("removed") is True
                    fact.remaining_present = "remain" in tray
                    if fact.remaining_present:
                        fact.remaining = number(tray["remain"], "integer", 0, 100)
                        if fact.remaining is None:
                            report.errors.append("invalid_tray_remaining")
                    fact.material_present = "tray_type" in tray
                    if fact.material_present:
                        value = tray["tray_type"]
                        if (isinstance(value, str) and 1 <= len(value) <= 64
                                and value == value.strip() and value.isprintable()):
                            fact.material = value
                        else:
                            report.errors.append("invalid_tray_material")
                    fact.color_present = "tray_color" in tray
                    if fact.color_present:
                        value = tray["tray_color"]
                        if isinstance(value, str) and re.fullmatch(r"[0-9A-Fa-f]{8}", value):
                            fact.color = value.upper()
                        else:
                            report.errors.append("invalid_tray_color")
                    report.trays.append(fact)
                    report.evidence |= (fact.empty or fact.removed or fact.remaining is not None
                                        or fact.material is not None or fact.color is not None)
    return report
