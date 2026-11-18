#!/usr/bin/env python3
"""Offline project entry point. Nothing here publishes, syncs, or connects to a printer."""
import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

try:
    from .build_package import generate_package, write_package
    from .check_bindings import check_bindings, load_official
except ImportError:
    from build_package import generate_package, write_package
    from check_bindings import check_bindings, load_official

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_FILES = (
    "component.json",
    "negative-screen.json",
    "buttons.json",
    "share.json",
    "runtime/widget.json",
    "assets/glyphs.png",
    "pc/cover.png",
)
JSON_FILES = PACKAGE_FILES[:5]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))



def safe_workspace_path(path, root=ROOT):
    """Reject symlinks and writes outside the explicit project root."""
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Output must stay inside the project workspace: {path}") from error
    current = root.resolve()
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Refusing symlink in workspace path: {current}")
    return current


def require_regular_file(path, root=ROOT):
    path = safe_workspace_path(path, root)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"Expected an ordinary file: {path}")
    return path


def safe_output(path, root=ROOT):
    path = safe_workspace_path(path, root)
    canonical_root = Path(root).resolve()
    if path == canonical_root:
        raise ValueError("Output cannot be the project root")
    for name in ("package", "design", "dist"):
        protected = canonical_root / name
        if path == protected or protected in path.parents or path in protected.parents:
            raise ValueError(f"Output must not overwrite source or released resources: {path}")
    return path



def prepare_output_files(directory, filenames):
    """Check every preview/glyph output before rendering can write anything."""
    directory = safe_output(directory)
    if directory.exists() and not directory.is_dir():
        raise ValueError(f"Output directory is not a directory: {directory}")
    for filename in filenames:
        target = safe_output(directory / filename)
        if target.exists():
            require_regular_file(target)
    return directory


def build(output_dir=None, *, root=ROOT):
    """Stage generated JSON and only the two approved checked-in PNGs."""
    root = Path(root).resolve()
    output_dir = Path(output_dir) if output_dir else root / ".build/package"
    output_dir = safe_output(output_dir, root)
    source = safe_workspace_path(root / "package", root)
    for relative in PACKAGE_FILES:
        require_regular_file(source / relative, root)
    for relative in PACKAGE_FILES:
        safe_workspace_path(output_dir / relative, root)
    if output_dir == source or source in output_dir.parents or output_dir in source.parents:
        raise ValueError("Build directory must be separate from the source package")
    if output_dir.exists():
        entries = list(output_dir.rglob("*"))
        for path in entries:
            safe_workspace_path(path, root)
            if not path.is_dir() and not stat.S_ISREG(path.stat().st_mode):
                raise ValueError(f"Non-ordinary file in build directory: {path}")
        extras = {str(path.relative_to(output_dir)).replace(os.sep, "/")
                  for path in entries if path.is_file()} - set(PACKAGE_FILES)
        if extras:
            raise ValueError(f"Build directory has unlisted files; choose a fresh --build-dir: {sorted(extras)}")
    metrics = read_json(root / "tools/glyph_metrics.json")
    write_package(output_dir, metrics)
    for relative in PACKAGE_FILES[5:]:
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    print(f"Build: {len(PACKAGE_FILES)} allowlisted files -> {output_dir}; checked-in atlas reused")
    return output_dir


def check_structure(runtime, buttons):
    variables = runtime["vars"]
    for name, definition in variables.items():
        value = definition["init"]
        expected_type = {"int": int, "string": str}.get(definition["type"])
        if expected_type is None or type(value) is not expected_type:
            raise ValueError(f"Invalid variable initializer: {name}")
    bindings = {node["var_bind"] for node in runtime["surface"]["elements"] if "var_bind" in node}
    if bindings != set(variables):
        raise ValueError(f"Variable bindings differ: declared={set(variables)}, used={bindings}")
    if len(variables) != 13:
        raise ValueError("Expected the unchanged 13-variable display contract")
    pages = runtime["pages"]
    for node in runtime["surface"]["elements"]:
        if "page" in node and node["page"] not in range(len(pages)):
            raise ValueError(f"Invalid element page: {node['page']}")
    count = check_bindings(runtime, buttons)
    print(f"Offline declarative bindings: {count} paths passed (not firmware interpreter execution)")


def check(*, root=ROOT, official=False):
    """Run local consistency, resource, schema and unittest checks without networking."""
    from PIL import Image
    try:
        from jsonschema import Draft202012Validator
    except ImportError as error:
        raise RuntimeError("check requires dev dependencies: python -m pip install -e '.[dev]'") from error
    root = Path(root).resolve()
    generated = generate_package(read_json(root / "tools/glyph_metrics.json"))
    if set(generated) != set(JSON_FILES):
        raise ValueError("Generator JSON allowlist changed")
    for relative in JSON_FILES:
        if generated[relative] != read_json(root / "package" / relative):
            raise ValueError(f"Generated JSON differs from checked-in source: {relative}")
    print("Generated JSON matches all five checked-in documents")
    runtime = generated["runtime/widget.json"]
    check_structure(runtime, generated["buttons.json"])
    if official:
        skill_dir = os.environ.get("PETUI_SKILL_DIR")
        if not skill_dir:
            raise ValueError("--official requires PETUI_SKILL_DIR")
        count = check_bindings(runtime, generated["buttons.json"], official=load_official(skill_dir))
        print(f"Optional PetUI bounded_app comparison: {count} paths passed; device unverified")
    for relative, dimensions in (("assets/glyphs.png", (192, 192)), ("pc/cover.png", (640, 480))):
        with Image.open(root / "package" / relative) as image:
            if image.format != "PNG" or image.size != dimensions:
                raise ValueError(f"Unexpected image format/dimensions: {relative}")
            image.verify()
    schemas = sorted((root / "schemas").glob("*.schema.json"))
    if not schemas:
        raise ValueError("No schemas found in schemas/")
    for path in schemas:
        schema = read_json(path)
        Draft202012Validator.check_schema(schema)
    print(f"JSON Schema syntax: {len(schemas)} document(s) passed")
    if not (root / "tests").is_dir():
        raise ValueError("Missing tests/; refusing an empty test run")
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, check=True)
    print("Offline checks passed. Cloud, LAN, USB and device runtime are unverified.")


