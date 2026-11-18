"""macOS adapters. Importing this module never installs or starts a service.

The tray reads runtime snapshots and submits bounded requests only. All AppKit
and pystray operations execute on the calling main thread, including timer ticks.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import signal
import stat
import subprocess
import sys
import threading
import webbrowser

LABEL = "com.upkiry.bambu-monitor"
_FILENAME = LABEL + ".plist"
_OWNER_KEY = "BambuMonitorLaunchAgentVersion"
_CODES = frozenset({
    "service_unsupported_platform", "service_main_thread_required",
    "tray_dependency_unavailable", "tray_start_failed", "service_invalid_argument",
    "service_unsafe_path", "service_already_installed", "service_foreign_agent",
    "service_install_failed", "service_bootstrap_failed_installed",
    "service_bootstrap_unknown_installed", "service_uninstall_failed",
})


class MacServiceError(ValueError):
    """Only fixed public codes are exposed; OS errors may contain private paths."""

    def __init__(self, code):
        self.code = code if code in _CODES else "service_install_failed"
        super().__init__(self.code)


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise MacServiceError("service_main_thread_required")


def check_tray_available():
    """Check platform/imports without creating a tray, app or runtime.

    This preflight can run before starting the HTTP listener or opening settings;
    it never instantiates a credential store or accesses configuration files.
    """
    if sys.platform != "darwin":
        raise MacServiceError("service_unsupported_platform")
    try:
        import pystray  # noqa: F401
        import AppKit  # noqa: F401
        from Foundation import NSTimer  # noqa: F401
        from PIL import Image, ImageDraw  # noqa: F401
    except Exception:
        raise MacServiceError("tray_dependency_unavailable") from None


def _presentation(status):
    phase = status.get("phase", "idle")
    connection = status.get("connection")
    if isinstance(connection, dict):
        connection = connection.get("state")
    if phase == "collecting":
        if connection == "online":
            return "目标遥测在线", "#36bd73"
        if connection == "offline":
            return "暂无目标遥测证据", "#e4ac36"
        if connection == "auth_required":
            return "云 MQTT 需重新登录 · 目标遥测未知", "#e4ac36"
        if connection == "connecting":
            return "云 MQTT 连接中 · 等待目标遥测", "#e4ac36"
        return "云 MQTT 采集中 · 等待目标遥测", "#e4ac36"
    if phase == "idle" and status.get("reason") in (
            "credential_rejected", "stored_token_unavailable", "auth_required",
            "login_required", "token_expired"):
        return "需重新登录", "#e4ac36"
    titles = {"starting": "正在启动", "stopping": "正在停止", "working": "正在处理设置",
              "closing": "正在退出", "closed": "已退出"}
    return titles.get(phase, "已停止" if status.get("configured") else "未配置"), "#92979d"


def run_tray(runtime, dashboard_url, quit_event, *, on_ready=None):
    """Run on the main thread until Quit, SIGINT/SIGTERM or quit_event.

    runtime.status() returns a detached snapshot; submit and stop_collector must
    return immediately. No controller, credentials or filesystem settings are read.
    on_ready, when supplied, runs once on main after the first visible refresh.
    """
    _main_thread()
    check_tray_available()
    import pystray
    from Foundation import NSTimer
    from PIL import Image, ImageDraw

    icon = timer = None
    previous = {}
    closing = False
    current = {}
    rendered = None
    failed = False
    ready_notified = False

    def request_quit(*_):
        # Signal handlers do no native work. The repeating main-loop timer exits.
        quit_event.set()

    def open_settings(*_):
        _main_thread()
        webbrowser.open(dashboard_url, new=1, autoraise=True)

    def start(*_):
        _main_thread()
        runtime.submit("collector/start", {})

    def stop(*_):
        _main_thread()
        runtime.stop_collector()

    def tick(_timer):
        nonlocal closing, current, rendered, failed, ready_notified
        _main_thread()
        try:
            if quit_event.is_set():
                if not closing:
                    closing = True
                    runtime.request_close()
                    icon.stop()
                return
            current = runtime.status()
            title, color = _presentation(current)
            signature = (title, color, current.get("phase"), current.get("configured"))
            if signature != rendered:
                image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                ImageDraw.Draw(image).ellipse((10, 10, 54, 54), fill=color)
                icon.icon = image
                icon.title = "Bambu Monitor · " + title
                icon.menu = pystray.Menu(
                    pystray.MenuItem(title, None, enabled=False),
                    pystray.MenuItem("打开本机设置…", open_settings),
                    pystray.MenuItem("启动采集", start, enabled=(current.get("phase") == "idle"
                                                               and bool(current.get("configured")))),
                    pystray.MenuItem("停止采集", stop,
                                     enabled=current.get("phase") in ("starting", "collecting")),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("退出服务", request_quit))
                icon.update_menu()
                rendered = signature
            icon.visible = True
            if not ready_notified:
                ready_notified = True
                if on_ready is not None:
                    on_ready()
        except Exception:
            # Never let an exception escape through an Objective-C callback.
            failed = True
            quit_event.set()
            closing = True
            icon.stop()

    try:
        icon = pystray.Icon("bambu-monitor", Image.new("RGBA", (64, 64)), "Bambu Monitor")
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_quit)
        timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, True, tick)
        # pystray's default setup runs visible=True on a worker. Leave native
        # setup to the Cocoa timer instead; Icon.run itself stays on main.
        icon.run(setup=lambda _icon: None)
        if failed:
            raise MacServiceError("tray_start_failed")
    except MacServiceError:
        raise
    except Exception:
        raise MacServiceError("tray_start_failed") from None
    finally:
        quit_event.set()
        if timer is not None:
            timer.invalidate()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if icon is not None:
            icon.stop()


def _absolute(path):
    # Deliberately lexical: resolving sys.executable destroys venv identity.
    return os.path.abspath(os.path.expanduser(os.fspath(path)))


def launch_agent_data(python, settings_path, port) -> bytes:
    """Build a current-user GUI service plist, without executing anything."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise MacServiceError("service_invalid_argument")
    try:
        interpreter = _absolute(python)
        if not os.fspath(python) or "\x00" in interpreter:
            raise ValueError
        args = [interpreter, "-m", "bambu_monitor", "service", "--no-browser", "--port", str(port)]
        if settings_path is not None:
            settings = _absolute(settings_path)
            if "\x00" in settings:
                raise ValueError
            args.extend(["--config", settings])
        return plistlib.dumps({"Label": LABEL, "ProgramArguments": args,
                               "RunAtLoad": True, "KeepAlive": False,
                               "ProcessType": "Interactive", _OWNER_KEY: 1}, sort_keys=True)
    except (ValueError, TypeError, OSError):
        raise MacServiceError("service_invalid_argument") from None


