#!/usr/bin/env python3
"""make_test_pattern.py — alignment chart for the two-projector canvas (Phase 8).

Physical projector setup is the long pole in Phase 8 and it does NOT depend on the
Unreal renders: position, keystone, focus, fill and black-level matching between
the two units all want a *static* image, held for minutes, with known geometry.
Reef footage is useless for that. This generates one.

The chart is built for a canvas that is two projectors butted side by side, so
every element exists to answer one question:

  outer border        is anything cropped or overscanned at the physical edges?
  seam line           do the two images meet exactly — no gap, no overlap?
  grid                is either unit keystoned? do horizontals cross the seam level?
  per-half border     where does each projector's own frame actually land?
  crosshair           is each unit centred on its half?
  corner L-marks      are the corners square, or is there pincushion/barrel?
  focus block         fine line pairs — the finest group that still resolves is focus
  greyscale ramp      identical in both halves: match black floor and gamma
  colour patches      identical in both halves: match colour temperature

The ramp and patches are duplicated per half deliberately. Two projectors are
never identical out of the box, and the only reliable way to match them is to put
the same patch on both sides of the seam and adjust until they stop disagreeing.

    python tools/make_test_pattern.py
    python tools/make_test_pattern.py --width 2560 --height 800 -o /tmp/chart.png

Display it fullscreen with no scaling (Preview → View → Actual Size, then
fullscreen). If the image is resampled, the focus block moires and the whole
exercise is void.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

GRID_PITCH = 80          # coarse grid, divides both 2560 and 800
GREY_STEPS = 11          # 0%..100% in 10% increments
COLOURS = ["FF0000", "00FF00", "0000FF", "00FFFF", "FF00FF", "FFFF00", "FFFFFF"]
FOCUS_PITCHES = [8, 6, 4, 3, 2]   # px; finest group that resolves = in focus


def box(x: int, y: int, w: int, h: int, colour: str, fill: bool = True, t: int = 1) -> str:
    """One drawbox filter. Coordinates are absolute pixels on the canvas."""
    thickness = "fill" if fill else str(t)
    return f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x{colour}:t={thickness}"


def half_elements(hx: int, hw: int, height: int) -> list[str]:
    """Everything drawn once per projector half, offset to that half's origin."""
    f: list[str] = []
    cx = hx + hw // 2

    # Per-half frame, inset so it is visibly *inside* the projector's own edge.
    f.append(box(hx + 8, 8, hw - 16, height - 16, "0080FF", fill=False, t=2))

    # Centre crosshair — 300 px arms.
    f.append(box(cx - 150, height // 2 - 1, 300, 2, "FFFFFF"))
    f.append(box(cx - 1, height // 2 - 150, 2, 300, "FFFFFF"))

    # Corner L-marks, 60 px arms, 4 px thick, inset 24 px.
    arm, thick, inset = 60, 4, 24
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        x0 = hx + inset if dx == 0 else hx + hw - inset - arm
        y0 = inset if dy == 0 else height - inset - arm
        f.append(box(x0, y0 if dy == 0 else y0 + arm - thick, arm, thick, "FFFFFF"))
        f.append(box(x0 if dx == 0 else x0 + arm - thick, y0, thick, arm, "FFFFFF"))

    # Focus block: groups of vertical line pairs at decreasing pitch. The finest
    # group still showing separate lines is the true focus limit for that unit.
    gap = 22
    block_w = sum(12 * p for p in FOCUS_PITCHES) + gap * (len(FOCUS_PITCHES) - 1)
    fx, fy, gh = cx - block_w // 2, 90, 90
    for pitch in FOCUS_PITCHES:
        for i in range(6):
            f.append(box(fx + i * pitch * 2, fy, pitch, gh, "FFFFFF"))
        fx += 12 * pitch + gap

    # Greyscale ramp — black floor and gamma. Compare across the seam.
    pw, ph, gy = 90, 70, height - 200
    total = GREY_STEPS * pw
    gx = hx + (hw - total) // 2
    for i in range(GREY_STEPS):
        v = round(255 * i / (GREY_STEPS - 1))
        f.append(box(gx + i * pw, gy, pw, ph, f"{v:02X}{v:02X}{v:02X}"))
    f.append(box(gx, gy, total, ph, "606060", fill=False, t=1))

    # Colour patches — colour temperature. Compare across the seam.
    cw, cy = 90, height - 110
    total = len(COLOURS) * cw
    px = hx + (hw - total) // 2
    for i, col in enumerate(COLOURS):
        f.append(box(px + i * cw, cy, cw, ph, col))
    f.append(box(px, cy, total, ph, "606060", fill=False, t=1))

    return f


def build_filters(width: int, height: int) -> list[str]:
    f: list[str] = []

    # Coarse grid first so everything else sits on top of it.
    f.append(f"drawgrid=w={GRID_PITCH}:h={GRID_PITCH}:t=1:c=0x303030")

    half_w = width // 2
    f += half_elements(0, half_w, height)
    f += half_elements(half_w, width - half_w, height)

    # The seam: where the two projectors meet. Drawn last so nothing hides it.
    f.append(box(half_w - 1, 0, 2, height, "FF2020"))

    # Outer border, 3 px — if any edge of this is missing, the image is cropped.
    f.append(box(0, 0, width, height, "FFFFFF", fill=False, t=3))
    return f


def main() -> None:
    default_out = Path(__file__).resolve().parent.parent / "projection" / "test-pattern.png"
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--width", type=int, default=2560)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("-o", "--output", type=Path, default=default_out)
    args = p.parse_args()

    filters = build_filters(args.width, args.height)
    cmd = [
        "ffmpeg", "-hide_banner", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=black:s={args.width}x{args.height}",
        "-vf", ",".join(filters),
        "-frames:v", "1", str(args.output),
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        sys.exit("ffmpeg not found — install it (brew install ffmpeg)")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"ffmpeg failed ({exc.returncode})")

    print(f"wrote {args.output}  ({args.width}x{args.height}, {len(filters)} elements)")
    print("display at Actual Size, fullscreen — any resampling moires the focus block")


if __name__ == "__main__":
    main()
