/// <summary>
/// extOSC listener for the orchestration server's broadcast (PROTOCOL.md §1).
///
/// A passive subscriber in the strictest sense: it holds nothing but the last
/// values received, never re-derives state from temperature, and never talks
/// back. The projection player is a static subscriber declared in the server's
/// config, so unlike the AR clients it sends no /client/hello.
///
/// Startup-order independence (CLAUDE.md §3): boots to state 0 / intensity 0 and
/// converges on the first broadcast, so it may be launched before, during or
/// after the server with no operator action.
/// </summary>
using System;
using extOSC;
using UnityEngine;

namespace Coral
{
    public class CoralOscListener : MonoBehaviour
    {
        /// <summary>Immediate, unquantised phase. Truth — use it to *know*.</summary>
        public int State { get; private set; }

        /// <summary>
        /// The phase to *switch* on: State held back to a musical bar line when the
        /// server has Live's clock (PROTOCOL.md §1). Every output that changes
        /// discretely reads this, so the clip swap lands on the same downbeat as the
        /// audio bed, the lamp blackout and the AR appearance.
        ///
        /// Falls back to State until a /coral/cue actually arrives, so a server that
        /// predates the address degrades to immediate switching rather than freezing
        /// the projection at state 0.
        /// </summary>
        public int Cue => _cueReceived ? _cue : State;

        public float Intensity { get; private set; }
        public float Temp { get; private set; }

        int _cue;
        bool _cueReceived;

        /// <summary>True once any broadcast has arrived. Used only for logging.</summary>
        public bool EverReceived { get; private set; }

        /// <summary>Seconds since the last broadcast; the 5 Hz stream is the heartbeat.</summary>
        public float SecondsSinceMessage => EverReceived ? Time.time - _lastRx : float.PositiveInfinity;

        OSCReceiver _receiver;
        float _lastRx;

        public void Begin(int port)
        {
            try
            {
                _receiver = gameObject.AddComponent<OSCReceiver>();
                _receiver.LocalPort = port;
                _receiver.Bind("/coral/state", OnState);
                _receiver.Bind("/coral/cue", OnCue);
                _receiver.Bind("/coral/intensity", OnIntensity);
                _receiver.Bind("/coral/temp", OnTemp);
                _receiver.Connect();
                Debug.Log($"[osc] listening on UDP {port}");
            }
            catch (Exception e)
            {
                // A busy port must not take the projection down — it will simply
                // hold state 0 (a healthy reef), which is the correct failure mode.
                Debug.LogError($"[osc] could not bind UDP {port}: {e.Message}");
            }
        }

        void OnState(OSCMessage m)
        {
            if (!TryReadFloat(m, out float v)) return;
            State = Mathf.Clamp(Mathf.RoundToInt(v), 0, 3);
            Mark();
        }

        void OnCue(OSCMessage m)
        {
            if (!TryReadFloat(m, out float v)) return;
            _cue = Mathf.Clamp(Mathf.RoundToInt(v), 0, 3);
            _cueReceived = true;
            Mark();
        }

        void OnIntensity(OSCMessage m)
        {
            if (!TryReadFloat(m, out float v)) return;
            Intensity = Mathf.Clamp01(v);
            Mark();
        }

        void OnTemp(OSCMessage m)
        {
            if (TryReadFloat(m, out float v)) { Temp = v; Mark(); }
        }

        void Mark()
        {
            _lastRx = Time.time;
            EverReceived = true;
        }

        /// <summary>
        /// Read the first argument as a float, accepting int/float/double. The
        /// server sends int32 for state and float32 for the rest, but being
        /// permissive here means a type change upstream degrades to a no-op
        /// rather than a silent freeze.
        /// </summary>
        static bool TryReadFloat(OSCMessage m, out float value)
        {
            value = 0f;
            if (m?.Values == null || m.Values.Count == 0) return false;
            var v = m.Values[0];
            switch (v.Type)
            {
                case OSCValueType.Float:  value = v.FloatValue; return true;
                case OSCValueType.Int:    value = v.IntValue; return true;
                case OSCValueType.Double: value = (float)v.DoubleValue; return true;
                default: return false;
            }
        }
    }
}
