"""Cue quantiser tests — the musical boundary, and every path that must fail soft.

No MIDI hardware: a fake port hands the quantiser exactly the clock bytes Live
would send, so the bar grid under test is the real one (24 pulses per beat).
"""
import logging

import pytest

from quantize import CLOCK, SPP, START, CueQuantizer

LOG = logging.getLogger("test")


class FakePort:
    """Stands in for an rtmidi input. ``feed`` queues messages for the next drain."""

    def __init__(self):
        self.queue = []

    def feed(self, *messages):
        self.queue.extend(messages)

    def pulses(self, n):
        self.queue.extend([[CLOCK]] * n)

    def get_message(self):
        return (self.queue.pop(0), 0.0) if self.queue else None


def make(**overrides):
    """A quantiser wired to a fake port, bypassing MIDI discovery entirely."""
    cfg = {"enabled": True, "quantize_bars": 8, "beats_per_bar": 4, "stale_s": 1.0}
    cfg.update(overrides)
    q = CueQuantizer(cfg, LOG)   # enabled but no real port matched
    port = FakePort()
    q._port = port
    return q, port


def test_period_is_eight_bars_of_four_four():
    q, _ = make()
    assert q.period == 768          # 24 pulses * 4 beats * 8 bars


def test_cold_start_publishes_immediately():
    """Nothing to hold back on the first tick — the piece must boot into state 0."""
    q, port = make()
    assert q.update(0, now=0.0) == (0, False)


def test_change_is_held_until_the_boundary():
    q, port = make()
    q.update(0, now=0.0)

    # A third of the way through the window, the coral bleaches.
    port.pulses(250)
    q.update(0, now=1.0)
    cue, moved = q.update(2, now=1.05)
    assert (cue, moved) == (0, False), "cue jumped early"

    # Still short of the line.
    port.pulses(500)
    assert q.update(2, now=2.0) == (0, False)

    # Crossing 768 releases it.
    port.pulses(20)
    assert q.update(2, now=3.0) == (2, True)

    # And it stays put afterwards.
    assert q.update(2, now=3.05) == (2, False)


def test_only_the_latest_value_survives_the_window():
    """Chatter inside one window collapses to a single swap — Live's own launch
    quantisation behaves the same way, and it is what stops a visitor mashing both
    buttons from stuttering the soundscape."""
    q, port = make()
    q.update(0, now=0.0)

    port.pulses(100)
    q.update(1, now=1.0)
    port.pulses(100)
    q.update(0, now=2.0)
    port.pulses(100)
    q.update(1, now=3.0)

    port.pulses(500)                      # cross the boundary
    assert q.update(1, now=4.0) == (1, True)


def test_start_resets_the_grid():
    q, port = make()
    q.update(0, now=0.0)
    port.pulses(700)
    q.update(0, now=1.0)

    port.feed([START])                    # transport relocated to the top
    port.pulses(100)
    assert q.update(2, now=2.0) == (0, False), "START must reseat the bar line"

    port.pulses(700)
    assert q.update(2, now=3.0) == (2, True)


def test_song_position_reseats_the_count():
    q, port = make()
    q.update(0, now=0.0)
    # SPP is in 16th notes (6 pulses each); 127 -> 762, just short of 768.
    port.feed([SPP, 127, 0])
    port.pulses(10)
    assert q.update(2, now=1.0) == (2, True)


def test_silent_clock_falls_back_to_immediate():
    """Live closed or transport stopped: quantisation must get out of the way
    rather than freeze the soundscape on a stale cue."""
    q, port = make(stale_s=0.5)
    q.update(0, now=0.0)
    port.pulses(10)
    q.update(0, now=1.0)                  # clock is alive here

    # No pulses for longer than stale_s.
    assert q.update(2, now=1.6) == (2, True)


def test_disabled_is_pure_passthrough():
    q = CueQuantizer({"enabled": False}, LOG)
    assert q.update(0, now=0.0) == (0, False)
    assert q.update(2, now=0.05) == (2, True)
    assert q.update(2, now=0.10) == (2, False)
    q.close()


def test_missing_config_block_is_harmless():
    """A config predating this feature must still start the server."""
    q = CueQuantizer({}, LOG)
    assert q.update(1, now=0.0) == (1, False)
    q.close()


@pytest.mark.parametrize("bars,expected", [(1, 96), (4, 384), (8, 768), (16, 1536)])
def test_quantize_bars_is_configurable(bars, expected):
    """The 8-vs-4 bar decision is a config change, never a code change."""
    q, _ = make(quantize_bars=bars)
    assert q.period == expected
