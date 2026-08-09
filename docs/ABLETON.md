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
carries five messages (full spec in [PROTOCOL.md](PROTOCOL.md)):

| Address | Type | Range | Use it to… |
|---|---|---|---|
| `/coral/bed` | int32 | `0 – 3` | **switch** — which of the four beds is audible. Bar-locked. |
| `/coral/intensity` | float32 | `0.0 – 1.0` | **interpolate** — continuous colour *within* a bed |
| `/coral/latch` | float32 | `0.0 – 1.0` | **anticipate** — ramps across the 10 s hold *before* the bleach |
| `/coral/state` | int32 | `0 – 3` | immediate, unquantised. Prefer `bed` for switching. |
| `/coral/temp` | float32 | °C | reference only; not usually mapped to sound |

The narrative these values trace (from [BUILD_ORDER.md](BUILD_ORDER.md) and the
state machine in [../CLAUDE.md](../CLAUDE.md)):

| State | Name | intensity | What the soundscape does |
|---|---|---|---|
| 0 | Natural | ~0.0 | the natural bed; reef alive |
| 1 | Fluorescent | rising, **reversible** | the fluorescent bed, pushed harder as intensity climbs |
| 2 | Bleached | ~1.0, **latched** | the bleached bed, arriving on the bar after the riser resolves |
| 3 | Recovery | falling after a lag | the recovery bed, healing as intensity walks back to 0 |

Two points that shape every mapping below:

**`bed` switches, `intensity` shades.** The four beds are mutually exclusive and
`bed` picks one; `intensity` moves things *inside* whichever bed is playing. Never
derive the bed from `intensity` — it is ambiguous. `1.0` is both state 2 and the
first 30 s of state 3, and `0.5` is state 1 warming *and* state 3 healing.

**`latch` is the only value that sees the future.** Everything else reports what
is already true. `latch` reports what is about to be, which is what lets the
soundscape lead the bleach instead of chasing it.

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

## Step 3 — Four beds, all playing at once

The instinct is to *start* the right loop when the state changes. Don't. Live has
no way to start something on cue from a mapped parameter, and a loop that starts
from cold is never in sync with the one it replaced.

Instead: **all four 16-bar beds play continuously, forever, and you only change
which one is audible.** Lay them out as four group tracks — one per state — in
Arrangement View with the loop brace around the 16 bars, or as four clips in one
Session scene. Press play once. They stay phase-locked for the whole exhibition,
so a switch cuts from bar 9 of one bed to bar 9 of the next, never mid-phrase.

"Playing at the appropriate time" is therefore a *volume* change, which is exactly
what the OSC receiver can drive.

---

## Step 4 — Map `/coral/bed` to the four groups

Six rows in the OSC receiver. Every row carries the address **`/coral/bed`** —
the same address six times, each with its own Map target.

| Row | In min | In max | Map to | Min | Max |
|---|---|---|---|---|---|
| 1 | `0` | `1` | **Natural** → Speaker On | `100 %` | `0 %` |
| 2 | `0` | `1` | **Fluorescent** → Speaker On | `0 %` | `100 %` |
| 3 | `1` | `2` | **Fluorescent** → Track Volume | `85 %` | `0 %` |
| 4 | `1` | `2` | **Bleached** → Speaker On | `0 %` | `100 %` |
| 5 | `2` | `3` | **Bleached** → Track Volume | `85 %` | `0 %` |
| 6 | `2` | `3` | **Recovery** → Speaker On | `0 %` | `100 %` |

Map the **group** fader, not its children. `85 %` is where Live's volume fader
sits at 0 dB — use your own mixed level if the groups aren't at unity.

Which state ends up audible:

| bed | Natural | Fluorescent | Bleached | Recovery |
|---|---|---|---|---|
| 0 Natural | **on** | off | off | off |
| 1 Fluorescent | off | **on, 0 dB** | off | off |
| 2 Bleached | off | on, −inf | **on, 0 dB** | off |
| 3 Recovery | off | on, −inf | on, −inf | **on** |

Natural and Recovery need one row each because they sit at the ends of the range,
where a single row's clamping already gives a step. Fluorescent and Bleached sit
in the middle, and one row can only push a parameter in one direction — so the
**Speaker On** row switches each of them on at the bottom of its band and the
**Track Volume** row switches it off at the top. Both must be true to hear it.

Three things that bite:

- **Set `In min` / `In max` before mapping.** Leaving the default `0.00–1.00` on a
  `bed` row clamps states 1, 2 and 3 into one value and nothing past Fluorescent
  ever fires.
- **Smoothing off on the `Speaker On` rows.** Smoothing a binary parameter makes
  it flicker through intermediate values on every change. ~30 ms on the Track
  Volume rows is good — it turns each step into a short fade and stops the click.
- **The Parameter field shows the parameter name, not the track.** To find out
  which group a row actually grabbed, set its Min *and* Max both to `0 %` and see
  which group goes silent. Rows 1, 2, 4 and 6 must land on four *different* groups.

Send all four groups to one reverb return. The return keeps ringing through a
switch, so the outgoing bed's tail carries across the cut instead of it sounding
like an edit. This matters more than anything else for making a hard swap feel
intentional.

