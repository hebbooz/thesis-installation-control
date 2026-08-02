#!/usr/bin/env python3
"""loop_seam.py — close the wrap seam on a looping clip, and prove it closed.

A hold clip plays to its last frame and jumps back to its first. If the caustics
don't line up across that jump you get a visible hitch, at a strictly regular
interval, for the entire exhibition. It is the one defect that cannot be fixed
downstream: the player has no way to hide it, and it recurs forever.

The fix is to overlap the tail onto the head with a cross dissolve:

    head = 0..N          mid = N..D-N          tail = D-N..D
    out  = xfade(tail -> head over N)  ++  mid          (length D-N)

The output's last frame is input[D-N], and its first frame is also input[D-N] —
so the wrap is continuous by construction. Nothing plays backwards; the tail and
head both run forward throughout the dissolve, which is what makes this
compatible with the forward-only invariant the player relies on.

Cost: a soft spot of length N at every wrap, where the caustics are a double
exposure of two moments. With static geometry only the caustics ghost, and a
short N keeps it subtle.

    python tools/loop_seam.py healthy.mov
    python tools/loop_seam.py *.mp4 --overlap 1.5 -o looped/
    python tools/loop_seam.py healthy.mov --measure-only

Measures wrap discontinuity before and after, so the fix is verified rather than
assumed. The metric is mean luma difference between the last and first frames,
expressed as a ratio against the clip's own ordinary frame-to-frame motion — the
MEDIAN adjacent-frame difference over pairs sampled across the whole clip. A
seamless loop scores near 1x; anything above ~3x is a visible jump.

The median matters. Sampling one pair mid-clip (as this did originally) is a
lottery on footage conformed up to 30 fps from a slower capture, which carries
duplicate frames throughout: land on one and the baseline collapses to ~0, and a
perfectly good loop is reported as a 30x jump. See measure().

Every frame is grabbed as 8-bit rgb24 regardless of source depth, so the numbers
mean the same thing for a 10-bit ProRes master as for an 8-bit h264 one. Without
that, measuring a ProRes clip reports differences ~257x larger and WRAP_FLOOR —
which is defined in 8-bit units — silently stops applying.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def duration(path: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def fps(path: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(path)])
    try:
        num, _, den = r.stdout.strip().partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def grab_first(path: Path, out: Path) -> bool:
    run(["ffmpeg", "-v", "error", "-y", "-i", str(path),
         "-frames:v", "1", "-pix_fmt", "rgb24", str(out)])
    return out.exists()


def grab_last(path: Path, out: Path) -> bool:
    """Last decoded frame, via -sseof plus -update (write-and-overwrite)."""
    run(["ffmpeg", "-v", "error", "-y", "-sseof", "-1", "-i", str(path),
         "-update", "1", "-pix_fmt", "rgb24", str(out)])
    return out.exists()


def grab_pair(path: Path, t: float, out0: Path, out1: Path) -> bool:
    """
    Two consecutive frames starting near time t, using INPUT seek (-ss before -i).

    Input seek jumps by keyframe and decodes only from there, which is what makes
    sampling a 60 s 2560x800 clip nine times viable at all — select=eq(n,N) decodes
    the whole file up to N every single time, so the old approach cost a full
    decode per sample and simply never finished on the projection masters.

    The exact frame landed on does not matter here: this measures how much an
    ordinary frame-to-frame step differs, and any pair of genuinely adjacent frames
    answers that. The wrap frames, where exactness DOES matter, are grabbed by
    grab_first/grab_last instead.
    """
    tmpl = out0.parent / f"{out0.stem}_%d.png"
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path),
         "-frames:v", "2", "-pix_fmt", "rgb24", str(tmpl)])
    a, b = out0.parent / f"{out0.stem}_1.png", out0.parent / f"{out0.stem}_2.png"
    if not (a.exists() and b.exists()):
        return False
    a.replace(out0)
    b.replace(out1)
    return True


def mean_diff(a: Path, b: Path) -> float | None:
    """Mean luma difference between two stills, 0-255."""
    r = run(["ffmpeg", "-i", str(a), "-i", str(b), "-filter_complex",
             "blend=all_mode=difference,signalstats,"
             "metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"])
    for line in (r.stderr + r.stdout).splitlines():
        if "YAVG" in line:
            try:
                return float(line.split("=")[-1].strip())
            except ValueError:
                pass
    return None


BASELINE_SAMPLES = 9

# Mean luma difference (0-255) below which a wrap is grain rather than content,
# no matter how it compares to the clip's own motion. See measure().
WRAP_FLOOR = 3.0


def measure(path: Path) -> tuple[float, float, float] | None:
    """(wrap difference, adjacent-frame baseline, ratio).

    The baseline is the MEDIAN adjacent-frame difference over pairs sampled across
    the whole clip, not a single pair at the midpoint. A single pair is a lottery:
    footage conformed up to 30 fps from a slower capture — which microscopy usually
    is — carries duplicate frames throughout, and landing on one collapses the
    baseline to ~0 and reports a spectacular false JUMP on a loop that is fine.

    Observed on bleached-microscale.mp4: the midpoint pair differed by 0.05 where
    the true median was 0.94, turning a healthy 1.8x wrap into a reported 33.8x.
    The median is unbothered as long as under half the sampled pairs are duplicates.
    """
    dur = duration(path)
    rate = fps(path)
    if dur <= 0.0 or rate <= 0.0 or dur * rate < 8:
        return None

    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        if not (grab_first(path, t / "f0.png") and grab_last(path, t / "fN.png")):
            return None
        wrap = mean_diff(t / "fN.png", t / "f0.png")
        if wrap is None:
            return None

        diffs = []
        for k in range(BASELINE_SAMPLES):
            # Spread the samples over the interior, staying clear of both ends so
            # the baseline never accidentally measures the seam it is calibrating.
            at = dur * (k + 1) / (BASELINE_SAMPLES + 1)
            if at + 2.0 / rate > dur:
                continue
            if not grab_pair(path, at, t / "b0.png", t / "b1.png"):
                continue
            d = mean_diff(t / "b0.png", t / "b1.png")
            if d is not None:
                diffs.append(d)

    if not diffs:
        return None
    diffs.sort()
    base = diffs[len(diffs) // 2]

    # Absolute floor, checked before the ratio. On a near-static clip the baseline
    # is almost zero, so grain alone divides out to a huge ratio and the tool cries
    # wolf — bleached-microscale.mp4 reported 23x on a wrap of 1.7/255, which is
    # sensor noise. Amplify such a difference 20x and it is uniform speckle with no
    # structure in it, where a real jump shows the subject's edges. Seamless clips
    # here measure 1.5-2.2 from grain alone; a genuine content cut is 10+.
    if wrap < WRAP_FLOOR:
        return wrap, base, 1.0

    ratio = wrap / base if base > 0.02 else float("inf")
    return wrap, base, ratio


def close_seam(src: Path, dst: Path, overlap: float, prores: bool) -> bool:
    d = duration(src)
    if d <= overlap * 2:
        print(f"    clip is {d:.2f}s — too short for a {overlap:g}s overlap")
        return False

    chain = (
        f"[0:v]split=3[a][b][c];"
        f"[a]trim=0:{overlap},setpts=PTS-STARTPTS[head];"
        f"[b]trim={overlap}:{d - overlap},setpts=PTS-STARTPTS[mid];"
        f"[c]trim={d - overlap}:{d},setpts=PTS-STARTPTS[tail];"
        f"[tail][head]xfade=transition=fade:duration={overlap}:offset=0[blend];"
        f"[blend][mid]concat=n=2:v=1[out]"
    )
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(src),
           "-filter_complex", chain, "-map", "[out]"]
    # Profile 2 = ProRes 422 (not 3 = 422 HQ). The source is 8-bit 4:2:0 H.264, so
    # 422 at 10-bit already exceeds it comfortably; HQ only triples the file size.
    # ProRes at all is worth it for *decode* cost — intra-only, hardware-accelerated
    # on the M1 Pro, and the player runs three streams beside the server and Ableton.
    cmd += (["-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le"]
            if prores else ["-c:v", "libx264", "-crf", "16", "-preset", "slow"])
    cmd += ["-an", str(dst)]

    r = run(cmd)
    if r.returncode != 0:
        print(f"    ffmpeg failed: {r.stderr.strip().splitlines()[-1:] or ''}")
        return False
    return True


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("clips", nargs="+", type=Path)
    p.add_argument("--overlap", type=float, default=1.0,
                   help="dissolve length in seconds (default 1.0)")
    p.add_argument("-o", "--output-dir", type=Path, default=None,
                   help="default: a 'looped' folder beside the first clip")
    p.add_argument("--measure-only", action="store_true",
                   help="report seam quality without writing anything")
    p.add_argument("--x264", action="store_true", help="encode h264 instead of ProRes")
    args = p.parse_args()

    out_dir = args.output_dir or (args.clips[0].resolve().parent / "looped")
    if not args.measure_only:
        out_dir.mkdir(parents=True, exist_ok=True)

    for src in args.clips:
        if not src.exists():
            print(f"{src.name}: not found")
            continue

        before = measure(src)
        if before is None:
            print(f"{src.name}: could not measure")
            continue
        w0, b0, r0 = before
        state = "already seamless" if r0 < 3 else "JUMP"
        print(f"{src.name}\n    before: {r0:5.1f}x baseline   {state}"
              f"   (wrap {w0:.3f}, motion {b0:.3f})")

        if args.measure_only:
            continue
        if r0 < 3:
            print("    skipped — nothing to fix")
            continue

        dst = out_dir / f"{src.stem}{'.mov' if not args.x264 else '.mp4'}"
        print(f"    closing seam with a {args.overlap:g}s dissolve ...")
        if not close_seam(src, dst, args.overlap, prores=not args.x264):
            continue

        after = measure(dst)
        if after is None:
            print(f"    wrote {dst.name} (could not verify)")
            continue
        w1, b1, r1 = after
        verdict = "SEAM CLOSED" if r1 < 3 else f"still {r1:.1f}x — try a longer --overlap"
        print(f"    after:  {r1:5.1f}x baseline   {verdict}"
              f"   (wrap {w1:.3f}, motion {b1:.3f})")
        print(f"    -> {dst}  ({duration(dst):.2f}s, was {duration(src):.2f}s)")


if __name__ == "__main__":
    main()
