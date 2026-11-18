"""Offline macOS adapter tests: fake Cocoa/pystray, temporary owned plists only."""
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from bambu_monitor import macos_service as service


class Runtime:
    def __init__(self):
        self.value = dict(phase="idle", reason=None, connection=None, configured=True)
        self.calls = []

    def status(self):
        return dict(self.value)

    def submit(self, action, data):
        self.calls.append((action, data))
        return {"ok": True, "operation": "test"}

    def stop_collector(self):
        self.calls.append("stop")
        return {"ok": True}

    def request_close(self):
        self.calls.append("close")
        return {"ok": True}


class TrayTests(unittest.TestCase):
    def run_fake(self, body, runtime=None, event=None, on_ready=None):
        runtime = runtime or Runtime()
        event = event or threading.Event()
        native_threads = []
        timer = types.SimpleNamespace(invalidated=False)
        timer.invalidate = lambda: setattr(timer, "invalidated", True)

        class Timer:
            @staticmethod
            def scheduledTimerWithTimeInterval_repeats_block_(interval, repeats, callback):
                native_threads.append(threading.current_thread())
                self.assertEqual(interval, 0.5)
                self.assertTrue(repeats)
                timer.callback = callback
                return timer

        class Menu:
            SEPARATOR = object()
            def __init__(self, *items):
                self.items = items

        class Item:
            def __init__(self, text, action, enabled=True):
                self.text, self.action, self.enabled = text, action, enabled

        class Icon:
            def __init__(self, *args):
                native_threads.append(threading.current_thread())
                self.stopped = False
            def run(icon, setup):
                worker = threading.Thread(target=setup, args=(icon,))
                worker.start()
                worker.join()
                body(icon, timer, runtime, event)
            def stop(icon):
                native_threads.append(threading.current_thread())
                icon.stopped = True
            def update_menu(icon):
                native_threads.append(threading.current_thread())

        modules = {"pystray": types.SimpleNamespace(Icon=Icon, Menu=Menu, MenuItem=Item),
                   "AppKit": types.ModuleType("AppKit"),
                   "Foundation": types.SimpleNamespace(NSTimer=Timer)}
        old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        with patch.dict(sys.modules, modules), patch.object(service.sys, "platform", "darwin"):
            service.run_tray(runtime, "http://127.0.0.1:8765/#test", event, on_ready=on_ready)
        self.assertTrue(timer.invalidated)
        self.assertTrue(event.is_set())
        self.assertTrue(all(thread is threading.main_thread() for thread in native_threads))
        for sig, handler in old_handlers.items():
            self.assertEqual(signal.getsignal(sig), handler)
        return runtime

    def test_ready_callback_once_on_main_after_visible_refresh(self):
        calls = []
        active = {}
        def ready():
            icon = active["icon"]
            self.assertTrue(icon.visible)
            self.assertIn("Bambu Monitor", icon.title)
            self.assertTrue(icon.menu.items)
            self.assertIs(threading.current_thread(), threading.main_thread())
            calls.append("ready")
        def body(icon, timer, runtime, event):
            active["icon"] = icon
            self.assertEqual(calls, [])
            for _ in range(4):
                timer.callback(timer)
            runtime.value.update(phase="collecting", connection="online")
            timer.callback(timer)
            self.assertEqual(calls, ["ready"])
            event.set()
            timer.callback(timer)
            self.assertEqual(calls, ["ready"])
        self.run_fake(body, on_ready=ready)

    def test_quit_before_initial_refresh_does_not_signal_ready(self):
        calls = []
        def body(icon, timer, runtime, event):
            event.set()
            timer.callback(timer)
        self.run_fake(body, on_ready=lambda: calls.append("ready"))
        self.assertEqual(calls, [])

    def test_main_thread_refresh_actions_and_quit(self):
        def body(icon, timer, runtime, event):
            timer.callback(timer)
            self.assertIn("已停止", icon.title)
            self.assertTrue(icon.menu.items[2].enabled)
            self.assertFalse(icon.menu.items[3].enabled)
            with patch.object(service.webbrowser, "open") as open_browser:
                icon.menu.items[1].action(icon, None)
                open_browser.assert_called_once_with("http://127.0.0.1:8765/#test", new=1, autoraise=True)
            icon.menu.items[2].action(icon, None)
            runtime.value.update(phase="collecting", connection=None)
            timer.callback(timer)
            self.assertIn("云 MQTT", icon.title)
            self.assertNotIn("目标遥测在线", icon.title)
            self.assertFalse(icon.menu.items[2].enabled)
            self.assertTrue(icon.menu.items[3].enabled)
            icon.menu.items[3].action(icon, None)
            runtime.value["connection"] = "online"
            timer.callback(timer)
            self.assertIn("目标遥测在线", icon.title)
            runtime.value["connection"] = "offline"
            timer.callback(timer)
            self.assertIn("暂无目标遥测证据", icon.title)
            icon.menu.items[-1].action(icon, None)
            timer.callback(timer)
            self.assertTrue(icon.stopped)
        runtime = self.run_fake(body)
        self.assertEqual(runtime.calls, [("collector/start", {}), "stop", "close"])

    def test_signal_sets_event_and_next_main_thread_tick_closes(self):
        def body(icon, timer, runtime, event):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertTrue(event.is_set())
            self.assertFalse(icon.stopped)
            timer.callback(timer)
            self.assertTrue(icon.stopped)
            timer.callback(timer)
        self.assertEqual(self.run_fake(body).calls, ["close"])

    def test_many_ticks_are_iterative_and_closing_disables_actions(self):
        def body(icon, timer, runtime, event):
            for phase in ("starting", "stopping", "working", "closing", "closed"):
                runtime.value["phase"] = phase
                timer.callback(timer)
                self.assertFalse(icon.menu.items[2].enabled)
            for _ in range(2000):
                timer.callback(timer)
        self.run_fake(body)

    def test_callback_error_is_safe_and_cleanup_runs(self):
        runtime = Runtime()
        def broken_status():
            raise RuntimeError("private account canary")
        runtime.status = broken_status
        def body(icon, timer, runtime, event):
            timer.callback(timer)
            self.assertTrue(event.is_set())
            self.assertTrue(icon.stopped)
        with self.assertRaisesRegex(service.MacServiceError, "^tray_start_failed$"):
            self.run_fake(body, runtime=runtime)

    def test_connection_labels_do_not_infer_mqtt_success(self):
        for connection in (None, "connecting", "auth_required", "offline"):
            label, color = service._presentation(dict(phase="collecting", reason="connected",
                                                      connection=connection))
            self.assertNotIn("目标遥测在线", label)
            self.assertNotIn("已连接", label)
            self.assertEqual(color, "#e4ac36")

    def test_preflight_imports_only_without_creating_native_objects(self):
        icon = unittest.mock.Mock(side_effect=AssertionError("must not construct tray"))
        timer = unittest.mock.Mock(side_effect=AssertionError("must not schedule timer"))
        modules = {"pystray": types.SimpleNamespace(Icon=icon),
                   "AppKit": types.ModuleType("AppKit"),
                   "Foundation": types.SimpleNamespace(NSTimer=timer)}
        with patch.dict(sys.modules, modules), patch.object(service.sys, "platform", "darwin"):
            self.assertIsNone(service.check_tray_available())
        icon.assert_not_called()
        timer.assert_not_called()

    def test_preflight_reports_safe_platform_and_dependency_errors(self):
        with patch.object(service.sys, "platform", "linux"), self.assertRaisesRegex(service.MacServiceError, "service_unsupported_platform"):
            service.check_tray_available()
        with patch.object(service.sys, "platform", "darwin"), patch.dict(sys.modules, {"pystray": None}), self.assertRaisesRegex(service.MacServiceError, "tray_dependency_unavailable"):
            service.check_tray_available()

    def test_idle_invalid_credentials_are_visible(self):
        for reason in ("credential_rejected", "stored_token_unavailable"):
            title, color = service._presentation(dict(phase="idle", reason=reason, configured=True))
            self.assertEqual(title, "需重新登录")
            self.assertEqual(color, "#e4ac36")

    def test_background_thread_rejected_before_native_import(self):
        errors = []
        def work():
            try:
                service.run_tray(Runtime(), "unused", threading.Event())
            except service.MacServiceError as error:
                errors.append(error.code)
        thread = threading.Thread(target=work)
        thread.start()
        thread.join()
        self.assertEqual(errors, ["service_main_thread_required"])

    def test_run_error_still_restores_signals_and_timer(self):
        def body(*_):
            raise RuntimeError("private account canary")
        old = signal.getsignal(signal.SIGTERM)
        with self.assertRaisesRegex(service.MacServiceError, "^tray_start_failed$"):
            self.run_fake(body)
        self.assertEqual(signal.getsignal(signal.SIGTERM), old)


class LaunchAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # macOS temporary-directory prefixes may themselves be symlinks.
        self.home = Path(self.temp.name).resolve()
        self.home_patch = patch.object(service.Path, "home", return_value=self.home)
        self.platform_patch = patch.object(service.sys, "platform", "darwin")
        self.home_patch.start()
        self.platform_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.addCleanup(self.platform_patch.stop)
        self.addCleanup(self.temp.cleanup)
        self.target = self.home / "Library/LaunchAgents" / (service.LABEL + ".plist")

    def install(self, **kwargs):
        with patch.object(service.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
            result = service.install_launch_agent(**kwargs)
        return result, run

    def test_plist_roundtrip_preserves_venv_and_escapes_paths(self):
        interpreter = self.home / "venv & <x>/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        settings = self.home / "settings & <x>.json"
        doc = plistlib.loads(service.launch_agent_data(interpreter, settings, 9876))
        self.assertEqual(doc["ProgramArguments"][0], str(interpreter))
        self.assertIn("--no-browser", doc["ProgramArguments"])
        self.assertEqual(doc["ProgramArguments"][-4:], ["--port", "9876", "--config", str(settings)])
        self.assertTrue(doc["RunAtLoad"])
        self.assertFalse(doc["KeepAlive"])

    def test_invalid_ports_rejected(self):
        for port in (True, 0, 65536, "8765", None):
            with self.subTest(port=port), self.assertRaisesRegex(service.MacServiceError, "service_invalid_argument"):
                service.launch_agent_data(sys.executable, None, port)

    def test_install_private_current_user_gui_and_no_overwrite(self):
        target, run = self.install(port=9876)
        self.assertEqual(target, self.target)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)])
        self.assertEqual(kwargs["timeout"], 15)
        before = target.read_bytes()
        with patch.object(service.subprocess, "run") as run, self.assertRaisesRegex(service.MacServiceError, "service_already_installed"):
            service.install_launch_agent()
        run.assert_not_called()
        self.assertEqual(target.read_bytes(), before)

    def test_bootstrap_failure_and_timeout_keep_installed_plist(self):
        for failure, code in ((types.SimpleNamespace(returncode=5), "service_bootstrap_failed_installed"),
                              (subprocess.TimeoutExpired("launchctl", 15), "service_bootstrap_unknown_installed")):
            with self.subTest(code=code):
                if self.target.exists():
                    self.target.unlink()
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch.object(service.subprocess, "run", **kwargs), self.assertRaisesRegex(service.MacServiceError, "^" + code + "$"):
                    service.install_launch_agent()
                self.assertTrue(service._owned_plist(self.target.read_bytes()))

    def test_symlink_parent_and_target_are_never_followed(self):
        outside = self.home / "outside"
        outside.mkdir()
        (self.home / "Library").symlink_to(outside, target_is_directory=True)
        with patch.object(service.subprocess, "run") as run, self.assertRaisesRegex(service.MacServiceError, "service_unsafe_path"):
            service.install_launch_agent()
        run.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])
        (self.home / "Library").unlink()
        self.target.parent.mkdir(parents=True)
        sentinel = outside / "sentinel"
        sentinel.write_text("keep")
        self.target.symlink_to(sentinel)
        with patch.object(service.subprocess, "run") as run, self.assertRaises(service.MacServiceError):
            service.uninstall_launch_agent()
        run.assert_not_called()
        self.assertEqual(sentinel.read_text(), "keep")

    def test_uninstall_validates_before_launchctl_and_removes_only_own_file(self):
        self.install()
        neighbor = self.target.parent / "other.plist"
        neighbor.write_bytes(b"keep")
        with patch.object(service.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
            self.assertEqual(service.uninstall_launch_agent(), self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(neighbor.read_bytes(), b"keep")
        self.assertEqual(run.call_args.args[0], ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{service.LABEL}"])
        self.target.write_bytes(plistlib.dumps({"Label": service.LABEL, "ProgramArguments": ["/bin/evil"]}))
        with patch.object(service.subprocess, "run") as run, self.assertRaisesRegex(service.MacServiceError, "service_foreign_agent"):
            service.uninstall_launch_agent()
        run.assert_not_called()
        self.assertTrue(self.target.exists())

    def test_uninstall_failure_retains_file_and_missing_is_noop(self):
        with patch.object(service.subprocess, "run") as run:
            self.assertIsNone(service.uninstall_launch_agent())
        run.assert_not_called()
        self.install()
        for failure in (types.SimpleNamespace(returncode=5), subprocess.TimeoutExpired("launchctl", 15)):
            kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with patch.object(service.subprocess, "run", **kwargs), self.assertRaisesRegex(service.MacServiceError, "service_uninstall_failed"):
                service.uninstall_launch_agent()
            self.assertTrue(self.target.exists())

    def test_changed_file_during_bootout_is_not_deleted(self):
        self.install()
        def replace(*args, **kwargs):
            replacement = self.target.with_suffix(".replacement")
            replacement.write_bytes(b"foreign replacement")
            replacement.replace(self.target)
            return types.SimpleNamespace(returncode=0)
        with patch.object(service.subprocess, "run", side_effect=replace), self.assertRaisesRegex(service.MacServiceError, "service_unsafe_path"):
            service.uninstall_launch_agent()
        self.assertEqual(self.target.read_bytes(), b"foreign replacement")

    def test_malformed_or_tampered_plist_never_boots_out(self):
        self.target.parent.mkdir(parents=True)
        original = plistlib.loads(service.launch_agent_data(sys.executable, None, 8765))
        tampered = dict(original, ProgramArguments=["/bin/evil"])
        for data in (b"<?xml version='1.0'?><plist><broken", plistlib.dumps(tampered),
                     plistlib.dumps(dict(original, KeepAlive=True))):
            self.target.write_bytes(data)
            with patch.object(service.subprocess, "run") as run, self.assertRaisesRegex(service.MacServiceError, "service_foreign_agent"):
                service.uninstall_launch_agent()
            run.assert_not_called()
            self.assertEqual(self.target.read_bytes(), data)

    def test_public_errors_only_expose_fixed_codes(self):
        self.assertEqual(str(service.MacServiceError("private secret")), "service_install_failed")


if __name__ == "__main__":
    unittest.main()
