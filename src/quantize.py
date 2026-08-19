"""Musical quantisation of the switching cue (ABLETON.md).

Every output that *switches* discretely — which audio bed is audible, the lamp
blackout, the projection's clip pair and latch rupture, the AR appearance — follows
one bar-quantised value instead of the raw state, so they all change on the same
musical boundary rather than scattering across whichever 200 ms broadcast happened
to carry the transition. This module holds the new value back and releases it only
on that boundary.

Two things deliberately do **not** pass through here:

* ``intensity`` and ``latch``, the continuous drivers. Quantising them would step
  a crossfade at 0.5 Hz, and ``latch`` exists precisely to *precede* the bleach.
* The heater and fan, which are gated off ``target`` (actuation.py) — they sit
  *upstream* of the water temperature, not downstream of the state, so delaying
  them could not align anything. The output-side hold below is the only place
  simultaneity is produced.

Live is the clock master. It sends MIDI clock out to a virtual (IAC) bus and the
server counts those pulses — 24 per beat — so it always knows where the next bar
line is. Nothing is ever asked of Live; the server only listens.

**This is a deliberate, contained deviation from CLAUDE.md's "outputs never talk
back" rule.** What crosses the boundary is a clock, never state: Live cannot
influence *what* the server publishes, only *when* the cue moves. And it fails
soft in the strongest sense — with no clock arriving (Live closed, transport
stopped, IAC misconfigured) this degrades to publishing immediately, which is
exactly the unquantised behaviour it replaces. The installation never depends on
Live being alive.

The published value rides the normal broadcast as ``/coral/cue``. When
quantisation is disabled or the clock is silent, ``/coral/cue`` is identical to
``/coral/state``, so subscribers map it unconditionally and never care which mode
the server is in.
"""
from __future__ import annotations

import logging

# MIDI System Real-Time / Common status bytes we care about.
CLOCK = 0xF8      # 24 per quarter note
START = 0xFA      # transport started from the top -> reset the count
CONTINUE = 0xFB   # transport resumed -> keep the count
STOP = 0xFC       # transport stopped -> keep the count, let it go stale
SPP = 0xF2        # song position pointer, in 16th notes -> reseat the count

PULSES_PER_BEAT = 24


