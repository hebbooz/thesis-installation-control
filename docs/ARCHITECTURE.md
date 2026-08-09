# Cross-Scale Coral Installation — Technical Architecture

**Document type:** System Design Document (SDD)
**Version:** 1.0 · 22 Jul 2026
**Status:** For review
**Companion document:** `coral_installation_procurement_KISS.md` (every hardware item referenced here maps to a line in that list; procurement section letters are cited as [Proc A], [Proc D], etc.)

**Design principle:** KISS / commercial-off-the-shelf (COTS) first. Exactly one bespoke software component (the orchestration server); every other element is a purchased product, ready-made firmware, or a reused existing asset. No custom electronics, no soldering.

---

## 1. Purpose and scope

### 1.1 Purpose
This document specifies the technical architecture of a centrally orchestrated, cross-scale augmented-reality installation. It defines the components, their interfaces, the messaging and network layers, addressing, data and control flow, timing, and failure behaviour in sufficient detail to implement, review, and test the system.

### 1.2 Scope
In scope: the control and integration architecture — the orchestration server, the input subsystem, the temperature-control subsystem, and the four output subsystems (AR, projection, audio, light), plus the network and messaging that bind them.

Out of scope: the creative content itself (Unreal reef render, Unity AR materials, Ableton composition, coral fabrication), and all ethics/safety/compliance approvals, which are handled in a separate workstream.

### 1.3 Design goals and constraints
- **COTS-first / minimal bespoke surface.** One custom software artifact; everything else bought or reused.
- **Loose coupling.** Outputs are stateless subscribers that derive their presentation solely from a broadcast signal; they hold no dependency on each other and no knowledge of the source of temperature.
- **Idempotent, self-healing runtime.** Any component may restart at any time and reconverge within one broadcast interval without operator action; startup order is immaterial.
- **Single operator, single power-on.** The complete system must reach a known good state (State 0) from cold with no manual sequencing.
- **Local-only operation.** No dependency on the public internet at runtime. All control traffic stays on a private LAN.
- **Deterministic pacing.** Environmental transitions are driven by a continuous signal at a fixed rate, producing smooth cross-fades rather than visible steps.

---

## 2. Architectural overview

### 2.1 Style
The system follows a **hub-and-spoke, publish/subscribe** control architecture with a single authoritative producer (the orchestration server) and multiple independent subscribers (the output subsystems). A separate **request/response** control path connects the server to the temperature-control actuator and sensor. This separation of a fan-out *event* plane from a point-to-point *actuation* plane is the central design decision and is what keeps each subsystem simple and independently testable.

### 2.2 Logical layers
The architecture is organised into five layers. Each layer depends only on the contract of the layer beneath it, not its implementation.

| Layer | Responsibility | Elements |
|---|---|---|
| L1 — Physical / Device | Real-world sensing and actuation | Buttons + encoder, heater, smart plug, temp sensor, LED light, projectors, speakers, phones |
| L2 — Network / Transport | Private LAN, addressing, transport protocols | Travel router, DHCP reservations, UDP + HTTP |
| L3 — Messaging / Interface | Message contracts and endpoints | OSC event schema, HTTP actuation calls, hello/registry protocol |
| L4 — Orchestration / Logic | State derivation, control loop, client management | Orchestration server (the one bespoke component) |
| L5 — Experience / Presentation | Rendering the state to the visitor | AR app, projection player, Ableton set, WLED preset mapping |

### 2.3 Component-to-procurement map
Every L1/L2 element is a procured or held item:

| Component | Procurement ref | Status |
|---|---|---|
| Button set + USB encoder + housing | [Proc B] | Buy |
| Aquarium heater (self-regulating) | [Proc A] | Buy (real-water only) |
| Smart plug (local API) | [Proc A] | Buy (real-water only) |
| WiFi temperature sensor | [Proc A] | Buy (real-water only) |
| Cooling fan + 2nd plug | [Proc A] | Defer |
| WLED controller + LED strip + PSU | [Proc D] | Buy |
| Coral tracking spotlight | [Proc D] | Buy |
| Travel router + Ethernet | [Proc E] | Buy |
| AR devices (×3) | [Proc F] | Borrow first |
| Epson EB-435W (×2) | [Proc G] | Held |
| HDMI, USB-C→HDMI adapter | [Proc G] | Buy |
| Powered speakers + audio cabling | [Proc H] | Borrow / Buy |
| Display phone/tablet | [Proc C] | Held / Buy |
| MacBook Pro (host) | Held | Held |

