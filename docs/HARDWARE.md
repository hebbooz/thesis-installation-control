# Hardware

Devices, wiring, firmware and network setup. Nothing here requires soldering.

---

## Inventory

| Role | Device | Notes |
|---|---|---|
| Host | MacBook Pro M1 Pro | Runs server + projection player + Ableton |
| Smart plugs ×4 | Athom Tasmota ESP32-C3 AU Plug V3 | Heater, fan, lamp, spare. Pre-flashed Tasmota, local HTTP |
| Temperature sensor | ESP32-C3 Mini dev board (with headers) | Runs ESPHome |
| Probe | Waterproof DS18B20 (genuine Maxim) | Sits in the water; board stays dry |
| Pull-up | 4.7 kΩ resistor | Required for the 1-Wire bus |
| Prototyping | 170-point mini breadboard, M/F jumper wires | No soldering |
| Heater | 100 W thermostatic aquarium heater, ~9 cm | Self-regulating; dial ≈28.5 °C |
| Cooling | Clip-on aquarium fan (USB) | Needs a USB brick; brick plugs into the fan's smart plug |
| Buttons | Arcade buttons + USB "zero delay" encoder | Enumerates as a **gamepad**, not a keyboard |
| Router | GL.iNet Opal (GL-SFT1200) | Private LAN, DHCP reservations |
| Display | Spare phone/tablet | Shows the server's web page |
| Lamp | Any lamp, switch left permanently on | On/off via smart plug |

---

## Network setup

1. **Separate the SSIDs.** In the Opal's admin panel, give the 2.4 GHz and 5 GHz networks *different names* (e.g. `coral-24` and `coral-5`). ESP-class devices frequently fail to join combined-band SSIDs — this eliminates an entire category of frustrating problems.
2. **Band allocation:**
   - 2.4 GHz — smart plugs, temperature sensor (their radios are 2.4 GHz only)
   - 5 GHz — AR phones, display device
   - Wired — MacBook (stable producer address, keeps 5 GHz clear)
3. **DHCP reservations by MAC** for the plugs, sensor and Mac. Leave AR phones on the dynamic pool; they self-register at the application layer.
4. **No internet required.** The LAN is self-contained. Do not rely on a WAN connection at runtime.

---

## Temperature sensor wiring

The DS18B20 has three wires: **red** (VCC), **black** (GND), **yellow/white** (data).

```
   ESP32-C3                    Breadboard
   ┌────────┐
   │    3V3 ├──────────────────▶ + rail ──────┬──▶ DS18B20 RED
   │        │                                  │
   │        │                            [4.7 kΩ]   ← pull-up resistor
   │        │                                  │
   │  GPIO4 ├──────────────────▶ row ─────────┴──▶ DS18B20 YELLOW
   │        │
   │    GND ├──────────────────▶ − rail ─────────▶ DS18B20 BLACK
   └────────┘
```

The 4.7 kΩ resistor bridges the **data line to 3V3** — this is mandatory; the bus will not work without it.

The probe's bare wires push directly into breadboard rows. M/F jumper wires connect breadboard rows to the ESP32's header pins.

Power the ESP32 from a USB brick (not the smart plugs — the sensor must stay powered continuously).

---

## ESPHome firmware

Config lives at `esphome/coral-temp-sensor.yaml`. Install ESPHome (`pip install esphome`), edit the WiFi credentials, then:

```bash
esphome run esphome/coral-temp-sensor.yaml
```

First flash is over USB; subsequent updates are over-the-air. Once running, verify:

```bash
curl http://<sensor-ip>/sensor/water_temperature
```

---

## Smart plugs (Tasmota)

The Athom plugs arrive pre-flashed. Setup:

1. Power on; the plug creates a temporary WiFi access point.
2. Connect to it, enter the `coral-24` SSID and password.
3. Find its IP (router admin panel); set a DHCP reservation.
4. Verify local control:

```bash
curl "http://<plug-ip>/cm?cmnd=Power%20On"     # → {"POWER":"ON"}
curl "http://<plug-ip>/cm?cmnd=Power%20Off"    # → {"POWER":"OFF"}
```

If those two commands work, the plug is fully integrated — no further configuration needed.

**Power chains:**
- Wall → heater plug → heater
- Wall → fan plug → USB brick → fan
- Wall → lamp plug → lamp (lamp's own switch left ON)

---

## Buttons

Wire each button's two microswitch terminals to the encoder using the supplied spade connectors — push-on, no soldering. Connect the encoder to the Mac by USB.

**The encoder presents as a USB gamepad.** Find the button indices:

```bash
python tools/discover_buttons.py
```

Press each button; record the reported indices in `config.yaml` under `input.warm_button` / `input.cool_button`.

---

## Water container

Choose *after* the heater arrives, so you can check fit. Requirements:

- Wide enough for the ~9 cm heater laid horizontally, with the coral print alongside.
- Deep enough to cover the heater's minimum submersion line (check its markings).
- A rim under ~9 mm thick for the fan's clamp — or plan to mount the fan to the plinth instead.
- The coral print fixed so it cannot shift; a moved model target breaks AR alignment.

Evaporation lowers the level over an exhibition day — plan top-ups with distilled water.

---

## Projection

Two Epson EB-435W as one spanned canvas:

- Projector 1 → the Mac's built-in HDMI port
- Projector 2 → USB-C→HDMI adapter on a Thunderbolt port

Each display needs its own physical port. **Do not use a dual-HDMI hub** — those rely on DisplayPort MST, which macOS does not support; you would get mirroring, not two independent halves.

In *System Settings → Displays*, set **extend** (not mirror) and arrange side by side to form 2560×800.

---

## Known constraints

| Constraint | Consequence |
|---|---|
| Plugs and sensor are 2.4 GHz only | Separate SSIDs; don't put them on the 5 GHz network |
| Heater dial has ±0.5–1 °C slop | Set dial ≈28.5 so water reliably crosses the 27.8 latch threshold |
| Cooling is passive + fan only | Cool-down is slower than heat-up; measure and tune config timings to match |
| Encoder is a gamepad | Read with `pygame.joystick`, never a keyboard listener |
| Aquarium fan clamps to a thin rim | Verify against the chosen container, or mount to the plinth |
| Room light dims in state 2 | A separate constant spotlight on the coral is required for AR tracking |