---

## Step 5 — Map `/coral/intensity` for continuous colour

The bed switch is a step; `intensity` is what makes the piece move *between*
switches. Note where the range actually lives:

- state 0 spans only intensity `0.0 – 0.1` (26.0 → 26.2 °C)
- state 1 carries `0.1 – 0.95` across the whole ~80 s warm-up
- state 3 walks `1.0 → 0.0` down the 45 s recovery ramp

So **Natural and Bleached are essentially static beds; Fluorescent and Recovery
are the two that need modulation.** One or two parameters each is plenty — a
filter cutoff and a reverb send. Use the row's `In min` / `In max` to window the
useful part of the range (e.g. `0.15 → 0.95`) rather than mapping the full sweep.

---

## Step 6 — `/coral/latch`: the riser that arrives *before* the bleach

Bleaching requires 10 s of sustained heat before it latches. For those 10 s the
outcome is already decided and merely unspent — and the server publishes that as
`/coral/latch`, ramping `0.0 → 1.0` across the hold.

That is the cue for a noise sweep, a riser, a swell: map `/coral/latch` to its
level or filter and the sweep starts exactly when the threat begins and resolves
exactly as the coral bleaches. If the visitor cools off mid-hold, the ramp
retreats to 0.0 and the sweep backs down — the piece un-promises the bleach,
which is the reversibility argument made audible.

It pins at `1.0` once latched and stays there through states 2 and 3. With
quantisation on, that means the riser *sustains* through the wait for the bar
line and cuts when the bleached bed finally lands. Put the sweep on a track that
survives the switch (outside the four groups, or fed to the reverb return) if you
want it to ring out rather than stop dead.

---

## Step 7 — Bar-locked switching

Switches driven straight off `/coral/state` land wherever the packet happens to
arrive, usually mid-bar. `/coral/bed` is the same value held back to the next
musical boundary, so the cut lands on bar 1 or bar 9 of the loop.

Live is the clock master; the server only listens.

1. **Audio MIDI Setup** → *Window ▸ Show MIDI Studio* → double-click **IAC Driver**
   → tick **Device is online**. One bus is enough.
2. **Live ▸ Settings ▸ Link/Tempo/MIDI** → find the IAC Driver *Output* row →
   turn **Sync** on.
3. In `config.yaml` set `quantize.enabled: true`. Check `midi_clock_port` matches
   part of the bus name (default `"IAC"`).
4. `pip install -r requirements.txt` (adds `python-rtmidi`).
5. Restart the server. It logs `QUANTIZE listening for MIDI clock on '…'` when the
   port is open, and `BED -> n` on every quantised switch.

**Live's transport must be running** — MIDI clock only flows while it plays. In
Arrangement, keep the loop brace on and let it run.

### Choosing the window

`quantize_bars: 8` on a 16-bar loop puts the cut on bar 1 or bar 9. The cost is
the wait: **worst case is half your loop length, average a quarter.** At 120 BPM
8 bars is 16 s. If that feels dead in rehearsal, drop to `4` — it is a config
change, not a code change. The `/coral/latch` riser is what covers the wait
before a bleach; nothing covers it on the other transitions, which is the honest
argument for 4 over 8.

`lead_ms` fires the switch slightly early to absorb the server tick, OSC transit
and Live's own parameter latency. Start at `0`; if the cut feels consistently
late, try `40`.

### If it doesn't quantise

The failure mode is silent by design — no clock means `/coral/bed` publishes
immediately, which is exactly the behaviour you had before. Check the server log
at startup:

| Log line | Meaning |
|---|---|
| `QUANTIZE listening for MIDI clock on …` | Port open. If it still doesn't quantise, Live's **Sync** isn't on or the transport is stopped. |
| `QUANTIZE no MIDI input matching 'IAC' (available: …)` | The IAC bus is offline. Step 1. |
| `QUANTIZE python-rtmidi not installed` | Step 4. |
| nothing at all | `quantize.enabled` is still `false`. |

This is the one place anything talks *back* to the server, which CLAUDE.md
otherwise forbids. What crosses the boundary is a clock and never state — Live
cannot influence *what* the server publishes, only *when* the bed value moves —
and with no clock the system is byte-identical to before. Ableton is still never
a dependency.

---

## Acceptance test (Phase 2)

With server + fake rig running and Ableton mapped:

1. **Warm slowly.** The natural bed degrades continuously; industrial bleeds in.
2. **Cool before the latch.** It reverses smoothly — state 1 is reversible; state 2
   never occurs.
3. **Warm and hold at 28 °C.** The riser builds across the 10 s hold, the bleach
   latches, and the bleached bed lands on the next bar line.
4. **Cool.** It stays bleached through the recovery lag, then the recovery bed
   takes over and heals back toward Natural.
5. **Walk away 3 minutes.** The idle reset returns everything to Natural.

On (5), the walk-away path plays the Recovery bed too. The idle reset presses the
cool button and leaves the latch alone, so an unattended bleach heals 2 → 3 → 0
exactly like a deliberate cool press — just starting 180 s later. There is no
path from Bleached back to Fluorescent; once bleached, the only way out is
through Recovery.

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