The orchestration server, AR listener code, and projection player are software (L4/L5) and appear under [Proc L] as free/owned.

---

## 3. Runtime topology and host allocation

### 3.1 Process/host allocation
The **MacBook Pro** is the single host for all bespoke and playback software. It runs three cooperating processes:

1. **Orchestration server** — the control brain (Python).
2. **Projection player** — dual-clip video renderer to the spanned projector canvas.
3. **Ableton Live** — audio engine with a Max for Live OSC-receiver device.

All other computing elements are self-contained appliances on the LAN: the three AR devices (own render), the WLED controller, the smart plug, the WiFi sensor, and the temperature display. The USB button encoder is a local HID device on the host.

### 3.2 Rationale
Consolidating server, video, and audio on one host is safe because the video is **pre-rendered** (playback, not real-time engine rendering) and the server is computationally trivial. The identified scaling escape hatch: the orchestration server is deliberately host-agnostic and can be relocated to a Raspberry Pi with only a configuration change to its IP, should the full-load rehearsal reveal contention. No other component is affected by that move because all coupling is over the network, not in-process.

### 3.3 Deployment diagram (textual)
```
                       ┌────────────────────────── MacBook Pro (host) ──────────────────────────┐
   USB HID             │                                                                          │
 [Button encoder]──────┤ Orchestration Server ──in-process──▶ (none; server talks over network)   │
                       │        │                                                                  │
                       │        ├── OSC/UDP broadcast (L3 event plane)                             │
                       │        ├── HTTP req/res (L3 actuation plane)                              │
                       │        │                                                                  │
                       │ Projection Player ◀── OSC/UDP (localhost)                                 │
                       │ Ableton + M4L OSC receiver ◀── OSC/UDP (localhost)                        │
                       │                                                                          │
   HDMI ×2 ◀───────────┤ (spanned 2560×800 canvas) │        audio out ─────────────────────────▶ │
                       └───────────┬──────────────────────────────────┬──────────────────────────┘
                                   │                                  │
                             [Epson EB-435W ×2]              [Powered speakers]
                                   │
        ┌──────────────────────────┼───────────── Private LAN (travel router) ─────────────────────┐
        │                          │                        │                    │                 │
   [AR phone 1]  [AR phone 2]  [AR phone 3]          [WLED light]         [Smart plug]      [WiFi temp sensor]
   OSC in +hello  …             …                    OSC/HTTP in          HTTP in            HTTP/MQTT out
                                                                              │
                                                                         [Heater] (mains via plug)
                                                                          + [Coral spotlight] (constant, unmanaged)
                                   │
                             [Display phone] ◀── HTTP (served page from server)
```

---

## 4. Network architecture (L2)

### 4.1 Private LAN
A single dedicated dual-band travel router [Proc E] provides an isolated Layer-2/3 network. The public internet is not required at runtime and is not relied upon. University/venue Wi-Fi is explicitly excluded because client isolation on such networks blocks the device-to-device (peer) traffic this system depends on, and because captive-portal/registration flows prevent appliance onboarding.

### 4.2 Addressing plan
Static addressing is achieved via **DHCP reservation by MAC** on the router (not hand-set static IPs on devices), so every device continues to use ordinary DHCP and remains portable to any network, while always receiving a known address here.

| Host | Reserved IP (example) | Band | Transport role |
|---|---|---|---|
| Router / gateway | 192.168.50.1 | — | DHCP, DNS |
| MacBook Pro (host) | 192.168.50.10 | Wired (Ethernet) | OSC producer, HTTP client, HTTP server (display) |
| WLED light | 192.168.50.20 | 2.4 GHz | OSC/HTTP consumer |
| Smart plug (heater) | 192.168.50.21 | 2.4 GHz | HTTP server (actuation) |
| WiFi temp sensor | 192.168.50.22 | 2.4 GHz | HTTP/MQTT producer |
| Cooling plug (optional) | 192.168.50.23 | 2.4 GHz | HTTP server (actuation) |
| Display phone/tablet | 192.168.50.30 | 2.4/5 GHz | HTTP client |
| AR phone 1–3 | DHCP pool (dynamic) | 5 GHz preferred | OSC consumer + hello producer |

