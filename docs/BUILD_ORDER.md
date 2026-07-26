# Build Order

Phased build sequence. Each phase ends with an **acceptance test** — do not proceed until it passes, because every later phase assumes the earlier ones are solid.

Phases 1–3 require **no hardware** and can be completed entirely on a laptop.

---

## Phase 1 — Server + simulated rig
*No hardware. This is the critical path; everything else is blocked behind it.*

**Build:**
1. `config.example.yaml` → `config.yaml` loader. All constants read from config.
2. `src/state.py` — intensity formula, state derivation, bleach latch, recovery lag/ramp, idle timer.
3. `src/broadcast.py` — OSC emit at fixed rate, client registry (insert/refresh/prune).
4. `src/temperature.py` — the ingestion seam with `SimulatedSource` implemented.
5. `src/server.py` — single-threaded fixed-tick loop (~20 Hz) wiring the above; rotating timestamped logging.
6. `tools/fake_rig.py` — thermal simulation toward target, keyboard keys as buttons.

**Acceptance test:**
Run server + fake rig. Then, from the keyboard:
- Press warm → temperature climbs, `intensity` rises smoothly, state 1 engages above 26.2 °C.
- Cool *before* the latch → everything reverses smoothly; state 2 never occurs.
- Warm and hold at 28 °C → after 10 s sustained above 27.8 °C, state 2 latches. Further temperature wobble changes nothing.
- Press cool → stays visibly bleached for 30 s, then heals gradually, returns to state 0.
- Walk away 3 minutes → idle reset fires, returns to Natural.
- Every event appears in `logs/` with a timestamp.

---

## Phase 2 — Ableton integration
*No hardware. First end-to-end proof of the whole concept.*

Server side is already satisfied: Ableton is a static subscriber on UDP 9010, so the server emits to it the moment it runs — no code changes. This phase is Ableton GUI work. **Full step-by-step in [ABLETON.md](ABLETON.md).**

**Build:**
1. Add a free Max for Live **OSC-receiver device** (e.g. OSC Mapper) listening on UDP 9010. *(The old Connection Kit is discontinued; its OSC Monitor is display-only — see [ABLETON.md](ABLETON.md).)*
2. Two parallel stem groups (natural / industrial) routed through a crossfader.
3. Map `/coral/intensity` → crossfader, plus 2–3 macros (filter cutoff, drone level, reverb decay).
4. Optionally map `/coral/state` → a discrete musical event at the bleach latch.

**Verify the pipe first:** `python tools/osc_monitor.py` binds port 9010 and prints exactly what Ableton would receive, so any fault can be isolated to the server side or the Ableton side. Quit it before pointing Ableton at 9010 (one program per UDP port).

**Acceptance test:**
Drive the fake rig through a full arc and *hear* the soundscape degrade continuously with warming, land the bleach moment, and calm through the lagged recovery. **Record this** — it is the first demonstrable proof of the concept and useful thesis documentation.

---

## Phase 3 — Multi-client registry hardening
*No hardware.*

**Build:** `tools/fake_client.py` — sends `/client/hello` every 5 s, prints received broadcasts.

**Acceptance test:**
- Run three instances; all three receive identical, synchronised broadcasts.
- Kill one → pruned from the registry within 15 s.
- Restart it → rejoins and resyncs within one broadcast interval.
- Start clients *before* the server → they converge silently once it appears.
- Restart the server mid-arc → clients reconverge without intervention.

---

## Phase 4 — Network + first real hardware
*Requires: router, plugs.*

**Build:**
1. Configure the GL.iNet Opal: **separate SSIDs** for 2.4 GHz and 5 GHz. DHCP reservations by MAC.
2. Join the plugs to the 2.4 GHz SSID; note their IPs; record in config.
3. Implement `src/actuation.py` — Tasmota HTTP on/off with timeouts and soft failure.
4. Wire the Mac to the router by Ethernet.

*Implementation notes:* `src/actuation.py` is edge-driven (a plug is commanded only when its desired state changes) and runs all HTTP on a single background worker thread, so a plug that blocks until timeout never stalls the 5 Hz loop. The heater/fan follow the **target** (warm → heater on / fan off); the lamp follows the **state** (`lamp_mode: on_until_bleach` blacks it out at the bleach latch). The session ignores any system proxy so plug traffic stays on the isolated LAN.

**Verify without hardware first:** `tools/fake_plug.py` emulates a Tasmota plug. Run three (heater/fan/lamp) on localhost ports, point config at them, set `plugs.enabled: true`, and drive `tools/fake_rig.py` — the whole acceptance test below is reproducible with zero plugs (see the README "Verifying the plugs" section). `tests/test_actuation.py` guards the policy and fail-soft behaviour permanently.

**Acceptance test:**
With `plugs.enabled: true` and still in simulated temperature mode, the fake rig's target changes physically switch the plugs (a lamp plugged into each makes this visible). Unplug a plug mid-run → server logs the failure and keeps running.

---

## Phase 5 — Temperature sensor + real mode
*Requires: ESP32-C3, DS18B20 probe, resistor, breadboard, jumpers, heater, fan, container.*

**Build:**
1. Wire ESP32-C3 + DS18B20 + 4.7 kΩ pull-up on the breadboard (see `docs/HARDWARE.md`).
2. Flash `esphome/coral-temp-sensor.yaml`; join 2.4 GHz SSID; note IP.
3. Implement `RealSource` in `src/temperature.py`.
4. Set `temperature.mode: real` and point `temperature.real.sensor_url` at the sensor.

