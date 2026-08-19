/// <summary>
/// The projection player — renders the broadcast state to the spanned 2560x800
/// projector canvas (ARCHITECTURE.md §6.5).
///
/// THREE LOOPS AND ONE RUPTURE
/// ---------------------------
/// There are no directional transition clips. The CUE selects a PAIR of looping
/// clips and intensity sets the weight within that pair:
///
///     cue 0,1   healthy <-> fluorescent    w_fluorescent = intensity
///     cue 2,3   healthy <-> bleached       w_bleached    = intensity
///
/// The cue, not the state: it is the same phase held back to a musical bar line
/// (PROTOCOL.md §1), so this swap lands on the same downbeat as the audio bed and
/// the lamp blackout rather than scattering across whichever broadcast carried the
/// transition. Intensity stays immediate, which is why only one moment visibly
/// waits — see BAR-QUANTISED SWITCHING below.
///
/// States 2 and 3 collapse into one rule because the server pins intensity to 1.0
/// while bleached and ramps it 1.0 -> 0.0 across recovery_ramp_s. So the heal is
/// driven entirely by the broadcast: retime it in config.yaml and the projection
/// follows, with nothing to re-render. A cancelled recovery is just the weight
/// travelling back toward bleached.
///
/// The single exception is the bleach latch. Intensity is already ~0.9 when it
/// fires, so there is no intensity movement to carry it — the cue change has to.
/// A short one-shot plays once on 1->2.
///
///     crossfade_s  <  latch clip  <  recovery_lag_s
///        1.5 s     <     20 s     <       30 s
///
/// Those two config values are coupled with only 10 s of slack. Lowering
/// recovery_lag_s below ~25 s would cut the bleach off part-way, snapping to fully
/// white before healing — the exact failure the rest of this design removes. The
/// clip is now defended against that directly in OnCueChanged rather than resting
/// on the config staying in range, because quantisation can compress the gap on
/// its own (see below).
///
/// BAR-QUANTISED SWITCHING
/// -----------------------
/// Because the pair is chosen by cue but the weight is driven by intensity, most
/// of the arc is unaffected by quantisation: 0->1 and 3->0 are continuous blends
/// that look identical whether the cue is held or not. THE ONLY MOMENT THAT WAITS
/// IS THE BLEACH. During that wait intensity is pinned at 1.0, so the coral holds
/// at full fluorescence — steady, not drifting — until the rupture lands on the bar.
///
/// The one hazard is at the other end of state 2. Quantising moves both of its
/// edges onto the bar grid, and the grid can compress the cue's view of a 30 s
/// state below the 20 s clip: at quantize_bars 8 (16 s at 120 BPM) a latch firing
/// in the last ~2 s before a bar line puts cue 1->2 and cue 2->3 exactly one 16 s
/// window apart. OnCueChanged therefore refuses to abandon the one-shot mid-play.
///
/// THE FORWARD-ONLY INVARIANT
/// --------------------------
/// Every clip carries caustics, and caustics running backwards read instantly as
/// an error. So:
///
///     Playheads only ever advance. A clip is seeked only while its weight is 0.
///
/// Intensity therefore drives OPACITY, never a playhead. Cooling off before the
/// latch lowers a blend weight while both videos keep running forward. The
/// one-shot rewinds only while faded out, enforced by TryFirePending.
///
/// Layers are blended in LINEAR light (see CoralBlend.shader + Project Settings
/// > Color Space: Linear). A gamma-space dissolve between the bright healthy look
/// and the deliberately stopped-down bleached look sags visibly through the
/// midtones; a linear one does not.
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Video;

namespace Coral
{
    [RequireComponent(typeof(Camera))]
    public class ProjectionPlayer : MonoBehaviour
    {
        enum Layer { Healthy = 0, Fluorescent = 1, Bleached = 2, Latch = 3 }
        const int LayerCount = 4;

        /// <summary>What the player is showing, independent of the raw state int.</summary>
        enum Mode
        {
            Continuous,     // states 0/1 — healthy <-> fluorescent by intensity
            LatchOneShot,   // the bleach rupture, played once
            Latched         // states 2/3 — healthy <-> bleached by intensity
        }

        [Tooltip("Assign CoralBlend.shader here so it is guaranteed to ship in the build.")]
        public Shader blendShader;

        ProjectionConfig _cfg;
        CoralOscListener _osc;
        Material _blend;

        /// <summary>
        /// The whole spanned image, composited once per frame. Each CoralOutput blits
        /// its own slice of this to its own projector, so the halves cannot drift.
        /// </summary>
        public RenderTexture Canvas => _canvas;
        RenderTexture _canvas;

