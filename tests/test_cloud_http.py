"""No-account HTTP tests using the production parser and scripted connections."""
from collections import deque
import importlib
import json
import os
import socket
import ssl
from threading import Event, Thread
import traceback
import unittest
from unittest.mock import patch

from bambu_monitor.auth import CloudAuth, Response, Secret
from bambu_monitor.cloud_http import BambuHttp, HTTPError, LOGIN, EMAIL_CODE, SMS_CODE, BIND, PREFERENCE

BASE = "https://api.bambulab.cn"
CANARY = "SYNTHETIC-HTTP-CANARY-DO-NOT-EXPORT"
LOGIN_BODY = {"account": "synthetic@example.invalid", "password": "synthetic-password", "apiError": ""}


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class FakeSocket:
    def __init__(self):
        self.timeouts = []
        self.shutdowns = []
        self.release = None

    def settimeout(self, value):
        self.timeouts.append(value)

    def shutdown(self, how):
        self.shutdowns.append(how)
        if self.release is not None:
            self.release.set()


class WireResponse:
    def __init__(self, body=b'{"accessToken":"synthetic-token"}', *, status=200, headers=None,
                 chunks=None, on_read=None):
        self.status = status
        self.raw = body
        self.headers = headers if headers is not None else [("Content-Type", "application/json")]
        self.chunks = deque(chunks) if chunks is not None else None
        self.on_read = on_read
        self.read_calls = []

    def getheaders(self):
        return self.headers

    def read1(self, amount):
        self.read_calls.append(amount)
        if self.on_read is not None:
            self.on_read()
        if self.chunks is not None:
            return self.chunks.popleft() if self.chunks else b""
        value, self.raw = self.raw[:amount], self.raw[amount:]
        return value


class Connection:
    def __init__(self, response=None, *, fail=None, on_connect=None):
        self.sock = None
        self.timeout = None
        self.socket = FakeSocket()
        self.response = response if response is not None else WireResponse()
        self.fail = fail
        self.on_connect = on_connect
        self.calls = []
        self.closed = 0

    def connect(self):
        self.calls.append("connect")
        if self.on_connect is not None:
            self.on_connect()
        self.sock = self.socket
        if self.fail is not None:
            raise self.fail

    def request(self, method, path, *, body, headers):
        # Tests may inspect synthetic wire credentials; nothing here is a log/export.
        self.calls.append((method, path, body, headers))

    def getresponse(self):
        self.calls.append("headers")
        return self.response

    def close(self):
        self.closed += 1


class Factory:
    def __init__(self, connections=()):
        self.connections = deque(connections)
        self.calls = []

    def __call__(self, host, **kwargs):
        self.calls.append((host, kwargs))
        return self.connections.popleft()


