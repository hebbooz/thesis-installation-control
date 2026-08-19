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
| Router | TP-Link Archer C50 AC1200 | Private LAN, DHCP reservations. Mains-powered (9 V DC brick), 4× 10/100 LAN |
| Display | Spare phone/tablet | Shows the server's web page |
| Lamp | Any lamp, switch left permanently on | On/off via smart plug |

---

## Network setup

The router is a **TP-Link Archer C50 AC1200, hardware v3** — the older green admin UI with a left-hand menu
tree (v5 uses a different blue Basic/Advanced UI; menu paths below are the v3 ones). Admin panel:
`http://192.168.0.1` or `http://tplinkwifi.net`, login `admin`/`admin`. Nothing below needs a WAN cable — the
router serves DHCP on the LAN whether or not its WAN port is connected. If the Quick Setup wizard insists on
an internet type, choose Dynamic IP and click through its "no internet" complaint.

1. **Move the LAN off `192.168.0.x`.** *Network → LAN*: set the router IP to `192.168.50.1`, save, reboot,
   then browse to `http://192.168.50.1`. Everything in these docs uses `192.168.50.x`.
   The reason is collision: `192.168.0.x` is one of the two most common home/venue subnets, and the Mac is
   often wired to this LAN *and* on university Wi-Fi at the same time. Two interfaces on the same subnet fail
   silently and confusingly. One field here removes the whole class of problem.
2. **Set the DHCP pool** to `192.168.50.100`–`192.168.50.199` in *DHCP → DHCP Settings*, so the reserved
   addresses below (`.10`–`.40`) sit outside the pool.
3. **Separate the SSIDs.** v3 configures each radio in its own menu — *Wireless 2.4GHz → Wireless Settings*
   and *Wireless 5GHz → Wireless Settings* — so there is no band-steering/Smart Connect toggle to disable.
   Give them *different names* (e.g. `coral-24` and `coral-5`). ESP-class devices frequently fail to join
   combined-band SSIDs; this eliminates an entire category of frustrating problems.
4. **Make the 2.4 GHz radio ESP-friendly.** On that band set security to **WPA2-PSK (AES)** — not WPA/WPA2
   mixed — channel width **20 MHz**, and a fixed channel (1, 6 or 11) rather than Auto. The plugs and the
   sensor are 802.11 b/g/n 2.4 GHz single-band devices and are much happier with all three.
5. **Band allocation:**
   - 2.4 GHz — smart plugs, temperature sensor (their radios are 2.4 GHz only)
   - 5 GHz — AR phones, display device
   - Wired — MacBook (stable producer address, keeps 5 GHz clear)
6. **DHCP reservations by MAC** — *DHCP → Address Reservation → Add New* — for the plugs, sensor and Mac.
   **Enter the MAC with colons** (`BC:D0:74:F2:13:44`). This firmware rejects the dash form the TP-Link manual
   documents with "Invalid MAC Address format", on desktop and mobile alike.
   **Reservations only bind after a router reboot** (*System Tools → Reboot*) — renewing the client's DHCP
   lease is not enough, it will just take another pool address. So add every reservation first and reboot
   once at the end, rather than rebooting per device.
   Read each MAC from *DHCP → DHCP Client List* rather than from the device:
   that is the address the router actually sees, which is what the reservation must match. Reboot each
   appliance afterwards so it picks up its reserved address. Leave AR phones on the dynamic pool; they
   self-register at the application layer.

   **macOS randomises its Wi-Fi MAC per network.** Before reserving the Mac's wireless address, join
   `coral-5`, then *System Settings → Wi-Fi → Details… → Private Wi-Fi Address: **Off***. Left on the default
   (*Fixed*), the address is still stable but has been known to change across macOS upgrades — and a
   reservation that quietly stops matching is a bad thing to discover at a venue. Ethernet adapters are not
   randomised.
7. **Leave the guest network off.** Guest Wi-Fi isolates clients from the LAN, which would block exactly the
   device-to-device traffic this system depends on.
8. **No internet required.** The LAN is self-contained. Do not rely on a WAN connection at runtime. If you do
   want internet on it during development, patch the venue's Ethernet into the C50's **WAN** port — never put
   the installation directly on venue Wi-Fi.

The MacBook has no Ethernet port; the wired link needs a USB-C/Thunderbolt Ethernet adapter. Until it exists,
the Mac can sit on `coral-5` with its Wi-Fi MAC reserved to `192.168.50.11` — everything works, but retest
wired before the exhibition, and never leave the Mac on Wi-Fi *and* Ethernet on this LAN at once.

The 10/100 Mbps LAN ports are not a limitation here: the whole control plane is three OSC floats at 5 Hz.

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

Config lives at `esphome/coral-temp-sensor.yaml`.

ESPHome gets **its own virtualenv**, kept out of the project's. It pulls in platformio, esptool and a
compiler toolchain, with pins on `pyyaml`/`requests` that the server also depends on — a build tool has no
business being able to break the runtime. Like every venv here it lives outside iCloud (see the note under
Known constraints):

```bash
python3 -m venv ~/.venvs/esphome
~/.venvs/esphome/bin/pip install esphome
```

ESPHome requires Python ≥3.12 (and, as of 2026.7, <3.15).

Then copy `esphome/secrets.example.yaml` to `esphome/secrets.yaml` (gitignored) and fill in the `coral-24`
credentials. Flash with the board on USB — a **data** cable, not a charge-only one:

```bash
~/.venvs/esphome/bin/esphome run esphome/coral-temp-sensor.yaml
```

First flash is over USB; subsequent updates are over-the-air. Once running, verify:

```bash
curl http://<sensor-ip>/sensor/water_temperature
```

The REST path is built from the sensor's **`name:`**, not its `id:` — so renaming the entity moves the
endpoint, and a space in the name becomes `%20` in the URL. This is why the name is the URL-safe
`water_temperature` rather than a prettier "Water Temperature". Whatever the path is, it must match
`temperature.real.sensor_url` in `config.yaml`. To list what the device actually serves:

```bash
curl -s -m 6 -N http://<sensor-ip>/events | head -c 1500
```

With no probe attached the reply is `{"value":null,"state":"NA"}`. That is the correct result at this stage:
`RealSource._parse` turns it into a `TypeError` and holds the last good reading, so an unplugged probe
degrades to a frozen temperature rather than garbage entering the state machine.

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
| macOS blocks local-network traffic per app | Grant **Privacy & Security → Local Network** to whatever launches the server (Terminal, VS Code, the Python binary). Without it, plug/sensor HTTP fails silently while the router and internet stay reachable — it looks exactly like router client isolation. Restart the app after granting |
| Router is mains-powered (9 V brick), not USB | Needs its own outlet on the plinth power board; it reboots unattended after a power cut, so the LAN comes back on its own |
| Heater dial has ±0.5–1 °C slop | Set dial ≈28.5 so water reliably crosses the 27.8 latch threshold |
| Cooling is passive + fan only | Cool-down is slower than heat-up; measure and tune config timings to match |
| Encoder is a gamepad | Read with `pygame.joystick`, never a keyboard listener |
| Aquarium fan clamps to a thin rim | Verify against the chosen container, or mount to the plinth |
| Room light dims in state 2 | A separate constant spotlight on the coral is required for AR tracking |