        readonly VideoPlayer[] _players = new VideoPlayer[LayerCount];
        readonly RenderTexture[] _targets = new RenderTexture[LayerCount];
        readonly float[] _weight = new float[LayerCount];
        readonly float[] _target = new float[LayerCount];
        readonly bool[] _dead = new bool[LayerCount];      // clip missing or failed to open
        readonly bool[] _started = new bool[LayerCount];

        bool _latchPending;      // one-shot requested, waiting for a safe seek
        float _latchFiredAt;

        /// <summary>
        /// How long after firing the one-shot before its reported length/time can
        /// be trusted. Covers Unity's decoder settling after the rewind; well under
        /// the clip's own length, so a genuinely dead clip still fails soft.
        /// </summary>
        const float LatchSettleS = 0.5f;

        Mode _mode = Mode.Continuous;
        int _lastCue = 0;

        void Awake()
        {
            _cfg = ProjectionConfig.Load();

            // Exhibition hygiene: the window will lose focus (server terminal,
            // Ableton) and must keep rendering; the machine must never sleep.
            Application.runInBackground = true;
            Screen.sleepTimeout = SleepTimeout.NeverSleep;

            // This camera hosts the player; it no longer draws. The per-projector
            // CoralOutput cameras do, each onto its own display.
            var cam = GetComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = Color.black;
            cam.enabled = false;

            // sRGB to match the layer targets, so the slice blit stays in the same
            // colour space the shader wrote in.
            _canvas = new RenderTexture(_cfg.display_width, _cfg.display_height, 0,
                                        RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
            _canvas.Create();
            ClearCanvas();

            var shader = blendShader != null ? blendShader : Shader.Find("Coral/Blend2");
            if (shader == null) Debug.LogError("[blend] CoralBlend.shader not found — assign it on the ProjectionPlayer component");
            else
            {
                _blend = new Material(shader);
                _blend.SetVector("_UvRect", ComputeUvRect());
            }

            _osc = gameObject.AddComponent<CoralOscListener>();
            _osc.Begin(_cfg.osc_port);

            CreateOutputs();

            CreateLayer(Layer.Healthy, _cfg.clips.healthy, loop: true);
            CreateLayer(Layer.Fluorescent, _cfg.clips.fluorescent, loop: true);
            CreateLayer(Layer.Bleached, _cfg.clips.bleached, loop: true);
            CreateLayer(Layer.Latch, _cfg.clips.latch, loop: false);
        }

        /// <summary>
        /// One output camera per projector, left to right. macOS cannot present one
        /// window across two displays, so the canvas is sliced here instead of being
        /// stretched over a desktop that does not exist.
        ///
        /// Fail-soft, because an exhibition machine must always come up: an index
        /// that is not attached is dropped, and if that leaves nothing the player
        /// falls back to a single output showing the whole canvas — which is exactly
        /// the windowed bench setup drive_projection.py is used against.
        /// </summary>
        void CreateOutputs()
        {
            Log($"{Display.displays.Length} display(s) attached:");
            for (int i = 0; i < Display.displays.Length; i++)
                Log($"  display {i}: {Display.displays[i].systemWidth}x{Display.displays[i].systemHeight}" +
                    (i == 0 ? "  (macOS Main Display)" : ""));

            var wanted = _cfg.displays != null && _cfg.displays.Length > 0 ? _cfg.displays : new[] { 0 };
            var use = new List<int>();
            foreach (int idx in wanted)
            {
                if (idx < 0 || idx >= Display.displays.Length)
                {
                    Debug.LogWarning($"[display] index {idx} is not attached — skipping it. " +
                                     "Check the display list above against \"displays\" in coral-projection.json.");
                    continue;
                }
                if (!use.Contains(idx)) use.Add(idx);
            }
            if (use.Count == 0) use.Add(0);

            int n = use.Count;
            int sliceW = Mathf.Max(_cfg.display_width / n, 1);

            // Sizes the MAIN window only (display 0). Activate() gives every other
            // display its own native fullscreen surface.
            Screen.SetResolution(sliceW, _cfg.display_height, _cfg.fullscreen);

            for (int i = 0; i < n; i++)
            {
                int idx = use[i];
                if (idx != 0) Display.displays[idx].Activate();

                var host = new GameObject($"Output_{idx}");
                host.transform.SetParent(transform, false);

                var c = host.AddComponent<Camera>();
                c.clearFlags = CameraClearFlags.SolidColor;
                c.backgroundColor = Color.black;
                c.cullingMask = 0;            // draws nothing; it only carries the blit
                c.targetDisplay = idx;
                c.depth = i;

                host.AddComponent<CoralOutput>().Bind(this, i, n);
            }

            Log($"outputs: {n} x {sliceW}x{_cfg.display_height} on display(s) [{string.Join(", ", use)}]" +
                (n == 1 ? " — single output, showing the whole canvas" : ""));
        }

        void CreateLayer(Layer layer, string clip, bool loop)
        {
            int i = (int)layer;
            string url = _cfg.ResolveClip(clip);
            if (url == null)
            {
                _dead[i] = true;
                Debug.LogError($"[clip] {layer}: no path configured");
                return;
            }

            // Sized to the SOURCE, not the canvas: Unity scales each decoded frame
            // into its target texture, so a canvas-shaped RenderTexture would
            // squash a differently-shaped clip before the compositor could fit it.
            // sRGB read/write so the shader's lerp happens on linearised samples.
            _targets[i] = new RenderTexture(_cfg.video_width, _cfg.video_height, 0,
                                            RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
            _targets[i].Create();

            // One child object per layer. Unity is unreliable about several
            // VideoPlayer components sharing a GameObject; a child each is free.
            var host = new GameObject($"Layer_{layer}");
            host.transform.SetParent(transform, false);

            var vp = host.AddComponent<VideoPlayer>();
            vp.playOnAwake = false;
            vp.source = VideoSource.Url;
            vp.url = url;
            vp.isLooping = loop;
            vp.renderMode = VideoRenderMode.RenderTexture;
            vp.targetTexture = _targets[i];
            vp.audioOutputMode = VideoAudioOutputMode.None;   // audio belongs to Ableton
            vp.skipOnDrop = true;
            vp.waitForFirstFrame = true;
            vp.errorReceived += (_, msg) =>
            {
                // Fail soft: one bad clip must not take the projection down.
                _dead[i] = true;
                _target[i] = _weight[i] = 0f;
                Debug.LogError($"[clip] {layer} ({url}): {msg}");
            };
            vp.Prepare();
            _players[i] = vp;
        }

        void Update()
        {
            StartPreparedLoops();
            TryFirePending();

            // Cue, not State: the bar-quantised twin, so this swap lands on the same
            // downbeat as the audio bed and the lamp. Intensity below stays immediate.
            int cue = _osc.Cue;
            if (cue != _lastCue)
            {
                OnCueChanged(_lastCue, cue);
                _lastCue = cue;
            }

            ChooseTargets(_osc.Intensity);
            EaseWeights(Time.deltaTime);
        }

        /// <summary>Loops begin the instant they are ready and then run untouched forever.</summary>
        void StartPreparedLoops()
        {
            for (int i = 0; i < LayerCount; i++)
            {
                var vp = _players[i];
                if (_dead[i] || vp == null || _started[i] || !vp.isPrepared) continue;
                _started[i] = true;

                // The RenderTexture was sized from config before this clip existed.
                // If they disagree the frame is being rescaled into the wrong shape,
                // which is invisible in code and very visible on the wall.
                if (vp.width != _cfg.video_width || vp.height != _cfg.video_height)
                    Debug.LogWarning($"[clip] {(Layer)i} is {vp.width}x{vp.height} but config says " +
                                     $"{_cfg.video_width}x{_cfg.video_height} — fix video_width/video_height");

                if (vp.isLooping) vp.Play();   // the one-shot stays parked at frame 0 until fired
            }
        }

        void OnCueChanged(int from, int to)
        {
            Log($"cue {from} -> {to}");

            // Never abandon the rupture part-way. Quantisation moves both ends of
            // state 2 onto the bar grid, and the grid can compress the cue's view of
            // it below the clip's own length: at quantize_bars 8 (16 s at 120 BPM) a
            // latch firing in the last ~2 s before a bar line puts cue 1->2 and
            // cue 2->3 exactly one window — 16 s — apart, against a 20 s clip. The
            // state itself still holds 2 for the full recovery_lag_s; only the
            // quantised view of it is short.
            //
            // Letting it finish costs nothing: state 3 and state 2 share Mode.Latched,
            // so the only difference is that the heal picks up a few seconds into its
            // ramp. Cutting to a hard white snap is the failure this whole design
            // exists to remove, so the clip wins and ChooseTargets retires it via
            // LatchNearEnd as usual.
            if (_mode == Mode.LatchOneShot && to >= 2)
            {
                Log("cue moved during the rupture — letting the one-shot finish");
                return;
            }

            switch (to)
            {
                case 0:
                case 1:
                    // Also covers a 2 -> 1 arrival. The server no longer emits one
                    // (its idle reset presses cool and leaves the latch, so an
                    // unattended bleach heals 2 -> 3 -> 0), but handling it costs
                    // nothing: the weight crossfades bleached -> fluorescent and
                    // nothing rewinds. Total over the state space, not over the
                    // paths the server happens to emit today.
                    _mode = Mode.Continuous;
                    break;

                case 2:
                    if (from == 1)
                    {
                        // The rupture. Fired only on a genuine 1->2 latch; a cold
                        // start that lands directly in state 2 skips it, because the
                        // latch already happened before this player existed.
                        RequestLatchClip();
                        _mode = Mode.LatchOneShot;
                    }
                    else
                    {
                        // from 3: recovery cancelled by re-warming. Intensity snaps
                        // back to 1.0, so the weight simply travels back to bleached.
                        _mode = Mode.Latched;
                    }
                    break;

                case 3:
                    // Nothing to trigger — the heal is entirely the intensity ramp.
                    _mode = Mode.Latched;
                    break;
            }
        }

        void ChooseTargets(float intensity)
        {
            switch (_mode)
            {
                case Mode.Continuous:
                    // Opacity, not time. Reversible for free.
                    SetTargets(healthy: 1f - intensity, fluorescent: intensity);
                    break;

                case Mode.LatchOneShot:
                    if (_latchPending) break;   // hold the previous look until the seek is safe
                    SetTargets(latch: 1f);
                    if (LatchNearEnd()) { _mode = Mode.Latched; Log("latch clip -> bleached"); }
                    break;

                case Mode.Latched:
                    // States 2 and 3 are one rule: the server pins intensity to 1.0
                    // while bleached, then ramps it to 0 across recovery_ramp_s. The
                    // heal retimes itself from config with nothing to re-render.
                    SetTargets(healthy: 1f - intensity, bleached: intensity);
                    break;
            }
        }

        void SetTargets(float healthy = 0f, float fluorescent = 0f, float bleached = 0f, float latch = 0f)
        {
            _target[(int)Layer.Healthy] = healthy;
            _target[(int)Layer.Fluorescent] = fluorescent;
            _target[(int)Layer.Bleached] = bleached;
            _target[(int)Layer.Latch] = latch;
            for (int i = 0; i < LayerCount; i++) if (_dead[i]) _target[i] = 0f;
        }

        void EaseWeights(float dt)
        {
            // One rate for everything. On the intensity-driven blends this doubles
            // as a slew limit, so a dropped or late broadcast can never step the
            // image, and the latch's 0.9 -> 1.0 snap arrives as a fade.
            float step = dt / Mathf.Max(_cfg.crossfade_s, 0.001f);
            for (int i = 0; i < LayerCount; i++)
                _weight[i] = Mathf.MoveTowards(_weight[i], _target[i], step);
        }

        // ------------------------------------------------------------- one-shot

        /// <summary>
        /// Ask for the latch clip. It does NOT rewind here — it rewinds in
        /// <see cref="TryFirePending"/> once its weight has reached zero, so a
        /// rewind is never visible.
        /// </summary>
        void RequestLatchClip()
        {
            if (!_dead[(int)Layer.Latch]) _latchPending = true;
        }

        void TryFirePending()
        {
            int i = (int)Layer.Latch;
            if (!_latchPending || _dead[i]) return;
            if (_weight[i] > 0.001f) return;            // still visible — do not seek
            var vp = _players[i];
            if (vp == null || !vp.isPrepared) return;   // not ready — try again next frame

            vp.frame = 0;                               // safe: weight is 0, nobody sees it
            vp.Play();
            _latchFiredAt = Time.time;
            _latchPending = false;
            Log("latch clip fired");
        }

        /// <summary>
        /// True once the clip is within one crossfade of its end, so the handover
        /// completes as the last frame plays rather than freezing on it (a frozen
        /// frame stops the caustics, which is exactly as conspicuous as reversing
        /// them). Falls through to true on any failure so nothing can get stuck.
        /// </summary>
        bool LatchNearEnd()
        {
            int i = (int)Layer.Latch;
            var vp = _players[i];
            if (_dead[i] || vp == null) return true;

            // TryFirePending calls Play() earlier in this same Update, and Unity
            // does not report a usable clip state that fast: length still reads 0
            // and isPrepared can drop while the decoder re-buffers after the
            // rewind. The fall-through-to-true below would then hand over on the
            // very first frame, so the rupture never appears on screen -- observed
            // as "latch clip fired" and "latch clip -> bleached" logged in the same
            // millisecond. Give the player a moment before trusting what it says.
            float since = Time.time - _latchFiredAt;
            if (since < LatchSettleS) return false;

            if (!vp.isPrepared) return true;
            double len = vp.length;
            if (len <= 0d) return true;
            if (vp.time >= len - _cfg.crossfade_s) return true;

            // Backstop for a stalled decode: never hold a still frame forever.
            return since > len * 2d + 5d;
        }

        // ----------------------------------------------------------- compositing

        /// <summary>
        /// Composite the two heaviest layers, normalised so their contributions sum
        /// to 1 even mid-ease. At most two are ever meaningfully non-zero, so this
        /// is the "dual VideoPlayer cross-fade" of §6.5 — the others keep decoding
        /// but contribute nothing.
        ///
        /// Runs ONCE per frame into the shared canvas, in LateUpdate rather than in a
        /// camera callback: every projector then blits a slice of the same finished
        /// image, with no dependence on which output camera happens to render first.
        /// </summary>
        void LateUpdate()
        {
            if (_canvas == null || _blend == null) return;

            int a = -1, b = -1;
            for (int i = 0; i < LayerCount; i++)
            {
                if (_weight[i] <= 0.0001f || _targets[i] == null) continue;
                if (a < 0 || _weight[i] > _weight[a]) { b = a; a = i; }
                else if (b < 0 || _weight[i] > _weight[b]) { b = i; }
            }

            if (a < 0)
            {
                // Nothing up yet: the first crossfade_s from cold (which reads as a
                // house-lights fade-in), or every clip dead. Black is the whole job.
                ClearCanvas();
                return;
            }

            float wa = _weight[a];
            float wb = b >= 0 ? _weight[b] : 0f;
            float sum = wa + wb;

            _blend.SetTexture("_TexA", _targets[a]);
            _blend.SetTexture("_TexB", b >= 0 ? _targets[b] : _targets[a]);
            _blend.SetFloat("_Blend", sum > 0f ? wb / sum : 0f);

            // The shader ignores _MainTex; the source is passed only because Blit
            // requires one.
            Graphics.Blit(_targets[a], _canvas, _blend);
        }

        void ClearCanvas()
        {
            if (_canvas == null) return;
            var prev = RenderTexture.active;
            RenderTexture.active = _canvas;
            GL.Clear(true, true, Color.black);
            RenderTexture.active = prev;
        }

        /// <summary>
        /// Map canvas UV onto source UV so a clip whose aspect differs from the
        /// spanned canvas is cropped or letterboxed rather than distorted. A no-op
        /// when the exports match the canvas, which they should; it exists so a
        /// mis-sized clip degrades visibly-but-correctly instead of squashing.
        /// </summary>
        Vector4 ComputeUvRect()
        {
            float srcW = _cfg.video_width, srcH = _cfg.video_height;
            float dstW = _cfg.display_width, dstH = _cfg.display_height;
            if (srcW <= 0f || srcH <= 0f || dstW <= 0f || dstH <= 0f) return new Vector4(1f, 1f, 0f, 0f);

            float src = srcW / srcH, dst = dstW / dstH;
            float sx = 1f, sy = 1f;

            switch ((_cfg.fit ?? "cover").ToLowerInvariant())
            {
                case "stretch":
                    break;                                        // force it; distorts
                case "contain":
                    if (src > dst) sy = src / dst; else sx = dst / src;   // scale > 1 -> bars
                    break;
                default:                                          // "cover"
                    if (src > dst) sx = dst / src; else sy = src / dst;   // scale < 1 -> crop
                    break;
            }

            // Centre the window, then slide it within whatever margin the fit left.
            float ox = (1f - sx) * 0.5f * (1f + Mathf.Clamp(_cfg.pan_x, -1f, 1f));
            float oy = (1f - sy) * 0.5f * (1f + Mathf.Clamp(_cfg.pan_y, -1f, 1f));

            Log($"fit '{_cfg.fit}': source {srcW:0}x{srcH:0} ({src:0.00}:1) -> canvas " +
                $"{dstW:0}x{dstH:0} ({dst:0.00}:1), uv scale ({sx:0.000}, {sy:0.000})");
            return new Vector4(sx, sy, ox, oy);
        }

        void Log(string msg)
        {
            if (_cfg.log_transitions) Debug.Log($"[{DateTime.Now:HH:mm:ss.fff}] [projection] {msg}");
        }

        void OnDestroy()
        {
            for (int i = 0; i < LayerCount; i++)
                if (_targets[i] != null) _targets[i].Release();
            if (_canvas != null) _canvas.Release();
        }
    }
}
