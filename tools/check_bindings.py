#!/usr/bin/env python3
"""Check all page/button paths offline; optionally compare PetUI bounded_app.

The default checker inspects this component's declarative transition table. It is
not the firmware interpreter and does not establish device or USB correctness.
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_EVENTS = {
    "button.sw1.short_press": 1,
    "button.sw2.short_press": -1,
    "joystick.up": 1,
    "joystick.down": -1,
    "knob.rotate_cw": 1,
    "knob.rotate_ccw": -1,
}


def check_bindings(runtime, buttons, *, official=None):
    """Check each physical event from each page, including wraparound."""
    if runtime.get("states") != ["standby"] or runtime.get("initial_state") != "standby":
        raise ValueError("Offline checker only supports the current single standby state")
    if runtime.get("tick"):
        raise ValueError("Offline checker does not execute automatic ticks")
    for transition in runtime["transitions"]:
        if set(transition) != {"on", "from", "page"}:
            raise ValueError("Offline checker only supports unconditional on/from/page transitions")
    pages = [page["id"] for page in runtime["pages"]]
    if len(pages) != 3 or len(set(pages)) != len(pages):
        raise ValueError("Expected three distinct pages")
    if len(buttons) != 6 or {b["event"] for b in buttons} != set(EXPECTED_EVENTS):
        raise ValueError("Expected six distinct physical button events")
    if runtime["initial_page"] not in pages or runtime["initial_state"] not in runtime["states"]:
        raise ValueError("Invalid initial page or state")
    count = 0
    for page_index, page in enumerate(pages):
        for button in buttons:
            transitions = [t for t in runtime["transitions"]
                           if t.get("on") == button["action"]
                           and t.get("from") in ("*", runtime["initial_state"])]
            if len(transitions) != 1:
                raise ValueError(f"Expected one transition for {button['event']} from {page}")
            transition = transitions[0]
            target = transition.get("page")
            if target not in ("next", "prev"):
                raise ValueError(f"Unsupported page transition: {target!r}")
            direction = 1 if target == "next" else -1
            expected = (page_index + EXPECTED_EVENTS[button["event"]]) % len(pages)
            actual = (page_index + direction) % len(pages)
            if actual != expected or actual == page_index:
                raise ValueError(f"Wrong page transition: {page} / {button['event']}")
            if official is not None:
                start = official.initial(runtime)
                start["page"] = page
                after = official.action(runtime, start, button["action"])
                if after["page"] != pages[expected]:
                    raise ValueError(f"PetUI interpreter disagrees for {page} / {button['event']}")
            count += 1
    return count


def load_official(skill_dir):
    script = Path(skill_dir).expanduser() / "scripts" / "bounded_app.py"
    if not script.is_file():
        raise FileNotFoundError(f"PETUI_SKILL_DIR must contain scripts/bounded_app.py: {script}")
    spec = importlib.util.spec_from_file_location("petui_bounded_app", script)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(script.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, default=ROOT / "package")
    parser.add_argument("--official", action="store_true", help="also run configured PetUI bounded_app")
    args = parser.parse_args(argv)
    runtime = json.loads((args.package_dir / "runtime/widget.json").read_text(encoding="utf-8"))
    buttons = json.loads((args.package_dir / "buttons.json").read_text(encoding="utf-8"))
    skill_dir = os.environ.get("PETUI_SKILL_DIR")
    if args.official and not skill_dir:
        parser.error("--official requires PETUI_SKILL_DIR")
    official = load_official(skill_dir) if args.official else None
    count = check_bindings(runtime, buttons, official=official)
    print(f"Offline declarative binding check: {count} paths passed (not a firmware interpreter test)")
    if official is not None:
        print(f"Optional PetUI bounded_app comparison: {count} paths passed; device unverified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