class CloudHttpTests(unittest.TestCase):
    def client(self, response=None, *, clock=None, fail=None, maximum=65536):
        connection = Connection(response, fail=fail)
        factory = Factory([connection])
        client = BambuHttp("cn", connection_factory=factory, clock=clock or Clock(), max_response_bytes=maximum)
        return client, connection, factory

    def login(self, client, **kwargs):
        return client.request("POST", BASE + LOGIN, body=dict(LOGIN_BODY), **kwargs)

    def error(self, expected, operation):
        with self.assertRaises(HTTPError) as caught:
            operation()
        self.assertEqual(caught.exception.code, expected)
        self.assertEqual(str(caught.exception), expected)
        self.assertNotIn(CANARY, repr(caught.exception))
        rendered = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn(CANARY, rendered)
        self.assertIsNone(caught.exception.__context__)
        return caught.exception

    def test_import_does_not_connect_and_default_factory_is_standard_library(self):
        with patch("http.client.HTTPSConnection") as factory:
            module = importlib.import_module("bambu_monitor.cloud_http")
            client = module.BambuHttp("global")
            factory.assert_not_called()
            client.close()
            factory.assert_not_called()

    def test_verified_tls_direct_host_no_proxy_identity_encoding_and_private_login_projection(self):
        response = WireResponse(json.dumps({"accessToken": "opaque-token", "refreshToken": CANARY,
                                           "email": CANARY, "expiresIn": 120, "profile": {"name": CANARY}}).encode())
        client, connection, factory = self.client(response)
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1"}):
            result = self.login(client)
        self.assertEqual(result.body, {"accessToken": "opaque-token", "expiresIn": 120})
        self.assertEqual(result.status, 200)
        self.assertNotIn(CANARY, repr(result))
        host, options = factory.calls[0]
        self.assertEqual((host, options["port"]), ("api.bambulab.cn", 443))
        self.assertEqual(options["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(options["context"].check_hostname)
        method, path, body, headers = connection.calls[1]
        self.assertEqual((method, path), ("POST", LOGIN))
        self.assertEqual(json.loads(body), LOGIN_BODY)
        self.assertEqual(headers["Accept-Encoding"], "identity")
        self.assertEqual(headers["User-Agent"], "bambu-monitor/0.1.6")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(connection.closed, 1)

    def test_global_region_and_only_expected_bind_metadata_leave_transport(self):
        response = WireResponse(json.dumps({"devices": [{"dev_id": "printer-B", "name": CANARY,
                                    "dev_product_name": "A1 mini", "dev_model_name": CANARY,
                                    "dev_access_code": CANARY, "online": True}], "account": CANARY}).encode())
        connection = Connection(response)
        factory = Factory([connection])
        client = BambuHttp("global", connection_factory=factory, clock=Clock())
        result = client.request("GET", "https://api.bambulab.com" + BIND, token=Secret("opaque-token"))
        self.assertEqual(result.body, {"devices": [{"dev_id": "printer-B", "dev_product_name": "A1 mini"}]})
        self.assertEqual(factory.calls[0][0], "api.bambulab.com")
        self.assertEqual(connection.calls[1][3]["Authorization"], "Bearer opaque-token")
        self.assertNotIn(CANARY, json.dumps(result.body))

    def test_uid_is_read_from_preference_and_not_from_opaque_token(self):
        for uid in (1234567890, "1234567890"):
            with self.subTest(uid_type=type(uid).__name__):
                response = WireResponse(json.dumps({"uid": uid, "name": CANARY, "avatar": CANARY}).encode())
                client, connection, _ = self.client(response)
                result = client.request("GET", BASE + PREFERENCE, token=Secret("opaque-without-jwt"))
                self.assertEqual(result.body, {"uid": "1234567890"})
                self.assertEqual(connection.calls[1][1], PREFERENCE)
        for uid in (True, -1, "not-numeric", "9" * 33, None, 1.5):
            client, _, _ = self.client(WireResponse(json.dumps({"uid": uid}).encode()))
            self.error("invalid_response", lambda: client.request("GET", BASE + PREFERENCE, token=Secret("opaque")))

    def test_cn_phone_password_challenge_sms_and_code_use_production_transport(self):
        # Synthetic domestic-format number, never sent to a live service.
        phone = "13800000000"
        connections = [Connection(WireResponse(b'{"loginType":"verifyCode"}')),
                       Connection(WireResponse(b"")), Connection(WireResponse())]
        factory = Factory(connections)
        auth = CloudAuth("cn", BambuHttp("cn", connection_factory=factory))
        self.assertFalse(auth.login(Secret(phone), Secret("synthetic-password")))
        self.assertEqual((auth.reason, auth.challenge), ("code_required", "sms_code"))
        self.assertEqual(len(factory.calls), 1)  # No automatic SMS send.
        self.assertTrue(auth.send_code(Secret(phone)))
        self.assertTrue(auth.login(Secret(phone), Secret("123456"), method="code"))
        self.assertEqual([call[0] for call in factory.calls],
                         ["api.bambulab.cn", "bambulab.cn", "api.bambulab.cn"])
        wire = [connection.calls[1] for connection in connections]
        self.assertEqual([item[1] for item in wire], [LOGIN, SMS_CODE, LOGIN])
        self.assertEqual(json.loads(wire[0][2]),
                         {"account": phone, "password": "synthetic-password", "apiError": ""})
        self.assertEqual(json.loads(wire[1][2]), {"phone": phone, "type": "codeLogin"})
        self.assertEqual(json.loads(wire[2][2]), {"account": phone, "code": "123456"})
        self.assertTrue(all("Authorization" not in item[3] for item in wire))
        self.assertNotIn(phone, repr(auth))

    def test_phone_region_format_and_code_channel_are_checked_before_connect(self):
        for region in ("cn", "global"):
            base = "https://api.bambulab.cn" if region == "cn" else "https://api.bambulab.com"
            invalid_accounts = ["12345", "1380000000", "138000000000", "1380000000a",
                                "１３８００００００００", "13800000000\n", "+8613800000000"]
            if region == "global":
                invalid_accounts.append("13800000000")
            for account in invalid_accounts:
                factory = Factory()
                client = BambuHttp(region, connection_factory=factory)
                self.error("invalid_request", lambda: client.request("POST", base + LOGIN,
                           body={"account": account, "password": "pw"}))
                self.assertEqual(factory.calls, [])
            for body in ({"phone": "a@b.invalid", "type": "codeLogin"},
                         {"phone": "13800000000", "type": "sms"},
                         {"email": "a@b.invalid", "type": "codeLogin"},
                         {"phone": "13800000000", "type": "codeLogin", "extra": CANARY}):
                client = BambuHttp(region, connection_factory=Factory())
                self.error("invalid_request", lambda: client.request("POST", "https://bambulab.cn" + SMS_CODE, body=body))
        client, _, factory = self.client()
        self.error("invalid_request", lambda: client.request("POST", BASE + EMAIL_CODE,
                   body={"email": "13800000000", "type": "codeLogin"}))
        self.assertEqual(factory.calls, [])
        client = BambuHttp("global", connection_factory=Factory())
        self.error("invalid_request", lambda: client.request("POST", "https://bambulab.cn" + SMS_CODE,
                   body={"phone": "13800000000", "type": "codeLogin"}))

    def test_email_password_and_code_are_exclusive_and_invalid_accounts_rejected(self):
        for body in ({**LOGIN_BODY, "code": "123456"}, {"account": "12345", "password": "pw"},
                     {"account": "a@b.invalid", "password": "pw", "apiError": CANARY},
                     {"account": "a@b.invalid", "password": "pw", "extra": CANARY},
                     {"account": "a@b.invalid", "code": ""}):
            client, _, factory = self.client()
            self.error("invalid_request", lambda: client.request("POST", BASE + LOGIN, body=body))
            self.assertEqual(factory.calls, [])
        for branch in ("verifyCode", "tfa"):
            client, connection, _ = self.client(WireResponse(json.dumps({"loginType": branch, "tfaKey": CANARY}).encode()))
            result = client.request("POST", BASE + LOGIN, body={"account": "a@b.invalid", "code": "123456"})
            self.assertEqual(result.body, {"loginType": branch})
            self.assertNotIn("password", json.loads(connection.calls[1][2]))
            self.assertNotIn(CANARY, json.dumps(result.body))

    def test_email_code_route_accepts_empty_200_and_has_fixed_request_type(self):
        for body in (b"", b'{"message":"success","extra":"' + CANARY.encode() + b'"}', b'{"success":true}'):
            client, connection, _ = self.client(WireResponse(body))
            result = client.request("POST", BASE + EMAIL_CODE, body={"email": "a@b.invalid", "type": "codeLogin"})
            self.assertEqual(result.status, 200)
            self.assertNotIn(CANARY, json.dumps(result.body))
            self.assertEqual(connection.calls[1][1], EMAIL_CODE)
        client, _, _ = self.client(WireResponse(b""))
        self.error("invalid_response", lambda: self.login(client))
        client, _, _ = self.client(WireResponse(b"{}"))
        self.error("invalid_response", lambda: self.login(client))
        client, _, _ = self.client()
        self.error("invalid_request", lambda: client.request("POST", BASE + EMAIL_CODE,
                                                               body={"email": "a@b.invalid", "type": "sms"}))

    def test_no_redirect_error_body_read_cookie_or_retry(self):
        for status in (301, 302, 307, 308, 400, 401, 403, 429, 500, 503):
            response = WireResponse(CANARY.encode(), status=status,
                                    headers=[("Location", "https://elsewhere.invalid/" + CANARY), ("Set-Cookie", CANARY)])
            client, connection, factory = self.client(response)
            result = self.login(client)
            self.assertEqual(result, Response(status, {}))
            self.assertEqual(response.read_calls, [])
            self.assertEqual(len(factory.calls), 1)
            self.assertEqual(connection.closed, 1)
            self.assertNotIn(CANARY, repr(result) + json.dumps(result.body))

    def test_url_method_token_and_body_policy_reject_before_connection(self):
        invalid = [("POST", "http://api.bambulab.cn" + LOGIN, LOGIN_BODY, None),
                   ("POST", BASE + ":443" + LOGIN, LOGIN_BODY, None),
                   ("POST", BASE + LOGIN + "?token=" + CANARY, LOGIN_BODY, None),
                   ("POST", "https://api.bambulab.com" + LOGIN, LOGIN_BODY, None),
                   ("POST", "https://api.bambulab.cn@evil.invalid" + LOGIN, LOGIN_BODY, None),
                   ("GET", BASE + BIND + "#fragment", None, Secret("t")),
                   ("DELETE", BASE + BIND, None, Secret("t")),
                   ("GET", BASE + BIND, {}, Secret("t")),
                   ("GET", BASE + BIND, None, None),
                   ("GET", BASE + BIND, None, Secret("token\r\n" + CANARY)),
                   ("POST", BASE + LOGIN, LOGIN_BODY, Secret("t")),
                   ("POST", BASE + "/v1/user-service/my/task", {}, None),
                   ("POST", "https://bambulab.cn/api/sign-in/tfa", {}, None)]
        for method, url, body, token in invalid:
            client, _, factory = self.client()
            self.error("invalid_request", lambda: client.request(method, url, body=body, token=token))
            self.assertEqual(factory.calls, [])

    def test_response_json_byte_depth_key_number_encoding_and_root_budgets(self):
        invalid_json = [b'{"accessToken":"one","accessToken":"two"}', b'[]', b'null', b'\xff',
                        b'{"accessToken":NaN}', b'{"accessToken":Infinity}',
                        b'{"accessToken":1e999}', b'{"a":' + b'[' * 33 + b'0' + b']' * 33 + b'}',
                        b'{"x":' + b'9' * 129 + b'}', br'{"accessToken":"\ud800"}']
        for raw in invalid_json:
            client, _, _ = self.client(WireResponse(raw))
            self.error("invalid_response", lambda: self.login(client))
        client, _, _ = self.client(WireResponse(b"x" * 65), maximum=64)
        self.error("response_too_large", lambda: self.login(client))
        client, _, _ = self.client(WireResponse(chunks=[b"x" * 100000]))
        self.error("response_too_large", lambda: self.login(client))
        for headers, code in (([("Content-Length", "65537")], "response_too_large"),
                              ([("Content-Length", "-1")], "response_too_large"),
                              ([("Content-Length", "1"), ("Content-Length", "1")], "response_too_large"),
                              ([("Content-Encoding", "gzip")], "unsupported_encoding"),
                              ([("Content-Type", "text/html")], "invalid_response"),
                              ([("Transfer-Encoding", "chunked"), ("Content-Length", "1")], "invalid_response"),
                              ([("Long", "x" * 16385)], "invalid_response")):
            client, _, _ = self.client(WireResponse(headers=headers))
            self.error(code, lambda: self.login(client))

    def test_chunked_success_and_truncated_content_length(self):
        client, connection, _ = self.client(WireResponse(headers=[("Transfer-Encoding", "chunked")],
                                                        chunks=[b'{"access', b'Token":"opaque"}', b'']))
        self.assertEqual(self.login(client).body, {"accessToken": "opaque"})
        self.assertTrue(all(0 < timeout <= 10 for timeout in connection.socket.timeouts))
        client, _, _ = self.client(WireResponse(body=b"{}", headers=[("Content-Length", "5")]))
        self.error("invalid_response", lambda: self.login(client))

    def test_device_duplicate_invalid_and_overbound_ids_do_not_leak(self):
        for devices in ([{"dev_id": "same"}, {"dev_id": "same"}], [{"dev_id": "../../bad"}],
                        [{"dev_id": True}], [{"dev_id": "p"}] * 129, "not-list"):
            client, _, _ = self.client(WireResponse(json.dumps({"devices": devices}).encode()))
            self.error("invalid_response", lambda: client.request("GET", BASE + BIND, token=Secret("t")))

    def test_deadline_is_rechecked_after_connect_before_request_and_between_chunks(self):
        clock = Clock()
        client, connection, _ = self.client(clock=clock)
        connection.on_connect = lambda: setattr(clock, "value", 111)
        self.error("deadline_exceeded", lambda: self.login(client))
        self.assertEqual(connection.calls, ["connect"])
        clock = Clock()
        def read_late():
            clock.value += 3
        response = WireResponse(chunks=[b'{"accessToken":', b'"t"}', b''], on_read=read_late)
        client, connection, _ = self.client(response, clock=clock)
        self.error("deadline_exceeded", lambda: self.login(client, timeout=5))
        self.assertLessEqual(len(response.read_calls), 2)
        self.assertIn(2, connection.socket.timeouts)

    def test_errors_do_not_keep_transport_exception_text_or_chain(self):
        for failure, code in ((OSError(CANARY), "network_error"), (ssl.SSLError(CANARY), "tls_error"),
                              (TimeoutError(CANARY), "deadline_exceeded")):
            client, connection, _ = self.client(fail=failure)
            self.error(code, lambda: self.login(client))
            self.assertEqual(connection.closed, 1)
        client = BambuHttp("cn", connection_factory=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError(CANARY)))
        self.error("network_error", lambda: self.login(client))

    def test_close_cancels_active_socket_read_and_prevents_late_success_and_new_connections(self):
        entered, released = Event(), Event()
        def blocking_read():
            entered.set()
            if not released.wait(2):
                raise TimeoutError("test release missing")
        response = WireResponse(on_read=blocking_read)
        client, connection, factory = self.client(response)
        connection.socket.release = released
        results = []
        def worker():
            try:
                self.login(client)
            except HTTPError as error:
                results.append(error.code)
        thread = Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            client.close()
        finally:
            released.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["closed"])
        self.assertIn(socket.SHUT_RDWR, connection.socket.shutdowns)
        self.error("closed", lambda: self.login(client))
        self.assertEqual(len(factory.calls), 1)

    def test_single_flight_and_close_failure_are_observable(self):
        entered, released = Event(), Event()
        def waiting_read():
            entered.set()
            if not released.wait(2):
                raise TimeoutError("test release missing")
        client, _, factory = self.client(WireResponse(on_read=waiting_read))
        results = []
        thread = Thread(target=lambda: results.append(self.login(client)))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.error("busy", lambda: self.login(client))
        finally:
            released.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(len(factory.calls), 1)
        client, connection, _ = self.client()
        def fail_close():
            raise OSError(CANARY)
        connection.close = fail_close
        self.error("close_failed", lambda: self.login(client))
        self.error("closed", lambda: self.login(client))
        self.error("close_failed", client.close)

    def test_request_bytes_and_credential_types_are_bounded_before_connect(self):
        for body in ({"account": "a@b.invalid", "password": "\U0001F600" * 4096},
                     {"account": "a@b.invalid", "password": "x" * 4097},
                     {"account": "a@b.invalid", "password": 12345},
                     {"account": "a@b.invalid", "code": "x" * 65},
                     {"account": "a@b.invalid", "password": chr(0xD800)}):
            client, _, factory = self.client()
            self.error("invalid_request", lambda: client.request("POST", BASE + LOGIN, body=body))
            self.assertEqual(factory.calls, [])

    def test_close_before_factory_returns_discards_connection_without_claiming_dns_cancellation(self):
        entered, release = Event(), Event()
        connection = Connection()
        calls = []
        def waiting_factory(*args, **kwargs):
            calls.append("factory_entered")
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test release missing")
            return connection
        client = BambuHttp("cn", connection_factory=waiting_factory, clock=Clock())
        results = []
        def worker():
            try:
                self.login(client)
            except HTTPError as error:
                results.append(error.code)
        thread = Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            client.close()
            self.assertTrue(thread.is_alive())  # No false claim of cancelling arbitrary DNS/factory work.
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["closed"])
        self.assertEqual(connection.calls, [])
        self.assertEqual(connection.closed, 1)
        self.assertEqual(calls, ["factory_entered"])

    def test_invalid_timeout_and_clock_are_rejected_without_connection(self):
        for timeout in (0, -1, True, float("inf"), float("nan"), 10**1000, 61):
            client, _, factory = self.client()
            self.error("invalid_request", lambda: self.login(client, timeout=timeout))
            self.assertEqual(factory.calls, [])
        client = BambuHttp("cn", clock=lambda: 10**1000)
        self.error("clock_error", lambda: self.login(client))
        for region in ("auto", "", None, ["cn"]):
            self.error("invalid_request", lambda: BambuHttp(region))

    def test_cloud_auth_consumes_real_transport_boundary_without_account_network(self):
        factory = Factory([Connection(WireResponse(b'{"loginType":"verifyCode","account":"' + CANARY.encode() + b'"}')),
                           Connection(WireResponse(b'{"accessToken":"opaque-token"}')),
                           Connection(WireResponse(b'{"devices":[{"dev_id":"printer-A","dev_access_code":"' + CANARY.encode() + b'"}]}')),
                           Connection(WireResponse(CANARY.encode(), status=401))])
        client = BambuHttp("cn", connection_factory=factory, clock=Clock())
        auth = CloudAuth("cn", client)
        self.assertFalse(auth.login(Secret("synthetic@example.invalid"), Secret("pw")))
        self.assertEqual(auth.reason, "code_required")
        self.assertTrue(auth.login(Secret("synthetic@example.invalid"), Secret("123456"), method="code"))
        devices = auth.discover()
        self.assertEqual(auth.select(devices, "printer-A").id, "printer-A")
        self.assertEqual(auth.discover(), ())
        self.assertEqual(auth.state, "auth_required")
        self.assertTrue(auth.close())
        self.assertEqual(len(factory.calls), 4)
        self.assertNotIn(CANARY, repr(devices))


if __name__ == "__main__":
    unittest.main()
