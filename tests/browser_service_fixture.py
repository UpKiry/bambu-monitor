"""Manual browser verification fixture. Synthetic transports; workspace settings only.

Run with the project Python, then run browser_service_check.cjs with that Python.
Never use this fixture as live data or bind it outside loopback.
"""
import json
from pathlib import Path
import signal
import sys
from threading import Thread
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_local_service import synthetic_controller
from bambu_monitor.service_runtime import ServiceRuntime
from bambu_monitor.local_service import LocalControlServer


def main():
    with TemporaryDirectory(dir=Path('.build').resolve()) as folder:
        runtime = ServiceRuntime(controller=synthetic_controller(Path(folder) / 'provider.toml'))
        server = LocalControlServer(('127.0.0.1', 0), runtime=runtime)
        def stop(*_):
            Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        print(json.dumps({'url':server.dashboard_url}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            runtime.close()


if __name__ == '__main__':
    main()
