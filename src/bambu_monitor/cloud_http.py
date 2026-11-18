"""Bounded, direct HTTPS transport for the selected Bambu Cloud routes.

Importing the module never opens a connection. This is an independently written
transport, not a copied HA client. Public protocol references and real-network
limitations are recorded in docs/HTTP-RESEARCH.md. No real account was used to
validate it; injectable connections exercise the same implementation offline.
"""
from __future__ import annotations

import http.client
import json
import math
import re
import socket
import ssl
from threading import RLock
import time

from .auth import Response, Secret
from .accounts import is_email as _email, is_cn_phone, valid_account
from .config import ENDPOINTS

LOGIN = "/v1/user-service/user/login"
EMAIL_CODE = "/v1/user-service/user/sendemail/code"
SMS_CODE = "/api/v1/user-service/user/sendsmscode"
BIND = "/v1/iot-service/api/user/bind"
PREFERENCE = "/v1/design-user-service/my/preference"
ROUTES = {LOGIN: "POST", EMAIL_CODE: "POST", SMS_CODE: "POST", BIND: "GET", PREFERENCE: "GET"}
MAX_REQUEST_BYTES = 16384
MAX_RESPONSE_BYTES = 65536
MAX_DEPTH = 32
MAX_HEADER_BYTES = 16384
KNOWN_MODELS = frozenset({"A1", "A1 mini", "P1P", "P1S", "X1", "X1C", "X1E", "X1 Carbon"})
ERROR_CODES = frozenset({
    "invalid_request", "closed", "busy", "deadline_exceeded", "clock_error",
    "tls_error", "network_error", "invalid_response", "response_too_large",
    "unsupported_encoding", "close_failed",
})


class HTTPError(Exception):
    """Only fixed diagnostic categories; never retain a URL, body or exception."""
    def __init__(self, code="network_error"):
        self.code = code if isinstance(code, str) and code in ERROR_CODES else "network_error"
        super().__init__(self.code)


def _timeout(value):
    if type(value) not in (float, int) or not 0 < value <= 60 or not math.isfinite(value):
        raise HTTPError("invalid_request")
    return float(value)


def _text(value, maximum):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _json_depth(raw):
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > MAX_DEPTH:
                return False
        elif byte in (93, 125):
            depth -= 1
    return True


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _integer(raw):
    if len(raw) > 128:
        raise ValueError("integer budget")
    return int(raw)


def _float(raw):
    result = float(raw)
    if not math.isfinite(result):
        raise ValueError("nonfinite")
    return result


def _constant(_):
    raise ValueError("non-JSON constant")


def _decode(raw):
    if not _json_depth(raw):
        raise HTTPError("invalid_response")
    failure = False
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_int=_integer, parse_float=_float, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        failure = True
    if failure or not isinstance(value, dict):
        raise HTTPError("invalid_response")
    return value


