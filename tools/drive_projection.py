#!/usr/bin/env python3
"""drive_projection.py — drive the projection player directly, without the water (Phase 8).

`osc_monitor.py` proves the server→port pipe is alive. This is its mirror: it
*becomes* the server, emitting the same `/coral/*` fan-out at the same 5 Hz to the
projection subscriber's port, so the Unity player can be exercised on its own.

Two jobs the real server cannot do:

  * **Hold a state still.** Physical alignment (position, keystone, focus, fill)
    and black-level matching between the two projectors need a fixed image for
    minutes at a time. The server is always drifting toward a target — it has no
    "pause". Here, a state stays put until you change it.
  * **Reach rare paths on demand.** Recovery-cancelled (3→2→3) and the idle-reset
    exit (2→1 with intensity still 1.0) each take minutes of real water to
    provoke. Press two keys instead.

This does NOT shorten the latch or the recovery lag — those live in config.yaml and
stay exactly as authored (CLAUDE.md). It bypasses the state machine entirely rather
than reconfiguring it, which is why it is a bench tool and never part of a rehearsal.

Run it **with the server stopped** — both would be writing the same UDP port.

    python tools/drive_projection.py                 # manual, keys below
    python tools/drive_projection.py --arc           # scripted full arc, 1x
    python tools/drive_projection.py --arc --speed 6 # same arc, 6x faster
    python tools/drive_projection.py --state 2       # park on bleached and walk away

Keys (manual mode):
    0 1 2 3   jump to that state
    w / c     ramp intensity up / down while held down (warm / cool)
    [ / ]     nudge intensity by 0.05
    a         run the scripted arc from here
    q         quit
"""
from __future__ import annotations

import argparse
import select
import socket
import sys
import termios
import time
import tty
from pathlib import Path

from pythonosc.osc_message_builder import OscMessageBuilder

# Reuse the one config loader rather than duplicating it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from config import load_config  # noqa: E402

STATE_NAMES = {0: "Natural", 1: "Fluorescent", 2: "Bleached", 3: "Recovery"}
RAMP_PER_S = 0.08               # manual w/c ramp rate; ~12 s end to end

# Scripted arc: (label, seconds, state, intensity_from, intensity_to).
# Durations mirror the simulated thermal model in config.example.yaml so the
# fades are exercised at the pace they will really run at.
ARC = [
    ("natural",            5, 0, 0.00, 0.00),
    ("warming",           64, 1, 0.10, 0.90),   # the long reversible climb
    ("cooling off",       20, 1, 0.90, 0.50),   # reversibility: weights fall, clips do not
    ("warming again",     25, 1, 0.50, 1.00),
    ("BLEACH LATCH",      30, 2, 1.00, 1.00),   # latch one-shot fires on entry
    ("recovery",          60, 3, 1.00, 0.00),   # heal is the intensity ramp; no clip
    ("healed tail",       20, 3, 0.00, 0.00),   # state 3 persists while water cools
    ("natural",           10, 0, 0.00, 0.00),
]


def _osc(address: str, *args) -> bytes:
    builder = OscMessageBuilder(address=address)
    for arg in args:
        builder.add_arg(arg)
    return builder.build().dgram


def parse_args() -> argparse.Namespace:
    cfg = load_config()
    subs = {s["name"]: s for s in cfg["broadcast"]["static_subscribers"]}
    default = subs.get("projection", {"host": "127.0.0.1", "port": 9020})

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default=default["host"], help="projection player host")
    p.add_argument("--port", type=int, default=default["port"], help="projection player port")
    p.add_argument("--rate", type=float, default=cfg["broadcast"]["rate_hz"],
                   help="broadcast rate in Hz (match the server)")
    p.add_argument("--arc", action="store_true", help="run the scripted arc and exit")
    p.add_argument("--speed", type=float, default=1.0, help="arc speed multiplier")
    p.add_argument("--state", type=int, choices=[0, 1, 2, 3],
                   help="park on one state and hold it (for projector alignment)")
    return p.parse_args()


