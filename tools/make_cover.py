#!/usr/bin/env python3
"""Create a build cover from an offline rendered first frame, without publishing."""
import argparse
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def make_cover(source, destination):
    destination = Path(destination)
    with Image.open(source) as image:
        if image.size != (640, 480):
            raise ValueError(f"Cover must be 640x480, got {image.size}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(destination, "PNG", optimize=True)
    print(f"Cover -> {destination} ({destination.stat().st_size} bytes)")
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / ".build/preview/page1-task.png")
    parser.add_argument("--output", type=Path, default=ROOT / ".build/cover.png")
    args = parser.parse_args(argv)
    make_cover(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
