"""State derivation — the thermal narrative, and the only place it lives.

Given the current water temperature and the target set by the buttons, this
module derives the two published quantities:

    intensity  float 0.0-1.0   continuous severity (the primary driver)
    state      int   0-3        discrete phase (Natural/Fluorescent/Bleached/Recovery)

plus the bleach latch and recovery lag/ramp that make bleaching *irreversible in
the moment* — the thematic core of the piece (CLAUDE.md, ARCHITECTURE.md §8).

This is the single source of truth. Subscribers never re-derive any of it from
temperature; they only render what is broadcast. Nothing here knows or cares
whether the temperature came from the simulation or a real sensor — that
distinction lives below the ingestion seam in temperature.py.
"""
from __future__ import annotations

STATE_NAMES = {0: "Natural", 1: "Fluorescent", 2: "Bleached", 3: "Recovery"}


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


class CoralState:
    """Holds all mutable state and derives (state, intensity) each tick.

    The server calls :meth:`apply_input` with the buttons pressed this tick, then
    :meth:`update` with the latest temperature and the tick's ``dt``. Meaningful
    transitions are recorded in :attr:`events` for the server to log; drain them
    with :meth:`drain_events`.
    """

    def __init__(self, cfg_state: dict, cfg_targets: dict) -> None:
        s = cfg_state
        self.temp_natural = float(s["temp_natural"])      # intensity 0.0 anchor
        self.temp_bleached = float(s["temp_bleached"])    # intensity 1.0 anchor
        self.rise_threshold = float(s["rise_threshold"])  # above -> state 1
        self.latch_threshold = float(s["latch_threshold"])
        self.latch_hold_s = float(s["latch_hold_s"])
        self.recovery_lag_s = float(s["recovery_lag_s"])
        self.recovery_ramp_s = float(s["recovery_ramp_s"])
        self.idle_timeout_s = float(s["idle_timeout_s"])
        self.warm_target = float(cfg_targets["warm"])
        self.cool_target = float(cfg_targets["cool"])

        # Cold start: Natural, latch clear, commanding the cool (natural) target
        # so a freshly-started system sits at rest until a visitor warms it.
        self.target = self.cool_target
        self.temp = self.temp_natural
        self.state = 0
        self.intensity = 0.0
        self.bleach_latched = False

        self._recovering = False   # True once the recovery lag has elapsed (state 3)
        self._latch_timer = 0.0    # time sustained hot while warming (-> latch)
        self._lag_timer = 0.0      # time cooling since latch (-> recovery begins)
        self._ramp_timer = 0.0     # time healing in state 3 (-> Natural)
        self._idle_timer = 0.0     # time since last button press (-> idle reset)

        self.events: list[str] = []

    # ------------------------------------------------------------------ helpers
    @property
    def state_name(self) -> str:
        return STATE_NAMES[self.state]

    @property
    def cooling(self) -> bool:
        """True when the commanded target is the cool one.

        Latching only happens while *warming*: you bleach the coral by driving it
        hot and holding, not by letting it drift. This also stops the latch from
        re-arming during a cool-down after a reset.
        """
        return self.target <= self.cool_target

    def _set_state(self, new_state: int, note: str = "") -> None:
        if new_state != self.state:
            msg = f"STATE {self.state}->{new_state} ({STATE_NAMES[new_state]})"
            if note:
                msg += f" — {note}"
            self.events.append(msg)
            self.state = new_state

    # ------------------------------------------------------------------- inputs
    def apply_input(self, warm_pressed: bool, cool_pressed: bool) -> None:
        """Apply this tick's button presses. Idempotent; **cool wins** on conflict."""
        if not (warm_pressed or cool_pressed):
            return
        self._idle_timer = 0.0  # any press keeps the installation "awake"
        new_target = self.cool_target if cool_pressed else self.warm_target
        if new_target != self.target:
            which = "cool" if cool_pressed else "warm"
            self.events.append(f"INPUT {which} -> target {new_target:.1f}")
        self.target = new_target

    # -------------------------------------------------------------- derivation
    def update(self, temperature: float, dt: float) -> None:
        """Advance timers and derive (state, intensity) from the new temperature."""
        self.temp = temperature
        self._idle_timer += dt

        # Idle reset: nobody has touched it for idle_timeout_s. Release any latch
        # and command cooling so the piece returns itself to Natural for the next
        # visitor. Derivation below then cools smoothly (no forced teleport — real
        # water can't be teleported, and neither should the simulation).
        if self._idle_timer >= self.idle_timeout_s:
            if self.bleach_latched or self.target != self.cool_target:
                self._idle_reset()
            self._idle_timer = 0.0

        if not self.bleach_latched:
            self._derive_reversible(temperature, dt)
        elif not self._recovering:
            self._derive_bleached(dt)
        else:
            self._derive_recovery(temperature, dt)

    def _derive_reversible(self, temperature: float, dt: float) -> None:
        """States 0/1: continuous and fully reversible until the latch fires."""
        # Accumulate the sustained-hot timer only while warming; any dip below the
        # threshold (or a switch to cooling) resets it, so momentary spikes and
        # cool-downs never latch.
        if temperature >= self.latch_threshold and not self.cooling:
            self._latch_timer += dt
            if self._latch_timer >= self.latch_hold_s:
                self.bleach_latched = True
                self._latch_timer = 0.0
                self.intensity = 1.0
                self._set_state(2, "bleach latched (irreversible)")
                return
        else:
            self._latch_timer = 0.0

        self.intensity = _clamp(
            (temperature - self.temp_natural) / (self.temp_bleached - self.temp_natural),
            0.0, 1.0,
        )
        # Hysteresis across the 0/1 boundary: engage Fluorescent above rise_threshold,
        # but only fall back to Natural once the water is fully cool (temp_natural).
        # The gap between those two config anchors is wider than the sensor noise, so
        # a reading jittering around the threshold can't chatter the state 0<->1.
        if temperature > self.rise_threshold:
            self._set_state(1)
        elif temperature <= self.temp_natural or self.state != 1:
            self._set_state(0)
        # else: in the (temp_natural, rise_threshold] band while already Fluorescent
        # — hold state 1.

    def _derive_bleached(self, dt: float) -> None:
        """State 2: latched and fully bleached. Waiting out the recovery lag.

        Temperature no longer affects anything here — the coral stays visibly
        bleached. Cooling for recovery_lag_s continuously begins recovery; warming
        again resets the lag (the cool button is a commitment, not an undo).
        """
        self.intensity = 1.0
        self._set_state(2)
        if self.cooling:
            self._lag_timer += dt
            if self._lag_timer >= self.recovery_lag_s:
                self._recovering = True
                self._ramp_timer = 0.0
                self._set_state(3, "recovery lag elapsed, healing begins")
        else:
            self._lag_timer = 0.0

    def _derive_recovery(self, temperature: float, dt: float) -> None:
        """State 3: the scripted heal, intensity ramping 1.0 -> 0.0.

        Re-warming cancels recovery and re-bleaches (back to state 2). Otherwise
        intensity heals over recovery_ramp_s; the latch only clears once healed
        *and* the water has actually returned below the rise threshold, so the
        coral never flickers back to Fluorescent on the way out.
        """
        if not self.cooling:
            self._recovering = False
            self._ramp_timer = 0.0
            self._lag_timer = 0.0
            self.intensity = 1.0
            self._set_state(2, "re-warmed, recovery cancelled")
            return

        self._ramp_timer += dt
        progress = _clamp(self._ramp_timer / self.recovery_ramp_s, 0.0, 1.0)
        self.intensity = 1.0 - progress
        self._set_state(3)
        if progress >= 1.0 and temperature <= self.rise_threshold:
            self._clear_latch()
            self.intensity = 0.0
            self._set_state(0, "recovered")

    # ------------------------------------------------------------------- resets
    def _clear_latch(self) -> None:
        self.bleach_latched = False
        self._recovering = False
        self._latch_timer = 0.0
        self._lag_timer = 0.0
        self._ramp_timer = 0.0

    def _idle_reset(self) -> None:
        was_latched = self.bleach_latched
        self._clear_latch()
        self.target = self.cool_target
        note = "bleach released" if was_latched else "returning to natural"
        self.events.append(
            f"IDLE reset after {self.idle_timeout_s:.0f}s idle — "
            f"target {self.cool_target:.1f} ({note})"
        )

    # -------------------------------------------------------------------- events
    def drain_events(self) -> list[str]:
        """Return and clear the accumulated transition log lines."""
        ev = self.events
        self.events = []
        return ev
