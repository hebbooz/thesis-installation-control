# Protocol Specification

Message contracts between the orchestration server and every other component. This document is authoritative — implementations must match it exactly.

Two planes:

- **Event plane** — OSC over UDP. One-to-many fan-out, loss-tolerant, continuously repeated.
- **Actuation plane** — HTTP over TCP. Point-to-point, must-succeed, confirmed.

---

## 1. Event plane — server to subscribers

Emitted as an OSC bundle at a fixed rate (default 5 Hz, configurable) to every subscriber: all registered AR clients plus the static localhost subscribers (projection player, Ableton).

| Address | Type | Range | Meaning |
|---|---|---|---|
| `/coral/state` | int32 | 0–3 | Discrete phase — used to *switch* (which clip, which material set) |
| `/coral/intensity` | float32 | 0.0–1.0 | Continuous severity — used to *interpolate* (dim, cross-fade, colour) |
| `/coral/temp` | float32 | °C | Live temperature, for display and reference |

### Subscriber contract

Subscribers **MUST**:
- Treat `intensity` as the primary continuous driver and `state` as the discrete selector.
- Apply values idempotently — the same values arrive repeatedly by design.
- Boot assuming state 0 / intensity 0.0 and converge silently on the first message received.
- Tolerate missing messages; hold the last known value.

Subscribers **MUST NOT**:
- Derive state from temperature themselves. The server is the single source of truth.
- Send anything back to the server except `/client/hello` (AR clients only).
- Depend on startup order.

### Why fixed-rate repetition

The broadcast is simultaneously the data plane and the heartbeat. A lost UDP datagram is corrected 200 ms later; a client that restarts converges within one interval. There is no separate keep-alive, no acknowledgement, and no retry logic anywhere in the system.

---

## 2. Registration — AR clients to server

Mobile AR devices are transient and interchangeable, so they are not given reserved addresses. They announce themselves instead.

| Address | Type | Meaning |
|---|---|---|
| `/client/hello` | string | Client identifier, sent every 5 s |

Server behaviour:
- On receipt: insert or refresh `{id, ip, port, last_seen}` in the client registry. **Both the IP and the port are taken from the UDP packet source**, not the payload.
- Broadcast is unicast to that `(ip, port)` for every registered client.
- Entries with `last_seen` older than 15 s are pruned.

Client requirement (**MUST**):
- A client **MUST send `/client/hello` from the same socket it listens for broadcasts on.** The server replies to the packet's source `(ip, port)`, so a client that transmits hello from a different or ephemeral port than it listens on will register successfully but **never receive a broadcast**. In practice: use one bidirectional UDP/OSC socket, bound to the client's listen port (default 9001), for both sending hello and receiving the fan-out. (In Unity/extOSC, set the transmitter's local port to the receiver's port.)

Why `(ip, port)` and not a fixed port: taking the port from the packet lets several clients share one host — e.g. multiple `tools/fake_client.py` instances on one laptop during localhost testing, which could not all bind the same fixed port — and is NAT-friendly. The cost is the MUST above.

This is application-layer service discovery: a phone that joins, sleeps, crashes, or is swapped mid-exhibition is handled with no configuration.

---

## 3. Actuation plane — server to smart plugs (Tasmota)

The Athom plugs run Tasmota and expose a local HTTP command endpoint. No cloud, no hub, no authentication on a trusted LAN.

```
GET http://<plug-ip>/cm?cmnd=Power%20On
GET http://<plug-ip>/cm?cmnd=Power%20Off
GET http://<plug-ip>/cm?cmnd=Power          # query current state
```

Response is JSON, e.g. `{"POWER":"ON"}`.

Plugs in use (IPs from config):

| Role | Purpose |
|---|---|
| `heater` | Powers the thermostatic aquarium heater. On = warming toward its dial setpoint. |
| `fan` | Powers the aquarium cooling fan (via USB brick). On = active cooling. |
| `lamp` | Powers the room lamp. On/off only — no dimming. |

