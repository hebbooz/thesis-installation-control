# Projection player

The Unity player that drives the spanned 2560×800 projector canvas (Phase 8).
It is a passive subscriber: it listens on UDP 9020 for `/coral/state`,
`/coral/intensity` and `/coral/temp`, and answers only *given this state and
intensity, what do I show?*

The Unity project itself is not version-controlled here (it is mostly generated
files). What is committed is the bespoke code — drop `Scripts/` into a new
project per the setup below.

---

## The forward-only invariant

Every exported clip carries caustics. Caustics running **backwards** read
instantly as an error, far more obviously than any imperfection in a dissolve.
The whole player is built around one rule:

> **Playheads only ever advance. A clip is seeked only while its weight is 0.**

Consequences, all deliberate:

- **Intensity drives opacity, never a playhead.** When a visitor cools off before
  the bleach latch, the blend weight falls but both videos keep running forward.
  This is the reason to cross-fade rather than scrub a transition clip to
  `intensity × duration` — scrubbing would reverse on cooling.
- **The three hold clips loop forever and are never touched** after they start.
- **The latch one-shot rewinds only while invisible.** `RequestLatchClip` does
  not seek; it sets a pending flag, and `TryFirePending` performs the rewind only
  once that layer's weight has reached zero, so a rewind is never on screen.
- **The one-shot hands over one cross-fade *before* its last frame**, so it never
  freezes on a still. A frozen frame stops the caustics, which is exactly as
  conspicuous as reversing them.

## Layers

| Layer | Clip | Mode | Length |
|---|---|---|---|
| Healthy | `coral-healthy.mp4` | loop, free-running | 60 s |
| Fluorescent | `coral-fluorescent.mp4` | loop, free-running | 60 s |
| Bleached | `coral-bleached.mp4` | loop, free-running | 60 s |
| Latch | `coral-fluorescent-to-bleached.mp4` | one-shot on 1→2 | **20 s** |

There are **no directional transition clips**. Every change except the bleach
latch is an opacity blend between two loops.

At most **two** layers are ever meaningfully visible — the "dual VideoPlayer
cross-fade" of ARCHITECTURE.md §6.5. Typical concurrent decode is three streams.

## State mapping

The **state** selects a pair of loops; **intensity** sets the weight within it.

| State | Pair | Weight |
|---|---|---|
| 0, 1 | healthy ↔ fluorescent | `w_fluorescent = intensity` |
| 2, 3 | healthy ↔ bleached | `w_bleached = intensity` |

States 2 and 3 are one rule because the server pins intensity to 1.0 while
bleached, then ramps it 1.0 → 0.0 across `recovery_ramp_s`. The heal is therefore
driven entirely by the broadcast: **retime it in `config.yaml` and the projection
follows, with nothing to re-render.**

Recovery heals **bleached → healthy directly**, never back through fluorescent —
fluorescence is a stress response, so it has no place on the way out.

The compositor normalises (`w_b / (w_a + w_b)`), so the two contributions sum to
1 by construction, including mid-ease when both weights are still moving.

The one exception is **1 → 2, the bleach latch**, where a short one-shot plays
once. Intensity is already ~0.9 when it fires, so there is no intensity movement
to carry it — the state change has to.

Paths worth knowing about, all handled:

- **3 → 2, recovery cancelled** by re-warming. Intensity snaps back to 1.0 and
  the weight simply travels back toward bleached. Nothing to restart.
- **2 → 1** is no longer produced by the server: the idle reset presses cool and
  leaves the latch alone, so walking away heals 2 → 3 → 0 like any other recovery.
  The player still handles the transition (cross-fades bleached → fluorescent,
  nothing rewinds) — it costs nothing and keeps the player total over the state
  space rather than over the paths the server happens to emit today.
- **3 → 0** on completion. Intensity is already 0, so both rules agree on pure
  healthy and nothing changes visually.
- **Cold start directly into state 2** skips the latch clip and cuts to the
  blend. That rupture already happened before the player existed; replaying it
  would be a lie.

---

## Setup

The project has already been created at
`../../projection-mapping/coral-projection-player` (Unity 6000.5.3f1, the same
editor the AR client uses). It is not version-controlled — `Assets/Editor/CoralSetup.cs`
inside it rebuilds the whole thing headlessly, so it never has to be assembled by
hand again:

```bash
cd "../projection-mapping/coral-projection-player"
/Applications/Unity/Hub/Editor/6000.5.3f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -nographics -quit -projectPath "$PWD" \
  -executeMethod Coral.Editor.CoralSetup.Build
```

