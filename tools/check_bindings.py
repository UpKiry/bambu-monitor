#!/usr/bin/env python3
"""Assert that every declared button actually changes the visible page.

A binding that merely exists in buttons.json is not evidence of anything: this
runs the real transition table from bounded_app and reports each physical input's
before/after page. Exit code is non-zero if any binding is a no-op.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, "/Users/upkiry/.agents/skills/petui/scripts")
import bounded_app  # noqa: E402

runtime = json.loads((ROOT / "package" / "runtime" / "widget.json").read_text(encoding="utf-8"))
buttons = json.loads((ROOT / "package" / "buttons.json").read_text(encoding="utf-8"))

start = bounded_app.initial(runtime)
print("initial page: %s" % start["page"])

failed = []
for b in buttons:
    after = bounded_app.action(runtime, start, b["action"])
    changed = after["page"] != start["page"]
    if not changed:
        failed.append(b["event"])
    print("%-24s %-12s %-14s -> %-6s %s"
          % (b["event"], b["action"], b["label"], after["page"],
             "ok" if changed else "NO-OP"))

if failed:
    print("\nbindings with no visible effect: %s" % ", ".join(failed))
    raise SystemExit(1)
print("\nall %d bindings change the page" % len(buttons))
