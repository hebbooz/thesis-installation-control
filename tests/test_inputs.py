"""Unit tests for the input plane (PROTOCOL.md §5, src/inputs.py).

The pygame/SDL joystick access is isolated behind :class:`inputs.Backend`, so the
whole input *policy* — button-level reporting, disabled/absent handling, cool-wins at
the boundary, and the Phase 6 fail-soft requirement (an encoder unplugged mid-run
must not crash the server) — is exercised here with a fake backend and zero hardware.
That mirrors how test_temperature and test_actuation guard their hardware seams.
"""
import logging

import pytest

from inputs import ButtonReader
from state import CoralState


# --------------------------------------------------------------- fake backend
class FakeDevice:
    """A stand-in pygame Joystick. ``removed`` makes reads raise, as SDL does when
    the physical device is yanked."""

    def __init__(self, backend: "FakeBackend") -> None:
        self._b = backend

    def get_name(self) -> str:
        return "Fake Zero-Delay Encoder"

    def get_numbuttons(self) -> int:
        return 8

    def get_button(self, index: int) -> int:
        if self._b.removed:
            raise RuntimeError("SDL_JoystickGetButton: device removed")
        return 1 if self._b.pressed.get(index) else 0


class FakeBackend:
    """Emulates the joystick seam. Flip ``present`` to unplug/replug; ``removed`` makes
    an already-open device raise mid-read (a disconnect during a poll)."""

    def __init__(self, present: bool = True) -> None:
        self.present = present
        self.removed = False
        self.pressed: dict[int, bool] = {}
        self.pumped = 0
        self.closed = False

    def pump(self) -> None:
        self.pumped += 1

    def count(self) -> int:
        return 1 if self.present else 0

    def open(self, index: int):
        if not self.present or index != 0:
            return None
        return FakeDevice(self)

    def close(self) -> None:
        self.closed = True


CFG = {"enabled": True, "device_index": 0, "warm_button": 0, "cool_button": 1}


def make_reader(backend: FakeBackend, **over) -> ButtonReader:
    cfg = dict(CFG)
    cfg.update(over)
    return ButtonReader(cfg, backend=backend)


# ------------------------------------------------------------------- disabled
def test_disabled_never_builds_a_backend_and_reports_no_press():
    sentinel = {"built": False}

    def factory():
        sentinel["built"] = True
        raise AssertionError("must not construct a backend when disabled")

    reader = ButtonReader({"enabled": False}, backend_factory=factory)
    assert reader.read() == (False, False)
    assert sentinel["built"] is False


# --------------------------------------------------------------- basic reads
def test_reads_warm_button():
    be = FakeBackend()
    be.pressed = {0: True}
    reader = make_reader(be)
    assert reader.read() == (True, False)
    assert be.pumped >= 1  # SDL state was refreshed before reading


def test_reads_cool_button():
    be = FakeBackend()
    be.pressed = {1: True}
    assert make_reader(be).read() == (False, True)


def test_no_press_reports_false():
    assert make_reader(FakeBackend()).read() == (False, False)


def test_button_indices_are_configurable():
    be = FakeBackend()
    be.pressed = {5: True}  # a unit whose warm button enumerated as index 5
    assert make_reader(be, warm_button=5, cool_button=6).read() == (True, False)


def test_out_of_range_index_is_safe():
    be = FakeBackend()  # device exposes 8 buttons
    be.pressed = {0: True}
    reader = make_reader(be, warm_button=99)  # misconfigured, beyond the device
    assert reader.read() == (False, False)  # never registers, never raises


# ------------------------------------------------------- cool-wins at the seam
def test_both_pressed_reports_both_and_cool_wins_downstream():
    """Simultaneous press reports (True, True); apply_input resolves cool-wins."""
    be = FakeBackend()
    be.pressed = {0: True, 1: True}
    warm, cool = make_reader(be).read()
    assert (warm, cool) == (True, True)

    # Feed the reader's output through the real state machine, exactly as the server
    # does, and confirm the target lands on cool — the Phase 6 acceptance criterion.
    cs = CoralState(
        {"temp_natural": 26.0, "temp_bleached": 28.0, "rise_threshold": 26.2,
         "latch_threshold": 27.8, "latch_hold_s": 10, "recovery_lag_s": 30,
         "recovery_ramp_s": 45, "idle_timeout_s": 180},
        {"warm": 28.0, "cool": 26.0},
    )
    cs.apply_input(warm, cool)
    assert cs.target == 26.0


def test_press_resets_the_idle_timer():
    """A button press must keep the installation awake (acceptance test)."""
    cs = CoralState(
        {"temp_natural": 26.0, "temp_bleached": 28.0, "rise_threshold": 26.2,
         "latch_threshold": 27.8, "latch_hold_s": 10, "recovery_lag_s": 30,
         "recovery_ramp_s": 45, "idle_timeout_s": 180},
        {"warm": 28.0, "cool": 26.0},
    )
    cs.update(26.0, dt=120.0)     # 120 s pass with no input
    assert cs._idle_timer == pytest.approx(120.0)
    be = FakeBackend(); be.pressed = {0: True}
    warm, cool = make_reader(be).read()
    cs.apply_input(warm, cool)    # a warm press
    assert cs._idle_timer == 0.0  # timer reset


# ---------------------------------------------------------------- fail-soft
def test_missing_device_reports_no_press_and_does_not_raise():
    reader = make_reader(FakeBackend(present=False))
    assert reader.read() == (False, False)


def test_backend_init_failure_degrades_not_crashes(caplog):
    def factory():
        raise RuntimeError("SDL video init failed")

    with caplog.at_level(logging.WARNING, logger="coral"):
        reader = ButtonReader(dict(CFG), backend_factory=factory)
    assert reader.read() == (False, False)
    assert any("could not initialise" in r.getMessage() for r in caplog.records)


def test_device_removed_midrun_is_soft_and_logs_once(caplog):
    be = FakeBackend()
    be.pressed = {0: True}
    reader = make_reader(be)
    assert reader.read() == (True, False)  # connected, warm down

    be.removed = True  # encoder yanked out of the USB port
    with caplog.at_level(logging.WARNING, logger="coral"):
        assert reader.read() == (False, False)  # soft-failed, not raised
        assert reader.read() == (False, False)  # still gone, but silent now
    disconnects = [r for r in caplog.records if "disconnected" in r.getMessage()]
    assert len(disconnects) == 1  # logged exactly once


def test_reacquires_after_replug():
    be = FakeBackend()
    be.pressed = {0: True}
    reader = make_reader(be)
    assert reader.read() == (True, False)

    be.removed = True
    assert reader.read() == (False, False)  # lost

    # Re-plug: device healthy again. Bypass the reacquire throttle for the test.
    be.removed = False
    reader._next_acquire = 0.0
    assert reader.read() == (True, False)  # reconnected and reading again


# ---------------------------------------------------------------- lifecycle
def test_close_releases_backend_and_is_idempotent():
    be = FakeBackend()
    reader = make_reader(be)
    reader.close()
    assert be.closed is True
    reader.close()  # second call must be a no-op, never raise
    assert reader.read() == (False, False)  # closed reader reports nothing
