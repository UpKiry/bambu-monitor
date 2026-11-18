"""Synthetic transactional setup acceptance; no accounts or OS credential calls."""
from dataclasses import replace
from threading import Event
import unittest
from unittest.mock import patch

from bambu_monitor.auth import FakeHttp, Response, Secret
from bambu_monitor.config import Config
from bambu_monitor.credentials import Credentials
from bambu_monitor.setup_controller import SetupController

CANARY = "SYNTHETIC-CONTROLLER-SECRET-ae4b"


class Settings:
    def __init__(self, config=None):
        self.config = config
        self.saved = []
        self.fail = False
    def load(self):
        return self.config
    def save(self, config):
        if self.fail:
            raise OSError(CANARY)
        self.saved.append(config)
        self.config = config


class Backend:
    def __init__(self):
        self.values = {}
        self.fail_set = self.fail_delete = False
    def get_password(self, service, key):
        return self.values.get((service, key))
    def set_password(self, service, key, value):
        if self.fail_set:
            raise OSError(CANARY)
        self.values[service, key] = value
    def delete_password(self, service, key):
        if self.fail_delete:
            raise OSError(CANARY)
        self.values.pop((service, key), None)


def token(value=CANARY):
    return Response(200, {"accessToken": value})


def uid():
    return Response(200, {"uid": "12345"})


def devices(*ids):
    return Response(200, {"devices": [{"dev_id": printer, "dev_model_name": "A1 mini"} for printer in ids]})


class SetupControllerTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.backend = Backend()
        self.https = []
        self.scripts = []
        self.stores = []
        def http_factory(region):
            http = FakeHttp(self.scripts.pop(0) if self.scripts else ())
            self.https.append(http)
            return http
        def store_factory(region, reference, *, memory_only=False):
            store = Credentials(region, reference, memory_only=memory_only, backend_factory=lambda: self.backend)
            self.stores.append(store)
            return store
        self.http_factory, self.store_factory = http_factory, store_factory
        self.controller = SetupController(self.settings, http_factory, store_factory, mqtt_factory=lambda _: None)

    def tearDown(self):
        for http in self.https:
            if getattr(http, "broken_close", False):
                http.close = FakeHttp.close.__get__(http)
        self.controller.close()

    def prepare(self, *, region="cn", selected="P1", extra=()):
        self.scripts.append([token(), uid(), devices("P1", "P2"), *extra])
        self.assertTrue(self.controller.begin(region))
        self.assertTrue(self.controller.login(Secret("person@example.test"), Secret(CANARY)))
        self.assertEqual([device.id for device in self.controller.discover()], ["P1", "P2"])
        self.assertTrue(self.controller.select(selected))

    def commit(self, *, persistent=True):
        self.prepare(extra=[uid()])
        self.assertTrue(self.controller.confirm(persistent))
        return self.controller.config

    def test_initial_load_no_network_or_credential_access(self):
        self.assertIsNone(self.controller.config)
        self.assertEqual(self.controller.reason, "unconfigured")
        self.assertEqual(self.https, [])
        self.assertEqual(self.stores, [])
        self.assertFalse(self.controller.restore())
        self.assertEqual(self.https, [])

    def test_draft_login_only_memory_until_confirm(self):
        self.prepare(extra=[uid()])
        draft_ref = self.controller.config.credential_ref
        self.assertFalse(self.controller.persistent)
        self.assertEqual(self.backend.values, {})
        self.assertEqual(self.settings.saved, [])
        self.assertTrue(self.controller.confirm(True))
        self.assertEqual(self.controller.selected.id, "P1")
        self.assertEqual(self.controller.config.printer_id, "P1")
        self.assertNotEqual(self.controller.config.credential_ref, draft_ref)
        self.assertEqual(len(self.backend.values), 1)
        self.assertTrue(self.controller.persistent)
        self.assertNotIn(CANARY, repr(self.settings.saved) + repr(self.controller))
        self.assertEqual(self.settings.config, self.controller.config)

    def test_cancel_uncommitted_first_setup_discards_token_and_closes_http(self):
        self.prepare()
        auth = self.controller.auth
        self.assertTrue(self.controller.cancel())
        self.assertIsNone(self.controller.config)
        self.assertIsNone(self.controller.auth)
        self.assertIsNone(auth._token)
        self.assertTrue(self.https[-1].closed)
        self.assertEqual(self.backend.values, {})
        self.assertEqual(self.settings.saved, [])

    def test_cancel_new_account_keeps_old_auth_token_device_and_settings(self):
        old_config = self.commit()
        old_auth, old_values = self.controller.auth, dict(self.backend.values)
        self.scripts.append([token("new-secret"), uid(), devices("P2")])
        self.assertTrue(self.controller.begin("global"))
        self.assertTrue(self.controller.login("new@example.test", "new-password"))
        self.assertEqual([item.id for item in self.controller.discover()], ["P2"])
        self.assertTrue(self.controller.select("P2"))
        self.assertTrue(self.controller.cancel())
        self.assertIs(self.controller.auth, old_auth)
        self.assertEqual(self.controller.config, old_config)
        self.assertEqual(self.controller.selected.id, "P1")
        self.assertEqual(self.backend.values, old_values)
        self.assertEqual(self.settings.config, old_config)

    def test_settings_failure_cleans_new_os_slot_and_preserves_previous_session(self):
        old_config = self.commit()
        old_auth, old_values = self.controller.auth, dict(self.backend.values)
        self.prepare(region="global", selected="P2", extra=[uid()])
        self.settings.fail = True
        self.assertFalse(self.controller.confirm(True))
        self.assertEqual(self.controller.reason, "settings_save_failed")
        self.assertEqual(self.settings.config, old_config)
        self.assertEqual(self.backend.values, old_values)
        self.assertTrue(self.controller.cancel())
        self.assertIs(self.controller.auth, old_auth)
        self.assertEqual(self.controller.config, old_config)

    def test_storage_failure_falls_back_to_current_memory_and_never_plaintext(self):
        self.prepare(extra=[uid()])
        self.backend.fail_set = True
        self.assertTrue(self.controller.confirm(True))
        self.assertFalse(self.controller.persistent)
        self.assertEqual(self.controller.reason, "keyring_unavailable_session_memory")
        self.assertIsNotNone(self.controller.auth._token)
        self.assertEqual(self.backend.values, {})
        self.assertEqual(len(self.settings.saved), 1)

    def test_only_session_mode_never_attempts_native_storage(self):
        self.commit(persistent=False)
        self.assertFalse(self.controller.persistent)
        self.assertEqual(self.backend.values, {})
        self.assertTrue(all(not store.persistent for store in self.stores))
        self.assertEqual(self.controller.reason, "configured_session_only")

    def test_restore_same_process_preserves_session_only_and_checks_actual_device(self):
        config = self.commit(persistent=False)
        auth = self.controller.auth
        http = self.https[-1]
        http.responses.extend([uid(), devices("P2", "P1")])
        self.assertTrue(self.controller.restore())
        self.assertIs(self.controller.auth, auth)
        self.assertEqual(self.controller.config, config)
        self.assertEqual(self.controller.selected.id, "P1")
        self.assertFalse(self.controller.persistent)

    def test_restore_new_process_uses_os_token_and_explicit_id_not_first_item(self):
        config = self.commit()
        self.assertTrue(self.controller.close())
        self.scripts.append([uid(), devices("P2", "P1")])
        self.controller = SetupController(self.settings, self.http_factory, self.store_factory, lambda _: None)
        self.assertTrue(self.controller.restore())
        self.assertEqual(self.controller.config, config)
        self.assertEqual(self.controller.selected.id, "P1")
        self.assertTrue(self.controller.persistent)
        self.assertEqual(len(self.settings.saved), 1)

    def test_new_process_cannot_restore_session_only_or_send_any_http(self):
        self.commit(persistent=False)
        self.controller.close()
        self.controller = SetupController(self.settings, self.http_factory, self.store_factory, lambda _: None)
        self.assertFalse(self.controller.restore())
        self.assertEqual(self.https[-1].requests, [])
        self.assertIsNone(self.controller.selected)

    def test_missing_selected_device_does_not_switch_to_another(self):
        self.commit()
        self.https[-1].responses.extend([uid(), devices("P2")])
        self.assertFalse(self.controller.restore())
        self.assertIsNone(self.controller.selected)
        self.assertEqual(self.controller.reason, "selected_device_unavailable")
        self.assertEqual(self.controller.config.printer_id, "P1")
        self.assertEqual([item.id for item in self.controller.devices], ["P2"])

    def test_restore_401_revokes_token_and_does_not_keep_old_selection(self):
        self.commit()
        self.https[-1].responses.append(Response(401, {}))
        self.assertFalse(self.controller.restore())
        self.assertEqual(self.backend.values, {})
        self.assertIsNone(self.controller.selected)
        self.assertEqual(self.controller.reason, "credential_rejected")

    def test_change_device_without_draft_preserves_auth_and_reference(self):
        config = self.commit()
        auth, original_values = self.controller.auth, dict(self.backend.values)
        self.https[-1].responses.extend([uid(), devices("P2", "P1"), uid()])
        self.controller.discover()
        self.assertTrue(self.controller.select("P2"))
        self.assertEqual(self.controller.run(Event(), lambda _: None), 2)
        self.assertEqual(self.controller.reason, "settings_not_confirmed")
        self.assertTrue(self.controller.confirm(True))
        self.assertIs(self.controller.auth, auth)
        self.assertEqual(self.controller.config.credential_ref, config.credential_ref)
        self.assertEqual(self.controller.config.printer_id, "P2")
        self.assertEqual(self.backend.values, original_values)

    def test_cancel_device_change_restores_committed_selected_device(self):
        config = self.commit()
        self.assertTrue(self.controller.select("P2"))
        self.assertEqual(self.controller.selected.id, "P2")
        self.assertTrue(self.controller.cancel())
        self.assertEqual(self.controller.selected.id, "P1")
        self.assertEqual(self.controller.config, config)

    def test_confirm_requires_current_discovery_and_validated_private_uid(self):
        self.scripts.append([token()])
        self.controller.begin("cn")
        self.controller.login("person@example.test", CANARY)
        self.assertFalse(self.controller.confirm(True))
        self.assertEqual(self.controller.reason, "selection_required")
        self.assertFalse(self.controller.select("P1"))
        self.assertEqual(self.settings.saved, [])
        self.assertEqual(self.backend.values, {})

    def test_send_code_is_only_one_explicit_call_and_login_branches_propagate(self):
        self.scripts.append([Response(200, {}), Response(200, {"loginType": "verifyCode"}), Response(200, {"loginType": "tfa"})])
        self.controller.begin("cn")
        self.assertTrue(self.controller.send_code(Secret("person@example.test")))
        self.assertEqual(len(self.https[-1].requests), 1)
        self.assertFalse(self.controller.login("person@example.test", CANARY))
        self.assertEqual(self.controller.reason, "code_required")
        self.assertFalse(self.controller.login("person@example.test", "123456", method="code"))
        self.assertEqual(self.controller.reason, "mfa_unsupported")
        self.assertEqual(len(self.https[-1].requests), 3)
        self.assertEqual(self.settings.saved, [])

    def test_confirmed_replacement_removes_previous_os_scope_only_after_save(self):
        old = self.commit()
        old_http = self.https[-1]
        self.prepare(region="global", selected="P2", extra=[uid()])
        self.assertTrue(self.controller.confirm(True))
        self.assertNotEqual(self.controller.config.credential_ref, old.credential_ref)
        self.assertEqual(self.controller.config.region, "global")
        self.assertEqual(len(self.backend.values), 1)
        self.assertTrue(old_http.closed)

    def test_settings_failure_with_failed_rollback_reports_incomplete_cleanup(self):
        self.prepare(extra=[uid()])
        self.settings.fail = True
        self.backend.fail_delete = True
        self.assertFalse(self.controller.confirm(True))
        self.assertEqual(self.controller.reason, "settings_save_failed_cleanup_failed")
        self.assertIsNone(self.settings.config)
        self.assertEqual(len(self.backend.values), 1)
        self.assertNotIn(CANARY, self.controller.reason)

    def test_logout_revokes_own_token_but_keeps_regular_settings(self):
        config = self.commit()
        self.assertTrue(self.controller.logout())
        self.assertEqual(self.backend.values, {})
        self.assertIsNone(self.controller.auth)
        self.assertIsNone(self.controller.selected)
        self.assertEqual(self.settings.config, config)
        self.assertEqual(self.controller.reason, "logged_out")

    def test_logout_failure_is_reported_not_success(self):
        self.commit()
        self.backend.fail_delete = True
        self.assertFalse(self.controller.logout())
        self.assertEqual(self.controller.reason, "credential_revoke_failed")
        self.assertEqual(len(self.backend.values), 1)

    def test_run_consumes_common_runner_and_auth_rejection_revokes_saved_token(self):
        self.commit()
        self.https[-1].responses.extend([uid(), devices("P1")])
        seen = []
        def runner(session, uid_value, token_value, *, stop_event, emit):
            seen.append((session.config.printer_id, uid_value.value, token_value.value))
            emit({"snapshot": {"connection": {"state": "auth_required"}}})
            return 3
        output = []
        with patch("bambu_monitor.setup_controller.collect", side_effect=runner):
            self.assertEqual(self.controller.run(Event(), output.append), 3)
        self.assertEqual(seen, [("P1", "12345", CANARY)])
        self.assertEqual(self.backend.values, {})
        self.assertIsNone(self.controller.selected)
        self.assertEqual(self.controller.reason, "credential_rejected")
        self.assertEqual(len(output), 1)

    def test_run_already_stopped_has_no_http_or_mqtt_side_effects(self):
        self.commit()
        http = self.https[-1]
        before = len(http.requests)
        stop = Event()
        stop.set()
        self.assertEqual(self.controller.run(stop, lambda _: self.fail("must not emit")), 0)
        self.assertEqual(len(http.requests), before)
        self.assertEqual(self.controller.reason, "stopped")

    def test_close_normal_preserves_os_token_and_is_terminal(self):
        self.commit()
        self.assertTrue(self.controller.close())
        self.assertTrue(all(http.closed for http in self.https))
        self.assertEqual(len(self.backend.values), 1)
        self.assertFalse(self.controller.begin("cn"))
        self.assertEqual(self.controller.reason, "closed")

    def test_update_ordinary_settings_before_restore_needs_no_login_or_network(self):
        self.settings.config = Config("cn", "P1", "saved-slot")
        self.controller = SetupController(self.settings, self.http_factory, self.store_factory, lambda _: None)
        updated = replace(self.controller.config, liveness_seconds=120)
        self.assertTrue(self.controller.update_settings(updated))
        self.assertEqual(self.controller.config, updated)
        self.assertEqual(self.settings.config, updated)
        self.assertIsNone(self.controller.auth)
        self.assertEqual(self.https, [])
        self.assertEqual(self.stores, [])

    def test_update_settings_changes_only_confirmed_same_identity_after_save(self):
        config = self.commit()
        changed = replace(config, liveness_seconds=90, max_delay_seconds=120)
        self.assertTrue(self.controller.update_settings(changed))
        self.assertEqual(self.controller.config, changed)
        self.assertEqual(self.controller._committed.config, changed)
        self.settings.fail = True
        self.assertFalse(self.controller.update_settings(replace(changed, liveness_seconds=30)))
        self.assertEqual(self.controller.config, changed)
        self.assertEqual(self.controller._committed.config, changed)
        self.assertFalse(self.controller.update_settings(replace(config, printer_id="P2")))
        self.assertEqual(self.controller.reason, "settings_identity_change_requires_setup")

    def test_post_commit_settings_error_retains_token_and_actual_saved_selection(self):
        self.prepare(extra=[uid()])
        original = self.settings.save
        def saved_then_failed(config):
            original(config)
            raise OSError(CANARY)
        self.settings.save = saved_then_failed
        self.assertFalse(self.controller.confirm(True))
        self.assertEqual(self.controller.reason, "settings_saved_verification_failed")
        self.assertEqual(self.controller.config, self.settings.config)
        self.assertEqual(self.controller.selected.id, "P1")
        self.assertTrue(self.controller.persistent)
        self.assertEqual(len(self.backend.values), 1)
        self.assertTrue(self.controller.cancel())
        self.assertEqual(len(self.backend.values), 1)

    def test_unknown_save_outcome_does_not_delete_possibly_committed_os_token(self):
        self.prepare(extra=[uid()])
        self.settings.fail = True
        def unreadable():
            raise OSError(CANARY)
        self.settings.load = unreadable
        self.assertFalse(self.controller.confirm(True))
        self.assertEqual(self.controller.reason, "settings_save_outcome_unknown")
        self.assertEqual(len(self.backend.values), 1)
        self.assertFalse(self.controller.cancel())
        self.assertFalse(self.controller.begin("global"))
        self.assertTrue(self.controller.close())
        self.assertEqual(self.controller.reason, "closed_settings_outcome_unknown")
        self.assertEqual(len(self.backend.values), 1)

    def test_stop_during_run_uid_check_prevents_discovery_and_mqtt(self):
        self.commit()
        stop = Event()
        http = self.https[-1]
        http.responses.append(uid())
        original = http.request
        before = len(http.requests)
        def stopping(*args, **kwargs):
            result = original(*args, **kwargs)
            stop.set()
            return result
        http.request = stopping
        with patch("bambu_monitor.setup_controller.collect") as runner:
            self.assertEqual(self.controller.run(stop, lambda _: None), 0)
        self.assertEqual(len(http.requests), before + 1)
        runner.assert_not_called()
        self.assertEqual(self.controller.reason, "stopped")
        self.assertFalse(self.controller._running)

    def test_collect_cleanup_failure_retains_session_and_gates_reopening(self):
        from bambu_monitor.session import FakeMqtt
        self.commit()
        self.https[-1].responses.extend([uid(), devices("P1")])
        transport = FakeMqtt()
        self.controller._mqtt_factory = lambda _: transport
        original = transport.close
        def broken():
            raise OSError(CANARY)
        transport.close = broken
        def cleanup_failed(session, *args, **kwargs):
            session.start(uid=Secret("123"), token=Secret(CANARY), monotonic=0)
            self.assertFalse(session.stop())
            raise RuntimeError(CANARY)
        with patch("bambu_monitor.setup_controller.collect", side_effect=cleanup_failed):
            self.assertEqual(self.controller.run(Event(), lambda _: None), 2)
        self.assertEqual(self.controller.reason, "cleanup_failed")
        self.assertFalse(self.controller.begin("global"))
        self.assertFalse(self.controller.close())
        self.assertEqual(len(self.controller._failed_sessions), 1)
        transport.close = original
        self.assertTrue(self.controller.close())
        self.assertEqual(self.controller._failed_sessions, [])

    def test_real_common_collect_stops_and_closes_adapter_on_emit_request(self):
        from bambu_monitor.session import FakeMqtt
        self.commit(persistent=False)
        self.https[-1].responses.extend([uid(), devices("P1")])
        class Pump(FakeMqtt):
            def pump(self, timeout=.1):
                self.callbacks["on_connack"](True)
        transport = Pump()
        self.controller._mqtt_factory = lambda _: transport
        stop, output = Event(), []
        def emit(item):
            output.append(item)
            stop.set()
        self.assertEqual(self.controller.run(stop, emit), 0)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["snapshot"]["printer"]["id"], "P1")
        self.assertTrue(transport.closed)
        self.assertEqual(self.controller.reason, "stopped")
        self.assertIsNotNone(self.controller.auth._token)

    def test_http_cleanup_failure_cannot_be_ignored_or_start_new_draft(self):
        self.prepare()
        http = self.https[-1]
        http.broken_close = True
        def failed():
            raise OSError(CANARY)
        http.close = failed
        self.assertFalse(self.controller.cancel())
        self.assertEqual(self.controller.reason, "cleanup_failed")
        self.assertFalse(self.controller.begin("global"))
        self.assertEqual(len(self.https), 1)
        self.assertFalse(self.controller.close())
        http.broken_close = False
        http.close = FakeHttp.close.__get__(http)
        self.assertTrue(self.controller.close())


if __name__ == "__main__":
    unittest.main()