*Implementation notes:* `RealSource` polls the ESPHome web server on a **single background thread** (like `actuation.py`), so a sensor that is unplugged or slow blocks only that thread, never the 5 Hz loop — `read()` returns the last cached reading instantly. On timeout or a malformed/null reply it **holds the last good reading** and logs once (and once on recovery), per PROTOCOL §4. Ingestion is **HTTP only** by design (CLAUDE.md: no MQTT broker). Before the first poll lands it reports `real.start_temp`, so the system boots at state 0.

**Verify without hardware first:** `tools/fake_sensor.py` emulates the ESPHome endpoint, serving a keyboard-driven thermal ramp. Point `sensor_url` at it, set `mode: real`, and drive `w`/`c` in its terminal — the whole arc runs through the real ingestion path with zero hardware (see the README "Verifying the sensor" section). Press `b` to blind the probe and watch the server hold the last reading and keep broadcasting. `tests/test_temperature.py` guards the parse/hold/fail-soft policy permanently. This is the correctness proof of the seam: nothing above `temperature.py` changes between simulated and real.

**Critical measurements — do these before tuning anything:**
- Time 26 → 28 °C with the heater on. Target ~60–90 s.
- Time 28 → 26 °C with the fan on. Note the actual rate.
- Update `state` timings in config to match measured reality.
- Set the heater's dial slightly **above** 28 (≈28.5) so the water reliably crosses the 27.8 latch threshold rather than plateauing below it.

**Acceptance test:**
Real water drives the full arc at acceptable pacing. Swapping the fake rig for the real sensor required **zero changes** above the ingestion seam — this is the correctness proof of the architecture. Run a multi-hour soak test; check water level drop and that the coral print is unaffected.

---

## Phase 6 — Buttons
*Requires: arcade kit.*

**Build:**
1. Wire buttons to the encoder (spade connectors, no soldering); connect by USB.
2. `tools/discover_buttons.py` — print gamepad button indices; record in config.
3. Implement `src/inputs.py` using `pygame.joystick`. **Not a keyboard listener.**

*Implementation notes:* `ButtonReader` reads the encoder with `pygame.joystick` and answers one question per tick — *which buttons are down now?* — returning `(warm, cool)`. Those funnel into the **exact same** set-target path the OSC harness drives (`/sim/warm` · `/sim/cool` → `state.apply_input`), so the state machine is byte-identical whether input comes from the encoder or the keyboard rig; **cool-wins** and the idle-timer reset are resolved downstream in `apply_input`. Unlike the sensor and plugs it runs **in-loop, not on a thread** — a local HID read is non-blocking, so threading it would add complexity for nothing and cost the loop its determinism. It is **fail-soft**: a missing/unplugged/mid-run-removed encoder reports `(False, False)` and never raises (the server keeps broadcasting and the idle reset returns the piece to Natural), and a re-plugged encoder re-acquires automatically. The pygame access sits behind a small backend seam so the policy is unit-testable with no hardware.

**Verify without hardware first:** there is no new emulator to run — the OSC harness (`tools/fake_rig.py`) *is* the no-hardware input path, injecting the same warm/cool presses the encoder will. `tests/test_inputs.py` drives a fake joystick backend to guard the whole policy permanently: button-level reporting, configurable indices, cool-wins at the seam, idle-timer reset, and the fail-soft/reacquire behaviour on unplug. Run `python tools/discover_buttons.py` once the encoder is wired to read off each button's index (it uses the identical `get_button` call the server does, so whatever it sees, the server reads too).

**Acceptance test:**
Physical buttons drive the real water. Cool wins on simultaneous press. Presses reset the idle timer. Unplug the encoder → server continues, logs it, idle reset eventually fires.

---

## Phase 7 — AR application
*Requires: AR devices (borrowed).*

**Build:**
1. Add extOSC to the Unity project; implement the hello/listener client.
2. Remove distance→state logic; bind materials to broadcast state/intensity.
3. Implement the magnifier: distance → masked blend into polyp video; fade to stillness in state 2.
4. Exhibition config: never-sleep, app pinned.

**Acceptance test:** three devices around the artefact, all synced; magnifier reveals life at states 0–1 and absence at state 2; a device that sleeps and wakes resyncs itself.

---

## Phase 8 — Projection
*Requires: HDMI cables, USB-C→HDMI adapter.*

**Build:**
1. Export Unreal state loops at 2560×800 (apply the prep guide's exposure/black-level locking).
2. Projection player: dual VideoPlayer cross-fade, extOSC on 9020, fullscreen on the spanned canvas.
3. Configure macOS Displays: **extend**, not mirror; arrange side by side.
4. Physically align both projectors: position first, minimal keystone, focus, fill.

**Acceptance test:** the spanned projection cross-fades with state; both halves inherently in sync at native resolution.

---

## Phase 9 — Full rehearsal
**Acceptance test:**
- Single power-on from cold, arbitrary order → everything reaches state 0 unaided.
- Naive testers complete a full arc without instruction.
- Multi-hour run: monitor water level, phone batteries, machine temperature, log growth.
- Calibrate on the wall in exhibition lighting.
- Tag the repo `exhibition-v1`; print the power-on checklist and config sheet.