def read_keys(fd: int) -> str:
    """Drain everything typed since the last tick. Non-blocking, cbreak mode."""
    keys = ""
    while select.select([sys.stdin], [], [], 0)[0]:
        keys += sys.stdin.read(1)
    return keys


def temp_for(intensity: float) -> float:
    """Plausible °C for a given intensity, so the player's temp readout looks sane."""
    return 26.0 + 2.0 * intensity


class Sender:
    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addr = (host, port)

    def send(self, state: int, intensity: float) -> None:
        # Same three addresses, same order and types as broadcast.py emits.
        for dgram in (
            _osc("/coral/state", int(state)),
            _osc("/coral/intensity", float(intensity)),
            _osc("/coral/temp", float(temp_for(intensity))),
        ):
            try:
                self.sock.sendto(dgram, self.addr)
            except OSError as exc:            # fail soft, exactly like the server
                print(f"\r[send] {exc}", flush=True)


def run_arc(tx: Sender, period: float, speed: float) -> None:
    print(f"\n-- scripted arc at {speed:g}x --")
    for label, seconds, state, i_from, i_to in ARC:
        span = max(seconds / speed, period)
        started = time.monotonic()
        print(f"\n{label:>16}  state {state} ({STATE_NAMES[state]})  "
              f"intensity {i_from:.2f} -> {i_to:.2f}  over {span:.1f}s")
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= span:
                break
            k = elapsed / span
            intensity = i_from + (i_to - i_from) * k
            tx.send(state, intensity)
            print(f"\r  {STATE_NAMES[state]:<12} i={intensity:0.3f} "
                  f"T={temp_for(intensity):0.2f}C  {elapsed:5.1f}/{span:.1f}s   ", end="", flush=True)
            time.sleep(period)
        tx.send(state, i_to)
    print("\n\narc complete.")


def run_manual(tx: Sender, period: float, speed: float, fd: int) -> None:
    state, intensity = 0, 0.0
    print("\nkeys: 0-3 state | w/c ramp | [/] nudge | a arc | q quit\n")
    while True:
        keys = read_keys(fd)
        if "q" in keys:
            print("\nbye.")
            return
        if "a" in keys:
            run_arc(tx, period, speed)
            state, intensity = 0, 0.0
        for ch in keys:
            if ch in "0123":
                state = int(ch)
                # Entering 2 or leaving it mirrors what the server would publish.
                if state == 2:
                    intensity = 1.0
            elif ch == "[":
                intensity = max(0.0, intensity - 0.05)
            elif ch == "]":
                intensity = min(1.0, intensity + 0.05)
        # Held keys arrive as repeats; each one advances the ramp by one tick.
        intensity += RAMP_PER_S * period * keys.count("w")
        intensity -= RAMP_PER_S * period * keys.count("c")
        intensity = min(1.0, max(0.0, intensity))

        tx.send(state, intensity)
        print(f"\r  state {state} {STATE_NAMES[state]:<12} i={intensity:0.3f} "
              f"T={temp_for(intensity):0.2f}C   ", end="", flush=True)
        time.sleep(period)


def main() -> None:
    args = parse_args()
    period = 1.0 / max(args.rate, 0.1)
    tx = Sender(args.host, args.port)
    print(f"driving projection at {args.host}:{args.port} @ {args.rate:g} Hz "
          f"(run with the server stopped)")

    if args.state is not None:
        intensity = 1.0 if args.state >= 2 else 0.0
        print(f"holding state {args.state} ({STATE_NAMES[args.state]}) — Ctrl-C to stop")
        try:
            while True:
                tx.send(args.state, intensity)
                time.sleep(period)
        except KeyboardInterrupt:
            print("\nbye.")
        return

    if args.arc:
        try:
            run_arc(tx, period, args.speed)
        except KeyboardInterrupt:
            print("\nbye.")
        return

    if not sys.stdin.isatty():
        print("manual mode needs a TTY; use --arc or --state instead")
        return

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        run_manual(tx, period, args.speed, fd)
    except KeyboardInterrupt:
        print("\nbye.")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
