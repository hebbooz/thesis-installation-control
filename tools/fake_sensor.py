#!/usr/bin/env python3
"""fake_sensor.py — a stand-in ESPHome temperature sensor for exercising Phase 5.

Emulates the ESPHome web-server endpoint the orchestration server polls in real
mode (PROTOCOL.md §4), so `temperature.mode: real` can be verified end-to-end with
zero hardware — the same "verify without hardware first" step fake_plug provides
for the actuation plane:

    GET /sensor/water_temperature
    -> {"id":"sensor-water_temperature","value":26.42,"state":"26.42 °C"}

The served value follows a thermal model identical to the simulated source's — T
drifts toward a target at the configured heat/cool rates — but the target is driven
from *this* terminal's keyboard, standing in for the heater/fan physically moving
the water. That lets you drive the full arc through the real ingestion path and
prove the architectural claim: swapping the fake rig for a real sensor changes
*nothing* above the seam.

    w / up-arrow : warm      c / down-arrow : cool
    b            : toggle a "broken probe" (serves value: null)
    q            : quit

Run it in its own terminal, point config.yaml at it, and set mode: real::

    python tools/fake_sensor.py --port 8085

    # config.yaml
    temperature:
      mode: real
      real:
        sensor_url: "http://127.0.0.1:8085/sensor/water_temperature"

Then start the server and press w/c here: the server polls this endpoint, derives
state, and broadcasts exactly as it will off the real DS18B20. Press `b` to blind
the probe mid-arc and watch the server log `SENSOR read failed … holding last
reading` and keep broadcasting — the fail-soft requirement (PROTOCOL.md §4).

This is a test/diagnostic harness, not part of the exhibition runtime.
"""
from __future__ import annotations

import argparse
import json
import select
import sys
import termios
import threading
import time
import tty
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# Reuse the one config loader rather than duplicating it (as fake_rig does).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from config import load_config  # noqa: E402


class _Sensor:
    """The thermal model behind the endpoint: T drifts toward a keyboard target.

    Mirrors SimulatedSource so the fake sensor and the fake rig warm and cool at
    the same rates. ``broken`` blinds the probe (the endpoint then serves a null
    value, as ESPHome does for a disconnected DS18B20) to exercise fail-soft.
    """

    def __init__(self, cfg: dict) -> None:
        sim = cfg["temperature"]["simulated"]
        self.temp = float(sim["start_temp"])
        self.heat_rate = float(sim["heat_rate"])
        self.cool_rate = float(sim["cool_rate"])
        self.warm_target = float(cfg["targets"]["warm"])
        self.cool_target = float(cfg["targets"]["cool"])
        self.target = self.temp
        self.broken = False
        # Serialised: the HTTP handler thread reads temp/broken while the main
        # loop advances them. A lock keeps a read from tearing across an update.
        self.lock = threading.Lock()

    def advance(self, dt: float) -> None:
        with self.lock:
            if self.target > self.temp:
                self.temp = min(self.target, self.temp + self.heat_rate * dt)
            elif self.target < self.temp:
                self.temp = max(self.target, self.temp - self.cool_rate * dt)

    def reading(self) -> float | None:
        """Current value the endpoint should serve, or None for a broken probe."""
        with self.lock:
            return None if self.broken else self.temp


def _make_handler(sensor: _Sensor):
    class ESPHomeHandler(BaseHTTPRequestHandler):
        # Silence the default per-request stderr logging; the status line owns stdout.
        def log_message(self, *_args) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            path = urlparse(self.path).path
            # Serve any /sensor/<id> path (the server only ever polls the one URL);
            # anything else is a 404, as the real ESPHome web server would return.
            if not path.startswith("/sensor/"):
                self._json(404, {"error": "not found"})
                return
            sensor_id = "sensor-" + path.rsplit("/", 1)[-1]
            value = sensor.reading()
            # ESPHome shape: `value` is the machine-readable number (null on a bad
            # probe); `state` is the human string carrying the unit.
            state = "NaN" if value is None else f"{value:.2f} °C"
            self._json(200, {"id": sensor_id, "value": value, "state": state})

        def _json(self, code: int, body: dict) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return ESPHomeHandler


def _read_key(fd: int) -> str | None:
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


def _render(sensor: _Sensor) -> str:
    with sensor.lock:
        temp, target, broken = sensor.temp, sensor.target, sensor.broken
    aim = "warm" if target > sensor.cool_target else "cool"
    probe = "BROKEN (serving null)" if broken else "ok"
    return f"\rserving T={temp:6.2f}C  ->{aim:<4s}  probe:{probe:<21s}"


def run(sensor: _Sensor, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), _make_handler(sensor))
    # Serve on a daemon thread; the main thread owns the keyboard + thermal model.
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    url = f"http://{host}:{port}/sensor/water_temperature"
    print(f"fake_sensor serving {url}")
    print("  w / up = warm    c / down = cool    b = toggle broken probe    q = quit\n")

    interactive = sys.stdin.isatty()
    if not interactive:
        print("fake_sensor: stdin is not a TTY — keyboard disabled, holding start temp.")

    last = time.monotonic()
    try:
        while True:
            key = _read_key(sys.stdin.fileno()) if interactive else None
            if key in ("q", "\x03"):  # q or Ctrl-C
                break
            if key == "w":
                sensor.target = sensor.warm_target
            elif key == "c":
                sensor.target = sensor.cool_target
            elif key == "b":
                with sensor.lock:
                    sensor.broken = not sensor.broken

            now = time.monotonic()
            sensor.advance(now - last)
            last = now

            sys.stdout.write(_render(sensor))
            sys.stdout.flush()
            time.sleep(0.05)
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Stand-in ESPHome sensor for Phase 5 testing.")
    ap.add_argument("--port", type=int, default=8085, help="HTTP port to serve on")
    # Bind 0.0.0.0 by convention (CLAUDE.md): localhost testing and a real LAN
    # differ only by the address in config, never by code.
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    args = ap.parse_args()

    sensor = _Sensor(cfg)
    fd = sys.stdin.fileno()
    old = None
    try:
        if sys.stdin.isatty():
            old = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        run(sensor, args.host, args.port)
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\nfake_sensor stopped.")


if __name__ == "__main__":
    main()