Design notes:
- The host is **wired** to the router for a stable producer address and to keep the 5 GHz band clear for the AR devices, which are the only latency/bandwidth-sensitive wireless clients.
- Appliances (plug, sensor, WLED) are 2.4 GHz (their radios) and are low-traffic.
- AR devices are intentionally left on the **dynamic pool** rather than reserved, because they are transient/interchangeable; they self-register at the application layer (§6.4) instead of relying on fixed addresses.

### 4.3 Transport protocols
Two transports, each chosen to match its plane:

- **UDP** carries the OSC event plane (§5). Connectionless fan-out; loss-tolerant because state is re-broadcast continuously (§7.3). No handshakes, minimal latency, natural fit for one-to-many.
- **HTTP (TCP)** carries the actuation plane: server→plug (on/off) and server←sensor (read), plus server→display-phone (serve page). Request/response with confirmation is appropriate here because these are discrete, must-succeed operations against a single endpoint, and the devices expose HTTP-native local APIs off the shelf.

### 4.4 Ports (canonical, all configurable)
| Endpoint | Proto/Port | Direction |
|---|---|---|
| Server OSC listener (hello, sensor-push if used) | UDP 9000 | inbound |
| AR devices OSC listener | UDP 9001 | inbound (per device) |
| Ableton M4L OSC-receiver listener | UDP 9010 | inbound (localhost) |
| Projection player listener | UDP 9020 | inbound (localhost) |
| WLED (native UDP realtime / JSON API) | UDP 21324 / HTTP 80 | inbound to WLED |
| Smart plug local API | HTTP 80 | inbound to plug |
| WiFi sensor local API/MQTT | HTTP 80 / MQTT 1883 | outbound from sensor |
| Display web page | HTTP 8080 | served by host |

---

## 5. Messaging architecture (L3) — event plane

### 5.1 Protocol
The event plane uses **OSC (Open Sound Control)** over UDP. OSC is chosen because it is the lingua franca of the subscriber tools (Ableton via a Max for Live OSC device, Unity via extOSC, WLED-adjacent tooling), is trivially small (an address pattern plus typed arguments), and requires no schema negotiation.

### 5.2 Canonical message set (server → all subscribers)
Broadcast as an OSC bundle at a fixed rate (§7.1):

| Address | Arg type | Range | Semantics |
|---|---|---|---|
| `/coral/state` | int32 | 0–3 | Discrete phase; used for switching (which clip, which material set) |
| `/coral/intensity` | float32 | 0.0–1.0 | Continuous severity; used for interpolation (dimming, cross-fade, colour) |
| `/coral/temp` | float32 | °C | Live temperature, for the display and for reference |

**Contract:** subscribers MUST treat `intensity` as the primary continuous driver and `state` as the discrete selector. Subscribers MUST NOT infer state from temperature themselves; the server is the single source of truth. Subscribers MUST tolerate receiving the same values repeatedly (idempotent application).

### 5.3 Registration protocol (dynamic subscribers → server)
| Address | Arg type | Semantics |
|---|---|---|
| `/client/hello` | string (client id) | Announced by each AR device every 5 s; carries the sender's identity. Source `(ip, port)` is taken from the UDP packet. |

The server maintains a **client registry**: on `hello`, insert/refresh `{id, ip, port, last_seen}` using the packet's source `(ip, port)`; the fixed-rate broadcast is unicast to that address for every registered client, plus the static localhost subscribers; entries idle >15 s are pruned. Because the reply target is the packet source, a client **MUST send `hello` from the socket it listens on** (one bidirectional socket bound to its receive port) — this is what lets multiple clients share a host during localhost testing, at the cost of that constraint (see PROTOCOL.md §2). This is application-layer service discovery and is what makes AR devices plug-and-play without reserved addresses (§4.2).

