"""Foreground collector runner; private inputs and machine output stay separate."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import signal
import sys
from threading import Event
import time

from .auth import CloudAuth
from .credentials import Credentials
from .interaction import PrivateTTY, InteractionError, authenticate, select_device
from .session import Session


class CollectorError(ValueError):
    pass


def collect(session, uid, token, *, emit, stop_event, duration=None,
            monotonic=time.monotonic, wall_clock=lambda: datetime.now(timezone.utc),
            wait=None, tick_seconds=0.25, handshake_seconds=15):
    """Drive one selected cloud session; no background network/USB threads.

    Pumps a concrete transport on the owner. Real DNS/OS keychain blocking is
    outside the controllable timers; it is never reported as a hard 5s budget.
    """
    if duration is not None and (type(duration) not in (int, float) or not 0 < duration <= 86400
                                 or not math.isfinite(duration)):
        raise CollectorError("invalid_duration")
    wait = wait or stop_event.wait
    started = monotonic()
    last_emit = None
    attempt_started = started
    first_evidence_deadline = started + session.config.liveness_seconds
    result = 0
    session.set_stop_check(stop_event.is_set)
    try:
        if not stop_event.is_set():
            session.start(uid=uid, token=token, monotonic=started)
        while not stop_event.is_set():
            now = monotonic()
            if duration is not None and now - started >= duration:
                break
            session.drain()
            session.advance_time(now)
            if session.diagnostics()["cleanup_failed"]:
                raise CollectorError("transport_cleanup_failed")
            if session.phase == "auth_required":
                if stop_event.is_set():
                    break
                snapshot = session.snapshot(now=wall_clock(), monotonic=now)
                if not stop_event.is_set():
                    emit({"input_class": "live-cloud", "snapshot": snapshot, "diagnostics": session.diagnostics(),
                          **session.inspection_view()})
                result = 3
                break
            if session.active:
                try:
                    session.transport.pump(timeout=min(0.1, tick_seconds))
                except Exception:
                    # Never render exception text from network libraries.
                    session.disconnect(session.generation, reason="network_error")
                now = monotonic()
                if stop_event.is_set():
                    break
                session.drain()
                if session.active and session.phase in ("connecting", "subscribing", "awaiting_suback"):
                    if now - attempt_started >= handshake_seconds:
                        session.disconnect(session.generation, reason="handshake_timeout")
                if (session.active and session.phase == "subscribed"
                        and session.core.metadata()["last_target_evidence_monotonic"] is None
                        and now >= first_evidence_deadline):
                    session.core.set_connection("offline", "telemetry_timeout")
            elif session.phase == "offline" and session.retry_at is not None and now >= session.retry_at:
                attempt_started = now
                first_evidence_deadline = now + session.config.liveness_seconds
                session.start(uid=uid, token=token, monotonic=now)
            snapshot = session.snapshot(now=wall_clock(), monotonic=now)
            # Emit at most 4/s; generation/time/quality still update on each owner tick.
            if snapshot is not None and (last_emit is None or now - last_emit >= tick_seconds):
                if stop_event.is_set():
                    break
                emit({"input_class": "live-cloud", "snapshot": snapshot, "diagnostics": session.diagnostics(),
                          **session.inspection_view()})
                last_emit = now
            wait(min(0.1, tick_seconds))
    finally:
        if not session.stop():
            raise CollectorError("transport_cleanup_failed")
    return result


def cloud_command(command, config, *, method="password", duration=None, memory_only=False,
                  tty_factory=PrivateTTY, store_factory=Credentials,
                  http_factory=None, mqtt_factory=None, output=None, diagnostics=None,
                  stop_event=None, monotonic=time.monotonic, wall_clock=None, wait=None):
    """Executable orchestration, injectable for integration tests without sockets."""
    if command not in ("login", "collect", "logout"):
        raise CollectorError("invalid_cloud_command")
    if http_factory is None:
        from .cloud_http import BambuHttp
        http_factory = BambuHttp
    if mqtt_factory is None:
        from .cloud_mqtt import PahoMqtt
        if command == "collect":
            PahoMqtt.check_available()
        mqtt_factory = PahoMqtt
    output = output if output is not None else sys.stdout
    diagnostics = diagnostics if diagnostics is not None else sys.stderr
    store = store_factory(config.region, config.credential_ref, memory_only=memory_only)
    auth = None
    try:
        if command == "logout":
            if not memory_only and not store.persistent:
                raise CollectorError("credential_store_unavailable")
            store.clear()
            if store.reason == "keyring_revoke_failed":
                raise CollectorError("credential_revoke_failed")
            print('{"event":"local_credentials_removed"}', file=diagnostics)
            return 0
        auth = CloudAuth(config.region, http_factory(config.region), store, config.credential_ref)
        with tty_factory() as ui:
            ui.tell("仅本机输入账号、密码或验证码；程序不会输出这些内容。")
            if not store.persistent:
                ui.tell("系统凭据库不可用或已选择会话模式：本次仅保存在内存，重启需重新登录。")
            if not authenticate(auth, ui, method=method, allow_restore=command == "collect"):
                print(json.dumps({"state": auth.state, "reason": auth.reason}), file=diagnostics)
                return 3
            if not store.persistent:
                ui.tell("当前为会话内存模式；退出后不保留登录。")
            credentials = auth.mqtt_credentials()
            if credentials is None:
                print(json.dumps({"state": auth.state, "reason": auth.reason}), file=diagnostics)
                return 3
            if command == "login":
                print(json.dumps({"event": "login_completed", "persistent": store.persistent,
                                  "storage": store.reason, "expires_in": auth.expires_in}), file=diagnostics)
                return 0
            devices = auth.discover()
            if auth.state != "authenticated" or auth.reason != "authenticated":
                print(json.dumps({"state": auth.state, "reason": auth.reason}), file=diagnostics)
                return 3
            selected = select_device(auth, devices, config.printer_id, ui)
            ui.tell("采集开始；终端输出为PC快照。Ctrl+C停止。当前尚未接入Pet Manager屏幕。")
        session = Session(replace(config, printer_id=selected.id), mqtt_factory(selected.id))
        # Capability remains unknown unless discovery returned a supported model name.
        session.core.model = selected.model
        print(json.dumps({"event": "collector_started", "profile": "legacy-cloud-v1",
                          "profile_verified_on_real_cloud": False, "baseline_auto_detection": False}), file=diagnostics)
        stop_event = stop_event if stop_event is not None else Event()

        def emit(item):
            print(json.dumps(item, ensure_ascii=True, allow_nan=False, separators=(",", ":")), file=output, flush=True)

        kwargs = {"emit": emit, "stop_event": stop_event, "duration": duration, "monotonic": monotonic, "wait": wait}
        if wall_clock is not None:
            kwargs["wall_clock"] = wall_clock
        restore_handlers = signal_stop(stop_event)
        try:
            result = collect(session, *credentials, **kwargs)
        finally:
            restore_handlers()
        if result == 3:
            auth.invalidate()
            print('{"state":"auth_required","reason":"credential_rejected"}', file=diagnostics)
        return result
    finally:
        if auth is not None:
            if not auth.close():
                raise CollectorError("http_cleanup_failed")
        else:
            store.close()


def signal_stop(event):
    """Install foreground stop handlers and return a callable that restores them."""
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: event.set())
    except ValueError:
        # Embedding outside the main thread may supply its own stop event.
        pass

    def restore():
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return restore
