#!/usr/bin/env python3
"""fake_plug.py — a stand-in Tasmota smart plug for exercising Phase 4 actuation.

Emulates just enough of the Athom/Tasmota HTTP command API (PROTOCOL.md §3) to
verify the actuation plane with zero hardware:

    GET /cm?cmnd=Power%20On     -> turns the plug on,  replies {"POWER":"ON"}
    GET /cm?cmnd=Power%20Off    -> turns the plug off, replies {"POWER":"OFF"}
    GET /cm?cmnd=Power          -> query only,         replies {"POWER":<current>}

Every command prints a timestamped line with a big ON/OFF banner, so a row of
these terminals makes the plugs' behaviour visible exactly as a lamp plugged into
each real plug would — the Phase 4 acceptance test, run on localhost.

Run one process per plug on its own port, then point config.yaml at them and set
``plugs.enabled: true`` (temperature can stay ``simulated``)::

    python tools/fake_plug.py --name heater --port 8091
    python tools/fake_plug.py --name fan    --port 8092
    python tools/fake_plug.py --name lamp   --port 8093

    # config.yaml
    plugs:
      enabled: true
      heater: "http://127.0.0.1:8091"
      fan:    "http://127.0.0.1:8092"
      lamp:   "http://127.0.0.1:8093"

Then drive tools/fake_rig.py through an arc and watch warm/cool flip heater+fan and
the bleach latch black out the lamp. Kill one fake_plug mid-run (Ctrl-C) to confirm
the server logs the failure and keeps broadcasting — the fail-soft requirement.

This is a test/diagnostic harness, not part of the exhibition runtime.
"""
from __future__ import annotations

import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class _Plug:
    """The plug's single bit of state: is it powered on?"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.on = False


def _make_handler(plug: _Plug):
    class TasmotaHandler(BaseHTTPRequestHandler):
        # Silence the default per-request stderr logging; we print our own line.
        def log_message(self, *_args) -> None:  # noqa: D401
            pass

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            parsed = urlparse(self.path)
            if parsed.path != "/cm":
                self._json(404, {"error": "not found"})
                return
            # Tasmota takes the command in the `cmnd` query parameter, e.g.
            # "Power On". A bare "Power" is a state query and changes nothing.
            cmnd = parse_qs(parsed.query).get("cmnd", [""])[0].strip()
            parts = cmnd.split()
            verb = parts[0].lower() if parts else ""
            arg = parts[1].lower() if len(parts) > 1 else ""

            if verb != "power":
                self._json(400, {"error": f"unsupported cmnd: {cmnd!r}"})
                return

            changed = ""
            if arg == "on" and not plug.on:
                plug.on, changed = True, " *"
            elif arg == "off" and plug.on:
                plug.on, changed = False, " *"
            elif arg in ("", "on", "off"):
                pass  # query, or a no-op repeat of the current state
            else:
                self._json(400, {"error": f"bad Power arg: {arg!r}"})
                return

            state = "ON" if plug.on else "OFF"
            ts = time.strftime("%H:%M:%S")
            banner = "●  ON " if plug.on else "○  OFF"
            print(f"{ts}  [{plug.name}]  {banner}{changed}", flush=True)
            self._json(200, {"POWER": state})

        def _json(self, code: int, body: dict) -> None:
            import json
            payload = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return TasmotaHandler


def main() -> None:
    ap = argparse.ArgumentParser(description="Stand-in Tasmota plug for Phase 4 testing.")
    ap.add_argument("--name", default="plug", help="label shown in the log (e.g. heater)")
    ap.add_argument("--port", type=int, default=8091, help="HTTP port to serve on")
    # Bind 0.0.0.0 by convention (CLAUDE.md): localhost testing and a real LAN
    # differ only by the address in config, never by code.
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    args = ap.parse_args()

    plug = _Plug(args.name)
    server = ThreadingHTTPServer((args.host, args.port), _make_handler(plug))
    print(
        f"fake_plug '{args.name}' listening on http://{args.host}:{args.port}/cm "
        f"— starting OFF. Ctrl-C to simulate an unplug.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[{args.name}] stopped.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