### Control contract

The server treats the temperature subsystem as **set-target → gate-power → observe-sensor**:

- Target warm (28.0) → heater plug **on**, fan plug **off**
- Target cool (26.0) → heater plug **off**, fan plug **on**

The server never assumes the heater's internal state — the heater has its own thermostat and self-regulates. The server only gates power and observes the sensor.

Failures must be non-fatal: log the error, continue deriving state from the real sensor reading, retry on the next actuation change.

---

## 4. Sensor ingestion — ESP32-C3 to server

The sensor runs ESPHome. Two supported modes, chosen by config; the ingestion function isolates the difference so nothing downstream changes.

**HTTP poll (default).** The server polls the ESPHome web server at 2–5 Hz:

```
GET http://<sensor-ip>/sensor/water_temperature
→ {"id":"sensor-water_temperature","value":26.4,"state":"26.4 °C"}
```

**MQTT push (alternative).** If a broker is present, ESPHome publishes to a topic the server subscribes to. Functionally equivalent.

On timeout or malformed response: log, hold the last known temperature, continue. Optionally fall back to the simulated ramp for graceful degradation during an exhibition.

---

## 5. Input — buttons to server

The arcade encoder enumerates as a **USB HID gamepad/joystick**, not a keyboard. Read it with `pygame.joystick` (or `hid`).

| Physical | Event | Server action |
|---|---|---|
| Warm button (red) | gamepad button N pressed | set target = 28.0 |
| Cool button (blue) | gamepad button M pressed | set target = 26.0 |

Semantics:
- One press sets the target. Idempotent — repeat presses are harmless.
- **Cool wins** if both are pressed simultaneously or in the same tick.
- Any press resets the idle timer.
- Button indices are configurable; discover them once and record in config.

### Harness input (no-hardware mode)

While the physical buttons are unbuilt (Phases 1–5), `tools/fake_rig.py` injects presses over OSC to the server's listen port (UDP 9000), so the whole arc is drivable from a keyboard:

| Address | Type | Server action |
|---|---|---|
| `/sim/warm` | (no args) | set target = warm — identical to the warm button |
| `/sim/cool` | (no args) | set target = cool — identical to the cool button |

These are **development-harness messages only**. They funnel into the exact same set-target path the HID buttons use (above), so state logic is byte-identical whether input arrives from the keyboard or the encoder. They are harmless to leave enabled in production on the isolated LAN.

---

## 6. Display — server to display device

The server hosts a minimal read-only web page (default port 8080) showing live temperature and a subtle indicator of the current target. A spare phone or tablet displays it in a browser.

```
GET http://<server-ip>:8080/        → the display page
GET http://<server-ip>:8080/api     → {"temp":26.4,"target":28.0,"state":1,"intensity":0.2}
```

Read-only. No control authority.

---

## 7. Port allocation (defaults; all configurable)

| Endpoint | Proto/Port |
|---|---|
| Server OSC listener (hello) | UDP 9000 |
| AR devices OSC listener | UDP 9001 (per device) |
| Ableton M4L OSC receiver | UDP 9010 (localhost) |
| Projection player | UDP 9020 (localhost) |
| Display web page | HTTP 8080 |
| Tasmota plugs | HTTP 80 |
| ESPHome sensor | HTTP 80 |

---

## 8. Reserved addressing

Fixed devices get DHCP reservations by MAC on the router; AR phones use the dynamic pool and self-register.

| Host | Example IP |
|---|---|
| Router / gateway | 192.168.8.1 |
| MacBook (server) | 192.168.8.10 (wired) |
| Heater plug | 192.168.8.21 |
| Fan plug | 192.168.8.22 |
| Lamp plug | 192.168.8.23 |
| Temperature sensor | 192.168.8.30 |
| Display device | 192.168.8.40 |
| AR phones | DHCP pool |

Adjust to the Opal's actual subnet; these are config values, not commitments.
