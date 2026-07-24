"""Temperature ingestion — the single abstraction seam (CLAUDE.md, ARCHITECTURE.md §9).

Temperature enters the server through one interface with two interchangeable
implementations, selected by ``temperature.mode`` in config:

    SimulatedSource  a thermal model advances T toward the target. No hardware.
                     The development default AND the permanent regression harness.
    RealSource       poll the ESPHome sensor over HTTP.  (Phase 5 — not yet built.)

Everything *above* this seam — state.py, broadcast.py, every subscriber — is
byte-identical across modes. That is the architectural guarantee that real water
is a drop-in upgrade. Nothing mode-specific may leak upward.
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import Callable


class TemperatureSource(ABC):
    """The seam. One method: given the elapsed time, return the current °C."""

    @abstractmethod
    def read(self, dt: float) -> float:
        """Return the latest water temperature in °C for a tick of length ``dt``."""
        raise NotImplementedError


class SimulatedSource(TemperatureSource):
    """A thermal model with no hardware: T drifts toward the commanded target.

    Warming and cooling use separate configured rates (heating is faster than the
    passive/fan cool-down, as with real water). A small observation noise is added
    to each reading so downstream code sees the jitter a real sensor produces —
    the model's internal temperature stays clean.
    """

    def __init__(self, cfg_sim: dict, get_target: Callable[[], float]) -> None:
        self.temp = float(cfg_sim["start_temp"])
        self.heat_rate = float(cfg_sim["heat_rate"])  # °C/s while warming
        self.cool_rate = float(cfg_sim["cool_rate"])  # °C/s while cooling
        self.noise = float(cfg_sim.get("noise", 0.0))
        self._get_target = get_target

    def read(self, dt: float) -> float:
        target = self._get_target()
        if target > self.temp:
            self.temp = min(target, self.temp + self.heat_rate * dt)
        elif target < self.temp:
            self.temp = max(target, self.temp - self.cool_rate * dt)
        if self.noise:
            return self.temp + random.uniform(-self.noise, self.noise)
        return self.temp


class RealSource(TemperatureSource):
    """Poll the ESPHome sensor over HTTP (or subscribe to its MQTT topic).

    Phase 5. Deliberately unimplemented so the seam is visible now while the
    behaviour is filled in once the sensor hardware exists.
    """

    def __init__(self, cfg_real: dict) -> None:  # pragma: no cover - Phase 5
        raise NotImplementedError(
            "temperature.mode: real arrives in Phase 5 (see docs/BUILD_ORDER.md). "
            "Use mode: simulated for Phases 1-3."
        )

    def read(self, dt: float) -> float:  # pragma: no cover - Phase 5
        raise NotImplementedError


def make_source(cfg_temp: dict, get_target: Callable[[], float]) -> TemperatureSource:
    """Construct the temperature source selected by ``cfg_temp['mode']``."""
    mode = str(cfg_temp.get("mode", "simulated")).lower()
    if mode == "simulated":
        return SimulatedSource(cfg_temp["simulated"], get_target)
    if mode == "real":
        return RealSource(cfg_temp["real"])
    raise ValueError(f"unknown temperature.mode: {mode!r} (expected 'simulated' or 'real')")
