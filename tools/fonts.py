#!/usr/bin/env python3
"""Resolve explicit fonts and portable macOS/Linux defaults without downloading."""
import os
from pathlib import Path

CJK_CANDIDATES = (
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
)
DIGIT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
)


def resolve_font(explicit=None, *, kind="cjk"):
    """Return an installed font; configured missing paths fail clearly."""
    variable = "BAMBU_CJK_FONT" if kind == "cjk" else "BAMBU_DIGIT_FONT"
    configured = explicit or os.environ.get(variable)
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Font does not exist: {path} ({variable})")
        return path
    candidates = CJK_CANDIDATES if kind == "cjk" else DIGIT_CANDIDATES
    for candidate in candidates:
        if Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError(f"No {kind} font found; set {variable} or pass --font to an installed font file")
