#!/usr/bin/env python3
"""osc_monitor.py — a static-subscriber stand-in for verifying the fan-out.

Phase 2 wires Ableton (and later the projection player) to the server as *static
subscribers* — fixed `(host, port)` targets the server always sends to and that,
unlike AR clients, never send `/client/hello`. That makes them impossible to
"see" from the server side: if Ableton isn't reacting, is the server not emitting,
or is Ableton not receiving?

This tool answers that by *becoming* the subscriber. It binds the exact port a
static subscriber listens on (Ableton's 9010 by default, read from config) and
prints the live `/coral/*` fan-out — the same bytes Ableton would get. If the
meter moves here, the server→port pipe is proven and any remaining fault is inside
Ableton's OSC device or its mapping. If it doesn't move, the fault is upstream
(server, config, or firewall) and Ableton was never the problem.

Because a UDP unicast port can only be held by one socket, run this **with Ableton
closed** (or point it at the projection port with `--name projection`). It sends
nothing back and holds no state — a pure passive listener, exactly like the real
subscribers are required to be.

    python tools/osc_monitor.py                 # bind the 'ableton' port from config
    python tools/osc_monitor.py --name projection
    python tools/osc_monitor.py --port 9010     # explicit override
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

from pythonosc.osc_packet import OscPacket

# Reuse the one config loader rather than duplicating it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from config import load_config  # noqa: E402

STATE_NAMES = {0: "Natural", 1: "Fluorescent", 2: "Bleached", 3: "Recovery"}


def resolve_port(cfg: dict, name: str, override: int | None) -> int:
    """Pick the port to bind: an explicit --port wins, else the named subscriber."""
    if override is not None:
        return override
    for sub in cfg["broadcast"].get("static_subscribers", []):
        if sub.get("name") == name:
            return int(sub["port"])
    known = ", ".join(repr(s.get("name")) for s in cfg["broadcast"].get("static_subscribers", []))
    raise SystemExit(
        f"no static subscriber named {name!r} in config (known: {known}). "
        f"Use --port to bind an explicit port."
    )


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Passive OSC monitor: stand in for a static subscriber.")
    ap.add_argument("--name", default="ableton",
                    help="static subscriber whose port to bind (default: ableton)")
    ap.add_argument("--port", type=int, default=None,
                    help="explicit port to bind, overriding --name")
    return ap.parse_args()


def render(status: dict, hz: float, count: int) -> str:
    intensity = status["intensity"]
    filled = int(round(intensity * 20))
    bar = "#" * filled + "-" * (20 - filled)
    name = STATE_NAMES.get(status["state"], "?")
    # bed lags state while quantisation holds a change back for the bar line; showing
    # both is what makes that lag visible rather than looking like a dropped packet.
    bed = "" if status["bed"] == status["state"] else f" bed={status['bed']}"
    latch = f"  latch={status['latch']:4.2f}" if status["latch"] > 0.0 else ""
    return (f"\rT={status['temp']:6.2f}C  state={status['state']}:{name:<11s}{bed}  "
            f"intensity[{bar}] {intensity:4.2f}{latch}  {hz:4.1f}Hz  n={count}")


def run(port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))   # 0.0.0.0, per project principle — never 127.0.0.1
    except OSError as exc:
        raise SystemExit(
            f"cannot bind port {port} ({exc}). Is Ableton (or another monitor) "
            f"already holding it? Close it, or point this at a free port with --port."
        )
    sock.settimeout(0.25)

    print(f"osc_monitor listening on 0.0.0.0:{port}  (Ctrl-C to quit)")
    print("  waiting for the server's fan-out — start src/server.py + a rig to drive it\n")

    status = {"temp": float("nan"), "state": 0, "intensity": 0.0, "bed": 0, "latch": 0.0}
    count = 0
    # Effective rate over a sliding window, to confirm the 5 Hz broadcast is arriving.
    window: list[float] = []
    seen_any = False
    try:
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                if not seen_any:
                    continue  # still waiting for the first packet; keep the banner up
                # A gap in an otherwise-live stream: hold the last line, note the stall.
                sys.stdout.write(render(status, _rate(window), count) + "  (no packet 250ms)")
                sys.stdout.flush()
                continue
            except OSError:
                break

            try:
                packet = OscPacket(data)   # transparently unpacks the OSC bundle
            except Exception:
                continue                    # a malformed datagram must not stall the monitor

            now = time.monotonic()
            window.append(now)
            window[:] = [t for t in window if now - t <= 2.0]
            for timed in packet.messages:
                msg = timed.message
                if msg.address == "/coral/temp" and msg.params:
                    status["temp"] = float(msg.params[0])
                elif msg.address == "/coral/state" and msg.params:
                    status["state"] = int(msg.params[0])
                elif msg.address == "/coral/intensity" and msg.params:
                    status["intensity"] = float(msg.params[0])
                elif msg.address == "/coral/bed" and msg.params:
                    status["bed"] = int(msg.params[0])
                elif msg.address == "/coral/latch" and msg.params:
                    status["latch"] = float(msg.params[0])
            count += 1
            if not seen_any:
                seen_any = True
                print("first packet received — pipe confirmed.\n")
            sys.stdout.write(render(status, _rate(window), count))
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        print()


def _rate(window: list[float]) -> float:
    """Datagrams per second over the sampling window (span, not just count/2s)."""
    if len(window) < 2:
        return 0.0
    span = window[-1] - window[0]
    return (len(window) - 1) / span if span > 0 else 0.0


def main() -> None:
    args = parse_args()
    cfg = load_config()
    port = resolve_port(cfg, args.name, args.port)
    run(port)


if __name__ == "__main__":
    main()
