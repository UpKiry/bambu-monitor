"""GUI-independent owner/mailbox invariants and opt-in real Tk interactions."""
import os
from threading import Event
import unittest

from bambu_monitor.setup_gui import Worker, metric_text, friendly


class WorkerTests(unittest.TestCase):
    def test_owner_is_serial_and_stoppable(self):
        worker = Worker()
        entered = Event()
        finished = Event()

        def collect():
            entered.set()
            worker.stop.wait(2)
            finished.set()
            return 3
        self.assertTrue(worker.submit(collect))
        self.assertTrue(entered.wait(2))
        self.assertFalse(worker.submit(lambda: self.fail("overlapping owner")))
        worker.stop.set()
        self.assertTrue(finished.wait(2))
        worker.thread.join(2)
        self.assertEqual(worker.result(), (True, 3))
        self.assertFalse(worker.busy)

    def test_snapshots_coalesce_and_exception_text_is_dropped(self):
        worker = Worker()
        for index in range(10000):
            worker.emit({"sequence": index})
        self.assertEqual(worker.snapshot(), {"sequence": 9999})
        self.assertIsNone(worker.snapshot())
        def fail():
            raise RuntimeError("canary-private-account-response")
        worker.submit(fail)
        worker.thread.join(2)
        self.assertEqual(worker.result(), (False, None))
        self.assertNotIn("canary", friendly("canary-private-account-response"))

    def test_quality_is_visible_and_zero_is_not_unknown(self):
        self.assertEqual(metric_text({"value": 0, "quality": "valid"}, "%"), "0%")
        self.assertIn("旧值", metric_text({"value": 0, "quality": "stale"}))
        self.assertEqual(metric_text({"value": None, "quality": "unsupported"}), "不支持")
        self.assertEqual(metric_text({"value": None, "quality": "unknown"}), "等待数据")