That sets Linear color space, builds the scene (camera + ProjectionPlayer with
the blend shader assigned), and writes `Build/CoralProjection.app`. After editing
anything in `Scripts/`, copy it into the project's `Assets/CoralProjection/` and
re-run the command.

Two things that project needs and a bare Unity project does not have:

- **extOSC**, vendored as an embedded package in `Packages/com.iam1337.extosc`
  (copied from the AR client, so both ends speak the same library).
- **`com.unity.ugui`**, added to `Packages/manifest.json`. extOSC ships UI
  helpers that import `UnityEngine.UI`, which a bare Unity 6 project omits — 
  without it the project fails to compile with ~200 `CS0234` errors that have
  nothing to do with this player.

The manual equivalent, if the project ever has to be rebuilt from the GUI:

**Unity 2022.3 LTS or newer, Built-In Render Pipeline.**

> ⚠️ **Not URP or HDRP.** The compositor uses `OnRenderImage`, which silently
> never fires under the scriptable pipelines. If you create the project from a
> URP template the screen stays black with no error.

1. **New project** → 3D (Built-In Render Pipeline).

2. **Project Settings → Player → Other Settings → Color Space: `Linear`.**
   This is not cosmetic. With Linear plus the sRGB RenderTextures the player
   creates, the shader's `lerp` is a linear-light blend. In Gamma the same
   dissolve between the bright healthy look and the deliberately stopped-down
   bleached look produces a midpoint darker than the true half-exposure, and the
   ~64 s state 0/1 fade visibly sags through the midtones.

3. **Import extOSC** (same package the AR client uses).

4. Copy `Scripts/` into `Assets/CoralProjection/`.

5. In the scene: select **Main Camera**, add the **ProjectionPlayer** component,
   and drag `CoralBlend.shader` onto its **Blend Shader** field. Assigning it
   explicitly is what guarantees the shader ships in the build — `Shader.Find`
   alone will fail in a player unless the shader is in Resources or Always
   Included. Everything else (video players, render textures, OSC listener) is
   created at runtime.

6. Delete any other cameras, and set the camera's Clear Flags to Solid
   Color / black.

7. **Build Settings → macOS**, and in Player Settings set Fullscreen Mode to
   *Fullscreen Window*, and disable the resolution dialog.

8. Copy `config.example.json` to `coral-projection.json` **beside the built
   `.app`**, set `video_dir` to wherever the clips live, and adjust paths.

## Config

`coral-projection.json` is found by, in order: a `--config <path>` command-line
argument, then searching upward from the player's data directory (which covers
both a `.app` bundle and a folder build), then StreamingAssets. The path it
actually used is logged at startup.

A missing or malformed config logs loudly and falls back to defaults rather than
refusing to start — an exhibition machine must always come up.

| Key | Notes |
|---|---|
| `osc_port` | Must match `broadcast.static_subscribers[projection].port` in `config.yaml` (9020) |
| `video_dir` | Absolute path to the clips; `""` resolves them beside the config file |
| `crossfade_s` | One fade rate for everything. Also the slew limit on the state 0/1 blend, and the lead-in used to hand a one-shot over before its last frame |
| `video_width/height` | Native size of the exported clips. Stated, not detected — RenderTextures are built before any clip is prepared. A prepared clip that disagrees logs a warning |
| `display_width/height` | 2560 × 800 spanned canvas |
| `fit`, `pan_x/y` | How a source of a different aspect maps onto the canvas — see *Canvas arithmetic* |

## Design decisions

Recorded because each of these looks like an arbitrary choice later, and at least
two of them are things a well-meaning edit would undo.

### 1. Cross-fade state loops; no directional transition clips

*Resolves ARCHITECTURE.md §14.3.* State durations are non-deterministic — set by
the visitor and by the water, not by the video. A fixed-length transition clip
therefore fails in both directions:

- **Cut short.** With a 75 s bleaching clip and a visitor who presses cool
  immediately, state 3 arrives at t=30 s while the coral is only 40% bleached.
  Whatever it hands over to starts *fully* bleached, so the image snaps from 40%
  to 100% white and then heals. This is the common path, not an edge case.
- **Outlasted.** If the state runs longer than the clip, the clip stops on its
  last frame and the caustics freeze — as conspicuous as reversing them.

Blending loops has neither failure mode: a blend has no duration of its own, so
it cannot be cut short or run out.

