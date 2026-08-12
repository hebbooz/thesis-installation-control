# Setup Log

Dated record of physical build steps, what was actually done, and the quirks discovered doing it.
`docs/HARDWARE.md` describes the *correct final configuration*; this file records *how it went*, so a
half-finished state can be picked up later and so the surprises are not rediscovered at the venue.

---

## 2026-08-12 — Network layer built (Phase 4, part 1)

**Router:** TP-Link Archer C50 AC1200, **hardware v3** (old green admin UI, left-hand menu tree).
Replaces the GL.iNet Opal named in the original plan; all docs updated.

### Done

| Item | Value | Verified |
|---|---|---|
| Router LAN IP | `192.168.50.1` (moved off factory `192.168.0.1`) | admin page HTTP 200 |
| Operation mode | Wireless Router | — |
| DHCP pool | `192.168.50.100` – `192.168.50.199` | clients landed in range |
| 2.4 GHz SSID | `coral-24` — WPA2-PSK/AES, channel 6, 20 MHz | — |
| 5 GHz SSID | `coral-5` — WPA2-PSK/AES | Mac joined |
| WAN uplink | Optus LAN port → C50 blue WAN port (development only) | `https://example.com` HTTP 200 from `coral-5` |
| MacBook reservation | `192.168.50.11`, MAC `bc:d0:74:f2:13:44`, Private Wi-Fi Address **off** | lease bound after router reboot |

Gateway MAC is `b0:4e:26:bb:3d:9f` — the last four digits match the factory SSID `TP-Link_3D9F`, which is the
reliable way to confirm you are on the coral router rather than some other `192.168.x` network.

### Quirks found (all now in HARDWARE.md)

- **The house Optus router is also `192.168.0.1`.** The factory C50 default collided with it exactly as
  feared, and a subnet check alone could not tell the two apart. This is why the LAN moved to
  `192.168.50.x`, and it is what will let the MacBook be wired to the coral LAN and on house Wi-Fi at once.
- **Address Reservation needs colons** (`BC:D0:74:F2:13:44`). The dash form printed in the TP-Link manual is
  rejected as "Invalid MAC Address format" on both desktop and mobile browsers.
- **Reservations only bind after a router reboot.** Renewing the client's DHCP lease is not enough — it just
  takes another pool address. Batch all reservations, then reboot once.
- **One admin session at a time.** If the radios restart mid-session the router keeps holding the dead
  session; power-cycle or wait ~5 minutes.
- **macOS randomises its Wi-Fi MAC per network**, so the address the router sees is not the hardware address
  until Private Wi-Fi Address is turned off for that network.
- **macOS auto-rejoins a network that has internet.** Before the WAN uplink existed, the Mac kept deserting
  `coral-24` for the house network; turn Auto-Join off on the house network during setup.

### Interim state

- No USB-C→Ethernet adapter yet, so the MacBook is on `coral-5` over Wi-Fi at `192.168.50.11` rather than
  wired at `.10`. `.10` is deliberately left free.
- The WAN uplink to the Optus router is **development scaffolding** — it exists so the Mac can be on the
  coral network and still reach the internet. Unplug it before the exhibition; nothing in the installation
  depends on it.

### Plugs — first one online

`tasmota-BC35E8-5608` (MAC `AC:A7:04:BC:35:E8`) joined `coral-24` and serves its Tasmota page: 243 V, relay
ON, power monitoring live. Reservations for `.21`/`.22`/`.23` are entered but not yet activated — the router
reboot is outstanding, so it currently sits on a pool address.

**The evening's real fault, after a long false trail: macOS Local Network privacy.** From the Mac, the plug
answered ARP but not ICMP or port 80, while the router (`192.168.50.1`) and the public internet stayed
perfectly reachable. That is not router client isolation — it is macOS silently dropping local-network
traffic for an app that has not been granted permission. ARP still resolves because it happens in the kernel,
below the check, which makes the imitation convincing.

Diagnosis in one step: **open the device's address in Safari.** Safari has the permission; if Safari loads
the page and the terminal cannot, it is the app permission, not the network.

Fix: *System Settings → Privacy & Security → Local Network* → enable for Terminal / VS Code / whatever
launches `src/server.py`, then restart that app. This will matter again on any new machine, after an OS
upgrade, and if the server is ever launched by a different process at the venue.

**Actuation proven** once the permission was granted, from the Mac to the plug over Wi-Fi:

