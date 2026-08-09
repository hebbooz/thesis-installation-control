"""State machine regression tests — the permanent guard on the thermal narrative.

Drives the real CoralState + SimulatedSource through every Phase 1 acceptance
scenario, fast-forwarded (the loop is pure computation; configured seconds cost no
wall time). Runs against the committed config.example.yaml so the shipped
thresholds are what's under test, independent of a locally edited config.yaml.
"""
import pytest

from config import EXAMPLE_CONFIG, load_config
from state import CoralState
from temperature import SimulatedSource

DT = 0.05  # 20 Hz, matching the server's internal tick


@pytest.fixture
def cfg():
    return load_config(EXAMPLE_CONFIG)


def make_system(cfg, noise=0.0):
    """A fresh state machine + simulated source. Noise off by default for determinism."""
    st = CoralState(cfg["state"], cfg["targets"])
    sim = dict(cfg["temperature"]["simulated"], noise=noise)
    return st, SimulatedSource(sim, lambda: st.target)


def advance(st, src, seconds, warm=False, cool=False):
    """Run the system forward. A press (if any) is applied on the first tick only."""
    for i in range(max(1, round(seconds / DT))):
        st.apply_input(warm and i == 0, cool and i == 0)
        st.update(src.read(DT), DT)
        st.drain_events()
    return st


def bleach(st, src):
    """Warm and hold until the bleach latch fires, then stop.

    Presses warm once (the target is sticky) and updates until state 2. Returns as
    soon as it latches (~82 s) — deliberately before the 180 s idle timeout, so the
    idle reset does not interfere with tests that build on a latched state.
    """
    st.apply_input(True, False)
    for _ in range(int(160 / DT)):
        st.update(src.read(DT), DT)
        st.drain_events()
        if st.state == 2:
            return
    raise AssertionError("did not latch within 160s of sustained warming")


# --------------------------------------------------------------------- cold start
def test_cold_start_is_natural(cfg):
    st, _ = make_system(cfg)
    assert st.state == 0
    assert st.intensity == 0.0
    assert not st.bleach_latched
    assert st.target == cfg["targets"]["cool"]


# --------------------------------------------------------------- warming / state 1
def test_warm_engages_state1_and_raises_intensity(cfg):
    st, src = make_system(cfg)
    advance(st, src, warm=True, seconds=15)
    assert st.state == 1
    assert st.temp > cfg["state"]["rise_threshold"]
    assert 0.0 < st.intensity < 1.0


def test_no_chatter_across_rise_threshold(cfg):
    """Sensor noise straddling 26.2 must not toggle state 0<->1 (hysteresis)."""
    noise = cfg["temperature"]["simulated"]["noise"]
    st, src = make_system(cfg, noise=noise)
    src.heat_rate = 0.01  # dwell inside the noise band around the threshold
    st.apply_input(True, False)  # target warm
    transitions = 0
    for _ in range(int(60 / DT)):
        st.update(src.read(DT), DT)
        transitions += sum(1 for e in st.drain_events() if e.startswith("STATE"))
        if st.temp > 26.4:
            break
    assert transitions <= 2  # a clean 0->1; chatter would produce many


# ------------------------------------------------------------------ reversibility
def test_cool_before_latch_never_bleaches(cfg):
    st, src = make_system(cfg)
    seen_bleached = False
    advance(st, src, warm=True, seconds=1)
    for _ in range(4000):  # warm toward, but stop below, the latch region
        st.update(src.read(DT), DT)
        st.drain_events()
        seen_bleached |= st.state == 2
        if st.temp > 27.0:
            break
    st.apply_input(False, True)  # cool
    for _ in range(8000):
        st.update(src.read(DT), DT)
        st.drain_events()
        seen_bleached |= st.state == 2
        if st.state == 0 and st.temp <= 26.05:
            break
    assert not seen_bleached
    assert st.state == 0
    assert st.intensity < 0.05


def test_brief_spike_above_threshold_does_not_latch(cfg):
    """Crossing 27.8 but cooling before the sustained hold must not bleach."""
    st, src = make_system(cfg)
    advance(st, src, warm=True, seconds=1)
    for _ in range(5000):
        st.update(src.read(DT), DT)
        st.drain_events()
        if st.temp >= cfg["state"]["latch_threshold"] + 0.02:
            break
    st.apply_input(False, True)  # cool immediately
    advance(st, src, seconds=5)
    assert not st.bleach_latched
    assert st.state != 2


# ------------------------------------------------------------------------- latch
def test_latch_after_sustained_hold(cfg):
    st, src = make_system(cfg)
    bleach(st, src)
    assert st.bleach_latched
    assert st.state == 2
    assert st.intensity == pytest.approx(1.0)


def test_latch_holds_while_still_warm(cfg):
    st, src = make_system(cfg)
    bleach(st, src)
    advance(st, src, warm=True, seconds=10)  # further warming changes nothing
    assert st.state == 2
    assert st.intensity == pytest.approx(1.0)


def test_short_cool_does_not_recover(cfg):
    """Cooling for less than the recovery lag leaves it bleached (cool is no undo)."""
    st, src = make_system(cfg)
    bleach(st, src)
    advance(st, src, cool=True, seconds=cfg["state"]["recovery_lag_s"] - 3)
    assert st.state == 2


