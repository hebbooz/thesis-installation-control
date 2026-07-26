#!/usr/bin/env python3
"""fake_client.py — a pretend AR device for hardening the client registry (Phase 3).

Stands in for a phone running the AR app before any real devices are in hand. It
does exactly what PROTOCOL.md §2 requires of an AR client and nothing more:

  * announces itself with ``/client/hello`` every 5 s, and
  * listens for the server's ``/coral/*`` fan-out, printing a live status line.

Both happen on ONE bidirectional UDP socket. This is the crux of the protocol:
the server replies to the *source* ``(ip, port)`` of the hello packet, so a client
that transmits hello from a different socket than it listens on would register but
never receive a broadcast. We send and receive on the same socket, exactly as a
real phone (extOSC transmitter local-port == receiver port) must.

Run several at once to exercise the registry — each instance binds its own
ephemeral port and carries a unique id (the registry is keyed by id, so distinct
ids are what let three clients on one laptop each get their own fan-out):

    python tools/fake_client.py            # terminal 3
    python tools/fake_client.py            # terminal 4
    python tools/fake_client.py            # terminal 5

Then check the Phase 3 acceptance behaviours: all instances show identical,
synchronised values; kill one and the server prunes it within 15 s; restart it and
it resyncs within one broadcast interval; start clients before the server and they
converge silently once it appears; restart the server mid-arc and every client
reconverges with no intervention. This tool is a permanent part of the regression
harness, not throwaway scaffolding.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path

from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_packet import OscPacket

# Reuse the one config loader rather than duplicating it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from config import load_config  # noqa: E402

HELLO_INTERVAL_S = 5.0          # re-announce well inside the 15 s prune window
STALL_AFTER_S = 1.0             # silence longer than this (≈5 missed broadcasts) = stalled
STATE_NAMES = {0: "Natural", 1: "Fluorescent", 2: "Bleached", 3: "Recovery"}


def _osc(address: str, *args) -> bytes:
    builder = OscMessageBuilder(address=address)
    for arg in args:
        builder.add_arg(arg)
    return builder.build().dgram


def parse_args() -> argparse.Namespace:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Pretend AR client: registers via hello, prints the fan-out.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="server address to send hello to (default: localhost)")
    ap.add_argument("--port", type=int, default=int(cfg["broadcast"]["listen_port"]),
                    help="server OSC listen port (default: from config)")
    ap.add_argument("--id", default=f"fake-client-{os.getpid()}",
                    help="client id sent in /client/hello (default: unique per process)")
    ap.add_argument("--listen-port", type=int, default=0,
                    help="local UDP port to bind (default: 0 = ephemeral, so many "
                         "instances coexist; a real phone would pin this to client_port)")
    return ap.parse_args()


def render(status: dict, hz: float, count: int, stalled: bool) -> str:
    intensity = status["intensity"]
    filled = int(round(intensity * 20))
    bar = "#" * filled + "-" * (20 - filled)
    name = STATE_NAMES.get(status["state"], "?")
    tail = "  (server silent)" if stalled else ""
    return (f"\rT={status['temp']:6.2f}C  state={status['state']}:{name:<11s}  "
            f"intensity[{bar}] {intensity:4.2f}  {hz:4.1f}Hz  n={count}{tail}")


def _rate(window: list[float]) -> float:
    """Datagrams per second over the sampling window (span, not just count/2s)."""
    if len(window) < 2:
        return 0.0
    span = window[-1] - window[0]
    return (len(window) - 1) / span if span > 0 else 0.0


def note(message: str) -> None:
    """Print a lifecycle event on its own line, above the refreshing status line."""
    sys.stdout.write("\n" + message + "\n")
    sys.stdout.flush()


def run(args: argparse.Namespace) -> None:
    server = (args.host, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # 0.0.0.0 per project principle; port 0 = ephemeral. The server records whatever
    # source (ip, port) this socket sends hello from and unicasts the fan-out back here.
    sock.bind(("0.0.0.0", args.listen_port))
    sock.settimeout(0.25)   # wake regularly to re-send hello and refresh the line even when silent
    bound_port = sock.getsockname()[1]

    print(f"fake_client id={args.id!r} -> server {args.host}:{args.port}  "
          f"(listening on 0.0.0.0:{bound_port})")
    print("  sending /client/hello every 5 s; waiting for the fan-out. Ctrl-C to quit.")

    def send_hello() -> None:
        try:
            sock.sendto(_osc("/client/hello", args.id), server)
        except OSError:
            pass  # server may not be up yet — keep trying, it will answer once it is

    status = {"temp": float("nan"), "state": 0, "intensity": 0.0}
    count = 0
    window: list[float] = []          # packet arrival times, for the effective-rate readout
    connected = False                 # have we ever received a broadcast?
    stalled = False                   # connected, but the stream has since gone silent
    last_packet = 0.0

    send_hello()
    last_hello = time.monotonic()
    try:
        while True:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                data = None
            except OSError:
                break

            now = time.monotonic()
            if data is not None:
                try:
                    packet = OscPacket(data)   # transparently unpacks the OSC bundle
                except Exception:
                    packet = None              # a malformed datagram must never stall the client
                if packet is not None:
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
                    count += 1
                    last_packet = now
                    if not connected:
                        connected = True
                        note("connected — first broadcast received, converged to server state.")
                    elif stalled:
                        stalled = False
                        note("reconnected — fan-out resumed, resynced.")

            # Heartbeat: re-announce on schedule so we are never pruned.
            if now - last_hello >= HELLO_INTERVAL_S:
                send_hello()
                last_hello = now

            # Detect an interrupted stream (server restarted / stopped) once we've connected.
            if connected and not stalled and last_packet and now - last_packet > STALL_AFTER_S:
                stalled = True
                note("server silent — no broadcast; will resync automatically when it returns.")

            if connected:
                sys.stdout.write(render(status, _rate(window), count, stalled))
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        print()


def main() -> None:
    args = parse_args()
    run(args)


if __name__ == "__main__":
    main()