```
curl "http://192.168.50.100/cm?cmnd=Power%20Off"  → {"POWER":"OFF"}
curl "http://192.168.50.100/cm?cmnd=Power%20On"   → {"POWER":"ON"}
```

Ping to the plug runs ~80 ms rather than the ~1 ms of a wired LAN — normal for an ESP in Wi-Fi power-save
mode, and irrelevant to this system, where actuation is edge-driven and runs on a background thread. From
`coral-5` it drops to 4–7 ms, and cross-band (host on 5 GHz → plug on 2.4 GHz) actuation is confirmed
working, which is the topology the installation actually runs.

### Plug identity map

All three plugs online at RSSI 100. Identified by switching each relay off in turn and watching which
physical plug's red LED went dark — the red LED follows the relay, not Wi-Fi.

| MAC | Reserve as | Role |
|---|---|---|
| `AC:A7:04:BC:35:E8` | `192.168.50.21` | heater |
| `AC:A7:04:BB:95:B8` | `192.168.50.22` | fan |
| `AC:A7:04:BC:8D:40` | `192.168.50.23` | lamp |

Tasmota hostnames are `tasmota-BC35E8-5608`, `tasmota-BB95B8-5560`, `tasmota-BC8D40-3392` respectively, which
is how they appear in the router's DHCP Client List. All three left switched **off** as a safe baseline.

**A reboot of the router is not enough to move an already-leased device onto its reservation.** The router
loads the new table, but a client holding a valid lease simply resumes on its old address — the plugs sat on
pool addresses through a full reboot. The client has to ask again. For Tasmota that is one command per plug,
no physical access needed:

```
curl "http://<current-ip>/cm?cmnd=Restart%201"
```

After ~20 s all three came up on `.21`/`.22`/`.23`. (The Mac rebound without this only because its Wi-Fi
dropped during the reboot, which forced a fresh request.)

### Phase 4 acceptance test — passed

Ran `src/server.py` + `tools/fake_rig.py` with `plugs.enabled: true` and `temperature.mode: simulated`, so a
simulated thermal arc drove real mains relays. Full arc at 21:35–21:42 on 2026-08-12:

| Criterion | Evidence |
|---|---|
| Warm → heater on, fan off | `21:36:36`, same tick |
| Cool reverses before the latch | `21:36:42` heater off, fan on |
| State 1 on rising temperature | `21:36:48` 0→1 Fluorescent |
| Latch on sustained heat, lamp blacks out | `21:38:03` 1→2, `lamp Off` |
| Idle reset at 180 s | `21:39:44` — last input `21:36:44`, exactly 180 s |
| Recovery lag 30 s | `21:39:44` → `21:40:14` 2→3, exactly 30 s |
| Heals to Natural, lamp returns | `21:42:30` 3→0, `lamp On` |

Every configured timing landed to the second, and the idle reset healed a bleached coral through 2 → 3 → 0
rather than un-bleaching it. Physical plug states afterwards (heater off, fan on, lamp on) matched the log.

**Fail-soft confirmed** at 21:45. Heater plug pulled from the wall mid-arc: each attempt logged a WARNING
after the 2 s connect timeout, while the fan and lamp kept actuating and the state machine latched the bleach
on schedule. The 5 Hz loop never stalled and the server never crashed. Phase 4 complete.

Two notes from that run:

- The latch fired despite the heater being unreachable, because temperature was *simulated* and the model
  kept warming. In real mode the same fault behaves better: no heater means the water does not warm, so the
  latch is simply never reached — it degrades to a coral that will not bleach, not one that bleaches without
  cause.
- **Failure logging is noisy** — every target change retries and logs a full connection-pool traceback,
  ~15 lines in 8 seconds. Over an exhibition day a dead plug would bury every interesting event.
  `temperature.py` already logs once on failure and once on recovery; `actuation.py` could match. Not a bug,
  but worth doing before the logs are relied on as thesis data.

### Next
3. Run the Phase 4 acceptance test (`plugs.enabled: true`, `temperature.mode: simulated`, server +
   `fake_rig.py`) — the plugs should physically switch on target changes.
4. Guest network off; change the admin password off `admin`/`admin`.
5. Phase 5: ESPHome sensor — credentials into `esphome/secrets.yaml`, flash, reserve `.30`.
6. When the Ethernet adapter arrives: reserve its MAC as `.10`, turn the Mac's Wi-Fi **off**, repoint the
   display page and any AR client from `.11` to `.10`, and retest wired with the AR phones on `coral-5`.
7. Write the printed config sheet: SSIDs, Wi-Fi password, admin password, reservation table.
