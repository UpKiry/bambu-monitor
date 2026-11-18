"""Bounded loopback HTTP control plane. No cloud or credential work on HTTP threads."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import secrets
import threading
import webbrowser

from .service_runtime import ServiceRuntime

WEB_ROOT = Path(__file__).with_name("webui")
MAX_BODY = 65536
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


class LocalServiceError(ValueError):
    pass


class LocalControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 8

    def __init__(self, address=("127.0.0.1", 8765), *, runtime):
        if address[0] != "127.0.0.1":
            raise LocalServiceError("loopback_only")
        self.runtime = runtime
        self.capability = secrets.token_urlsafe(32)
        self._slots = threading.BoundedSemaphore(8)
        super().__init__(address, LocalRequestHandler)
        self.host = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.host}"
        self.dashboard_url = self.origin + "/#" + self.capability

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(3)
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # Never print request/header/exception values, even on malformed input.
        pass


class LocalRequestHandler(BaseHTTPRequestHandler):
    server_version = "BambuMonitorLocal/1"
    sys_version = ""

    def log_message(self, *_):
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        headers = {"Content-Type": content_type, "Content-Length": str(len(body)),
                   "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                   "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                   "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
                   "Connection": "close"}
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (OSError, TimeoutError):
                pass

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("utf-8"))

    def send_error(self, code, message=None, explain=None):
        # Base class errors must not reflect request text or issue HTML tracebacks.
        self._json({"error": "invalid_request"}, code)

    def _host_ok(self):
        return (self.client_address[0] == "127.0.0.1"
                and self.headers.get_all("Host") == [self.server.host]
                and self.headers.get("Origin", self.server.origin) == self.server.origin)

    def _authorized(self):
        supplied = self.headers.get_all("X-Bambu-Local-Session", [])
        return (len(supplied) == 1 and supplied[0].isascii()
                and secrets.compare_digest(supplied[0], self.server.capability))

    def do_GET(self):
        if not self._host_ok():
            self._json({"error": "origin_rejected"}, 403)
        elif self.path == "/api/status":
            if not self._authorized():
                self._json({"error": "unauthorized"}, 403)
            else:
                self._json(self.server.runtime.status())
        elif self.path in ASSETS:
            filename, content_type = ASSETS[self.path]
            self._send(200, (WEB_ROOT / filename).read_bytes(), content_type)
        else:
            self._json({"error": "not_found"}, 404)

    def do_POST(self):
        if not self._host_ok() or not self._authorized() or self.headers.get_all("Origin") != [self.server.origin]:
            self._json({"error": "unauthorized"}, 403)
            return
        if (self.headers.get_all("Content-Type") != ["application/json"]
                or self.headers.get("Transfer-Encoding") is not None):
            self._json({"error": "invalid_request"}, 415)
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or len(lengths[0]) > 6 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_BODY:
            self._json({"error": "invalid_request"}, 413)
            return
        data = None
        try:
            raw = self.rfile.read(int(lengths[0]))
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys,
                              parse_constant=_invalid_constant)
            raw = None
            if type(data) is not dict:
                raise ValueError
            if self.path == "/api/collector/stop" and data == {}:
                result = self.server.runtime.stop_collector()
            elif self.path.startswith("/api/"):
                result = self.server.runtime.submit(self.path[5:], data)
            else:
                self._json({"error": "not_found"}, 404)
                return
            self._json(result, 202 if result.get("operation_id") else 200)
        except (ValueError, UnicodeError, RecursionError):
            self._json({"error": "invalid_request"}, 400)
        except (OSError, TimeoutError):
            self._json({"error": "request_timeout"}, 408)
        except Exception:
            self._json({"error": "operation_failed"}, 500)
        finally:
            if isinstance(data, dict):
                data.clear()


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("invalid_constant")


def run_service(*, settings_path=None, open_browser=True, port=8765, auto_collect=True, on_ready=None):
    from .macos_service import check_tray_available, run_tray
    check_tray_available()
    runtime = ServiceRuntime(settings_path)
    server = None
    thread = None
    quit_event = threading.Event()
    try:
        try:
            server = LocalControlServer(("127.0.0.1", port), runtime=runtime)
        except OSError:
            raise LocalServiceError("local_port_unavailable") from None
        thread = threading.Thread(target=server.serve_forever, name="bambu-local-http", daemon=True)
        thread.start()
        if auto_collect and runtime.status()["configured"]:
            runtime.submit("collector/start", {})
        def ready():
            if on_ready is not None:
                on_ready()
            if open_browser:
                webbrowser.open(server.dashboard_url, new=1)
        run_tray(runtime, server.dashboard_url, quit_event, on_ready=ready)
    finally:
        runtime.request_close()
        if server is not None:
            if thread is not None:
                server.shutdown()
                thread.join(3)
            server.server_close()
        if not runtime.close():
            raise LocalServiceError("service_cleanup_incomplete")
    return 0