*Cost:* a dissolve is a **linear** interpolation between two looks. If the real
path between them isn't linear — fluorescence blooming brighter before going
white, say — a cross-fade won't show it. See *Extension point* for the fix.

### 2. Intensity drives opacity, never a playhead

The alternative is scrubbing a clip to `intensity × duration`, which reads the
same on the way up and reverses the caustics on the way down. Backwards caustics
read instantly as an error; imperfect overlap during a dissolve does not. Hence
the forward-only invariant above.

### 3. Recovery has no clip of its own

The server already ramps intensity 1.0 → 0.0 across `recovery_ramp_s`, so state 3
is just the state 2 blend continuing to move. Consequences: the heal retimes
itself from `config.yaml` with no re-render, a cancelled recovery is a weight
travelling back rather than a clip to restart, and there is one less clip to
render and keep in sync.

### 4. The bleach latch keeps a short one-shot

The single exception, because intensity is already ~0.9 when the latch fires and
there is nothing left for it to drive. It is safe from interruption **by
construction**: state 2 cannot be shorter than `recovery_lag_s` (30 s — the
visitor cannot press cool before the latch, since latching only arms while
warming), so a 20 s clip settles on the bleached loop with ~10 s to spare.

That bound is the whole reason for the length:

```
crossfade_s  <  latch clip  <  recovery_lag_s
   1.5 s     <     20 s     <       30 s
```

⚠️ **The margin is now 10 s, not 22 s.** `recovery_lag_s` and this clip are
coupled — do not lower the lag below ~25 s without shortening the clip, or the
bleach gets cut off part-way and snaps to fully white before healing.

### 5. Loop seams are cross-faded in post, not engineered away

Requiring the caustic period to divide the clip length only works if the caustics
are driven by something periodic. If they come from a simulation there is no
period to divide. Overlapping the tail onto the head with a dissolve works
regardless, costs nothing at runtime, and still never runs anything backwards.

*Cost:* a soft spot at a strictly regular interval, which repetition can make
noticeable even when one instance isn't. If the caustics happen to be a texture
pan or a sine, a true seamless loop is free and strictly better.

### 6. Blending happens in linear light

Not cosmetic. A gamma-space dissolve between the bright healthy look and the
stopped-down bleached look yields a midpoint darker than the true half-exposure,
and the ~64 s state 0/1 fade visibly sags through the midtones.

## Canvas

Two 1280×800 projectors side by side make a **2560×800** desktop — each shows
exactly half. Clips are exported at **2560×800**, so every source pixel maps to
one projector pixel with no scaling anywhere in the chain.

`fit` is therefore a no-op (scale 1,1 offset 0,0) and `pan_x/y` are irrelevant.
Both stay as a safety net: a clip exported at the wrong size gets cropped to fill
rather than silently squashed. Rely on the startup warning rather than the
fallback — the player logs `[clip] … but config says …` when a prepared clip
disagrees with `video_width/height`.

## Clip lengths

| Clip | Length | Why |
|---|---|---|
| `coral-healthy` | 60 s | Loops forever; length is a quality choice, not a functional one |
| `coral-fluorescent` | 60 s | ” |
| `coral-bleached` | 60 s | ” |
| `coral-fluorescent-to-bleached` | **20 s** | Must be < state 2's 30 s floor so it can never be cut short |

The three loops don't affect behaviour at any length — they are the safety net
that covers indefinite dwell. 60 s balances the two things that do matter:

- **Wrap frequency.** Each wrap is a self-crossfade soft spot. Longer clip, rarer
  soft spot.
- **File size.** ProRes 422 at 2560×800/30 is ≈145 Mbps, so 60 s ≈ 1.1 GB. Three
  loops plus the latch ≈ 3.4 GB. Comfortable; 120 s clips would be ≈6.7 GB.

Same length for all three is deliberate: their wraps then coincide rather than
landing at three different times during a long cross-fade, so one soft spot
instead of two overlapping ones.

**Push `coral-healthy` to 90–120 s if the caustics are pronounced.** It is the
idle state — on screen between visitors, which is most of the exhibition — so it
is the only one whose repeat a visitor has time to notice.

## Encoding the clips

Export at **2560×800**, matching the canvas exactly.

- **30 or 60 fps, not 23.976.** The projectors run at 60 Hz; 23.976 needs 3:2
  pulldown and judders. The corals don't move, but the caustics do, and judder on
  slowly drifting light reads as broken playback rather than slow water. 30 goes
  into 60 as a clean 2:2.