# ---------------------------------------------------------------------- recovery
def test_recovery_lag_then_heal_to_natural(cfg):
    st, src = make_system(cfg)
    bleach(st, src)
    assert st.state == 2
    # Bleached through the lag...
    advance(st, src, cool=True, seconds=cfg["state"]["recovery_lag_s"] - 2)
    assert st.state == 2
    # ...then the heal ramp begins...
    advance(st, src, seconds=4)
    assert st.state == 3
    # ...and it returns all the way to Natural once healed and cool.
    advance(st, src, seconds=cfg["state"]["recovery_ramp_s"] + 300)
    assert st.state == 0
    assert not st.bleach_latched
    assert st.intensity < 0.05


def test_rewarming_during_recovery_rebleaches(cfg):
    st, src = make_system(cfg)
    bleach(st, src)
    advance(st, src, cool=True, seconds=cfg["state"]["recovery_lag_s"] + 2)
    assert st.state == 3
    advance(st, src, warm=True, seconds=1)
    assert st.state == 2
    assert st.intensity == pytest.approx(1.0)


# ------------------------------------------------------------------ inputs / idle
def test_cool_wins_on_simultaneous_press(cfg):
    st, _ = make_system(cfg)
    st.apply_input(warm_pressed=True, cool_pressed=True)
    assert st.target == cfg["targets"]["cool"]


def test_any_press_resets_idle_timer(cfg):
    st, src = make_system(cfg)
    advance(st, src, seconds=cfg["state"]["idle_timeout_s"] - 5)
    st.apply_input(True, False)  # a press just before timeout
    advance(st, src, warm=True, seconds=10)
    # Still warming toward its target — the idle reset did not fire.
    assert st.target == cfg["targets"]["warm"]


def test_idle_reset_returns_to_natural(cfg):
    st, src = make_system(cfg)
    bleach(st, src)
    assert st.state == 2
    advance(st, src, seconds=cfg["state"]["idle_timeout_s"] + 1)  # walk away
    assert st.target == cfg["targets"]["cool"]
    # The latch survives: idle presses the cool button, it does not un-bleach.
    assert st.bleach_latched
    advance(st, src, seconds=300)  # let it cool
    assert st.state == 0


def test_idle_reset_heals_through_recovery(cfg):
    """A visitor who bleaches the coral and walks away still gets the full heal.

    The unattended path must be the same 2 -> 3 -> 0 a deliberate cool press
    produces; a bleached coral recovers, it is never un-bleached.
    """
    st, src = make_system(cfg)
    bleach(st, src)

    seen = []
    for _ in range(int(600 / DT)):
        st.update(src.read(DT), DT)
        st.drain_events()
        if not seen or seen[-1] != st.state:
            seen.append(st.state)
        if st.state == 0:
            break

    assert seen == [2, 3, 0], f"expected 2->3->0 unattended, got {seen}"


def test_idle_reset_does_not_refire_during_the_heal(cfg):
    """Once cooling is commanded there is nothing left to reset, so the timer must
    not keep firing through a heal that outlasts idle_timeout_s."""
    st, src = make_system(cfg)
    bleach(st, src)

    resets = 0
    for _ in range(int(600 / DT)):
        st.update(src.read(DT), DT)
        resets += sum("IDLE reset" in e for e in st.drain_events())
        if st.state == 0:
            break

    assert resets == 1, f"idle reset fired {resets} times, expected once"


def test_recovery_exit_does_not_flicker_back_to_fluorescent(cfg):
    """3 -> 0 must not bounce to 1. Exiting at rise_threshold hands over at exactly
    the temperature that re-engages state 1, and sensor noise does the rest."""
    st, src = make_system(cfg, noise=0.02)   # noise ON — this is what triggered it
    bleach(st, src)
    st.apply_input(False, True)              # deliberate cool

    seen = []
    for _ in range(int(600 / DT)):
        st.update(src.read(DT), DT)
        st.drain_events()
        if not seen or seen[-1] != st.state:
            seen.append(st.state)
        if len(seen) >= 3 and seen[-1] == 0:
            break

    assert seen == [2, 3, 0], f"flickered on the way out: {seen}"


# ------------------------------------------------------------------ latch anticipation
def test_latch_progress_is_zero_when_nothing_is_pending(cfg):
    st, src = make_system(cfg)
    assert st.latch_progress == 0.0
    advance(st, src, seconds=10)          # idle and cool
    assert st.latch_progress == 0.0


def test_latch_progress_ramps_through_the_hold_and_pins_at_one(cfg):
    """The 10 s sustained hold is the window in which the outcome is already
    decided but not yet spent — it is what an anticipatory riser rides."""
    st, src = make_system(cfg)
    st.apply_input(True, False)

    seen_partial = False
    for _ in range(int(160 / DT)):
        st.update(src.read(DT), DT)
        st.drain_events()
        assert 0.0 <= st.latch_progress <= 1.0
        if st.state == 1 and 0.05 < st.latch_progress < 0.95:
            seen_partial = True
        if st.state == 2:
            break

    assert seen_partial, "no ramp observed before the latch — nothing to anticipate with"
    assert st.latch_progress == 1.0       # holds at full once latched


def test_latch_progress_retreats_when_the_hold_breaks(cfg):
    """Cooling before the latch has to walk the anticipation back, or the sound
    would promise a bleach the state machine then declines to deliver."""
    st, src = make_system(cfg)
    st.apply_input(True, False)
    for _ in range(int(160 / DT)):
        st.update(src.read(DT), DT)
        st.drain_events()
        if st.latch_progress > 0.4:
            break
    assert not st.bleach_latched, "needed a partial hold, not a completed one"

    advance(st, src, seconds=5, cool=True)
    assert st.latch_progress == 0.0
    assert st.state != 2