### 5.4 Why not a broker
A message broker (e.g. MQTT for the event plane) was considered and rejected under KISS: the fan-out set is tiny and mostly static, subscribers already speak OSC natively, and continuous re-broadcast removes the need for broker-provided retained messages or QoS. MQTT remains acceptable *only* as the sensor's own native output if the chosen sensor speaks MQTT rather than HTTP (§6.2); in that case the server subscribes to one topic. The event plane stays OSC regardless.

---

## 6. Component specifications (L1/L4/L5)

### 6.1 Orchestration server — bespoke (L4)
**Type:** Python service on the host. **Libraries:** `python-osc` (event plane), standard `http` client for actuation, a minimal HTTP server for the display page. **Config:** single external config file; binds `0.0.0.0`.

**Responsibilities:**
1. **Input ingestion.** Read button events from the USB encoder (HID keypresses) and interpret them as `warm`/`cool` target commands.
2. **Temperature ingestion.** Poll the WiFi sensor over HTTP (or subscribe to its MQTT topic) at ≥2 Hz; in simulated mode, run the thermal model instead (§9).
3. **State derivation.** Compute `intensity` and `state` from temperature, direction of travel, and the bleach latch/recovery timers (§8).
4. **Actuation.** Drive the heater smart plug on/off (and optional cooling plug) via HTTP to track the current target.
5. **Broadcast.** Emit the canonical OSC bundle at the fixed rate to all registered + static subscribers.
6. **Registry.** Maintain the client registry (§5.3).
7. **Display.** Serve the temperature/target page to the display phone.
8. **Logging.** Append timestamped records of transitions, inputs, temperature samples, and actuation to a rotating log (operational evidence + thesis data).

**Interfaces:** consumes HID (buttons), HTTP/MQTT (sensor); produces OSC/UDP (subscribers), HTTP (plug, display). All addresses/ports/thresholds/timings are configuration, not code.

**Internal structure (recommended):** a single-threaded event loop on a fixed tick (e.g. 20 Hz internal) that (a) services input, (b) updates the thermal/latch/timer state, (c) actuates, and (d) emits a broadcast every Nth tick to hit 5 Hz. This keeps timing deterministic and the code ~100–150 lines.

### 6.2 Temperature-control subsystem — COTS (L1) [Proc A]
Three sealed products replace any custom controller:

