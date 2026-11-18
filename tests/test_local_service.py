"""Real loopback + production controller/core, with synthetic HTTP/MQTT/stores."""
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Event, Thread, get_ident
import time
import unittest
from unittest.mock import patch

from bambu_monitor.auth import Response
from bambu_monitor.credentials import Credentials
from bambu_monitor.local_service import LocalControlServer, LocalServiceError
from bambu_monitor.service_runtime import ServiceRuntime
from bambu_monitor.session import FakeMqtt
from bambu_monitor.settings import Settings
from bambu_monitor.setup_controller import SetupController

CANARY = "SYNTHETIC-PRIVATE-9981"


class SyntheticHttp:
    def __init__(self):
        self.calls = []
        self.block_login, self.login_entered, self.release_login = False, Event(), Event()
        self.discoveries = 0
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append(get_ident())
        if url.endswith('/login'):
            self.login_entered.set()
            if self.block_login:
                self.release_login.wait(5)
            return Response(200, {"accessToken": CANARY})
        if url.endswith('/preference'):
            return Response(200, {"uid": "synthetic-user"})
        if url.endswith('/bind'):
            self.discoveries += 1
            return Response(200, {"devices": [{"dev_id": "SYNTHETIC-P1", "dev_product_name": "P1S",
                                               "dev_access_code": CANARY, "name": CANARY},
                                              {"dev_id": "SYNTHETIC-P2", "dev_product_name": "A1 mini"}]})
        return Response(200, {})

    def close(self):
        self.closed = True
        return True


class SyntheticMqtt(FakeMqtt):
    def __init__(self):
        super().__init__()
        self.connacked = self.subacked = False

    def pump(self, timeout=.1):
        if not self.connacked:
            self.connacked = True
            self.callbacks['on_connack'](True)
        elif self.subscriptions and not self.subacked:
            self.subacked = True
            self.callbacks['on_suback'](True)
        elif self.subacked:
            topic = self.subscriptions[0]
            self.callbacks['on_message'](topic, b'{"print":{"command":"push_status","nozzle_temper":0,"bed_temper":42}}',
                                         datetime.now(timezone.utc), time.monotonic())


def synthetic_controller(path, http=None, transports=None):
    http = http or SyntheticHttp()
    transports = transports if transports is not None else []
    def mqtt(_):
        transport = SyntheticMqtt()
        transports.append(transport)
        return transport
    return SetupController(Settings(path), http_factory=lambda _: http,
                           store_factory=lambda region, ref, memory_only=False: Credentials(region, ref, memory_only=True),
                           mqtt_factory=mqtt)


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        Event().wait(.01)
    raise AssertionError('synthetic operation did not settle')


class LocalServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.path = Path(self.folder.name).resolve() / 'provider.toml'
        self.http = SyntheticHttp()
        self.transports = []
        controller = synthetic_controller(self.path, self.http, self.transports)
        self.runtime = ServiceRuntime(controller=controller)
        self.server = LocalControlServer(('127.0.0.1', 0), runtime=self.runtime)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.release_login.set()
        self.runtime.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.folder.cleanup()

    def request(self, method, path, body=None, *, authenticated=True, origin=None, host=None, raw=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        request_headers = {'Host': host or self.server.host}
        if authenticated:
            request_headers['X-Bambu-Local-Session'] = self.server.capability
        if method == 'POST':
            request_headers.update({'Origin': origin or self.server.origin, 'Content-Type': 'application/json'})
            raw = json.dumps(body if body is not None else {}).encode() if raw is None else raw
        request_headers.update(headers or {})
        conn.request(method, path, raw, request_headers)
        response = conn.getresponse()
        data = response.read()
        result = response.status, data, dict(response.getheaders())
        conn.close()
        return result

    def action(self, command, data=None):
        status, body, _ = self.request('POST', '/api/' + command, data)
        self.assertIn(status, (200, 202))
        value = json.loads(body)
        if value.get('operation_id'):
            operation = until(lambda: (self.runtime.status()['operation'] or {}).get('done') and self.runtime.status()['operation'])
            return operation
        return value

    def prepare(self):
        self.assertTrue(self.action('setup/begin', {'region':'cn'})['ok'])
        self.assertTrue(self.action('setup/login', {'account':'person@example.invalid', 'credential':CANARY, 'method':'password'})['ok'])
        self.assertTrue(self.action('setup/select', {'printer_id':'SYNTHETIC-P1'})['ok'])
        self.assertTrue(self.action('setup/confirm', {'persistent':False})['ok'])

    def test_full_browser_api_setup_settings_collection_stop_logout(self):
        self.prepare()
        self.assertEqual(self.runtime.status()['setup_stage'], 'home')
        saved = self.path.read_bytes()
        for private in (CANARY.encode(), b'person@example.invalid', b'synthetic-user'):
            self.assertNotIn(private, saved)
        self.assertTrue(self.action('settings/save', {'liveness_seconds':90,'initial_delay_seconds':2,
                                                    'max_delay_seconds':60,'jitter':True})['ok'])
        self.assertEqual(Settings(self.path).load().liveness_seconds, 90)
        self.request('POST', '/api/collector/start')
        until(lambda: self.runtime.status()['connection'] == 'online')
        self.assertEqual(self.runtime.status()['metrics']['nozzle_celsius']['value'], 0)
        self.assertFalse(self.action('setup/logout')['ok'])
        self.assertEqual(self.action('collector/stop')['reason'], 'stopping')
        self.assertIsNone(self.runtime.status()['connection'])
        self.assertEqual(self.runtime.status()['metrics'], {})
        until(lambda: self.runtime.status()['phase'] == 'idle')
        self.assertTrue(self.transports[0].closed)
        self.assertTrue(self.action('setup/logout')['ok'])
        self.assertEqual(self.runtime.status()['setup_stage'], 'region')
        self.assertEqual(len(set(self.http.calls)), 1)  # One owner for auth/discovery/collection.
        self.assertNotEqual(self.http.calls[0], get_ident())
        self.assertNotIn(CANARY, json.dumps(self.runtime.status()))

    def test_assets_capability_host_origin_and_no_cors(self):
        for path in ('/', '/app.js', '/style.css'):
            status, body, headers = self.request('GET', path, authenticated=False)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertNotIn('Access-Control-Allow-Origin', headers)
            self.assertNotIn("unsafe-inline", headers['Content-Security-Policy'])
            self.assertNotIn(self.server.capability.encode(), body)
        self.assertEqual(self.request('GET','/api/status',authenticated=False)[0],403)
        self.assertEqual(self.request('GET','/api/status',host='evil.example')[0],403)
        self.assertEqual(self.request('POST','/api/setup/begin',{'region':'cn'},origin='http://evil.example')[0],403)
        self.assertEqual(self.request('GET','/api/setup/logout')[0],404)
        self.assertEqual(self.request('GET','/../../pyproject.toml')[0],404)

    def test_malformed_inputs_and_unrecognized_fields_cannot_login(self):
        for raw in (b'{"region":"cn","region":"global"}', b'{"region":NaN}', b'[' + b'[' * 2000):
            self.assertEqual(self.request('POST','/api/setup/begin',raw=raw)[0],400)
        result = self.action('setup/begin', {'region':'cn','password':CANARY})
        self.assertFalse(result['ok'])
        self.assertFalse(self.http.calls)
        self.assertEqual(self.request('POST','/api/setup/begin',{'region':'cn'},headers={'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.request('POST','/api/setup/begin',raw=b'',headers={'Content-Length':'65537'})[0],413)

    def test_blocking_login_keeps_http_status_responsive_and_close_cancels_followup(self):
        self.action('setup/begin', {'region':'cn'})
        self.http.block_login = True
        self.request('POST', '/api/setup/login', {'account':'person@example.invalid','credential':CANARY,'method':'password'})
        self.assertTrue(self.http.login_entered.wait(1))
        self.assertEqual(self.request('GET','/api/status')[0],200)
        self.assertFalse(self.action('setup/send-code',{'account':'person@example.invalid'})['ok'])
        self.assertFalse(self.runtime.close(.01))
        self.assertEqual(self.runtime.status()['phase'],'closing')
        self.http.release_login.set()
        self.assertTrue(self.runtime.close(2))
        self.assertEqual(self.http.discoveries,0)
        self.assertTrue(self.runtime.close())
        self.assertEqual(self.runtime.status()['phase'],'closed')

    def test_stop_during_restore_prevents_mqtt_and_old_publish(self):
        self.prepare()
        controller = self.runtime._controller
        entered, release = Event(), Event()
        original = controller.restore
        def restore():
            entered.set()
            release.wait(2)
            return original()
        with patch.object(controller, 'restore', restore):
            self.request('POST','/api/collector/start')
            self.assertTrue(entered.wait(1))
            self.action('collector/stop')
            release.set()
            until(lambda: self.runtime.status()['phase'] == 'idle')
        self.assertEqual(self.transports,[])
        self.runtime._emit({'snapshot':{'connection':{'state':'online'},'metrics':{'canary':CANARY}}})
        self.assertIsNone(self.runtime.status()['connection'])

    def test_cleanup_failure_and_exception_are_safe(self):
        with patch.object(self.runtime._controller, 'close', return_value=False):
            self.assertFalse(self.runtime.close())
        self.assertEqual(self.runtime.status()['reason'],'cleanup_failed')
        self.assertEqual(self.runtime.submit('setup/begin',{'region':'cn'})['reason'],'closing')

    def test_server_rejects_non_loopback_bind(self):
        with self.assertRaises(LocalServiceError):
            LocalControlServer(('0.0.0.0',0), runtime=self.runtime)


if __name__ == '__main__':
    unittest.main()
