"""PC cloud collector and tagged offline replay; no USB, publish or print control."""
import argparse
import json
import math
from pathlib import Path
import sys

from .config import load_config, ConfigError
from .replay import replay, ReplayError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replaying = commands.add_parser("replay", help="tagged synthetic/redacted NDJSON via common core")
    replaying.add_argument("fixture", type=Path)
    config = commands.add_parser("check-config", help="validate non-secret TOML without network")
    config.add_argument("config", type=Path)
    setup = commands.add_parser("setup", help="local guided login, settings and monitoring window")
    setup.add_argument("--config", type=Path, help="optional non-secret settings file")
    service = commands.add_parser("service", help="macOS menu-bar service with a loopback browser control panel")
    service.add_argument("--config", type=Path, help="optional non-secret settings file")
    service.add_argument("--no-browser", action="store_true", help="do not open a browser on launch")
    service.add_argument("--port", type=int, default=8765, help="loopback port (default: 8765)")
    service.add_argument("--no-auto-collect", action="store_true", help="do not restore and collect saved settings on launch")
    service.add_argument("--background", action="store_true", help="detach the tray process and wait for native readiness")
    service.add_argument("--ready-fd", type=int, help=argparse.SUPPRESS)
    service.add_argument("--install-launch-agent", action="store_true", help="explicitly install and start this user LaunchAgent")
    service.add_argument("--uninstall-launch-agent", action="store_true", help="explicitly stop and remove this user LaunchAgent")
    for name in ("login", "collect", "logout"):
        cloud = commands.add_parser(name, help={"login": "private local cloud login", "collect": "foreground cloud snapshots", "logout": "remove this local credential slot"}[name])
        cloud.add_argument("config", type=Path, help="non-secret TOML configuration")
        cloud.add_argument("--session-only", action="store_true", help="keep token only in current process memory")
        if name != "logout":
            cloud.add_argument("--login-method", choices=("password", "email-code", "sms-code"), default="password")
        if name == "collect":
            cloud.add_argument("--duration", type=float, help="stop after this many seconds (max 86400)")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            from .setup_gui import launch
            result = launch(args.config)
            if result:
                print('{"error":"setup_unavailable","hint":"Use Python with Tk and install .[cloud]; check settings path."}', file=sys.stderr)
            return result
        elif args.command == "service":
            from .local_service import LocalServiceError, run_service
            from .macos_service import MacServiceError, check_tray_available, install_launch_agent, uninstall_launch_agent
            from .service_launcher import notify, start_background
            invalid = (sum((args.install_launch_agent, args.uninstall_launch_agent, args.background)) > 1
                       or not 1 <= args.port <= 65535
                       or args.install_launch_agent and args.no_auto_collect
                       or args.ready_fd is not None and (args.ready_fd <= 2 or args.background
                                                        or args.install_launch_agent or args.uninstall_launch_agent))
            if invalid:
                print('{"error":"invalid_service_action"}', file=sys.stderr)
                return 2
            ready_fd = args.ready_fd
            def ready():
                nonlocal ready_fd
                notify(ready_fd, {"event": "service_ready"})
                ready_fd = None
            try:
                if args.background:
                    result = start_background(settings_path=args.config, port=args.port,
                                              open_browser=not args.no_browser, auto_collect=not args.no_auto_collect)
                    print(json.dumps(result))
                    return 2 if "error" in result else 0
                if args.install_launch_agent:
                    check_tray_available()
                    path = install_launch_agent(settings_path=args.config, port=args.port)
                    print(json.dumps({"event": "launch_agent_installed", "path": str(path)}))
                    return 0
                if args.uninstall_launch_agent:
                    path = uninstall_launch_agent()
                    print(json.dumps({"event": "launch_agent_removed", "existed": path is not None}))
                    return 0
                return run_service(settings_path=args.config, open_browser=not args.no_browser,
                                   port=args.port, auto_collect=not args.no_auto_collect, on_ready=ready)
            except (LocalServiceError, MacServiceError) as error:
                print(json.dumps({"error": str(error)}), file=sys.stderr)
                return 2
            finally:
                if ready_fd is not None:
                    notify(ready_fd, {"error": "service_start_failed"})
        elif args.command == "check-config":
            value = load_config(args.config)
            print(json.dumps({"state": "auth_required" if value.identity else "unconfigured",
                              "region": value.region, "selected": value.identity is not None,
                              "credential_storage": "session-memory-only"}))
        elif args.command in ("login", "collect", "logout"):
            duration = getattr(args, "duration", None)
            if duration is not None and (not 0 < duration <= 86400 or not math.isfinite(duration)):
                print('{"error":"invalid_duration"}', file=sys.stderr)
                return 2
            from .collector import cloud_command
            from .interaction import InteractionError
            from .collector import CollectorError
            from .cloud_http import HTTPError
            from .cloud_mqtt import SafeMqttError
            try:
                return cloud_command(args.command, load_config(args.config),
                                     method=getattr(args, "login_method", "password"),
                                     duration=duration, memory_only=args.session_only)
            except (InteractionError, CollectorError, HTTPError, SafeMqttError) as error:
                print(json.dumps({"error": str(error)}), file=sys.stderr)
                return 2
            except Exception:
                print('{"error":"cloud_operation_failed"}', file=sys.stderr)
                return 2
        else:
            with args.fixture.open("rb") as stream:
                for output in replay(stream):
                    print(json.dumps(output, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    except KeyboardInterrupt:
        print('{"event":"cancelled"}', file=sys.stderr)
        return 130
    except (ConfigError, ReplayError) as error:
        # These exception types contain only constant, value-free diagnostics.
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, RecursionError):
        # Do not render paths, exceptions from transports, payloads or tracebacks.
        print('{"error":"offline_input_error"}', file=sys.stderr)
        return 2
    return 0
