"""Strict non-secret configuration; region and device are never inferred."""
from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
import re
import tomllib


class ConfigError(ValueError):
    """A safe, value-free configuration error."""


def identifier(value, *, empty=False):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{0,128}", value):
        raise ConfigError("invalid identifier")
    if not empty and not value:
        raise ConfigError("missing identifier")
    return value


@dataclass(frozen=True)
class Identity:
    account_ref: str = field(repr=False)
    region: str
    printer_id: str = field(repr=False)

    def __post_init__(self):
        identifier(self.account_ref)
        identifier(self.printer_id)
        if self.region not in ("cn", "global"):
            raise ConfigError("region must be explicitly cn or global")

    @property
    def namespace(self):
        # Never use a display name or array index as identity.
        return hashlib.sha256("\0".join((self.account_ref, self.region, self.printer_id)).encode()).hexdigest()


ENDPOINTS = {
    "cn": ("https://api.bambulab.cn", "cn.mqtt.bambulab.com"),
    "global": ("https://api.bambulab.com", "us.mqtt.bambulab.com"),
}


@dataclass(frozen=True)
class Config:
    region: str
    printer_id: str = field(default="", repr=False)
    credential_ref: str = field(default="bambu-monitor/cloud/default", repr=False)
    liveness_seconds: float = 60
    initial_delay_seconds: float = 1
    max_delay_seconds: float = 60
    jitter: bool = True

    def __post_init__(self):
        if self.region not in ("cn", "global"):
            raise ConfigError("region must be explicitly cn or global")
        if not isinstance(self.printer_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{0,128}", self.printer_id):
            raise ConfigError("invalid selected printer identifier")
        identifier(self.credential_ref)
        for number in (self.liveness_seconds, self.initial_delay_seconds, self.max_delay_seconds):
            if type(number) not in (int, float) or not 0 < number <= 3600 or not math.isfinite(number):
                raise ConfigError("invalid time budget")
        if self.max_delay_seconds < self.initial_delay_seconds or type(self.jitter) is not bool:
            raise ConfigError("invalid reconnect policy")

    @property
    def identity(self):
        return Identity(self.credential_ref, self.region, self.printer_id) if self.printer_id else None


def load_config(path: str | Path) -> Config:
    """Load a bounded TOML document, refusing unknown settings (including secrets)."""
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(16385)
        if len(data) > 16384:
            raise ConfigError("configuration too large")
        raw = tomllib.loads(data.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise ConfigError("cannot read non-secret configuration") from None
    allowed = {
        "provider": {"kind", "region", "printer_id"},
        "credentials": {"reference"},
        "freshness": {"liveness_seconds"},
        "reconnect": {"initial_delay_seconds", "max_delay_seconds", "jitter"},
    }
    if set(raw) - set(allowed):
        raise ConfigError("unknown configuration section")
    for section, values in raw.items():
        if not isinstance(values, dict) or set(values) - allowed[section]:
            raise ConfigError("unknown configuration setting")
    provider = raw.get("provider", {})
    if provider.get("kind") != "bambu-cloud":
        raise ConfigError("only bambu-cloud is implemented")
    if provider.get("region") not in ("cn", "global"):
        raise ConfigError("region must be explicitly cn or global")
    printer = identifier(provider.get("printer_id", ""), empty=True)
    reference = identifier(raw.get("credentials", {}).get("reference", "bambu-monitor/cloud/default"))
    freshness = raw.get("freshness", {}).get("liveness_seconds", 60)
    reconnect = raw.get("reconnect", {})
    initial = reconnect.get("initial_delay_seconds", 1)
    maximum = reconnect.get("max_delay_seconds", 60)
    for number in (freshness, initial, maximum):
        if type(number) not in (int, float) or not 0 < number <= 3600 or not math.isfinite(number):
            raise ConfigError("invalid time budget")
    if maximum < initial:
        raise ConfigError("reconnect maximum precedes initial delay")
    jitter = reconnect.get("jitter", True)
    if type(jitter) is not bool:
        raise ConfigError("jitter must be boolean")
    return Config(provider["region"], printer, reference, freshness, initial, maximum, jitter)
