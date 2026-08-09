"""Orchestration server — the one bespoke component (CLAUDE.md).

A single-threaded, fixed-tick event loop. Every tick it: services the inbound OSC
plane (registrations + harness buttons) and the physical arcade buttons, reads the
temperature through the ingestion seam, derives (state, intensity), and — every 4th
tick, to hit the 5 Hz broadcast rate — fans the result out and prunes the client
registry. Everything is read from config; nothing here hard-codes a threshold,
address, or port.

Run it:  python src/server.py
"""
from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from actuation import PlugController
from broadcast import Broadcaster
from config import REPO_ROOT, load_config
from inputs import ButtonReader
from quantize import BedQuantizer
from state import STATE_NAMES, CoralState
from temperature import make_source

# Internal loop runs SUBTICKS× faster than the broadcast rate (5 Hz -> 20 Hz) so
# button input and temperature are serviced promptly between broadcasts. This is a
# structural loop constant, not a tunable — the broadcast rate itself lives in config.
SUBTICKS = 4


def setup_logging(cfg_log: dict) -> tuple[logging.Logger, logging.Logger]:
    """Configure rotating file + console logging.

    Returns (log, sample_log). ``log`` carries transitions, inputs and lifecycle to
    both console and file. ``sample_log`` carries the high-rate temperature samples
    to the file only, keeping the console readable while preserving thesis data.
    """
    log_dir = Path(cfg_log.get("dir", "logs"))
    if not log_dir.is_absolute():
        log_dir = REPO_ROOT / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, str(cfg_log.get("level", "INFO")).upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = RotatingFileHandler(
        log_dir / "coral.log",
        maxBytes=int(cfg_log.get("rotate_mb", 10)) * 1_000_000,
        backupCount=int(cfg_log.get("backups", 5)),
    )
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)

    log = logging.getLogger("coral")
    log.setLevel(level)
    log.handlers.clear()
    log.addHandler(file_handler)
    log.addHandler(console)
    log.propagate = False

    sample_log = logging.getLogger("coral.samples")
    sample_log.setLevel(level)
    sample_log.handlers.clear()
    sample_log.addHandler(file_handler)  # file only — not the console
    sample_log.propagate = False

    return log, sample_log


def main() -> None:
    # CORAL_CONFIG selects an alternate config file (the test suite uses this to run
    # an isolated instance; also handy for a second server). Defaults to config.yaml.
    cfg = load_config(os.environ.get("CORAL_CONFIG"))
    log, sample_log = setup_logging(cfg["logging"])
    log.info("=== coral orchestration server starting ===")

    state = CoralState(cfg["state"], cfg["targets"])
    # The seam is fed the *current* target lazily, so the simulation always drifts
    # toward whatever the buttons last set without state.py knowing about it.
    source = make_source(cfg["temperature"], get_target=lambda: state.target, log=log)
    bc = Broadcaster(cfg["broadcast"])
    # Actuation plane: gates the Tasmota plugs off-loop from (target, state). Inert
    # unless plugs.enabled, so it is safe to construct in simulated mode too.
    plugs = PlugController(cfg["plugs"], cfg["targets"]["cool"], log)
    # Physical arcade buttons (USB HID gamepad). Reports (False, False) when disabled
    # or no encoder is attached, so the OSC harness path below is always available.
    buttons = ButtonReader(cfg["input"], log)
    # Bar-quantises the soundscape's bed cue against Live's MIDI clock. Pure
    # pass-through when disabled or when no clock arrives, so it is safe to
    # construct unconditionally and in every no-Ableton scenario.
    quant = BedQuantizer(cfg.get("quantize", {}), log)

    broadcast_hz = float(cfg["broadcast"]["rate_hz"])
    dt = 1.0 / (broadcast_hz * SUBTICKS)
    log_samples = bool(cfg["logging"].get("log_temperature_samples", False))

    static_names = [s.get("name", "?") for s in cfg["broadcast"].get("static_subscribers", [])]
    log.info(
        "mode=%s broadcast=%.0fHz internal=%.0fHz listen=%s:%d static=%s",
        cfg["temperature"]["mode"], broadcast_hz, broadcast_hz * SUBTICKS,
        bc.bind_host, bc.listen_port, static_names,
    )

    tick = 0
    next_t = time.monotonic()
    try:
        while True:
            now = time.monotonic()

            # 1. Inbound plane: registrations + harness button events, plus the
            # physical encoder. Both funnel into the one set-target path — hardware
            # and the OSC harness are indistinguishable to the state machine, and a
            # press from either source counts. Cool-wins is resolved in apply_input.
            extras, newly = bc.poll_inbound(now)
            for cid in newly:
                log.info("CLIENT registered: %s (%d total)", cid, len(bc.registry))
            hw_warm, hw_cool = buttons.read()
            warm = hw_warm or any(addr == "/sim/warm" for addr, _ in extras)
            cool = hw_cool or any(addr == "/sim/cool" for addr, _ in extras)
            state.apply_input(warm, cool)

            # 2. Temperature (ingestion seam) and 3. state derivation.
            temp = source.read(dt)
            state.update(temp, dt)
            for event in state.drain_events():
                log.info(event)

            # 3b. Gate the plugs. Edge-driven and enqueue-only, so this is cheap
            # every tick and reacts to a button-driven target change immediately.
            plugs.update(state.target, state.state)

            # 3c. Bed cue: state held back to the next musical boundary when Live's
            # clock is available, otherwise identical to state.
            bed, bed_moved = quant.update(state.state, now)
            if bed_moved:
                log.info("BED -> %d (%s)", bed, STATE_NAMES[bed])

            # 4. Broadcast + prune at the configured rate (every SUBTICKS ticks).
            # A bed change landing between broadcasts is emitted immediately: waiting
            # up to 200 ms for the next scheduled one would drop the audio cut behind
            # the bar line we just waited for.
            broadcast_tick = tick % SUBTICKS == 0
            if broadcast_tick:
                for cid in bc.registry.prune(now):
                    log.info("CLIENT pruned (silent >%.0fs): %s", bc.registry.timeout_s, cid)
            if broadcast_tick or bed_moved:
                bc.emit(state.state, state.intensity, temp, bed, state.latch_progress)
            if broadcast_tick and log_samples:
                sample_log.info(
                    "SAMPLE temp=%.3f target=%.1f state=%d intensity=%.3f bed=%d",
                    temp, state.target, state.state, state.intensity, bed,
                )
            tick += 1

            # 5. Fixed-tick pacing against a monotonic clock; resync if we fall behind.
            next_t += dt
            sleep = next_t - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.monotonic()
    except KeyboardInterrupt:
        log.info("interrupt received — shutting down")
    finally:
        source.close()   # stop the sensor poll thread (no-op in simulated mode)
        quant.close()    # release the MIDI clock input (no-op when disabled)
        plugs.close()
        buttons.close()  # release the gamepad backend (no-op when disabled)
        bc.close()
        log.info("=== server stopped ===")


if __name__ == "__main__":
    main()
