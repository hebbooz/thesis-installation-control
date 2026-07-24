#!/usr/bin/env python3
"""fake_rig.py — the no-hardware Phase 1 harness (and permanent regression rig).

Stands in for the physical button box while there is no arcade encoder wired yet
(that is Phase 6). It reads the keyboard and sends warm/cool *button* events to
the server over OSC — feeding the exact same set-target path the real HID buttons
will later drive — and registers as a subscriber so it can print the live
broadcast. You drive a full visitor arc from here and watch the water respond.

    w / up-arrow : warm        c / down-arrow : cool        q : quit

The keyboard is read from the terminal (termios), NOT pygame — pygame.joystick is
reserved for the real encoder in Phase 6. Run in its own terminal alongside
`python src/server.py`. Preserve this tool: it is the regression harness for the
whole state machine, not throwaway scaffolding.
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
from pythonosc.osc_packet import OscPacket

# Reuse the one config loader rather than duplicating it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from config import load_config  # noqa: E402

HELLO_INTERVAL_S = 5.0
STATE_NAMES = {0: "Natural", 1: "Fluorescent", 2: "Bleached", 3: "Recovery"}


def _osc(address: str, *args) -> bytes:
    builder = OscMessageBuilder(address=address)
    for arg in args:
        builder.add_arg(arg)
    return builder.build().dgram


def parse_args() -> argparse.Namespace:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Keyboard button harness for the coral server.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="server address to send buttons/hello to (default: localhost)")
    ap.add_argument("--port", type=int, default=int(cfg["broadcast"]["listen_port"]),
                    help="server OSC listen port (default: from config)")
    ap.add_argument("--id", default="fake-rig", help="client id sent in /client/hello")
    return ap.parse_args()


def read_key(fd: int) -> str | None:
    """Non-blocking single keypress. Collapses arrow escape sequences to w/c."""
    if not select.select([sys.stdin], [], [], 0)[0]:
        return None
    ch = sys.stdin.read(1)
    if ch == "\x1b":  # escape — an arrow key sends "\x1b[A" etc.
        seq = ""
        while select.select([sys.stdin], [], [], 0)[0]:
            seq += sys.stdin.read(1)
        if seq == "[A":
            return "w"  # up = warm
        if seq == "[B":
            return "c"  # down = cool
        return None
    return ch


def render(status: dict) -> str:
    intensity = status["intensity"]
    filled = int(round(intensity * 20))
    bar = "#" * filled + "-" * (20 - filled)
    name = STATE_NAMES.get(status["state"], "?")
    return (f"\rT={status['temp']:6.2f}C  state={status['state']}:{name:<11s}  "
            f"intensity[{bar}] {intensity:4.2f}  last sent: {status['sent']:<4s}")


def run(args: argparse.Namespace) -> None:
    server = (args.host, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))   # ephemeral port; server replies here (source addr)
    sock.setblocking(False)

    status = {"temp": float("nan"), "state": 0, "intensity": 0.0, "sent": "-"}
    print(f"fake_rig -> server {args.host}:{args.port}  (id={args.id!r})")
    print("  w / up = warm    c / down = cool    q = quit\n")

    def send_hello() -> None:
        try:
            sock.sendto(_osc("/client/hello", args.id), server)
        except OSError:
            pass

    send_hello()
    last_hello = time.monotonic()
    while True:
        # Keyboard -> button events
        key = read_key(sys.stdin.fileno())
        if key in ("q", "\x03"):  # q or Ctrl-C
            break
        if key == "w":
            sock.sendto(_osc("/sim/warm"), server)
            status["sent"] = "warm"
        elif key == "c":
            sock.sendto(_osc("/sim/cool"), server)
            status["sent"] = "cool"

        # Re-register periodically so we are not pruned (heartbeat).
        now = time.monotonic()
        if now - last_hello >= HELLO_INTERVAL_S:
            send_hello()
            last_hello = now

        # Drain broadcasts -> live status line.
        updated = False
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except (BlockingIOError, OSError):
                break
            try:
                packet = OscPacket(data)
            except Exception:
                continue
            for timed in packet.messages:
                msg = timed.message
                if msg.address == "/coral/temp" and msg.params:
                    status["temp"] = float(msg.params[0])
                elif msg.address == "/coral/state" and msg.params:
                    status["state"] = int(msg.params[0])
                elif msg.address == "/coral/intensity" and msg.params:
                    status["intensity"] = float(msg.params[0])
                    updated = True
        if updated:
            sys.stdout.write(render(status))
            sys.stdout.flush()

        time.sleep(0.03)

    sock.close()


def main() -> None:
    args = parse_args()
    if not sys.stdin.isatty():
        print("fake_rig: stdin is not a TTY — keyboard disabled, running read-only.")
        # Still useful as a passive subscriber; loop without raw-mode keys.
    fd = sys.stdin.fileno()
    old = None
    try:
        if sys.stdin.isatty():
            old = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        run(args)
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\nfake_rig stopped.")


if __name__ == "__main__":
    main()