- **ProRes 422** is the recommendation. The M1 Pro has a hardware ProRes engine,
  so three or four simultaneous 2560×800 streams are essentially free. 8-bit
  H.264 bands badly on slow, subtle lighting ramps in dark scenes, which is
  exactly what this piece is. If Unity's `VideoPlayer` misbehaves with ProRes on
  your Unity version, HEVC 10-bit is the fallback.
- **Lock exposure manually in Unreal across all six renders.** With auto-exposure
  live, each clip settles to its own drifted exposure and you get a brightness
  pop at every junction that no cross-fade will hide.
- **Render all six from one continuous master timeline** so the last frame of a
  transition is by construction the first frame of the hold it leads into.
- **Close the loop** on the three hold clips. Either render the caustic animation
  on a period that divides the clip length exactly (free and strictly better, if
  the caustics are a texture pan or a sine), or overlap the tail onto the head
  with a cross dissolve in your NLE (works for a simulation with no period). An
  open wrap is a hitch every 60 s, forever, in a quiet gallery.
- The latch clip must be longer than `crossfade_s` and shorter than
  `recovery_lag_s` (30 s). **20 s**, leaving 10 s of margin.

## Testing

**Without projectors or water** — run the player windowed, then drive it:

```bash
python tools/drive_projection.py            # manual: 0-3 states, w/c ramp intensity
python tools/drive_projection.py --arc      # scripted full arc at real pace
python tools/drive_projection.py --arc --speed 6
python tools/drive_projection.py --state 2  # park on bleached for projector alignment
```

Run it **with `src/server.py` stopped** — both write the same UDP port, and
interleaved senders will make the state appear to flicker.

This tool exists for two things the real server cannot do: hold a state still
(alignment, keystone, focus, black-level matching between the two units need a
fixed image for minutes, and the server is always drifting toward a target), and
reach the rare paths on demand (3→2→3 recovery-cancelled takes minutes of real
water to provoke, and 2→1 the server no longer emits at all, so this is the only
way to exercise it).

**With the server** — the normal path. `config.yaml` already lists projection as
a static subscriber, so nothing needs registering:

```bash
python src/server.py
python tools/fake_rig.py
```

Start the player before the server, kill the server mid-arc, restart it: the
player must reconverge within one broadcast interval with no operator action.

Transitions are logged with timestamps to Unity's `Player.log`
(`~/Library/Logs/<company>/<product>/Player.log`).

## Troubleshooting

| Symptom | Cause |
|---|---|
| ~200 `CS0234` errors in `Packages/com.iam1337.extosc/Scripts/UI/` | `com.unity.ugui` missing from `Packages/manifest.json` — see Setup |
| Colours shifted vs the Unreal render | Clips carry no colour-primaries tag; `AVFoundationVideoMedia` logs it and falls back. Tag the exports bt709. |
| Black screen, no errors | URP/HDRP project — `OnRenderImage` never fires. Must be Built-In RP. |
| Black screen, `[blend]` error | Blend Shader field not assigned on the component |
| Midtones sag during the long fade | Color Space is Gamma, not Linear |
| Coral looks vertically squashed | `fit` is `stretch`, or `video_width/height` disagree with the files — check for the `[clip] … but config says` warning |
| Black bars down both sides | `fit` is `contain` on a taller-than-canvas source |
| Crop keeps the wrong half of the frame | Adjust `pan_y`; flip its sign if it moves the wrong way |
| State appears to flicker | `src/server.py` and `drive_projection.py` both sending to 9020 |
| One layer black, others fine | Check `[clip]` errors in `Player.log` — bad path. Other layers keep working by design. |
| Nothing arrives on 9020 | Another process holds the port, or `osc_port` disagrees with `config.yaml` |
| Video stutters | Re-encode to ProRes; check the Mac isn't also rendering in Unreal |

## Extension point — mid anchors

A cross-fade is a *linear* interpolation between two looks (decision 1). If the
authored path between two states isn't linear — fluorescence blooming brighter
before it goes white, coral browning before it whitens — a dissolve will not show
it.

The fix costs one clip and one config line, no code change: render an extra loop
at the midpoint and add it as a third anchor on the intensity axis.

```
healthy @ 0.0    mid @ 0.5    fluorescent @ 1.0
```

`ChooseTargets` blends the neighbouring pair, so the compositor still only ever
handles two clips and the decode load is unchanged. The same trick works on the
bleached↔healthy axis if the heal wants an authored midpoint.

Worth deciding *before* you render: a mid anchor is another 60 s loop, and it has
to come from the same master timeline as the anchors either side of it.
