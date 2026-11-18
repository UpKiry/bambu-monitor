"""One serial owner for setup, credentials and collection, shared by HTTP and tray.

Request threads see only a copied public view and submit at most one operation.
They never read or mutate the controller. Stop/close are Events; blocking OS calls
may delay actual cleanup and are never reported as completed before the owner exits.
"""
from copy import deepcopy
from dataclasses import replace
from threading import Event, Lock, Thread
from uuid import uuid4

from .settings import Settings
from .setup_controller import SetupController


COMMAND_FIELDS = {
    "setup/begin": {"region"},
    "setup/login": {"account", "credential", "method"},
    "setup/send-code": {"account"},
    "setup/discover": set(),
    "setup/select": {"printer_id"},
    "setup/confirm": {"persistent"},
    "setup/cancel": set(),
    "setup/restore": set(),
    "setup/logout": set(),
    "settings/save": {"liveness_seconds", "initial_delay_seconds", "max_delay_seconds", "jitter"},
    "collector/start": set(),
}


def valid_command(command, data):
    if command not in COMMAND_FIELDS or type(data) is not dict or set(data) != COMMAND_FIELDS[command]:
        return False
    if command == "setup/begin":
        return data["region"] in ("cn", "global")
    if command == "setup/confirm":
        return type(data["persistent"]) is bool
    if command == "setup/login" and data["method"] not in ("password", "code"):
        return False
    for key in ("account", "credential", "printer_id"):
        if key in data:
            value = data[key]
            limit = 16384 if key == "credential" else 512 if key == "account" else 128
            if type(value) is not str or not 0 < len(value) <= limit or any(c in value for c in "\x00\r\n"):
                return False
    return True