class CueQuantizer:
    """Holds the switching cue until the next musical boundary.

    Constructed from the ``quantize`` config block. Inert (pure pass-through) when
    disabled, when no MIDI port matches, or whenever the clock has gone silent —
    so the server's call site is unconditional.
    """

    def __init__(self, cfg_quantize: dict, log: logging.Logger) -> None:
        cfg = cfg_quantize or {}
        self.log = log
        self.enabled = bool(cfg.get("enabled", False))
        self.quantize_bars = int(cfg.get("quantize_bars", 8))
        self.beats_per_bar = int(cfg.get("beats_per_bar", 4))
        self.lead_ms = float(cfg.get("lead_ms", 0.0))
        self.stale_s = float(cfg.get("stale_s", 1.0))
        self.port_name = str(cfg.get("midi_clock_port", ""))

        # Pulses between boundaries. 8 bars of 4/4 = 768.
        self.period = max(1, PULSES_PER_BEAT * self.beats_per_bar * self.quantize_bars)

        self._cue: int | None = None    # last published value; None until cold start
        self._pulses = 0                # running clock count
        self._interval = 0.0            # measured seconds per pulse (for lead_ms)
        self._last_clock_t = 0.0        # monotonic time of the last tick that saw pulses
        self._midi = None
        self._port = None

        if self.enabled:
            self._open()

    # ------------------------------------------------------------------- setup
    def _open(self) -> None:
        """Open the first MIDI input whose name contains ``port_name``.

        Any failure here is logged and leaves the quantizer in pass-through — a
        missing MIDI backend must never stop the installation from running.
        """
        try:
            import rtmidi
        except ImportError:
            self.log.warning("QUANTIZE python-rtmidi not installed — running unquantised")
            return
        try:
            midi = rtmidi.MidiIn()
            ports = midi.get_ports()
            wanted = self.port_name.lower()
            match = next((i for i, n in enumerate(ports) if wanted in n.lower()), None)
            if match is None:
                self.log.warning(
                    "QUANTIZE no MIDI input matching %r (available: %s) — running unquantised",
                    self.port_name, ports or "none",
                )
                return
            midi.open_port(match)
            # rtmidi drops clock messages by default; timing=False is what makes
            # this module work at all.
            midi.ignore_types(sysex=True, timing=False, active_sense=True)
            self._midi, self._port = midi, midi
            self.log.info(
                "QUANTIZE listening for MIDI clock on %r — %d bars (%d pulses/boundary)",
                ports[match], self.quantize_bars, self.period,
            )
        except Exception as exc:  # noqa: BLE001 — any backend failure is non-fatal
            self.log.warning("QUANTIZE could not open MIDI clock input (%s) — running unquantised", exc)

    # ------------------------------------------------------------------ runtime
    def update(self, state: int, now: float) -> tuple[int, bool]:
        """Advance the clock and return ``(cue, changed_off_schedule)``.

        ``changed_off_schedule`` is True when the cue moved on a tick that is not a
        broadcast tick, telling the server to emit immediately rather than wait up
        to 200 ms for the next scheduled one. Every switching output reads the cue
        from that one bundle, so they all turn on the same datagram.
        """
        crossed = self._drain(now)
        state = int(state)

        if self._cue is None:            # cold start: publish whatever is true now
            self._cue = state
            return state, False

        if not self._running(now):       # disabled, no port, or clock gone silent
            changed = state != self._cue
            self._cue = state
            return state, changed

        if crossed and state != self._cue:
            self._cue = state
            return state, True
        return self._cue, False

    def _running(self, now: float) -> bool:
        """True only while clock pulses are actually arriving."""
        if self._port is None or not self._last_clock_t:
            return False
        return (now - self._last_clock_t) <= self.stale_s

    def _drain(self, now: float) -> bool:
        """Consume every pending MIDI message. Returns True if a boundary passed.

        Draining from the server's own tick keeps this single-threaded: rtmidi
        buffers the ~48 pulses/second and we collect two or three each tick.
        """
        if self._port is None:
            return False
        before = self._boundary_index()
        seen = 0
        while True:
            msg = self._port.get_message()
            if msg is None:
                break
            data = msg[0]
            if not data:
                continue
            status = data[0]
            if status == CLOCK:
                self._pulses += 1
                seen += 1
            elif status == START:
                self._pulses = 0
            elif status == SPP and len(data) >= 3:
                # 14-bit value in 16th notes; 6 pulses per 16th.
                self._pulses = (((data[2] << 7) | data[1]) * 6)
            # CONTINUE and STOP deliberately leave the count alone.

        if seen:
            if self._last_clock_t:
                per = (now - self._last_clock_t) / seen
                if 0.002 < per < 0.5:    # ignore absurd gaps (first pulse, hiccups)
                    self._interval = per if not self._interval else self._interval * 0.8 + per * 0.2
            self._last_clock_t = now
        return self._boundary_index() != before

    def _boundary_index(self) -> int:
        """Which boundary window the clock currently sits in.

        ``lead_ms`` shifts the window earlier to absorb the server tick, OSC
        transit and Live's own parameter latency, so the cut lands *on* the bar
        rather than just after it.
        """
        lead = 0
        if self.lead_ms > 0 and self._interval > 0:
            lead = int(round((self.lead_ms / 1000.0) / self._interval))
        return (self._pulses + lead) // self.period

    def close(self) -> None:
        if self._midi is not None:
            try:
                self._midi.close_port()
            except Exception:  # noqa: BLE001 — shutdown must not raise
                pass
            self._midi = self._port = None
