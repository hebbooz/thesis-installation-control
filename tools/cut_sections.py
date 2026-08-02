#!/usr/bin/env python3
"""cut_sections.py — cut the projection clips out of one master render (Phase 8).

The four projection clips are meant to come from a **single continuous master
timeline**, so that the look at the end of one section is by construction the look
at the start of the next. This cuts that master into the sections without touching
the picture: same resolution, same framing, same colour.

Why not an NLE: iMovie cannot hold a 2560x800 canvas at all (fixed 16:9 presets
only) and would reshape the footage on the way through. Any editor that *can* hold
it will do the job, but cutting by timecode is a scripted operation — reproducible,
re-runnable when a boundary moves, and it cannot silently resample.

    python tools/cut_sections.py master.mov \\
      --cut coral-healthy=0,60 \\
      --cut coral-fluorescent=90,60 \\
      --cut coral-bleached=180,60 \\
      --cut coral-fluorescent-to-bleached=155,20

start and duration accept seconds (90, 12.5) or timecode (00:01:30, 1:30.5).

Cuts are **frame-accurate**: ffmpeg seeks to the preceding keyframe, decodes
forward and discards, then re-encodes to ProRes 422. `--copy` is faster but snaps
each cut to the nearest keyframe, so the durations come out wrong by up to a
second — never use it for the loops, whose length has to be exact.

Every output is probed afterwards and checked against what was asked for, because
a cut that lands half a second off is invisible until the loop seam is wrong.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

CANVAS = (2560, 800)


def parse_time(value: str) -> float:
    """Seconds from either a plain number or HH:MM:SS(.mmm) / MM:SS."""
    value = value.strip()
    if ":" not in value:
        return float(value)
    parts = [float(p) for p in value.split(":")]
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def parse_cut(spec: str) -> tuple[str, float, float]:
    """`name=start,duration` -> (name, start_s, duration_s)."""
    try:
        name, times = spec.split("=", 1)
        start, duration = times.split(",", 1)
        return name.strip(), parse_time(start), parse_time(duration)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"bad --cut {spec!r}; expected name=start,duration (e.g. coral-healthy=0,60)"
        )


def probe(path: Path) -> dict:
    """Width, height, fps and duration for a file, or {} if unreadable."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height,r_frame_rate:format=duration",
           "-of", "json", str(path)]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    num, _, den = (stream.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": fps,
        "duration": float(data.get("format", {}).get("duration", 0.0)),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("master", type=Path, help="the continuous master render")
    p.add_argument("--cut", type=parse_cut, action="append", required=True,
                   metavar="NAME=START,DURATION", help="repeatable")
    p.add_argument("-o", "--output-dir", type=Path, default=Path("."))
    p.add_argument("--copy", action="store_true",
                   help="stream-copy instead of re-encoding (fast, NOT frame-accurate)")
    p.add_argument("--ext", default=".mov", help="output extension (default .mov)")
    p.add_argument("--dry-run", action="store_true", help="print the commands only")
    args = p.parse_args()

    if not args.master.exists():
        sys.exit(f"master not found: {args.master}")

    info = probe(args.master)
    if not info:
        sys.exit(f"could not probe {args.master} — is ffmpeg installed?")
    print(f"master: {args.master.name}  {info['width']}x{info['height']}  "
          f"{info['fps']:.3f} fps  {info['duration']:.2f}s")

    if (info["width"], info["height"]) != CANVAS:
        print(f"  ⚠ not {CANVAS[0]}x{CANVAS[1]} — the cuts preserve this size, "
              f"they do not correct it")
    if abs(info["fps"] - round(info["fps"])) > 0.01:
        print(f"  ⚠ {info['fps']:.3f} fps is non-integer; 30 or 60 avoids judder "
              f"on the 60 Hz projectors")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for name, start, duration in args.cut:
        out = args.output_dir / f"{name}{args.ext}"
        if start + duration > info["duration"] + 0.05:
            print(f"  ⚠ {name}: {start:g}+{duration:g}s runs past the master's "
                  f"{info['duration']:.2f}s")

        # -ss before -i seeks fast; ffmpeg still decodes forward from the preceding
        # keyframe and discards, so with a re-encode the cut is frame-accurate.
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
               "-ss", f"{start}", "-i", str(args.master), "-t", f"{duration}"]
        if args.copy:
            cmd += ["-c", "copy"]
        else:
            # Profile 2 = ProRes 422, not 3 = 422 HQ. From an 8-bit 4:2:0 source,
            # 10-bit 4:2:2 already exceeds the original; HQ triples the size for
            # nothing. ProRes at all is for decode cost, not picture quality.
            cmd += ["-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le"]
        cmd += ["-an", str(out)]          # audio belongs to Ableton, never the video

        if args.dry_run:
            print("  " + " ".join(cmd))
            continue

        print(f"  cutting {name}: {start:g}s +{duration:g}s ...", end=" ", flush=True)
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"FAILED ({exc.returncode})")
            results.append((name, duration, None, None))
            continue

        got = probe(out)
        print("ok")
        results.append((name, duration, got.get("duration"), (got.get("width"), got.get("height"))))

    if args.dry_run or not results:
        return

    print("\nverification")
    ok = True
    for name, want, got, size in results:
        if got is None:
            print(f"  {name:<34} FAILED")
            ok = False
            continue
        drift = got - want
        flag = "" if abs(drift) < 0.1 else f"   ⚠ {drift:+.2f}s off"
        if flag:
            ok = False
        size_flag = "" if size == CANVAS else f"   ⚠ {size[0]}x{size[1]}"
        print(f"  {name:<34} {got:6.2f}s (asked {want:g}s){flag}{size_flag}")

    # Deliberately only about timing: a size mismatch is a property of the master,
    # already warned about above, and is not something a cut can or should fix.
    print("\nall cuts landed at the requested durations." if ok else
          "\nsome cuts drifted — re-cut without --copy, or check the boundaries.")
    print("Reminder: the three loops still need their wrap seams closed.")


if __name__ == "__main__":
    main()