class ServiceRuntime:
    def __init__(self, settings_path=None, *, controller=None):
        self._controller = controller if controller is not None else SetupController(Settings(settings_path))
        self._lock = Lock()
        self._wake, self._stop, self._closing = Event(), Event(), Event()
        self._pending = None
        self._stage = "home" if self._controller.config else "region"
        self._view = {"phase": "idle", "reason": self._controller.reason, "operation": None,
                      "connection": None, "metrics": {}, "devices": [], "setup_stage": self._stage}
        self._publish_controller()
        self._thread = Thread(target=self._owner, name="bambu-service-owner", daemon=False)
        self._thread.start()

    def status(self):
        with self._lock:
            return deepcopy(self._view)

    def submit(self, command, data):
        if not valid_command(command, data):
            return {"ok": False, "reason": "invalid_request"}
        with self._lock:
            if self._closing.is_set():
                return {"ok": False, "reason": "closing"}
            if self._view["phase"] != "idle":
                return {"ok": False, "reason": "busy"}
            operation = uuid4().hex
            self._stop.clear()
            self._view.update(phase="starting" if command == "collector/start" else "working",
                              operation={"id": operation, "command": command, "done": False},
                              connection=None, metrics={})
            self._pending = (command, dict(data), operation)
            self._wake.set()
            return {"ok": True, "operation_id": operation}

    def stop_collector(self):
        with self._lock:
            operation = self._view.get("operation") or {}
            if operation.get("command") == "collector/start" and not operation.get("done"):
                self._stop.set()
                self._view.update(phase="stopping", connection=None, metrics={})
                return {"ok": True, "reason": "stopping"}
            return {"ok": True, "reason": "stopped"}

    def request_close(self):
        with self._lock:
            if self._view["phase"] == "closed":
                return
            self._closing.set()
            self._stop.set()
            self._view.update(phase="closing", connection=None, metrics={})
            self._wake.set()

    def close(self, timeout=5):
        self.request_close()
        self._thread.join(timeout)
        return not self._thread.is_alive() and self.status()["reason"] != "cleanup_failed"

    def _publish_controller(self):
        # Only initialization and the serial owner call this method.
        controller = self._controller
        config, selected = controller.config, controller.selected
        view = {"configured": config is not None, "region": config.region if config else None,
                "printer_id": selected.id if selected else config.printer_id if config else "",
                "model": selected.model if selected else None,
                "persistent": controller.persistent, "setup_stage": self._stage,
                "reason": controller.reason,
                "devices": [{"id": d.id, "model": d.model} for d in controller.devices],
                "config": None if config is None else {
                    "liveness_seconds": config.liveness_seconds,
                    "initial_delay_seconds": config.initial_delay_seconds,
                    "max_delay_seconds": config.max_delay_seconds, "jitter": config.jitter}}
        with self._lock:
            self._view.update(view)

    def _emit(self, envelope):
        snapshot = envelope.get("snapshot") or {}
        with self._lock:
            if self._stop.is_set() or self._closing.is_set():
                return
            self._view.update(phase="collecting", connection=(snapshot.get("connection") or {}).get("state"),
                              metrics=deepcopy(snapshot.get("metrics", {})))

    def _execute(self, command, data):
        c = self._controller
        if command == "setup/begin":
            ok = c.begin(data["region"])
            if ok:
                self._stage = "login"
        elif command == "setup/login":
            ok = c.login(data["account"], data["credential"], method=data["method"])
            if ok and not self._closing.is_set():
                c.discover()
                ok = c.reason in ("select_printer", "no_bound_devices")
                # Discovery failure must still offer refresh, without resending login.
                self._stage = "devices"
        elif command == "setup/send-code":
            ok = c.send_code(data["account"])
        elif command == "setup/discover":
            c.discover()
            ok = c.reason in ("select_printer", "no_bound_devices")
            if ok:
                self._stage = "devices"
        elif command == "setup/select":
            ok = c.select(data["printer_id"])
            if ok:
                self._stage = "confirm"
        elif command == "setup/confirm":
            ok = c.confirm(data["persistent"])
            if ok:
                self._stage = "home"
        elif command == "setup/cancel":
            ok = c.cancel()
            if ok:
                self._stage = "home" if c.config else "region"
        elif command == "setup/restore":
            ok = c.restore()
            if ok:
                self._stage = "devices"
            elif c.reason in ("selected_device_unavailable", "selection_required"):
                self._stage = "devices"
        elif command == "setup/logout":
            ok = c.logout()
            if ok:
                self._stage = "region"
        elif command == "settings/save":
            try:
                updated = replace(c.config, **data)
            except (TypeError, ValueError):
                return False, "invalid_request"
            ok = c.update_settings(updated)
        elif command == "collector/start":
            if self._stage != "home":
                return False, "settings_not_confirmed"
            if self._stop.is_set():
                return True, "stopped"
            if not c.restore():
                if c.reason in ("selected_device_unavailable", "selection_required"):
                    self._stage = "devices"
                return False, c.reason
            if self._stop.is_set():
                return True, "stopped"
            with self._lock:
                if not self._stop.is_set():
                    self._view["phase"] = "collecting"
            result = c.run(self._stop, self._emit)
            return result == 0, c.reason
        else:
            return False, "invalid_request"
        return bool(ok), c.reason

    def _owner(self):
        try:
            while True:
                self._wake.wait()
                with self._lock:
                    self._wake.clear()
                    task, self._pending = self._pending, None
                if self._closing.is_set():
                    task = None
                    break
                if task is None:
                    continue
                command, data, operation = task
                try:
                    ok, reason = self._execute(command, data)
                    self._publish_controller()
                except Exception:
                    ok, reason = False, "service_operation_failed"
                finally:
                    # Do not keep private request bodies in an idle thread frame.
                    data.clear()
                    task = data = None
                with self._lock:
                    self._view.update(phase="closing" if self._closing.is_set() else "idle", reason=reason,
                                      connection=None, metrics={},
                                      operation={"id": operation, "command": command, "done": True,
                                                 "ok": ok, "reason": reason})
                if self._closing.is_set():
                    break
        finally:
            try:
                clean = self._controller.close()
            except Exception:
                clean = False
            with self._lock:
                self._pending = None
                self._view.update(phase="closed", connection=None, metrics={},
                                  reason="closed" if clean else "cleanup_failed")