- **Heater (actuator):** submersible aquarium heater with an integral thermostat, dial set to the bleach setpoint (≈28 °C). It performs its own closed-loop hold; the server only gates its **power**.
- **Smart plug (actuation endpoint):** local-API smart plug (Shelly Plug S baseline). Exposes an HTTP endpoint on the LAN (`GET /relay/0?turn=on|off` style). The server issues on to warm, off to cool. **Requirement:** local (LAN) control confirmed — not cloud-only (see companion list's verification note).
- **Sensor (feedback):** WiFi water-temperature sensor exposing a local HTTP read or MQTT publish. Provides the closed-loop signal the server derives state from and the value shown on the display.
- **Cooling (optional, deferred):** a fan on a second identical smart plug, gated the same way, added only if measured passive cooling is too slow for the recovery pacing.

**Control contract:** the server treats this subsystem as *set-target → gate-power → observe-sensor*. It never assumes the heater's internal state; it observes the sensor. Firmware-level safety (setpoint ceiling) lives in the heater's own thermostat and any product limits; safety approvals are the separate workstream.

### 6.3 Input subsystem — COTS (L1) [Proc B]
Two illuminated momentary buttons wired (spade connectors, no soldering) to a **plug-and-play USB arcade encoder** that enumerates as a USB HID keyboard on the host. Each button maps to a distinct key; the server reads these as `warm`/`cool`. A crafted enclosure carries the perceived quality; the electrical path is trivial and swappable without software change. **Button semantics:** one press sets the target (warm→28 °C, cool→26 °C); idempotent; cool wins on simultaneous/conflicting input.

### 6.4 AR subsystem — reuse (L5) [Proc F]
Existing Unity app on up to three ARKit/ARCore devices. Modifications: add **extOSC** listener on UDP 9001; remove the legacy distance→state logic; bind material state to `/coral/state` + `/coral/intensity`; repurpose device-to-target distance as a **magnifier** that blends the coral-surface material into embedded polyp video (VideoPlayer → RenderTexture via a masked reveal), with the footage fading to stillness in State 2. Each device emits `/client/hello` every 5 s. Devices are stateless subscribers: they boot to State 0 and converge on first broadcast. Exhibition config: never-sleep, app pinned, tethered or battery-rotated.

### 6.5 Projection subsystem — reuse + minimal glue (L5) [Proc G]
Pre-rendered Unreal reef exported as **three looping clips** (natural / fluorescent / bleached) plus one short one-shot for the bleach latch — no directional transition clips (§14.3 resolved; rationale in `projection/README.md`). A small **projection player** (Unity, reusing the same extOSC listener pattern) renders two VideoPlayers with a cross-fade driven by `/coral/state`/`/coral/intensity`, output fullscreen across a single **spanned 2560×800** canvas so the two Epson EB-435W units are one continuous image and cannot drift. Physical alignment per the existing Projection Prep Guide (position-first, minimal keystone, native 1280×800×60 Hz per unit); the guide's exposure/black-level guidance governs the mp4 export.

### 6.6 Audio subsystem — config (L5) [Proc H]
Ableton Live set with two parallel stem groups (natural / industrial) on a crossfader. A free **Max for Live OSC-receiver device** (e.g. OSC Mapper) receives `/coral/intensity` (UDP 9010) and maps it to the crossfader plus macros (filter cutoff, drone level, reverb decay); `/coral/state` optionally triggers a discrete musical event at the bleach latch. No custom Max patch required — off-the-shelf device only. Audio egresses the host to powered speakers. Setup in `docs/ABLETON.md`.

### 6.7 Light subsystem — COTS + firmware flash (L1) [Proc D]
An off-the-shelf ESP controller + addressable strip + PSU flashed with **WLED** (no code). The server maps `intensity` → brightness and warm/cool colour temperature via WLED's native realtime UDP or JSON HTTP API. A **separate constant neutral spotlight** on the coral is unmanaged (always on) to hold AR tracking illumination when the room light dims — architecturally it is *not* a subscriber and carries no state.

### 6.8 Display subsystem — COTS (L1) [Proc C]
A spare phone/tablet renders a page served by the orchestration server (HTTP 8080) showing live temperature and a subtle target indicator, or alternatively the sensor's own screen. Read-only; a stateless view of `/coral/temp` and the current target.

---

## 7. Timing and performance

### 7.1 Broadcast rate
Canonical rate **5 Hz** (200 ms period). Rationale: high enough that light dimming, audio cross-fade, and material interpolation read as continuous rather than stepped; low enough to be negligible traffic for the appliances and phones. Tunable in config (10 Hz if any subscriber shows stepping). Environmental change is slow (seconds–minutes), so 5 Hz is comfortably oversampled for smoothness.

### 7.2 Latency budget (button press → perceptible response)
| Segment | Budget |
|---|---|
| Button → HID → server ingest | <20 ms |
| Server target update → plug HTTP | <100 ms |
| Physical water response (heater) | seconds (intended; this is the experience's pace) |
| Sensor read → state derivation | <500 ms (≥2 Hz poll) |
| Broadcast → subscriber render | <200 ms (one interval) |

The dominant, *intentional* latency is thermal. The digital path is sub-second end to end.

### 7.3 Loss tolerance and convergence
Because the full state is re-broadcast every 200 ms, a lost UDP datagram is corrected by the next one. Any subscriber that (re)joins converges within one interval. This makes the fixed-rate broadcast simultaneously the data plane **and** the heartbeat — there is no separate keep-alive.

### 7.4 Sensor polling vs push
Default: server **polls** the sensor over HTTP at 2–5 Hz (simple, firewall-free within the LAN). If the chosen sensor is MQTT-native, it **pushes** to a broker/topic the server subscribes to; functionally equivalent, chosen by product capability, isolated behind the server's ingestion function so nothing downstream changes.

---

## 8. State model (L4 logic)

### 8.1 Derived quantities
- `intensity = clamp((T − 26.0) / (28.0 − 26.0), 0.0, 1.0)`
- `state ∈ {0,1,2,3}` derived from temperature, direction, and latch/timer state.

### 8.2 States
| State | Name | Entry condition | Presentation summary |
|---|---|---|---|
| 0 | Natural | intensity ≈ 0 and latch clear | warm light, healthy coral, calm audio, polyps alive |
| 1 | Fluorescent (transitional) | T rising above ~26.2 °C, not latched | continuous: dim light, vivid coral, building audio |
| 2 | Bleached (latched) | T ≥ 27.8 °C sustained ≥10 s | cold light, bleached coral, industrial audio, polyps gone |
| 3 | Recovery | from State 2, target cool AND ≥30 s lag elapsed | gradual heal back to State 0 |

### 8.3 Transition rules
*(As implemented in Phase 1 — the details below are behavioural contract for subscribers, not just guidance.)*
- **0↔1 (reversible, with hysteresis):** State 1 engages when T rises above `rise_threshold` (26.2) and releases back to 0 only once T falls to `temp_natural` (26.0). The gap between the two anchors exceeds sensor noise, preventing state chatter at the boundary. Below the latch threshold, cooling smoothly returns toward 0; State 2 is never entered. Only bleaching is irreversible in the moment.
- **1→2 latch:** requires T sustained ≥ `latch_threshold` for `latch_hold_s` (debounce against momentary spikes). The hold accumulates **only while warming** (target = warm); cooling resets it — so the system cannot latch on the way down, and a reset never re-latches during cool-off. Once latched, temperature variation does nothing until recovery.
- **2→3:** entered only after the cool target is set *and* the recovery lag elapses; visibly bleached during the lag (the cool button is not an undo).
- **3→2 (re-bleach):** setting the warm target *during* recovery cancels the heal and returns to State 2. **Subscribers must handle this backward transition.**
- **3→0:** on completion of the heal ramp **and** once T has returned below `rise_threshold`. State 3 therefore persists at intensity 0 until the water is actually cool, so the coral never flickers back to Fluorescent on the way out — meaning **State 3 may outlast `recovery_ramp_s`.**
- **Idle reset:** no input for `idle_timeout_s` (3 min) → server sets target = 26 °C and nothing else. It does **not** clear the latch: an unattended bleached coral runs the ordinary recovery lag and ramp (State 2 → 3 → 0), the same path a deliberate cool press produces. Un-latched, it simply converges to State 0 via normal cooling (no teleport — honest for real water). The earlier 2 → 1 shortcut was removed because it asserted that bleaching undoes itself when nobody is watching, which inverts the argument the piece is making.
- **Conflict resolution:** cool target wins.
- **Cold start:** all components assume State 0 until the first broadcast/first sensor reading.

### 8.4 Latch/timer state (server-held)
The server maintains: `target` (26/28), `bleach_latched` (bool), `bleach_hold_timer`, `recovery_timer`, `idle_timer`. These are internal to L4 and never exposed on the event plane except through their effect on `state`/`intensity`.

---

## 9. Simulated vs real-water modes (single abstraction seam)

Temperature enters the server through one **ingestion interface** with two interchangeable implementations:

- **Simulated:** a thermal model advances T toward `target` at configured heat/cool rates. No hardware. This is the development default and the basis of early testing.
- **Real:** the server reads the WiFi sensor and gates the heater plug to pursue `target`.

Everything above the ingestion seam — state derivation, broadcast, subscribers — is byte-for-byte identical across modes. This is the architectural guarantee that real water is a drop-in upgrade, and it is also the test harness: the simulated implementation is the permanent regression rig for the whole system. Selection of either mode is a config switch.

---

## 10. Failure modes and recovery (FMEA summary)

| Failure | Detection | System behaviour | Recovery |
|---|---|---|---|
| Lost OSC datagram | none needed | subscriber holds last value ≤200 ms | next broadcast |
| Subscriber crash/restart (AR, video, audio, light) | absence self-corrects | others unaffected (loose coupling) | rejoins, converges in 1 interval; AR re-registers via hello |
| AR device sleeps/leaves | registry idle >15 s | pruned from unicast set | re-hello on return |
| Server crash | subscribers hold last state | outputs freeze (safe, static) | relaunch; subscribers converge on first broadcast; heater plug fails to last commanded state (see note) |
| Sensor unreachable | poll timeout | server holds last T, logs error; can fall back to simulated ramp for graceful degradation | sensor reconnect |
| Smart plug unreachable | HTTP error | server logs; target not actuated; state derivation continues on real T (which won't move) | plug reconnect |
| Router/power loss | total | all appliances reboot to their configured join state; host relaunches processes | single power-on brings all to State 0 |
| Button encoder unplugged | HID absence | no new inputs; system idles → reset to 0 | replug (HID re-enumerates) |

**Safe-state note:** on server loss, the heater plug remains in its last commanded state. For fail-safe behaviour, the operational default is that idle/uncertainty tends toward *heater-off* (cooling), so the failure bias is toward the healthy/natural end rather than continued heating. (Physical safety limits and approvals are the separate workstream.)

---

## 11. Security and isolation
- **Network isolation.** Dedicated LAN, no internet dependency; the attack/interference surface is limited to devices physically on the private router.
- **No secrets in transit.** Control traffic is non-sensitive on-off/level data on an isolated network; no credentials or personal data traverse the event plane.
- **Local APIs only.** Smart plug and sensor must expose LAN-local control; cloud-tethered products are excluded to remove an internet dependency and an external failure/latency source.
- **Least privilege.** The display page is read-only; AR devices are subscribers with no actuation authority; only the server actuates.

---

## 12. Configuration management
A single config file (host) is the source of truth for: reserved IPs and ports; broadcast rate; temperature thresholds (26.0 / 26.2 / 27.8 / 28.0); latch hold (10 s), recovery lag (30 s), idle timeout (3 min); heat/cool simulation rates; mode (sim/real); WLED and plug/sensor endpoints. All bespoke code binds `0.0.0.0` and reads addresses from config so relocation (e.g. server→Raspberry Pi) or re-addressing is a one-file change. Recommended: version-control the server, projection player, config, and the simulated-rig harness together; keep a printed config sheet (IPs/ports/SSID) and a cold-restore USB [Proc K].

---

## 13. Traceability — requirement → mechanism
| Requirement | Mechanism |
|---|---|
| Coherent, unified reaction across scales | Single authoritative state/intensity broadcast; all outputs subscribe (§5) |
| Smooth (non-stepped) transitions | Continuous `intensity` at 5 Hz driving interpolation (§7.1) |
| Visitor agency over the crisis | Buttons → target → real thermal change → derived state (§6.3, §8) |
| Irreversibility of bleaching | Latch with sustained-hold + recovery lag (§8.3) |
| Plug-and-play multi-device AR | hello/registry service discovery (§5.3, §6.4) |
| Self-healing / order-independent start | Continuous re-broadcast as data+heartbeat (§7.3, §10) |
| Minimal bespoke surface (KISS) | One custom software component; all else COTS/reuse (§2.3, §6) |
| Real-water as non-disruptive upgrade | Single ingestion abstraction seam (§9) |
| Runs on existing hardware | Pre-rendered video + trivial server co-hosted on the Mac (§3) |

---

## 14. Open items for reviewer decision
1. **Mode for first exhibition:** simulated vs real-water (§9). Simulated is built regardless; real-water adds [Proc A].
2. **Sensor interface:** HTTP-poll vs MQTT-push — determined by the specific sensor product (§7.4).
3. ~~**Projection transitions:** cross-fade between state loops vs authored directional transition clips (§6.5).~~ **RESOLVED — cross-fade state loops.** State durations are non-deterministic (visitor- and water-driven), so a fixed-length transition clip fails both ways: cut short it snaps (a 75 s bleach clip is only 40 % through when state 3 arrives at t=30 s), and outlasted it freezes on its last frame and the caustics stop. A blend has no duration of its own and cannot do either. Recovery needs no clip at all — the server already ramps intensity 1.0→0.0 across `recovery_ramp_s`, so the heal retimes from config with nothing to re-render. The one exception is the bleach latch, where intensity is already ~0.9 and the state change must carry the beat: a ~8 s one-shot, safe by construction because state 2 cannot be shorter than `recovery_lag_s` (30 s). Full rationale in `projection/README.md` § Design decisions.
4. **Cooling:** rely on passive cooling or fit the deferred fan-on-plug (§6.2) — decide after measuring real cool-down rate.
5. **Server host:** remain co-hosted on the Mac or pre-emptively relocate to a Raspberry Pi (§3.2) — decide after the full-load rehearsal.
6. **Audio event on bleach:** whether `/coral/state` triggers a discrete musical hit at the latch (§6.6).