def resolve_petui(configured=None):
    configured = configured or os.environ.get("PETUI_BIN")
    if configured:
        candidate = shutil.which(str(configured))
        if not candidate:
            raise FileNotFoundError(f"Configured PetUI command not executable: {configured}")
        return candidate
    discovered = shutil.which("petui")
    if discovered:
        return discovered
    name = "petui.cmd" if os.name == "nt" else "petui"
    candidate = Path.home() / ".claw-pet/authoring" / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    raise FileNotFoundError("Official PetUI is required for pack; set PETUI_BIN or pass --petui")


def display_preview(output_dir=None, *, font=None):
    """Render the display-model pages; never touches the shipped package."""
    try:
        from .render_display import output_filenames, render_contact, render_scenarios
    except ImportError:
        from render_display import output_filenames, render_contact, render_scenarios
    directory = prepare_output_files(output_dir or ROOT / ".build/display-preview",
                                     output_filenames())
    frames = render_scenarios(directory, font)
    contact = render_contact(frames, directory / "review.png")
    print(f"Display review sheet: {contact}; shipped package unchanged")
    return directory


def pack(output=None, *, build_dir=None, petui=None):
    """Use the official packer and validator; never publish or sync."""
    command = resolve_petui(petui)
    package_dir = build(build_dir)
    output = Path(output) if output else ROOT / ".build/bambu-monitor-0.1.6.hachiapp"
    output = safe_output(output)
    if output.suffix != ".hachiapp" or package_dir == output or package_dir in output.parents:
        raise ValueError("Pack output must be a .hachiapp file outside the build package")
    if output.exists():
        require_regular_file(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging_parent = safe_output(ROOT / ".build/staging")
    staging_parent.mkdir(parents=True, exist_ok=True)
    # Keep this small staging directory for audit; never remove user directories.
    staging = Path(tempfile.mkdtemp(prefix="pack-", dir=staging_parent))
    for relative in PACKAGE_FILES:
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package_dir / relative, destination)
    subprocess.run([command, "pack", str(staging), str(output)], check=True)
    subprocess.run([command, "validate", str(output)], check=True)
    print(f"Official PetUI pack and validate passed: {output}; not published or synced")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    checking = subparsers.add_parser("check", help="offline checks, schema validation and unit tests")
    checking.add_argument("--official", action="store_true", help="also compare PETUI_SKILL_DIR bounded_app")
    building = subparsers.add_parser("build", help="build seven-file package without regenerating fonts")
    building.add_argument("--build-dir", type=Path, default=ROOT / ".build/package")
    preview = subparsers.add_parser("preview", help="build and render three offline pages")
    preview.add_argument("--build-dir", type=Path, default=ROOT / ".build/package")
    preview.add_argument("--output-dir", type=Path, default=ROOT / ".build/preview")
    preview.add_argument("--font", type=Path, help="CJK font, or BAMBU_CJK_FONT")
    display = subparsers.add_parser("display-preview",
                                    help="render the display-model pages and degraded states")
    display.add_argument("--output-dir", type=Path, default=ROOT / ".build/display-preview")
    display.add_argument("--font", type=Path, help="CJK font, or BAMBU_CJK_FONT")
    packing = subparsers.add_parser("pack", help="official PetUI pack and validate only")
    packing.add_argument("--build-dir", type=Path, default=ROOT / ".build/package")
    packing.add_argument("--output", type=Path, default=ROOT / ".build/bambu-monitor-0.1.6.hachiapp")
    packing.add_argument("--petui", help="official PetUI command, or PETUI_BIN")
    glyphs = subparsers.add_parser("glyphs", help="explicit glyph regeneration to an isolated directory")
    glyphs.add_argument("--output-dir", type=Path, default=ROOT / ".build/glyphs")
    glyphs.add_argument("--font", type=Path, help="digit font, or BAMBU_DIGIT_FONT")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            check(official=args.official)
        elif args.command == "build":
            build(args.build_dir)
        elif args.command == "preview":
            try:
                from .render_frames import render_all
            except ImportError:
                from render_frames import render_all
            directory = prepare_output_files(args.output_dir, ("page1-task.png", "page2-environment.png", "page3-ams.png"))
            render_all(build(args.build_dir), directory, args.font)
        elif args.command == "display-preview":
            display_preview(args.output_dir, font=args.font)
        elif args.command == "pack":
            pack(args.output, build_dir=args.build_dir, petui=args.petui)
        elif args.command == "glyphs":
            try:
                from .build_glyph_atlas import build_atlas
            except ImportError:
                from build_glyph_atlas import build_atlas
            directory = prepare_output_files(args.output_dir, ("glyphs.png", "glyph_metrics.json"))
            build_atlas(directory / "glyphs.png", directory / "glyph_metrics.json", args.font)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
