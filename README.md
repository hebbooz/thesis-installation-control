# Coral Installation — Control System

Control software for a cross-scale interactive installation about coral bleaching. A visitor warms or cools a small water bath containing a 3D-printed coral; the water temperature drives a system state that is broadcast to an AR app, a projected reef, a soundscape, and a lamp, so the whole environment responds as one.

Built for a university design thesis investigating whether embodied, cross-scale AR can counter "psychic numbing" around the climate crisis.

## How it works

```
  [buttons] ──USB──▶ ┌──────────────┐ ──HTTP──▶ [smart plug] ──▶ [heater / fan / lamp]
                     │              │
  [temp sensor] ─────▶│    server    │
       (WiFi/HTTP)    │              │ ──OSC/UDP 5Hz──▶ [AR phones ×3]
                     └──────────────┘                  [projection player]
                                                        [Ableton soundscape]
```

The server derives two values from the water temperature and broadcasts them continuously:

- **`state`** (0–3) — the discrete phase: Natural, Fluorescent, Bleached, Recovery
- **`intensity`** (0.0–1.0) — continuous severity, used for smooth fades

Everything else is a passive listener. Nothing talks to anything except the server.

## Requirements

- Python 3.11+
- macOS (developed on MacBook Pro M1 Pro) or Linux
- No hardware required for development — see *Running with simulated hardware* below

## Setup

```bash
git clone <repo>
cd coral-installation
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Edit `config.yaml` for your environment. The defaults run fully simulated on localhost.

## Running with simulated hardware

No purchases, no wiring, no network. Three terminals:

```bash
# Terminal 1 — the server
python src/server.py

# Terminal 2 — fake temperature rig (keyboard stands in for the buttons)
python tools/fake_rig.py
#   w = warm    c = cool    q = quit

# Terminal 3+ — pretend AR clients (run several; each needs a distinct id)
python tools/fake_client.py --id phone1     # phone2, phone3, ... in more terminals
#   (omit --id and each process auto-picks a unique one)

# Optional — stand in for Ableton/projection to verify the fan-out on their port
python tools/osc_monitor.py            # binds the 'ableton' static port (9010)
```

Drive a full visitor arc from the keyboard: press `w`, watch the temperature climb and `intensity` rise, see state 1 engage, hold at 28 °C until the bleach latch fires, press `c`, sit through the recovery lag, watch it heal back to state 0. Walk away for three minutes and the idle reset returns it to Natural.

### Verifying the plugs (Phase 4) without hardware

`tools/fake_plug.py` emulates a Tasmota smart plug, so the actuation plane can be
exercised with no real plugs. Run one per plug on its own port, point `config.yaml`
at them, and set `plugs.enabled: true` (temperature can stay `simulated`):

```bash
# Terminals A/B/C — a stand-in plug each. Each prints a big ON/OFF banner per command.
python tools/fake_plug.py --name heater --port 8091
python tools/fake_plug.py --name fan    --port 8092
python tools/fake_plug.py --name lamp   --port 8093
```

```yaml
# config.yaml
plugs:
  enabled: true
  heater: "http://127.0.0.1:8091"
  fan:    "http://127.0.0.1:8092"
  lamp:   "http://127.0.0.1:8093"
```

Now drive the fake rig: warming flips **heater on / fan off**, cooling flips them
back, and the bleach latch blacks the **lamp** out until recovery. `Ctrl-C` a
`fake_plug` mid-arc to simulate an unplug — the server logs `ACTUATE … FAILED` and
keeps broadcasting, because plug I/O runs on a background worker and never blocks
the loop.

### Verifying the sensor (Phase 5) without hardware

`tools/fake_sensor.py` emulates the ESPHome temperature endpoint, so `mode: real`
can be exercised with no ESP32. It serves a keyboard-driven thermal ramp at the
same URL the real sensor uses:

```bash
# Terminal A — the stand-in sensor. w/c ramp the served temperature; b blinds the probe.
python tools/fake_sensor.py --port 8085
```

```yaml
# config.yaml
temperature:
  mode: real
  real:
    sensor_url: "http://127.0.0.1:8085/sensor/water_temperature"
```

Start the server, then press `w`/`c` in the sensor's terminal: the server polls the
endpoint, derives state, and broadcasts exactly as it will off the real DS18B20 —
**nothing above the ingestion seam changes** between simulated and real. (In real
mode the temperature comes from the sensor, so drive warm/cool from `fake_sensor`
here rather than `fake_rig`; on the real rig the warm button additionally gates the
heater and arms the bleach latch.) Press `b` to blind the probe mid-arc — the server
logs `SENSOR read failed … holding last reading` and keeps broadcasting, because the
poll runs on a background thread and never blocks the loop.

## Running with real hardware

1. Set up the private network (separate 2.4 GHz and 5 GHz SSIDs — see `docs/HARDWARE.md`).
2. Flash the temperature sensor with `esphome/coral-temp-sensor.yaml`.
3. Note the reserved IPs of the sensor and plugs; put them in `config.yaml`.
4. Set `temperature.mode: real` in `config.yaml`.
5. Run `python src/server.py`.

The fake rig is retained permanently as a regression harness — switch back to `mode: simulated` at any time to test logic changes without hardware.

## Tests

Automated regression suite, no hardware required:

```bash
pip install -r requirements-dev.txt
pytest
```

- `tests/test_state.py` drives the state machine through every Phase 1 acceptance
  scenario (warm/reverse, latch, recovery, idle reset) against the committed
  `config.example.yaml`.
- `tests/test_broadcast.py` covers the client registry and the OSC bundle wire format.
- `tests/test_actuation.py` covers the plug policy (warm/cool/lamp mapping), the
  edge-driven commanding, and fail-soft behaviour when a plug is unreachable.
- `tests/test_temperature.py` covers the real-sensor ingestion seam: parsing the
  ESPHome reply, holding the last reading on a timeout/malformed/null response, and
  the poll thread's lifecycle.
- `tests/test_e2e.py` launches the real server on an isolated port and checks the
  OSC contract, registration, pruning, and startup-order independence over UDP.

This suite is the permanent guard on the state logic — run it after any change to
`src/`.

## Documentation

| Document | Contents |
|---|---|
| `CLAUDE.md` | Context and constraints for AI-assisted development |
| `docs/ARCHITECTURE.md` | Full system design, layers, components, failure handling |
| `docs/PROTOCOL.md` | OSC message schema, HTTP contracts, registry protocol |
| `docs/HARDWARE.md` | Devices, wiring, firmware, network configuration |
| `docs/BUILD_ORDER.md` | Phased build sequence with acceptance tests |
| `docs/ABLETON.md` | Phase 2: wiring the Ableton soundscape to the OSC broadcast |

## Project status

Hardware ordered. Development proceeds in phases (see `docs/BUILD_ORDER.md`); phases 1–3 require no hardware.

## Related repositories

This repository contains the **control system** only. Other components live separately, coupled to this one solely through the OSC contract in `docs/PROTOCOL.md`:

| Repository | Contents |
|---|---|
| `https://github.com/hebbooz/thesis-ar-app` | Unity AR application (micro-scale). A passive subscriber — receives state/intensity, sends only `/client/hello`. Mirrors `docs/PROTOCOL.md`. |
| *(this repo)* | Orchestration server, temperature ingestion, actuation, input, ESPHome sensor config |

`docs/PROTOCOL.md` in this repository is the **source of truth** for the contract. If it changes, update the mirrored copy in the AR repo.

## Licence

Academic project. All third-party firmware (Tasmota, ESPHome, WLED) under its own licence.