def _agent_directory(create=False):
    """Open every anchored directory without following any symlink.

    Return a directory fd so subsequent checks, creation and deletion remain
    anchored even if a parent directory is renamed concurrently.
    """
    home = Path(_absolute(Path.home()))
    target = home / "Library" / "LaunchAgents" / _FILENAME
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(target.parent.parts[1:]):
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create or index < len(home.parts) - 1:
                    raise
                os.mkdir(part, mode=0o700, dir_fd=fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return target, fd
    except BaseException:
        os.close(fd)
        raise


def _launchctl(*arguments):
    return subprocess.run(["/bin/launchctl", *arguments], stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False, timeout=15)


def install_launch_agent(settings_path=None, port=8765):
    """Explicit CLI action. Existing files are never overwritten.

    Once the plist is durably created, bootstrap failure/timeout retains it and
    reports an *_installed code: launchctl may have accepted the job already.
    """
    if sys.platform != "darwin":
        raise MacServiceError("service_unsupported_platform")
    payload = launch_agent_data(sys.executable, settings_path, port)
    directory = None
    try:
        target, directory = _agent_directory(create=True)
        try:
            descriptor = os.open(_FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
        except FileExistsError:
            raise MacServiceError("service_already_installed") from None
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
    except MacServiceError:
        raise
    except OSError:
        raise MacServiceError("service_unsafe_path") from None
    finally:
        if directory is not None:
            os.close(directory)
    try:
        result = _launchctl("bootstrap", f"gui/{os.getuid()}", str(target))
    except (OSError, subprocess.TimeoutExpired):
        raise MacServiceError("service_bootstrap_unknown_installed") from None
    if result.returncode:
        raise MacServiceError("service_bootstrap_failed_installed")
    return target


def _owned_plist(data):
    try:
        doc = plistlib.loads(data)
        args = doc["ProgramArguments"]
        if (type(doc.get(_OWNER_KEY)) is not int or doc[_OWNER_KEY] != 1
                or doc.get("Label") != LABEL or not isinstance(args, list)
                or len(args) not in (7, 9) or not isinstance(args[0], str)
                or not os.path.isabs(args[0])
                or args[1:6] != ["-m", "bambu_monitor", "service", "--no-browser", "--port"]):
            return False
        if len(args) == 9 and (args[7] != "--config" or not os.path.isabs(args[8])):
            return False
        return data == launch_agent_data(args[0], args[8] if len(args) == 9 else None, int(args[6]))
    except Exception:
        # Includes malformed XML parser errors; no file content reaches CLI errors.
        return False


def uninstall_launch_agent():
    """Validate our exact plist before bootout and unlink only its anchored name."""
    if sys.platform != "darwin":
        raise MacServiceError("service_unsupported_platform")
    directory = None
    try:
        target, directory = _agent_directory()
        # The deletion target is fixed and absolute; never accept one from plist.
        if not target.is_absolute() or target.name != _FILENAME:
            raise MacServiceError("service_unsafe_path")
        descriptor = os.open(_FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            original = os.fstat(stream.fileno())
            if not stat.S_ISREG(original.st_mode) or original.st_uid != os.getuid():
                raise MacServiceError("service_foreign_agent")
            data = stream.read(65537)
        if len(data) > 65536 or not _owned_plist(data):
            raise MacServiceError("service_foreign_agent")
        try:
            result = _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
        except (OSError, subprocess.TimeoutExpired):
            raise MacServiceError("service_uninstall_failed") from None
        if result.returncode not in (0, 3):
            raise MacServiceError("service_uninstall_failed")
        present = os.stat(_FILENAME, dir_fd=directory, follow_symlinks=False)
        if (present.st_dev, present.st_ino) != (original.st_dev, original.st_ino):
            raise MacServiceError("service_unsafe_path")
        os.unlink(_FILENAME, dir_fd=directory)
        return target
    except FileNotFoundError:
        return None
    except MacServiceError:
        raise
    except OSError:
        raise MacServiceError("service_unsafe_path") from None
    finally:
        if directory is not None:
            os.close(directory)
