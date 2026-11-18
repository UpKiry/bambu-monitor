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
