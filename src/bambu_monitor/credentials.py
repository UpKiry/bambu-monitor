"""Region-scoped native OS token storage, with explicit session-memory fallback."""
import hashlib
import importlib
import sys

from .auth import Secret
from .config import identifier, ConfigError

SERVICE = "bambu-monitor/cloud-token/v1"


def native_backend():
    """Choose only a bundled native backend; never auto-load third-party stores."""
    modules = {"darwin": ("keyring.backends.macOS", "Keyring"),
               "win32": ("keyring.backends.Windows", "WinVaultKeyring"),
               "linux": ("keyring.backends.SecretService", "Keyring")}
    try:
        module, name = modules[sys.platform]
        backend = getattr(importlib.import_module(module), name)()
        if backend.priority <= 0:
            return None
        return backend
    except Exception:
        return None


class Credentials:
    """A single logical account slot; credentials never go into configuration.

    `clear` revokes this slot, `close` drops only memory (retaining an OS entry).
    No raw account name or UID is used as an OS key. Backend is injectable only
    for tests; production chooses a native backend explicitly.
    """
    def __init__(self, region, reference, *, backend_factory=native_backend, memory_only=False):
        if region not in ("cn", "global"):
            raise ConfigError("explicit credential region required")
        self.reference = identifier(reference)
        self._key = hashlib.sha256((region + "\0" + reference).encode()).hexdigest()
        self._token = None
        self._backend = None
        self._closed = False
        self.persistent = False
        self.reason = "session_memory_only"
        if not memory_only:
            try:
                self._backend = backend_factory()
            except Exception:
                pass
            if self._backend is not None:
                self.persistent = True
                self.reason = "system_keyring"

    def __repr__(self):
        return f"Credentials(persistent={self.persistent}, reason={self.reason!r})"

    def _fallback(self):
        self._backend = None
        self.persistent = False
        self.reason = "keyring_unavailable_session_memory"

    def _check(self, reference):
        if reference != self.reference or self._closed:
            raise ValueError("credential scope unavailable")

    def get(self, reference):
        self._check(reference)
        if self._token is not None:
            return self._token
        if self._backend is not None:
            try:
                value = self._backend.get_password(SERVICE, self._key)
                if value is not None:
                    if not isinstance(value, str) or not 0 < len(value) <= 16384:
                        self.reason = "invalid_stored_token"
                        return None
                    self._token = Secret(value)
            except Exception:
                self._fallback()
        return self._token

    def put(self, reference, token):
        self._check(reference)
        if not isinstance(token, Secret) or not isinstance(token.value, str) or not 0 < len(token.value) <= 16384:
            raise ValueError("invalid private token")
        self._token = token
        if self._backend is not None:
            try:
                self._backend.set_password(SERVICE, self._key, token.value)
            except Exception:
                self._fallback()

    def clear(self):
        self._token = None
        if self._backend is not None:
            try:
                # Avoid interpreting 'not found' as a broken OS credential store.
                if self._backend.get_password(SERVICE, self._key) is not None:
                    self._backend.delete_password(SERVICE, self._key)
            except Exception:
                self._fallback()
                self.reason = "keyring_revoke_failed"

    def close(self):
        self._token = None
        self._closed = True
        self._backend = None
