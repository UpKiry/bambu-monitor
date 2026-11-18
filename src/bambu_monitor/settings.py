"""Non-secret TOML settings with anchored POSIX I/O and optimistic conflicts.

Parents are traversed using directory descriptors and O_NOFOLLOW; no symlink is
resolved. Cooperating writers serialize on a private lock file. A loaded content
and inode baseline is checked immediately before atomic replacement. An external
writer ignoring the advisory lock can still race that final check/replace; this
is not a filesystem compare-and-swap or protection from the same privileged user.
Windows lacks the required primitives here and fails explicitly, rather than
silently using a weaker path-based overwrite. Import/construction performs no I/O.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tomllib
import uuid

from .config import Config, ConfigError

LIMIT = 16384
_UNLOADED = object()


class SettingsError(ValueError):
    """Fixed, value-free diagnostics suitable for a user-facing error mapper."""
    def __init__(self, code="settings_io_error"):
        allowed = {"settings_io_error", "settings_unsupported_platform", "settings_invalid_path",
                   "settings_unsafe_path", "settings_invalid_config", "settings_load_required",
                   "settings_conflict", "settings_too_large"}
        self.code = code if isinstance(code, str) and code in allowed else "settings_io_error"
        super().__init__(self.code)


class SettingsConflict(SettingsError):
    def __init__(self):
        super().__init__("settings_conflict")


def new_credential_ref():
    """Create an opaque logical account slot; no email, token or UID is embedded."""
    return "bambu-monitor/cloud/" + uuid.uuid4().hex


def default_settings_path():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/bambu-monitor/provider.toml"
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return (Path(base) if base and Path(base).is_absolute() else Path.home() / "AppData/Roaming") / "bambu-monitor/provider.toml"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base and Path(base).is_absolute() else Path.home() / ".config") / "bambu-monitor/provider.toml"


def _config(data):
    try:
        raw = tomllib.loads(data.decode("utf-8"))
        allowed = {"provider": {"kind", "region", "printer_id"}, "credentials": {"reference"},
                   "freshness": {"liveness_seconds"},
                   "reconnect": {"initial_delay_seconds", "max_delay_seconds", "jitter"}}
        if set(raw) - set(allowed):
            raise ValueError
        for section, value in raw.items():
            if not isinstance(value, dict) or set(value) - allowed[section]:
                raise ValueError
        provider = raw.get("provider", {})
        if provider.get("kind") != "bambu-cloud":
            raise ValueError
        reconnect = raw.get("reconnect", {})
        return Config(region=provider.get("region"), printer_id=provider.get("printer_id", ""),
                      credential_ref=raw.get("credentials", {}).get("reference", "bambu-monitor/cloud/default"),
                      liveness_seconds=raw.get("freshness", {}).get("liveness_seconds", 60),
                      initial_delay_seconds=reconnect.get("initial_delay_seconds", 1),
                      max_delay_seconds=reconnect.get("max_delay_seconds", 60),
                      jitter=reconnect.get("jitter", True))
    except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
        raise SettingsError("settings_invalid_config") from None


def _serialize(config):
    # Accept only the actual frozen contract, not arbitrary mappings/objects with
    # additional properties that might accidentally persist private credentials.
    if type(config) is not Config:
        raise SettingsError("settings_invalid_config")
    try:
        Config(**vars(config))
        text = ("# Bambu Monitor non-secret settings. Credentials remain in the OS keyring or memory.\n"
                "[provider]\nkind = \"bambu-cloud\"\n"
                f"region = {json.dumps(config.region)}\nprinter_id = {json.dumps(config.printer_id)}\n\n"
                f"[credentials]\nreference = {json.dumps(config.credential_ref)}\n\n"
                f"[freshness]\nliveness_seconds = {config.liveness_seconds!r}\n\n"
                f"[reconnect]\ninitial_delay_seconds = {config.initial_delay_seconds!r}\n"
                f"max_delay_seconds = {config.max_delay_seconds!r}\njitter = {str(config.jitter).lower()}\n")
        result = text.encode("utf-8")
    except (ValueError, TypeError, OverflowError):
        raise SettingsError("settings_invalid_config") from None
    if len(result) > LIMIT or _config(result) != config:
        raise SettingsError("settings_invalid_config")
    return result


def _file_safe(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and not info.st_mode & 0o022)


def _signature(info, data):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, stat.S_IMODE(info.st_mode), hashlib.sha256(data).digest())


class Settings:
    def __init__(self, path=None):
        try:
            candidate = Path(path) if path is not None else default_settings_path()
            if "\x00" in str(candidate) or ".." in candidate.parts or candidate.name in ("", ".", ".."):
                raise ValueError
            self.path = Path(os.path.abspath(candidate.expanduser()))
            if self.path == Path(self.path.anchor):
                raise ValueError
        except (ValueError, TypeError, OSError):
            raise SettingsError("settings_invalid_path") from None
        self._baseline = _UNLOADED

    @staticmethod
    def _platform():
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise SettingsError("settings_unsupported_platform")

    @contextmanager
    def _parent(self, create=False):
        self._platform()
        descriptor = os.open(self.path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = self.path.parent.parts[1:]
            for index, part in enumerate(parts):
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        yield None
                        return
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                info = os.fstat(child)
                # Shared sticky ancestors such as /tmp are traversable; a final
                # settings directory must be owned by us and not writable by others.
                final = index == len(parts) - 1
                unsafe_shared = bool(info.st_mode & 0o022) and not bool(info.st_mode & stat.S_ISVTX)
                if unsafe_shared or final and (info.st_uid != os.getuid() or info.st_mode & 0o022):
                    os.close(child)
                    raise SettingsError("settings_unsafe_path")
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    def _read(self, parent):
        if parent is None:
            return None, None
        try:
            descriptor = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return None, None
        try:
            before = os.fstat(descriptor)
            if not _file_safe(before):
                raise SettingsError("settings_unsafe_path")
            chunks, count = [], 0
            while count <= LIMIT:
                chunk = os.read(descriptor, min(4096, LIMIT + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
            if count > LIMIT:
                raise SettingsError("settings_too_large")
            data = b"".join(chunks)
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise SettingsConflict()
            return data, _signature(after, data)
        finally:
            os.close(descriptor)

    def load(self):
        try:
            with self._parent() as parent:
                data, signature = self._read(parent)
                config = None if data is None else _config(data)
            self._baseline = signature
            return config
        except SettingsError:
            raise
        except OSError:
            raise SettingsError("settings_io_error") from None

    @contextmanager
    def _lock(self, parent):
        import fcntl
        name = "." + self.path.name + ".lock"
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
        try:
            if not _file_safe(os.fstat(descriptor)):
                raise SettingsError("settings_unsafe_path")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SettingsConflict() from None
            yield
        finally:
            os.close(descriptor)

    def save(self, config):
        data = _serialize(config)
        try:
            with self._parent(create=True) as parent, self._lock(parent):
                current, signature = self._read(parent)
                if self._baseline is _UNLOADED:
                    if current is not None:
                        raise SettingsError("settings_load_required")
                    self._baseline = None
                if signature != self._baseline:
                    raise SettingsConflict()
                name = "." + self.path.name + "." + uuid.uuid4().hex + ".tmp"
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                temporary = True
                try:
                    try:
                        remaining = memoryview(data)
                        while remaining:
                            count = os.write(descriptor, remaining)
                            if count <= 0:
                                raise OSError
                            remaining = remaining[count:]
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    # Reopen the parent from root before publishing, detecting
                    # parent renames/replacements while keeping all writes anchored.
                    with self._parent() as checked:
                        if checked is None or (os.fstat(checked).st_dev, os.fstat(checked).st_ino) != (os.fstat(parent).st_dev, os.fstat(parent).st_ino):
                            raise SettingsConflict()
                    _, latest = self._read(parent)
                    if latest != self._baseline:
                        raise SettingsConflict()
                    if latest is None:
                        # link is no-clobber: a newly created external entry wins.
                        try:
                            os.link(name, self.path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                        except FileExistsError:
                            raise SettingsConflict() from None
                        os.unlink(name, dir_fd=parent)
                    else:
                        os.replace(name, self.path.name, src_dir_fd=parent, dst_dir_fd=parent)
                    temporary = False
                    os.fsync(parent)
                    written, updated = self._read(parent)
                    if written != data:
                        raise SettingsConflict()
                    self._baseline = updated
                finally:
                    if temporary:
                        os.unlink(name, dir_fd=parent)
        except SettingsError:
            raise
        except OSError:
            raise SettingsError("settings_io_error") from None