@unittest.skipUnless(os.environ.get("BAMBU_TEST_GUI") == "1", "real Tk checks require an explicit graphical session")
class TkWindowTests(unittest.TestCase):
    def tearDown(self):
        # Destroyed Tk windows keep callback cycles; collect on their owning thread
        # before another test starts a worker that might trigger cyclic GC.
        import gc
        gc.collect()

    def test_full_setup_save_cancel_start_stop_with_fake_cloud(self):
        import tkinter as tk
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from dataclasses import replace
        import time
        from bambu_monitor.settings import Settings
        from bambu_monitor.setup_controller import SetupController
        from bambu_monitor.setup_gui import SetupWindow
        from bambu_monitor.auth import Response
        from bambu_monitor.credentials import Credentials
        from bambu_monitor.session import FakeMqtt

        class Http:
            def request(self, method, url, **kwargs):
                if url.endswith('/login'):
                    return Response(200, {"accessToken": "synthetic-token-never-real"})
                if url.endswith('/preference'):
                    return Response(200, {"uid": "synthetic-user"})
                return Response(200, {"devices": [{"dev_id": "SYNTHETIC-P1", "dev_product_name": "P1S"}]})
            def close(self):
                pass
        class Mqtt(FakeMqtt):
            def pump(self, timeout=.1):
                pass
        transports = []
        def mqtt_factory(_):
            mqtt = Mqtt()
            transports.append(mqtt)
            return mqtt
        with TemporaryDirectory() as folder:
            path = Path(folder).resolve() / "provider.toml"
            controller = SetupController(Settings(path), http_factory=lambda _: Http(),
                store_factory=lambda region, ref, memory_only=False: Credentials(region, ref, memory_only=True),
                mqtt_factory=mqtt_factory)
            root = tk.Tk()
            root.withdraw()
            window = SetupWindow(root, controller)
            def until(predicate):
                deadline = time.monotonic() + 4
                while not predicate() and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertTrue(predicate())
            def settle():
                until(lambda: not window.worker.busy)
            try:
                window.begin("cn")
                settle()
                window.account.insert(0, "synthetic@example.invalid")
                window.credential.insert(0, "synthetic-password")
                window.login()
                settle()
                self.assertEqual(window.stage, "devices")
                window.device_choice.set("SYNTHETIC-P1")
                window.select()
                settle()
                self.assertEqual(window.stage, "confirm")
                self.assertFalse(path.exists())
                window.persistent.set(False)
                window.confirm()
                settle()
                self.assertEqual(window.stage, "home")
                saved = path.read_bytes()
                self.assertNotIn(b"synthetic-token", saved)
                self.assertNotIn(b"synthetic-password", saved)
                self.assertNotIn(b"example.invalid", saved)
                window.go("settings")
                window.liveness.delete(0, "end")
                window.liveness.insert(0, "90")
                window.save_settings()
                settle()
                self.assertEqual(controller.config.liveness_seconds, 90)
                self.assertEqual(Settings(path).load().liveness_seconds, 90)
                saved = path.read_bytes()
                window.begin("global")
                settle()
                window.cancel()
                settle()
                self.assertEqual(path.read_bytes(), saved)
                self.assertEqual(controller.config.region, "cn")
                window.start()
                until(lambda: bool(transports))
                self.assertTrue(window.worker.busy)
                window.worker.emit({"snapshot": {"connection": {"state": "online"}, "metrics": {
                    "nozzle_celsius": {"value": 220, "quality": "valid"}}}})
                window.stop()
                root.update()
                self.assertNotIn("220", window.status.get())
                settle()
                self.assertTrue(transports[0].closed)
                self.assertFalse(window.collecting)
                self.assertIn("已停止", window.status.get())
                window.close()
                settle()
                self.assertTrue(window.destroyed)
            finally:
                window.worker.stop.set()
                if window.worker.thread is not None:
                    window.worker.thread.join(4)
                if not window.destroyed:
                    root.destroy()
                    controller.close()

    def test_cn_phone_code_and_global_email_controls(self):
        import tkinter as tk
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import time
        from bambu_monitor.settings import Settings
        from bambu_monitor.setup_controller import SetupController
        from bambu_monitor.setup_gui import SetupWindow
        from bambu_monitor.auth import Response

        calls = []
        class Http:
            def request(self, method, url, **kwargs):
                calls.append((url, kwargs.get("body")))
                return Response(200, {"loginType": "verifyCode"} if url.endswith("/login") else {})
            def close(self):
                pass
        with TemporaryDirectory() as folder:
            controller = SetupController(Settings(Path(folder).resolve() / "provider.toml"),
                                         http_factory=lambda _: Http())
            root = tk.Tk()
            root.withdraw()
            window = SetupWindow(root, controller)
            def settle():
                deadline = time.monotonic() + 3
                while window.worker.busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertFalse(window.worker.busy)
            def labels():
                return [child.cget("text") for child in window.shell.winfo_children()
                        if isinstance(child, tk.Label)]
            try:
                window.begin("cn")
                settle()
                self.assertIn("手机号或邮箱", labels())
                window.account.insert(0, "13800000000")
                window.credential.insert(0, "synthetic-password")
                window.login()
                settle()
                self.assertEqual(window.method, "code")
                self.assertEqual(window.account.get(), "13800000000")
                self.assertEqual(len(calls), 1)
                window.send_code()
                settle()
                self.assertEqual(calls[-1], ("https://bambulab.cn/api/v1/user-service/user/sendsmscode",
                                            {"phone": "13800000000", "type": "codeLogin"}))
                self.assertIn("短信", window.notice.get())
                window.toggle_method()
                self.assertEqual(window.account.get(), "13800000000")
                self.assertEqual(window.credential.get(), "")
                window.begin("global")
                settle()
                self.assertIn("邮箱", labels())
                self.assertNotIn("手机号或邮箱", labels())
                self.assertEqual(window.account.get(), "")
                self.assertFalse((Path(folder) / "provider.toml").exists())
            finally:
                if window.worker.thread is not None:
                    window.worker.thread.join(4)
                    window.worker.result()
                root.destroy()
                controller.close()

    def test_region_login_and_close_use_actual_window(self):
        import tkinter as tk
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import time
        from bambu_monitor.settings import Settings
        from bambu_monitor.setup_controller import SetupController
        from bambu_monitor.setup_gui import SetupWindow
        from bambu_monitor.auth import FakeHttp, Response

        with TemporaryDirectory() as folder:
            controller = SetupController(Settings(Path(folder).resolve() / "provider.toml"),
                                         http_factory=lambda _: FakeHttp([Response(200, {"loginType": "verifyCode"})]))
            root = tk.Tk()
            root.withdraw()
            window = SetupWindow(root, controller)
            def settle():
                deadline = time.monotonic() + 3
                while window.worker.busy and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertFalse(window.worker.busy)
            try:
                window.begin("cn")
                settle()
                self.assertEqual(window.stage, "login")
                window.account.insert(0, "synthetic@example.invalid")
                window.credential.insert(0, "synthetic-password")
                window.login()
                self.assertEqual(window.credential.get(), "")
                settle()
                self.assertEqual(window.method, "code")
                self.assertEqual(window.account.get(), "synthetic@example.invalid")
                self.assertFalse((Path(folder) / "provider.toml").exists())
                window.close()
                settle()
                self.assertTrue(window.destroyed)
            finally:
                if not window.destroyed:
                    root.destroy()
                    controller.close()


if __name__ == "__main__":
    unittest.main()
