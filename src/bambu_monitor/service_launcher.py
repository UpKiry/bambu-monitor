"""Detach the tray process with a bounded readiness handshake, without login items."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys


def notify(fd, result):
    if fd is None:
        return
    try:
        os.write(fd, (json.dumps(result) + '\n').encode('ascii'))
    except OSError:
        pass
    finally:
        os.close(fd)


def start_background(*, settings_path=None, port=8765, open_browser=True, auto_collect=True, timeout=20):
    from .macos_service import check_tray_available
    check_tray_available()
    read_fd, write_fd = os.pipe()
    args = [sys.executable, '-m', 'bambu_monitor', 'service', '--port', str(port), '--ready-fd', str(write_fd)]
    if settings_path is not None:
        args.extend(('--config', str(Path(settings_path).expanduser().absolute())))
    if not open_browser:
        args.append('--no-browser')
    if not auto_collect:
        args.append('--no-auto-collect')
    try:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True, pass_fds=(write_fd,))
    except Exception:
        os.close(read_fd)
        raise
    finally:
        os.close(write_fd)
    try:
        readable, _, _ = select.select([read_fd], [], [], timeout)
        if not readable:
            # An unknown startup result is not permission to spawn another copy.
            return {'event':'service_startup_pending', 'pid':process.pid}
        payload = os.read(read_fd, 4096)
        try:
            result = json.loads(payload)
        except (ValueError, UnicodeError):
            return {'error':'service_start_failed'}
        if result == {'event':'service_ready'}:
            return {'event':'service_started', 'pid':process.pid, 'port':port}
        # Child errors are fixed internal categories, never network exception text.
        return {'error':'service_start_failed'}
    finally:
        os.close(read_fd)
