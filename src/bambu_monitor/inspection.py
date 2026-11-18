"""Bounded, memory-only inspection of target printer reports.

This is observational data, not the normalized snapshot contract. Unknown fields
are visible after redaction; they never establish liveness or field validity.
"""
from copy import deepcopy
import json
import math
import re

from .telemetry.parser import _bounded_json, _pairs, _reject_constant

MAX_BYTES = 65536
MAX_FIELDS = 512
MAX_CACHE_BYTES = 256 * 1024
MAX_NODES = 2048
MAX_TEXT = 1024
REDACTED = "[已隐藏]"
TRUNCATED = "[已截断]"


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _private_key(key):
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return (any(part in compact for part in (
        "token", "password", "passwd", "secret", "credential", "authorization",
        "accesscode", "verifycode", "verificationcode", "cookie", "email", "phone",
        "username", "account", "privatekey", "apikey", "signature", "tfa", "uuid",
    )) or compact in {
        "uid", "userid", "devuserid", "sn", "serial", "serialnumber", "devicesn",
        "pwd", "pass", "auth", "authentication", "code", "pin", "key", "ssid",
        "devicename", "devname", "devaccesscode", "ip", "ipaddress", "mac", "macaddress",
        "url", "uri", "mqtt", "taskid", "projectid", "subtaskid", "trayinfoidx",
        "gcodefile", "gcodefilename", "subtaskname", "filename", "filepath", "path",
    } or compact.endswith(("url", "uri")))


class TelemetryInspection:
    def __init__(self):
        self.clear()
        self._secrets = ()

    def protect(self, *values):
        # Private values never enter snapshots, repr, files or log messages.
        self._secrets = tuple(str(value) for value in values if value is not None and str(value))

    def clear(self):
        self.generation = None
        self.latest = None
        self.fields = {}
        self._sizes = {}
        self._cache_bytes = 0
        self.truncated = False

    def _redact_text(self, value):
        for secret in self._secrets:
            value = value.replace(secret, REDACTED)
        value = re.sub(r"(?i)(?:https?|rtsps?|mqtts?)://[^\s]+", REDACTED, value)
        value = re.sub(r"(?i)bearer\s+[^\s]+", REDACTED, value)
        value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", REDACTED, value)
        return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", REDACTED, value)

    def sanitize_value(self, root):
        """Apply the same redaction to raw reports and derived string views."""
        budget = [MAX_NODES]
        clipped = [False]

        def sanitize(value, depth=0):
            budget[0] -= 1
            if budget[0] < 0 or depth > 16:
                clipped[0] = True
                return TRUNCATED
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    if budget[0] <= 0:
                        clipped[0] = True
                        break
                    # An arbitrary key can itself carry identifying information.
                    if len(key) > 96 or self._redact_text(key) != key:
                        clipped[0] = True
                        continue
                    result[key] = REDACTED if _private_key(key) else sanitize(item, depth + 1)
                return result
            if isinstance(value, list):
                result = []
                for item in value:
                    if budget[0] <= 0:
                        clipped[0] = True
                        break
                    result.append(sanitize(item, depth + 1))
                return result
            if isinstance(value, str):
                value = self._redact_text(value)
                if len(value) > MAX_TEXT:
                    clipped[0] = True
                    value = value[:MAX_TEXT] + TRUNCATED
                return value
            if isinstance(value, float) and not math.isfinite(value):
                return REDACTED
            if type(value) in (int, float):
                text = str(int(value)) if value == int(value) else str(value)
                if text in self._secrets:
                    return REDACTED
            return value

        return sanitize(root), clipped[0]

    def capture(self, payload, *, generation, received_at):
        if not isinstance(payload, bytes) or len(payload) > MAX_BYTES or not _bounded_json(payload):
            return False
        try:
            root = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs,
                              parse_constant=_reject_constant, parse_float=_finite_float)
        except (ValueError, UnicodeError, RecursionError):
            return False
        if not isinstance(root, dict):
            return False
        if generation != self.generation:
            self.clear()
            self.generation = generation
        safe, clipped = self.sanitize_value(root)
        stamp = received_at.isoformat()
        self.latest = {"received_at": stamp, "generation": generation, "data": safe}
        self.truncated |= clipped

        def remember(path, value):
            # A later leaf/empty object replaces earlier children, and vice versa.
            for old in list(self.fields):
                if old.startswith(path + "/") or path.startswith(old + "/"):
                    del self.fields[old]
                    self._cache_bytes -= self._sizes.pop(old)
            entry = {"path": path, "value": value, "updated_at": stamp, "generation": generation}
            size = len(json.dumps(entry, ensure_ascii=True).encode("utf-8"))
            previous_size = self._sizes.get(path, 0)
            if ((path not in self.fields and len(self.fields) >= MAX_FIELDS)
                    or self._cache_bytes - previous_size + size > MAX_CACHE_BYTES):
                self.truncated = True
                # Do not leave an old value looking like the newest observation.
                if path in self.fields:
                    del self.fields[path]
                    self._cache_bytes -= self._sizes.pop(path)
                return
            self.fields[path] = entry
            self._sizes[path] = size
            self._cache_bytes += size - previous_size

        def flatten(value, path=""):
            if isinstance(value, dict) and value:
                for key, item in value.items():
                    escaped = key.replace("~", "~0").replace("/", "~1")
                    flatten(item, path + "/" + escaped)
            else:
                # Arrays remain atomic: array positions are not stable AMS IDs,
                # and replacing an array must not invent retained child slots.
                remember(path or "/", value)
        flatten(safe)
        return True

    def snapshot(self, generation):
        # Revoked generations cannot be presented as current printer data.
        if self.generation != generation:
            return {"latest": None, "fields": [], "truncated": False}
        return {"latest": deepcopy(self.latest),
                "fields": [deepcopy(self.fields[path]) for path in sorted(self.fields)],
                "truncated": self.truncated}
