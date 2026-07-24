# CLAUDE.md

Context for Claude Code working in this repository. Read this before writing or changing code.

## What this project is

A cross-scale interactive art installation about coral bleaching, built for a university design thesis. Visitors press a button to warm or cool a small water bath containing a 3D-printed coral. The water temperature drives a single system state, which is broadcast to every output — an AR app on phones, a projected reef, a soundscape, and a lamp — so the whole room reacts as one organism.

Research context: the piece addresses "psychic numbing" around climate crisis by making an abstract, distant harm physically immediate.

## The one-sentence architecture

**Everything watches one number.** A Python server reads water temperature, derives a discrete `state` (0–3) and a continuous `intensity` (0.0–1.0), and broadcasts both over OSC/UDP ~5×/second. Every output is a passive listener that answers only: *given this state and intensity, what do I show?*

## Non-negotiable design principles

1. **KISS / off-the-shelf.** The orchestration server is the ONLY bespoke component. Everything else is a purchased product, ready-made firmware (Tasmota/ESPHome/WLED), or a reused existing asset. Do not propose building custom electronics, and do not add frameworks (no message brokers, no state-machine libraries, no container orchestration, no Home Assistant dependency). This system is simpler than any framework's overhead.
2. **Loose coupling.** Outputs never talk to each other and never talk back to the server (except AR clients sending `hello`). They hold no state beyond the last broadcast received.
3. **Startup-order independence.** Any component may start, crash, or restart at any time and must reconverge within one broadcast interval with no operator action. Test by starting clients before the server.
4. **Local-only.** No dependency on the public internet at runtime. All control traffic stays on a private LAN.
5. **Config, not code.** Every threshold, timing, IP, and port lives in one config file. Never hard-code an address or a magic number.
6. **Bind `0.0.0.0`,** never `127.0.0.1`, so the transition from localhost testing to real hardware is a config change.

## Repository layout

```
/
├── CLAUDE.md                  # this file
├── README.md                  # human-facing setup and run instructions
├── config.example.yaml        # committed template; copy to config.yaml (gitignored)
├── requirements.txt
├── src/
│   ├── server.py              # orchestration server (the one bespoke component)
│   ├── state.py               # state derivation: intensity, latch, timers
│   ├── broadcast.py           # OSC emit + client registry
│   ├── inputs.py              # button reading (USB gamepad HID)
│   ├── temperature.py         # ingestion seam: SimulatedSource | RealSource
│   ├── actuation.py           # Tasmota HTTP calls (heater, fan, lamp plugs)
│   └── display.py             # tiny HTTP server for the temperature web page
├── tools/
│   ├── fake_rig.py            # simulated temperature rig; keyboard = buttons
│   └── fake_client.py         # pretend AR client: sends hello, prints broadcasts
├── esphome/
│   └── coral-temp-sensor.yaml # ESP32-C3 + DS18B20 config
├── docs/
│   ├── ARCHITECTURE.md        # full system design
│   ├── PROTOCOL.md            # OSC + HTTP message contracts
│   ├── HARDWARE.md            # devices, wiring, firmware setup
│   └── BUILD_ORDER.md         # phased build with acceptance tests
└── logs/                      # gitignored
```

## The state machine (authoritative)

`intensity = clamp((T - 26.0) / (28.0 - 26.0), 0.0, 1.0)`

| State | Name | Entry | Notes |
|---|---|---|---|
| 0 | Natural | intensity ≈ 0, latch clear | healthy |
| 1 | Fluorescent | T rising above 26.2 °C, not latched | continuous, **reversible** — cooling before latch returns toward 0 |
| 2 | Bleached | T ≥ 27.8 °C sustained ≥10 s | **LATCHES** — further temperature change does nothing until recovery |
| 3 | Recovery | from 2, target is cool AND ≥30 s lag elapsed | stays visibly bleached during lag; then heals to 0 |

Additional rules:
- Buttons set a **target** (warm → 28.0, cool → 26.0). Idempotent. **Cool wins** on conflict.
- **Idle reset:** no input for 180 s → target = 26.0, return to state 0.
- Broadcast rate 5 Hz; this doubles as the heartbeat. There is no separate keep-alive.
- Cold start: everything assumes state 0 until the first reading/broadcast.

