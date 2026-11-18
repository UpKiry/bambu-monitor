"""Private authentication state and injectable HTTP boundary; no background login."""
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol
from threading import RLock
import re

from .accounts import is_cn_phone
from .config import ENDPOINTS, identifier, ConfigError


@dataclass(frozen=True, repr=False)
class Secret:
    value: str

    def __repr__(self):
        return "Secret(<redacted>)"


@dataclass(frozen=True)
class Response:
    status: int
    body: dict = field(repr=False)


class HttpTransport(Protocol):
    def request(self, method: str, url: str, *, body: dict | None = None,
                token: Secret | None = None, timeout: float = 10) -> Response: ...
    def close(self) -> None: ...


class MemoryCredentials:
    """M1 private session memory only; no silent file fallback."""
    persistent = False

    def __init__(self):
        self._items = {}

    def get(self, reference):
        return self._items.get(reference)

    def put(self, reference, token):
        if not isinstance(token, Secret):
            raise TypeError("private secret required")
        self._items[reference] = token

    def clear(self):
        self._items.clear()


@dataclass(frozen=True)
class Device:
    id: str = field(repr=False)
    model: str | None = None
    # No upstream display name, access code, full account or HTTP summary leaves here.


class CloudAuth:
    """Password/verification-code state machine exercised through replaceable HTTP.

    Website CSRF/TOTP require separate implementation; real cloud validation is pending.
    No method automatically resends a code or refreshes an opaque token.
    """
    def __init__(self, region, transport: HttpTransport, credentials=None, reference="cloud/default"):
        if region not in ("cn", "global"):
            raise ConfigError("explicit region required")
        self.region = region
        self.base = ENDPOINTS[region][0]
        self.transport = transport
        self.credentials = credentials if credentials is not None else MemoryCredentials()
        self.reference = identifier(reference)
        self.state = "auth_required"
        self.reason = "login_required"
        self.challenge = None
        self._token = None
        self._uid = None
        self._devices = ()
        self.expires_in = None
        self._closed = False
        self._epoch = 0
        self._lock = RLock()

    def _request(self, method, path, *, epoch, body=None, authenticated=False):
        with self._lock:
            if self._closed or epoch != self._epoch:
                return None
            token = self._token if authenticated else None
        try:
            base = "https://bambulab.cn" if path == "/api/v1/user-service/user/sendsmscode" else self.base
            response = self.transport.request(method, base + path, body=body, token=token, timeout=10)
        except Exception as error:
            with self._lock:
                if epoch == self._epoch and not self._closed:
                    # Adapter categories are fixed; never render arbitrary exception text.
                    category = getattr(error, "code", None)
                    self.reason = category if isinstance(category, str) and category in (
                        "invalid_request", "closed", "busy", "deadline_exceeded", "clock_error",
                        "tls_error", "network_error", "invalid_response", "response_too_large",
                        "unsupported_encoding", "close_failed") else "transport_error"
            return None
        with self._lock:
            if self._closed or epoch != self._epoch:
                return None
            if not isinstance(response, Response) or type(response.status) is not int:
                self.reason = "invalid_response"
                return None
            if response.status == 401:
                self.invalidate()
                return None
            if response.status != 200:
                self.reason = {403: "permission_denied", 429: "rate_limited"}.get(response.status, "service_error")
                return None
            if not isinstance(response.body, dict):
                self.reason = "invalid_response"
                return None
            return response.body

    def login(self, account: Secret, credential: Secret, *, method="password"):
        if method not in ("password", "code"):
            raise ValueError("unsupported login method")
        with self._lock:
            if self._closed:
                return False
            self.invalidate()
            epoch = self._epoch
        body = {"account": account.value, method: credential.value}
        if method == "password":
            body["apiError"] = ""
        response = self._request("POST", "/v1/user-service/user/login", body=body, epoch=epoch)
        with self._lock:
            if response is None or self._closed or epoch != self._epoch:
                return False
            token = response.get("accessToken")
            if isinstance(token, str) and 0 < len(token) <= 16384:
                self._token = Secret(token)
                try:
                    self.credentials.put(self.reference, self._token)
                except Exception:
                    # Optional future key store failure never becomes a cleartext file.
                    self.credentials = MemoryCredentials()
                    self.credentials.put(self.reference, self._token)
                ttl = response.get("expiresIn")
                self.expires_in = ttl if type(ttl) in (int, float) and 0 < ttl <= 365 * 86400 else None
                self.state, self.reason, self.challenge = "authenticated", "authenticated", None
                return True
            branch = response.get("loginType")
            if not isinstance(branch, str):
                branch = None
            self.challenge = ("sms_code" if self.region == "cn" and is_cn_phone(account.value)
                              else "email_code") if branch == "verifyCode" else None
            self.reason = {"verifyCode": "code_required", "tfa": "mfa_unsupported"}.get(branch, "credential_rejected")
            return False

    def send_code(self, account: Secret):
        """Dispatch one explicit send action by account form, never by retry."""
        if self.region == "cn" and is_cn_phone(account.value):
            return self.send_sms_code(account)
        return self.send_email_code(account)

    def send_sms_code(self, account: Secret):
        if self.region != "cn" or not is_cn_phone(account.value):
            self.reason = "invalid_request"
            return False
        return self._send_code("/api/v1/user-service/user/sendsmscode", "phone", account)

    def send_email_code(self, account: Secret):
        return self._send_code("/v1/user-service/user/sendemail/code", "email", account)

    def _send_code(self, path, field, account):
        # Explicit user action only; never called from login/retry.
        with self._lock:
            if self._closed:
                return False
            epoch = self._epoch
        response = self._request("POST", path, epoch=epoch,
                                 body={field: account.value, "type": "codeLogin"})
        with self._lock:
            if response is None or self._closed or epoch != self._epoch:
                return False
            # Reference protocol treats HTTP 200 as code-send acknowledgement.
            # This method is never invoked implicitly or retried automatically.
            self.reason = "code_sent"
            return True

    def discover(self):
        with self._lock:
            if self.state != "authenticated" or self._closed:
                return ()
            epoch = self._epoch
            self._devices = ()
        response = self._request("GET", "/v1/iot-service/api/user/bind", epoch=epoch, authenticated=True)
        with self._lock:
            if response is None or self._closed or epoch != self._epoch:
                return ()
            devices = response.get("devices")
            if not isinstance(devices, list) or len(devices) > 128:
                self.reason = "invalid_devices"
                return ()
            result = {}
            for raw in devices:
                try:
                    if not isinstance(raw, dict):
                        raise ConfigError("invalid device")
                    device_id = identifier(raw.get("dev_id"))
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", device_id):
                        raise ConfigError("invalid topic identity")
                    if device_id in result:
                        raise ConfigError("duplicate device identity")
                    from .telemetry.parser import MODELS
                    model = raw.get("dev_product_name")
                    if not isinstance(model, str) or model not in MODELS | {"X1 Carbon"}:
                        model = raw.get("dev_model_name")
                    if model == "X1 Carbon":
                        model = "X1C"
                    if not isinstance(model, str) or model not in MODELS:
                        model = None
                    result[device_id] = Device(device_id, model)
                except ConfigError:
                    self.reason = "invalid_devices"
                    return ()
            self._devices = tuple(result.values())
            self.reason = "authenticated"
            return self._devices

    def select(self, devices, printer_id):
        with self._lock:
            if self._closed or self.state != "authenticated":
                return None
            if not printer_id:
                self.reason = "selection_required"
                return None
            for device in devices:
                if device.id == printer_id and any(device is current for current in self._devices):
                    return device
            self.reason = "selected_device_unavailable"
            return None

    def invalidate(self):
        with self._lock:
            self._epoch += 1
            self.state, self.reason, self.challenge = "auth_required", "credential_rejected", None
            self._token = None
            self._uid = None
            self._devices = ()
            self.expires_in = None
            try:
                self.credentials.clear()
            except Exception:
                self.credentials = MemoryCredentials()
                self.reason = "credential_store_unavailable"

    def restore(self):
        """Load one scoped opaque token; it remains unverified until HTTP succeeds."""
        with self._lock:
            if self._closed:
                return False
            self._epoch += 1
            self._token = self._uid = None
            self._devices = ()
            self.expires_in = None
            self.challenge = None
            self.state, self.reason = "auth_required", "stored_token_unavailable"
            try:
                token = self.credentials.get(self.reference)
            except Exception:
                self.reason = "credential_store_unavailable"
                return False
            if not isinstance(token, Secret) or not isinstance(token.value, str) or not 0 < len(token.value) <= 16384:
                return False
            self._token, self._uid = token, None
            self.state, self.reason = "authenticated", "stored_token_unverified"
            return True

    def mqtt_credentials(self):
        """UID is queried via preference API, never decoded from an assumed JWT."""
        with self._lock:
            if self._closed or self.state != "authenticated":
                return None
            epoch = self._epoch
        response = self._request("GET", "/v1/design-user-service/my/preference", epoch=epoch, authenticated=True)
        with self._lock:
            if response is None or self._closed or epoch != self._epoch:
                return None
            uid = response.get("uid")
            if type(uid) not in (str, int) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(uid)):
                self.reason = "invalid_uid"
                return None
            self._uid = Secret(str(uid))
            self.reason = "authenticated"
            return self._uid, self._token

    def close(self):
        cleanup_ok = True
        with self._lock:
            self._closed = True
            self._epoch += 1
            self.state, self.reason, self.challenge = "auth_required", "closed", None
            self._token = self._uid = None
            self._devices = ()
            self.expires_in = None
            try:
                # Normal shutdown preserves a native OS entry, unlike revoke/401.
                close = getattr(self.credentials, "close", self.credentials.clear)
                close()
            except Exception:
                self.reason = "credential_store_unavailable"
                cleanup_ok = False
        try:
            self.transport.close()
        except Exception:
            self.reason = "close_failed"
            return False
        return cleanup_ok


class FakeHttp:
    """Scripted response/error inputs; requests retained as path/method only."""
    def __init__(self, responses=()):
        self.responses = deque(responses)
        self.requests = []
        self.closed = False

    def request(self, method, url, *, body=None, token=None, timeout=10):
        if self.closed:
            raise RuntimeError("transport closed")
        if len(self.requests) >= 1024:
            raise RuntimeError("fake request budget exceeded")
        self.requests.append((method, url))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True
        self.responses.clear()
