# Ableton Integration (Phase 2)

The first end-to-end proof of the whole concept: drive the fake rig through a full
visitor arc and *hear* the soundscape degrade continuously with warming, land the
bleach moment, and calm through the lagged recovery.

Ableton is a **passive OSC subscriber**, exactly like every other output. It never
talks back to the server. It answers one question 5×/second: *given this state and
intensity, what should the room sound like?*

> **What this document covers, and what it can't.** The server side of Phase 2 is
> already done — no code changes are needed (see *Server side* below). The rest is
> configuration inside Ableton's GUI, which is yours to click through. This guide
> gives the exact ports, addresses, value ranges, and the mapping flow; the precise
> knob/menu labels depend on which OSC-receiver device you install, so where a label
> is device-specific it's marked *(roughly this)* — follow the device's own panel.

> **Note on the Connection Kit (2026).** Ableton's *Connection Kit* pack has been
> discontinued; its devices are open-sourced at
> [github.com/Ableton/m4l-connection-kit](https://github.com/Ableton/m4l-connection-kit).
> Its only OSC-receive device, **OSC Monitor**, is *display-only* — it shows
> incoming OSC but can't map values to Live parameters — so it does **not** do what
> this phase needs (and our `tools/osc_monitor.py` already covers that diagnostic
> job). Use a purpose-built free OSC-receiver device instead — see Step 1.

---

## The contract you're subscribing to

The server fans out an OSC bundle to UDP **9010** (localhost) at 5 Hz. Each bundle
carries three messages (full spec in [PROTOCOL.md](PROTOCOL.md)):

| Address | Type | Range | Use it to… |
|---|---|---|---|
| `/coral/intensity` | float32 | `0.0 – 1.0` | **interpolate** — the primary continuous driver. Crossfader + macros. |
| `/coral/state` | int32 | `0 – 3` | **switch** — discrete phase. Optional one-shot at the bleach latch. |
| `/coral/temp` | float32 | °C | reference only; not usually mapped to sound |

The narrative these values trace (from [BUILD_ORDER.md](BUILD_ORDER.md) and the
state machine in [../CLAUDE.md](../CLAUDE.md)):

| State | Name | intensity | What the soundscape does |
|---|---|---|---|
| 0 | Natural | ~0.0 | full natural bed; reef alive |
| 1 | Fluorescent | rising, **reversible** | natural bed degrades continuously; industrial layer bleeds in |
| 2 | Bleached | ~1.0, **latched** | industrial dominates; the bleach one-shot fires; then stillness |
| 3 | Recovery | falling after a lag | stays bleached through the lag, then heals back toward natural |

The key point for mapping: **`intensity` is a single continuous 0→1 knob for the
whole arc.** Crossfade natural↔industrial across it and most of the piece works
before you touch a macro.

---

## Server side (already done — nothing to build)

Ableton is registered as a **static subscriber** in `config.yaml`:

```yaml
broadcast:
  static_subscribers:
    - { name: "ableton",    host: "127.0.0.1", port: 9010 }
    - { name: "projection", host: "127.0.0.1", port: 9020 }
```

Static subscribers are fixed `(host, port)` targets the server sends to every
broadcast, unconditionally — they never send `/client/hello` and never expire
(that mechanism is only for transient AR phones). So the moment the server is
running, it is *already* emitting to 9010. There is nothing to enable.

If you run Ableton on the **same Mac** as the server, leave the host at
`127.0.0.1`. If Ableton is on a **different machine**, change that host to its LAN
IP and restart the server.

---

## Step 0 — Prove the pipe before opening Ableton

Do this first. It removes Ableton from the equation so that when something later
doesn't work, you already know which side is at fault.

```bash
# Terminal 1
python src/server.py

# Terminal 2 — stand in for Ableton on its own port
python tools/osc_monitor.py            # binds the 'ableton' port (9010) from config

# Terminal 3 — drive the arc
python tools/fake_rig.py               # w = warm   c = cool
```

`osc_monitor.py` binds port 9010 and prints the exact bytes Ableton would receive,
with a live meter and the measured packet rate:

```
T= 26.35C  state=1:Fluorescent  intensity[####----------------] 0.18   5.0Hz  n=70
```

Press `w` in the rig and watch the meter climb and the state advance. If it moves
here, **the server→9010 pipe is proven** and any later fault is inside Ableton.

Because a UDP port can only be held by one program at a time, **quit `osc_monitor.py`
before you point Ableton at 9010** — otherwise Ableton's OSC device won't be able to
bind the port. (To watch the projection port instead: `python tools/osc_monitor.py
--name projection`.)

---

## Step 1 — Get a free OSC-receiver device

Receiving OSC into Live is a Max for Live device, so you need **Max for Live**:

- Included with **Ableton Live Suite**, or
- Live Standard **plus** a Max for Live add-on / Max license.

Live has **no native OSC input** and (see the note above) the Connection Kit no
longer provides a mapping receiver, so install one purpose-built free M4L device
that *receives* OSC and maps incoming values to Live parameters. Two good free ones:

| Device | Get it | Why |
|---|---|---|
| **OSC Mapper** (keenedesign) — *recommended* | [maxforlive.com/library/device/9922](https://maxforlive.com/library/device/9922/osc-mapper) · [keene.design/osc-mapper](https://keene.design/osc-mapper) | Free. Needs Live 11.1.3+ / M4L 8.1.5+. Each OSC address can drive up to **4 Live parameters at once** — a perfect fit for `/coral/intensity` → crossfader + several macros from one slot. |
| **SM OSC Receiver v2** (sampleme) | [maxforlive.com/library/device/8632](https://maxforlive.com/library/device/8632/sm-osc-receiver-v2) | Free. Needs Live 11.2.7+ / Max 8.5.2+. Has a **Learn** function, a built-in OSC monitor, and **value smoothing** — handy if the 5 Hz steps feel jumpy. |

Download the `.amxd`, then drag it into Live (or drop it in your User Library so it
shows in the browser). Either works; the guide below is written device-neutrally —
the concepts (listen port, OSC address, map, min/max) are the same on both.

---

## Step 2 — Add the OSC receiver and set the port

1. Create a MIDI track (it just hosts the device; it makes no sound). Call it
   `OSC IN`. (Most OSC-receiver M4L devices are MIDI-effect devices and live on a
   MIDI track.)
2. Drag the **OSC-receiver device** from Step 1 onto it.
3. Set its **listen / receive port to `9010`** — the same port as the server's
   `ableton` static subscriber.
4. Leave the host/IP alone; it's *receiving* on localhost.

**Sanity check with the meter still fresh in mind:** with `osc_monitor.py` **now
closed**, start the server + fake rig and press `w`. Both recommended devices show
incoming addresses/values (OSC Mapper and SM OSC Receiver both have a monitor pane).
You should see `/coral/intensity`, `/coral/state`, `/coral/temp` arriving. If nothing
arrives here but Step 0 worked, the fault is the device's port setting or macOS
blocking the app — see *Troubleshooting*.

> **Bundles are fine.** The server sends the three messages as one OSC *bundle*.
> Max — which every M4L OSC device is built on — unpacks bundles transparently, so
> the device sees three separate addresses. No special handling needed. (If a device
> ever shows nothing while `osc_monitor.py` sees packets, that device isn't unpacking
> bundles; switch to the other recommended device.)

---

## Step 3 — Build two stem groups and a crossfader

The musical structure that makes `intensity` do almost all the work:

1. Two parallel stem groups:
   - **`NATURAL`** — the healthy reef bed (field recordings, warm pads, life).
   - **`INDUSTRIAL`** — the degraded/bleached layer (drones, noise, cold tones).
   Build each as a **Group track** so you have one volume/level per side.
2. Assign the crossfader:
   - In the Session mixer, set the **crossfade assign** on `NATURAL` to **A** and
     on `INDUSTRIAL` to **B** (the `A · B` buttons per track).
   - The master **crossfader** now morphs A→B. Fully **A** = pure natural, fully
     **B** = pure industrial.

Author it so that **A = healthy = intensity 0** and **B = bleached = intensity 1**.

---

## Step 4 — Map `intensity` → crossfader (the core mapping)

Both recommended devices work the same way: each incoming address gets a **slot**,
a **Map / Learn** button, and a **Min/Max** range that scales the value onto the
target parameter. (In OSC Mapper one slot fans out to up to 4 targets — use that to
drive the crossfader *and* the Step 5 macros from this single `/coral/intensity`.)

1. In the OSC receiver, add / select the slot for address **`/coral/intensity`**.
2. Set its input range to **Min `0.0`, Max `1.0`** (the value already arrives
   normalized — this just says "don't rescale").
3. Click that slot's **Map**, then click the **crossfader** in Live's mixer.
4. Confirm direction: at `intensity 0` the crossfader sits at **A** (natural); at
   `intensity 1` it's at **B** (industrial). If it's inverted, swap the A/B
   assignments (Step 3) or swap the slot's Min/Max.

Test the whole arc now with the fake rig. Warming should slide the crossfader
smoothly toward industrial; cooling *before the latch* slides it back (state 1 is
reversible); once state 2 latches, `intensity` pins near 1.0 and the crossfader
sits at industrial until recovery — exactly the thematic behaviour, driven entirely
by the server. **Do not** add your own smoothing that fights the latch; the arc is
the server's to shape.

> **If the crossfader won't accept the map:** some setups won't let a M4L device
> map the master crossfader directly. Fallback that always works — map
> `/coral/intensity` to `INDUSTRIAL` group **volume** (0→1 quiet→loud) and, on a
> second slot with **Min/Max inverted** (`1.0 → 0.0`), to `NATURAL` group volume.
> Two inverse volume maps = a manual crossfade.

---

## Step 5 — Map 2–3 macros for continuous degradation

The crossfade alone is a hard A/B blend. These extra continuous maps make the
*degradation itself* audible. Map `/coral/intensity` (same address, more slots — or
duplicate the device) to 2–3 of:

| Target | Range as intensity 0 → 1 | Effect |
|---|---|---|
| Low-pass **filter cutoff** on `NATURAL` | open → closed | the reef bed goes muffled, "sick" |
| **Drone / noise level** on `INDUSTRIAL` | silent → present | the industrial layer swells in |
| **Reverb decay** (send/return) | tight → long, washed-out | space grows cold and cavernous |
| **Pitch / detune** on a natural pad | in-tune → detuned | subtle wrongness |

Keep it to two or three. Each is one OSC slot with a **Map** and a **Min/Max**.
Because they all ride the same `intensity`, they move together and reverse together
— the piece stays coherent for free.

---

## Step 6 (optional) — A discrete event at the bleach latch

`/coral/intensity` is continuous and reversible until it isn't. The **latch** — the
irreversible moment bleaching sets in — is carried by `/coral/state` crossing to
**2**. Marking it with a one-shot (a low sub hit, a sample stab, a sudden filter
slam, or muting the natural group) gives the arc its dramatic beat.

Approach: map / read `/coral/state` from the OSC receiver, and trigger when the
value equals **2**. Depending on your comfort level:

- **Simplest:** map `/coral/state` to a parameter whose jump to 2 is audible (e.g.
  a mute or a clip launch you've MIDI-mapped).
- **Cleaner:** a small Max patch that fires a bang on the 0→…→2 transition and
  launches a specific clip/scene once.

This is explicitly optional (per [BUILD_ORDER.md](BUILD_ORDER.md)); ship the
continuous version first.

---

## Acceptance test (Phase 2)

With server + fake rig running and Ableton mapped:

1. **Warm slowly.** The natural bed degrades continuously; industrial bleeds in.
2. **Cool before the latch.** It reverses smoothly — state 1 is reversible; state 2
   never occurs.
3. **Warm and hold at 28 °C.** After the sustained hold the bleach latches — the
   one-shot fires (if mapped) and the soundscape pins to industrial/stillness.
4. **Cool.** It stays bleached through the recovery lag, then heals gradually back
   toward the natural bed and state 0.
5. **Walk away 3 minutes.** The idle reset returns everything to Natural.

**Record this run** (audio + the `osc_monitor`/rig terminal, or a screen capture).
It is the first demonstrable proof of the concept and primary thesis documentation.
Save the export **outside** the repo — `*.mp4`/`*.mov`/`*.wav` are gitignored.

---

## Troubleshooting — isolate the layer

Always split the problem at the port with `osc_monitor.py`.

| Symptom | `osc_monitor` on 9010 shows… | Conclusion |
|---|---|---|
| Ableton not reacting | meter **moving** | Server→port is fine. Fault is Ableton: device port ≠ 9010, mapping not set, or macOS blocked the app. |
| Ableton not reacting | meter **frozen / nothing** | Fault is upstream. Is `src/server.py` running? Is `ableton` still on 9010 in `config.yaml`? Firewall? |
| Meter moves but stutters | rate **< 5 Hz** | Packets dropping. Raise `broadcast.rate_hz`, or check CPU load / Wi-Fi if Ableton is on another machine. |

Common specifics:

- **Port already in use / Ableton can't bind 9010.** Something else holds the port
  — most often a still-running `osc_monitor.py`. One program per UDP port. Quit it.
- **macOS "incoming connections" prompt.** Allow Max/Live to receive. *System
  Settings → Network → Firewall* must not be blocking it.
- **Values look like 0–127, not 0–1.** Some devices normalize, some pass raw. Set
  the slot's **Min/Max** to match what's actually arriving (watch `osc_monitor`).
- **Different machine.** Set the `ableton` static subscriber `host` to Ableton's
  LAN IP in `config.yaml` and restart the server. Both machines on the same subnet;
  no NAT between them.