The bleach latch and the recovery lag are deliberate and thematic — the cool button is NOT an undo button. Do not "optimise" them away.

## Protocol summary

Server → all subscribers, 5 Hz:
- `/coral/state` int32 0–3
- `/coral/intensity` float32 0.0–1.0
- `/coral/temp` float32 °C

AR clients → server every 5 s: `/client/hello` (string id). Registry prunes after 15 s silence.

Server → plugs: Tasmota HTTP, e.g. `GET http://<plug-ip>/cm?cmnd=Power%20On`
Sensor → server: ESP32-C3 running ESPHome reports temperature (HTTP poll or MQTT — see PROTOCOL.md).

Full detail in `docs/PROTOCOL.md`. Ports live in config.

## Hardware reality (already purchased)

- **Host:** MacBook Pro M1 Pro. Runs server + video player + Ableton. Two projectors via built-in HDMI + one USB-C→HDMI adapter as a spanned 2560×800 canvas.
- **Plugs:** Athom Tasmota ESP32-C3 AU Plug V3 ×4 (heater, fan, lamp, spare). Local HTTP, no cloud.
- **Sensor:** ESP32-C3 Mini dev board + waterproof DS18B20 + 4.7 kΩ pull-up on a mini breadboard. ESPHome.
- **Heater:** 100 W thermostatic aquarium heater, ~9 cm, dial set slightly above 28 °C (dial slop means set ~28.5 to reliably cross the 27.8 latch threshold).
- **Cooling:** clip-on aquarium fan on a second plug. Passive drift when heater is off.
- **Buttons:** arcade buttons + USB "zero delay" encoder. **IMPORTANT:** this enumerates as a USB **gamepad/joystick**, NOT a keyboard. Read it with `pygame` or `hid`, not a keyboard listener.
- **Network:** GL.iNet Opal (GL-SFT1200). Give 2.4 GHz and 5 GHz **separate SSIDs** — ESP devices can fail to join combined-band SSIDs. Plugs and sensor on 2.4 GHz; phones on 5 GHz; Mac wired.

## Simulated vs real temperature

`temperature.py` is an **abstraction seam** with two interchangeable implementations selected by config:

- **Simulated** — a thermal model advances T toward target at configured rates. No hardware. This is the development default AND the permanent regression harness.
- **Real** — poll the ESPHome sensor; gate the heater plug via Tasmota HTTP.

Everything above the seam is byte-identical across modes. Never let mode-specific logic leak into `state.py`, `broadcast.py`, or any client.

## Coding conventions

- Python 3.11+. Standard library plus `python-osc`, `requests`, `pyyaml`, `pygame` (input). Keep dependencies minimal.
- The server is a **single-threaded fixed-tick event loop** (~20 Hz internal), emitting a broadcast every 4th tick to hit 5 Hz. Deterministic timing, no async complexity.
- Target ~100–150 lines for `server.py`. If it grows much beyond that, something is being over-engineered.
- Log every state transition, button press, temperature sample, and actuation with a timestamp to a rotating log in `logs/`. This is debugging evidence, exhibition diagnostics, AND thesis data.
- Fail soft: a missing sensor, an unreachable plug, or a dropped client must never crash the server. Log and continue.
- Type hints on function signatures. Docstrings on modules and non-obvious functions. Comments explain *why*, not *what*.

## Testing without hardware

Everything through Phase 3 runs on localhost with zero hardware:

```bash
python src/server.py          # terminal 1
python tools/fake_rig.py      # terminal 2 — keyboard = buttons
python tools/fake_client.py   # terminals 3-5 — pretend AR devices
```

Any change to state logic must be verifiable this way. Preserve `fake_rig.py` permanently; it is the regression harness, not throwaway scaffolding.

## What NOT to do

- Don't add Home Assistant, MQTT brokers, Docker, or a web framework.
- Don't let clients derive state from temperature themselves — the server is the single source of truth.
- Don't hard-code IPs, ports, or thresholds.
- Don't remove or shorten the bleach latch / recovery lag to make testing faster; change them in config instead.
- Don't assume the button encoder is a keyboard.
- Don't build custom electronics or suggest soldering.
