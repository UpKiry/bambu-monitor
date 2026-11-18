"""Transactional synchronous setup controller shared by local UI front ends.

Call setup operations serially on one worker. During run(), other threads may
set its stop Event; cleanup is completed on that owner before another action.
Draft login tokens stay in memory until confirm() commits ordinary settings.
"""
from dataclasses import dataclass, replace
from uuid import uuid4

from .auth import CloudAuth, Device, MemoryCredentials, Secret
from .collector import collect
from .config import Config
from .credentials import Credentials
from .session import Session


@dataclass
class _Context:
    config: Config
    auth: CloudAuth
    devices: tuple = ()
    selected: Device | None = None


class SetupController:
    def __init__(self, settings, http_factory=None, store_factory=Credentials, mqtt_factory=None):
        self.settings = settings
        self._http_factory = http_factory
        self._store_factory = store_factory
        self._mqtt_factory = mqtt_factory
        self._config = settings.load()
        if self._config is not None and not isinstance(self._config, Config):
            raise ValueError("invalid_settings")
        self._committed = None
        self._draft = None
        self._pending_selected = None
        self._closed = False
        self._running = False
        self._stop_event = None
        self._failed_cleanup = []
        self._failed_sessions = []
        self._orphan_stores = []
        self._save_outcome_unknown = False
        self.reason = "configured" if self._config is not None else "unconfigured"

    def __repr__(self):
        return f"SetupController(configured={self._config is not None}, draft={self._draft is not None}, running={self._running})"

    @property
    def _active(self):
        return self._draft if self._draft is not None else self._committed

    @property
    def config(self):
        return self._draft.config if self._draft is not None else self._config

    @property
    def auth(self):
        return self._active.auth if self._active is not None else None

    @property
    def devices(self):
        return self._active.devices if self._active is not None else ()

    @property
    def selected(self):
        if self.auth is None or self.auth.state != "authenticated":
            return None
        if self._pending_selected is not None:
            return self._pending_selected
        return self._active.selected if self._active is not None else None

    @property
    def persistent(self):
        return bool(self.auth is not None and getattr(self.auth.credentials, "persistent", False))

    def _available(self):
        if self._closed:
            self.reason = "closed"
            return False
        if self._running:
            self.reason = "busy"
            return False
        if self._save_outcome_unknown:
            self.reason = "settings_save_outcome_unknown"
            return False
        if self._failed_cleanup or self._failed_sessions:
            self.reason = "cleanup_failed"
            return False
        return True

    def _http(self, region):
        if self._http_factory is not None:
            return self._http_factory(region)
        from .cloud_http import BambuHttp
        return BambuHttp(region)

    @staticmethod
    def _secret(value):
        if isinstance(value, Secret):
            value = value.value
        if not isinstance(value, str) or not 0 < len(value) <= 16384:
            raise ValueError("invalid_private_input")
        return Secret(value)

    @staticmethod
    def _ref():
        return "bambu-monitor/cloud/" + uuid4().hex

    def _close_auth(self, auth):
        try:
            successful = auth.close()
        except Exception:
            successful = False
        if not successful and all(auth is not prior for prior in self._failed_cleanup):
            self._failed_cleanup.append(auth)
        return successful

    @staticmethod
    def _clear_store(store):
        try:
            store.clear()
            return getattr(store, "reason", None) != "keyring_revoke_failed"
        except Exception:
            return False

    def _save_settings(self, config):
        """Return committed / not committed / unknown; never infer from an error.

        An atomic writer may replace the file and then fail durability checking.
        Re-read before deleting a newly migrated OS token. Unknown outcomes keep
        both the previous scope and new token available for explicit recovery.
        """
        try:
            if self.settings.save(config) is False:
                raise ValueError("settings_save_failed")
            return True, None
        except Exception as error:
            code = getattr(error, "code", None)
            safe = code if isinstance(code, str) and code in {
                "settings_io_error", "settings_unsupported_platform", "settings_invalid_path",
                "settings_unsafe_path", "settings_invalid_config", "settings_load_required",
                "settings_conflict", "settings_too_large"} else "settings_save_failed"
            try:
                observed = self.settings.load()
            except Exception:
                observed = object()
            if observed == config:
                return True, "settings_saved_verification_failed"
            if observed == self._config:
                return False, safe
            self._save_outcome_unknown = True
            return None, "settings_save_outcome_unknown"

    def begin(self, region):
        if not self._available():
            return False
        if region not in ("cn", "global"):
            self.reason = "invalid_region"
            return False
        if self._draft is not None and not self.cancel():
            return False
        base = self._config if self._config is not None else Config(region)
        config = replace(base, region=region, printer_id="", credential_ref=self._ref())
        try:
            auth = CloudAuth(region, self._http(region), MemoryCredentials(), config.credential_ref)
        except Exception:
            self.reason = "transport_unavailable"
            return False
        self._draft = _Context(config, auth)
        self._pending_selected = None
        self.reason = "login_required"
        return True

    def login(self, account, credential, method="password"):
        if not self._available():
            return False
        if self._draft is None:
            self.reason = "setup_not_started"
            return False
        self._draft.devices = ()
        self._draft.selected = self._pending_selected = None
        try:
            result = self._draft.auth.login(self._secret(account), self._secret(credential), method=method)
        except Exception:
            self.reason = "invalid_private_input" if method in ("password", "code") else "unsupported_login_method"
            return False
        self.reason = self._draft.auth.reason
        return result

    def send_code(self, account):
        if not self._available():
            return False
        if self._draft is None:
            self.reason = "setup_not_started"
            return False
        try:
            result = self._draft.auth.send_code(self._secret(account))
        except Exception:
            self.reason = "invalid_private_input"
            return False
        self.reason = self._draft.auth.reason
        return result

    def discover(self):
        if not self._available() or self.auth is None:
            if self.auth is None:
                self.reason = "login_required"
            return ()
        ctx = self._active
        self._pending_selected = None
        ctx.devices = ()
        if ctx.auth.mqtt_credentials() is None:
            ctx.selected = None
            self.reason = ctx.auth.reason
            return ()
        ctx.devices = ctx.auth.discover()
        if ctx.auth.reason != "authenticated":
            ctx.selected = None
            self.reason = ctx.auth.reason
            return ()
        if ctx.selected is not None:
            ctx.selected = ctx.auth.select(ctx.devices, ctx.selected.id)
        self.reason = "select_printer" if ctx.devices else "no_bound_devices"
        return ctx.devices

    def select(self, printer_id):
        if not self._available() or self.auth is None:
            if self.auth is None:
                self.reason = "login_required"
            return False
        selected = self.auth.select(self.devices, printer_id)
        self._pending_selected = selected
        if selected is None:
            self.reason = self.auth.reason
            return False
        self.reason = "confirm_settings"
        return True

    def confirm(self, persistent):
        if not self._available():
            return False
        if type(persistent) is not bool:
            self.reason = "invalid_storage_choice"
            return False
        ctx, selected = self._active, self.selected
        if ctx is None or selected is None:
            self.reason = "selection_required"
            return False
        if ctx.auth.select(ctx.devices, selected.id) is None:
            self.reason = "selected_device_unavailable"
            return False
        private = ctx.auth.mqtt_credentials()
        if private is None:
            self.reason = ctx.auth.reason
            return False
        _, token = private
        if self._draft is None and persistent == self.persistent:
            config = replace(ctx.config, printer_id=selected.id)
            committed, warning = self._save_settings(config)
            if committed is not True:
                self.reason = warning
                return False
            ctx.config, ctx.selected = config, selected
            self._config = config
            self._pending_selected = None
            self.reason = warning or ("configured" if self.persistent else "configured_session_only")
            return warning is None
        config = replace(ctx.config, printer_id=selected.id, credential_ref=self._ref())
        store = None
        prepared = False
        try:
            store = self._store_factory(config.region, config.credential_ref, memory_only=not persistent)
            store.put(config.credential_ref, token)
            prepared = True
        except Exception:
            pass
        committed, warning = self._save_settings(config) if prepared else (False, "credential_store_unavailable")
        if committed is None:
            # The new file may already reference this slot. Keep its token until
            # outcome can be examined; deleting it would break a successful save.
            self._orphan_stores.append(store)
            self.reason = warning
            return False
        if not committed:
            cleanup = True
            if store is not None:
                cleanup = self._clear_store(store)
                try:
                    store.close()
                except Exception:
                    cleanup = False
            self.reason = warning if cleanup else "settings_save_failed_cleanup_failed"
            return False
        # Publish committed state only after save returned successfully. In
        # particular, changing the auth's store earlier would revoke the old slot.
        old = self._committed
        source_store = ctx.auth.credentials
        ctx.auth.credentials, ctx.auth.reference = store, config.credential_ref
        self._committed = _Context(config, ctx.auth, ctx.devices, selected)
        self._config = config
        self._draft = None
        self._pending_selected = None
        cleanup = self._clear_store(source_store)
        try:
            source_store.close() if hasattr(source_store, "close") else source_store.clear()
        except Exception:
            cleanup = False
        if old is not None and old.auth is not ctx.auth:
            cleanup = self._clear_store(old.auth.credentials) and cleanup
            cleanup = self._close_auth(old.auth) and cleanup
        if not cleanup:
            self.reason = "settings_saved_old_credentials_cleanup_failed"
            return False
        self.reason = warning or ("configured" if self.persistent else
                                  "keyring_unavailable_session_memory" if persistent else "configured_session_only")
        return warning is None

    def update_settings(self, config):
        if not self._available():
            return False
        if self._draft is not None or self._config is None or self._pending_selected is not None:
            self.reason = "settings_not_confirmed"
            return False
        if type(config) is not Config or config.identity != self._config.identity:
            self.reason = "settings_identity_change_requires_setup"
            return False
        committed, warning = self._save_settings(config)
        if committed is not True:
            self.reason = warning
            return False
        self._config = config
        if self._committed is not None:
            self._committed.config = config
        self.reason = warning or "configured"
        return warning is None

    def restore(self):
        if not self._available():
            return False
        if self._draft is not None:
            self.reason = "setup_in_progress"
            return False
        if self._config is None:
            self.reason = "unconfigured"
            return False
        self._pending_selected = None
        if self._committed is not None:
            # Keep a confirmed session-only token alive for this process. A
            # second controller/process has no such token and must log in again.
            auth = self._committed.auth
            if auth.state != "authenticated" and not auth.restore():
                self.reason = auth.reason
                return False
        else:
            store = None
            try:
                store = self._store_factory(self._config.region, self._config.credential_ref, memory_only=False)
                auth = CloudAuth(self._config.region, self._http(self._config.region), store, self._config.credential_ref)
            except Exception:
                if store is not None:
                    store.close()
                self.reason = "transport_unavailable"
                return False
            self._committed = _Context(self._config, auth)
            if not auth.restore():
                self.reason = auth.reason
                return False
        if auth.mqtt_credentials() is None:
            self.reason = auth.reason
            return False
        devices = auth.discover()
        self._committed.devices = devices
        if auth.reason != "authenticated":
            self.reason = auth.reason
            return False
        selected = auth.select(devices, self._config.printer_id)
        self._committed.selected = selected
        if selected is None:
            self.reason = "selected_device_unavailable" if self._config.printer_id else "selection_required"
            return False
        self.reason = "ready"
        return True

    def cancel(self):
        if self._running:
            self._stop_event.set()
            self.reason = "stopping"
            return False
        if self._closed:
            self.reason = "closed"
            return False
        if self._save_outcome_unknown:
            self.reason = "settings_save_outcome_unknown"
            return False
        if self._draft is not None:
            draft = self._draft
            self._draft = None
            if not self._close_auth(draft.auth):
                self.reason = "cleanup_failed"
                return False
        self._pending_selected = None
        self.reason = "ready" if self._committed and self._committed.selected else ("configured" if self._config else "unconfigured")
        return True

    def run(self, stop_event, emit):
        if not self._available():
            return 2
        if self._draft is not None or self._pending_selected is not None:
            self.reason = "settings_not_confirmed"
            return 2
        ctx = self._committed
        if ctx is None or ctx.selected is None:
            self.reason = "selection_required"
            return 2
        if stop_event.is_set():
            self.reason = "stopped"
            return 0
        self._running, self._stop_event = True, stop_event
        session = None
        try:
            if self._mqtt_factory is None:
                from .cloud_mqtt import PahoMqtt
                try:
                    PahoMqtt.check_available()
                except Exception:
                    self.reason = "mqtt_dependency_unavailable"
                    return 2
                factory = PahoMqtt
            else:
                factory = self._mqtt_factory
            if stop_event.is_set():
                self.reason = "stopped"
                return 0
            private = ctx.auth.mqtt_credentials()
            if stop_event.is_set():
                self.reason = "stopped"
                return 0
            if private is None:
                self.reason = ctx.auth.reason
                return 3
            devices = ctx.auth.discover()
            if stop_event.is_set():
                self.reason = "stopped"
                return 0
            ctx.devices = devices
            selected = ctx.auth.select(devices, ctx.selected.id)
            if selected is None:
                ctx.selected = None
                self.reason = "selected_device_unavailable" if ctx.auth.state == "authenticated" else ctx.auth.reason
                return 3
            ctx.selected = selected
            if stop_event.is_set():
                self.reason = "stopped"
                return 0
            session = Session(ctx.config, factory(selected.id))
            session.core.model = selected.model
            result = collect(session, *private, stop_event=stop_event, emit=emit)
            if result == 3:
                ctx.auth.invalidate()
                self.reason = "credential_rejected"
            else:
                self.reason = "stopped"
            return result
        except Exception:
            if session is not None and session.diagnostics().get("cleanup_failed"):
                self._failed_sessions.append(session)
                self.reason = "cleanup_failed"
            else:
                self.reason = "collector_failed"
            return 2
        finally:
            self._running, self._stop_event = False, None

    def logout(self):
        if not self._available():
            return False
        if self._draft is not None and not self.cancel():
            return False
        if self._committed is None and self._config is None:
            self.reason = "unconfigured"
            return True
        store = self._committed.auth.credentials if self._committed is not None else None
        temporary = False
        if store is None:
            try:
                store = self._store_factory(self._config.region, self._config.credential_ref, memory_only=False)
                temporary = True
            except Exception:
                self.reason = "credential_store_unavailable"
                return False
            if not getattr(store, "persistent", False):
                store.close()
                self.reason = "credential_store_unavailable"
                return False
        cleared = self._clear_store(store)
        closed = True
        if self._committed is not None:
            closed = self._close_auth(self._committed.auth)
            self._committed = None
        elif temporary:
            try:
                store.close()
            except Exception:
                closed = False
        self._pending_selected = None
        self.reason = "logged_out" if cleared and closed else "credential_revoke_failed" if not cleared else "cleanup_failed"
        return cleared and closed

    def close(self):
        if self._running:
            self._stop_event.set()
            self.reason = "stopping"
            return False
        self._closed = True
        sessions, self._failed_sessions = self._failed_sessions, []
        successful = True
        for session in sessions:
            try:
                released = session.stop()
            except Exception:
                released = False
            if not released:
                self._failed_sessions.append(session)
                successful = False
        stores, self._orphan_stores = self._orphan_stores, []
        for store in stores:
            try:
                store.close()
            except Exception:
                self._orphan_stores.append(store)
                successful = False
        auths = list(self._failed_cleanup)
        for ctx in (self._draft, self._committed):
            if ctx is not None and all(ctx.auth is not prior for prior in auths):
                auths.append(ctx.auth)
        self._failed_cleanup = []
        for auth in auths:
            successful = self._close_auth(auth) and successful
        self._draft = self._committed = None
        self._pending_selected = None
        self.reason = ("closed_settings_outcome_unknown" if self._save_outcome_unknown else "closed") if successful else "cleanup_failed"
        return successful