def _project(path, body):
    """Minimal private protocol response; never return full account data/access code."""
    if path == LOGIN:
        result = {}
        token = body.get("accessToken")
        if token is not None:
            if not _text(token, 16384) or any(not 33 <= ord(char) <= 126 for char in token):
                raise HTTPError("invalid_response")
            result["accessToken"] = token
        branch = body.get("loginType")
        if branch in ("", "verifyCode", "tfa"):
            result["loginType"] = branch
        expires = body.get("expiresIn")
        if type(expires) is int and 0 < expires <= 10**10:
            result["expiresIn"] = expires
        if not result or not ("accessToken" in result or result.get("loginType") in ("verifyCode", "tfa")):
            raise HTTPError("invalid_response")
        return result
    if path in (EMAIL_CODE, SMS_CODE):
        result = {}
        if type(body.get("success")) is bool:
            result["success"] = body["success"]
        if body.get("message") == "success":
            result["message"] = "success"
        if type(body.get("code")) is int and -1000000 <= body["code"] <= 1000000:
            result["code"] = body["code"]
        return result
    if path == PREFERENCE:
        uid = body.get("uid")
        if type(uid) is int and 0 <= uid < 10**32:
            uid = str(uid)
        if not isinstance(uid, str) or not re.fullmatch(r"[0-9]{1,32}", uid):
            raise HTTPError("invalid_response")
        return {"uid": uid}
    devices = body.get("devices")
    if not isinstance(devices, list) or len(devices) > 128:
        raise HTTPError("invalid_response")
    projected, seen = [], set()
    for device in devices:
        device_id = device.get("dev_id") if isinstance(device, dict) else None
        if (not isinstance(device_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", device_id)
                or device_id in seen):
            raise HTTPError("invalid_response")
        seen.add(device_id)
        projected_device = {"dev_id": device_id}
        for field in ("dev_product_name", "dev_model_name"):
            model = device.get(field)
            if isinstance(model, str) and model in KNOWN_MODELS:
                projected_device[field] = model
        projected.append(projected_device)
    return {"devices": projected}


class BambuHttp:
    """A terminal-close, single-flight HttpTransport using direct HTTPSConnection.

    connection_factory(host, port=443, timeout=seconds, context=verified_context)
    may be injected with a scripted connection. clock returns monotonic seconds.
    A deadline is checked around connect/request/headers and every read1 chunk;
    each socket operation receives the remaining budget. DNS and stdlib internal
    header/chunk-line reads are not hard-cancellable total-deadline operations.
    There is no hidden worker/thread pool or automatic retry.
    """
    def __init__(self, region, *, connection_factory=None, clock=time.monotonic,
                 max_response_bytes=MAX_RESPONSE_BYTES):
        if region not in ("cn", "global"):
            raise HTTPError("invalid_request")
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES:
            raise HTTPError("invalid_request")
        self._region = region
        self._base = ENDPOINTS[region][0]
        self._host = self._base.removeprefix("https://")
        self._factory = connection_factory if connection_factory is not None else http.client.HTTPSConnection
        self._clock = clock
        self._limit = max_response_bytes
        self._lock = RLock()
        self._closed = False
        self._busy = False
        self._active = None

    def _now(self):
        failed = False
        try:
            value = self._clock()
        except Exception:
            failed = True
        if failed or type(value) not in (int, float) or not 0 <= value <= 1e12 or not math.isfinite(value):
            raise HTTPError("clock_error")
        return float(value)

    def _remaining(self, deadline):
        with self._lock:
            if self._closed:
                raise HTTPError("closed")
        remaining = deadline - self._now()
        if remaining <= 0:
            raise HTTPError("deadline_exceeded")
        return remaining

    def _budget(self, connection, deadline):
        remaining = self._remaining(deadline)
        connection.timeout = remaining
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
        return remaining

    @staticmethod
    def _dispose(connection):
        # shutdown interrupts an already-active socket read; close alone may not.
        sock = getattr(connection, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            except Exception:
                pass
        try:
            connection.close()
        except Exception:
            return False
        return True

    def close(self):
        with self._lock:
            self._closed = True
            active = self._active
        if active is not None:
            if not self._dispose(active):
                raise HTTPError("close_failed")
            with self._lock:
                if self._active is active:
                    self._active = None

    def _prepare(self, method, url, body, token):
        if not isinstance(method, str) or not isinstance(url, str):
            raise HTTPError("invalid_request")
        path = next((path for path, allowed in ROUTES.items()
                     if method == allowed and url == ("https://bambulab.cn" if path == SMS_CODE else self._base) + path), None)
        if path is None:
            raise HTTPError("invalid_request")
        headers = {"Accept": "application/json", "Accept-Encoding": "identity",
                   "User-Agent": "bambu-monitor/0.1.6", "Connection": "close"}
        if method == "GET":
            if body is not None or not isinstance(token, Secret) or not _text(token.value, 16384):
                raise HTTPError("invalid_request")
            if any(not 33 <= ord(char) <= 126 for char in token.value):
                raise HTTPError("invalid_request")
            headers["Authorization"] = "Bearer " + token.value
            return path, headers, None
        if token is not None or type(body) is not dict:
            raise HTTPError("invalid_request")
        if path == LOGIN:
            password = set(body) in ({"account", "password"}, {"account", "password", "apiError"})
            code = set(body) == {"account", "code"}
            if not valid_account(body.get("account"), self._region) or not (password or code):
                raise HTTPError("invalid_request")
            if password and (not isinstance(body.get("password"), str) or not 0 < len(body["password"]) <= 4096
                             or body.get("apiError", "") != ""):
                raise HTTPError("invalid_request")
            if code and not _text(body.get("code"), 64):
                raise HTTPError("invalid_request")
        elif path == SMS_CODE:
            if (self._region != "cn" or set(body) != {"phone", "type"}
                    or not is_cn_phone(body.get("phone")) or body.get("type") != "codeLogin"):
                raise HTTPError("invalid_request")
        elif (set(body) != {"email", "type"} or not _email(body.get("email"))
              or body.get("type") != "codeLogin"):
            raise HTTPError("invalid_request")
        failed = False
        try:
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            failed = True
        if failed or len(encoded) > MAX_REQUEST_BYTES:
            raise HTTPError("invalid_request")
        headers["Content-Type"] = "application/json"
        return path, headers, encoded

    def _read(self, response, connection, deadline):
        headers = response.getheaders()
        if (len(headers) > 64 or sum(len(name) + len(value) for name, value in headers) > MAX_HEADER_BYTES):
            raise HTTPError("invalid_response")
        names = {}
        for name, value in headers:
            names.setdefault(name.lower(), []).append(value)
        encoding = names.get("content-encoding", ["identity"])
        if len(encoding) != 1 or encoding[0].strip().lower() not in ("identity", ""):
            raise HTTPError("unsupported_encoding")
        content_type = names.get("content-type", [])
        if len(content_type) > 1 or content_type and content_type[0].split(";")[0].strip().lower() != "application/json":
            raise HTTPError("invalid_response")
        length = names.get("content-length", [])
        if len(length) > 1 or length and (not re.fullmatch(r"[0-9]{1,10}", length[0]) or int(length[0]) > self._limit):
            raise HTTPError("response_too_large")
        transfer = names.get("transfer-encoding", [])
        if len(transfer) > 1 or transfer and (transfer[0].strip().lower() != "chunked" or length):
            raise HTTPError("invalid_response")
        chunks, count = [], 0
        while True:
            self._budget(connection, deadline)
            chunk = response.read1(min(4096, self._limit + 1 - count))
            self._remaining(deadline)
            if not isinstance(chunk, bytes):
                raise HTTPError("invalid_response")
            if not chunk:
                break
            count += len(chunk)
            if count > self._limit:
                raise HTTPError("response_too_large")
            chunks.append(chunk)
        if length and count != int(length[0]):
            raise HTTPError("invalid_response")
        return b"".join(chunks)

    def request(self, method, url, *, body=None, token=None, timeout=10):
        timeout = _timeout(timeout)
        path, headers, encoded = self._prepare(method, url, body, token)
        deadline = self._now() + timeout
        with self._lock:
            if self._closed:
                raise HTTPError("closed")
            if self._busy:
                raise HTTPError("busy")
            self._busy = True
        connection = None
        result = None
        failure = None
        try:
            context = ssl.create_default_context()
            host = "bambulab.cn" if path == SMS_CODE else self._host
            connection = self._factory(host, port=443, timeout=self._remaining(deadline), context=context)
            with self._lock:
                if self._closed:
                    raise HTTPError("closed")
                self._active = connection
            self._budget(connection, deadline)
            connection.connect()
            self._budget(connection, deadline)
            connection.request(method, path, body=encoded, headers=headers)
            self._budget(connection, deadline)
            response = connection.getresponse()
            self._remaining(deadline)
            if type(response.status) is not int or not 100 <= response.status <= 599:
                raise HTTPError("invalid_response")
            if response.status != 200:
                # In particular: never follow Location, read error text, or log it.
                result = Response(response.status, {})
            else:
                raw = self._read(response, connection, deadline)
                value = {} if path in (EMAIL_CODE, SMS_CODE) and not raw else _decode(raw)
                result = Response(200, _project(path, value))
                self._remaining(deadline)
        except HTTPError as error:
            failure = error.code
        except ssl.SSLError:
            failure = "tls_error"
        except (TimeoutError, socket.timeout):
            failure = "deadline_exceeded"
        except Exception:
            # No underlying exception/body/header/path is retained or chained.
            failure = "network_error"
        finally:
            cleanup_ok = connection is None or self._dispose(connection)
            with self._lock:
                self._busy = False
                if not cleanup_ok:
                    # Do not admit another socket after failing to release this one.
                    self._closed = True
                    self._active = connection
                    failure = "close_failed"
                else:
                    self._active = None
                    if self._closed:
                        failure = "closed"
        if failure is not None:
            raise HTTPError(failure)
        return result
