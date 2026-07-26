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

import json
import logging
import random
import threading
from abc import ABC, abstractmethod
from typing import Callable, Optional

import requests


def _local_session() -> requests.Session:
    """A requests session pinned to the isolated LAN (mirrors actuation.py).

    ``trust_env = False`` stops requests from honouring any system HTTP(S)_PROXY /
    .netrc configuration. The exhibition Mac may carry a university/VPN proxy;
    without this, a poll of ``http://192.168.x.y`` would be routed off-LAN and
    fail, violating the local-only principle (CLAUDE.md #4). Sensor traffic, like
    plug traffic, must always go direct. Duplicated (not imported) so the two
    hardware-facing edge modules stay independent of each other.
    """
    session = requests.Session()
    session.trust_env = False
    return session


class TemperatureSource(ABC):
    """The seam. One method: given the elapsed time, return the current °C."""

    @abstractmethod
    def read(self, dt: float) -> float:
        """Return the latest water temperature in °C for a tick of length ``dt``."""
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources (threads, sockets). No-op unless overridden.

        Called from the server's shutdown path. SimulatedSource holds nothing, so
        the default is empty; RealSource overrides it to stop its poll thread.
        """


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
    """Poll the ESPHome sensor over HTTP, *off* the control loop (PROTOCOL.md §4).

    The DS18B20 on the ESP32-C3 runs ESPHome's web server; a GET of the sensor URL
    returns ``{"id": "...", "value": 26.4, "state": "26.4 °C"}``. This source polls
    that endpoint on a background thread at ``poll_hz`` and caches the reading;
    :meth:`read` returns the cache and never touches the network.

    **Why a thread, not a poll inside read().** A sensor that is unplugged or off
    the LAN blocks an HTTP GET until ``timeout_s``. Doing that on the 5 Hz control
    loop would stall the broadcast heartbeat and registry pruning on every poll —
    the identical hazard actuation.py isolates onto a worker thread. So all sensor
    I/O lives off-loop and the loop only ever reads a cached float. This is exactly
    what lets real water be a drop-in for the simulated model with nothing above
    the seam changing.

    **Fail-soft (PROTOCOL.md §4).** On timeout or a malformed reply the last good
    reading is *held* and the fault is logged once — and once more on recovery —
    never raised. Before the first successful poll the source reports the configured
    ``start_temp`` (the natural anchor), so the system boots at state 0 and
    converges on the first reading, honouring the cold-start rule in CLAUDE.md.

    HTTP poll is the only supported ingestion. MQTT is deliberately out of scope
    (CLAUDE.md: no message brokers); an ``ingestion`` other than ``http`` is a
    clear, early error rather than a silent fallback.
    """

    def __init__(
        self,
        cfg_real: dict,
        log: Optional[logging.Logger] = None,
        *,
        session: Optional[requests.Session] = None,
        start_polling: bool = True,
    ) -> None:
        ingestion = str(cfg_real.get("ingestion", "http")).lower()
        if ingestion != "http":
            raise NotImplementedError(
                f"temperature.real.ingestion: {ingestion!r} is not supported — this "
                "system is HTTP-poll only by design (CLAUDE.md: no MQTT broker). "
                "Set ingestion: http and point sensor_url at the ESPHome web server."
            )
        self.url = str(cfg_real["sensor_url"]).rstrip("/")
        self.timeout_s = float(cfg_real.get("timeout_s", 2.0))
        poll_hz = float(cfg_real.get("poll_hz", 2))
        self.poll_interval = 1.0 / poll_hz if poll_hz > 0 else 0.5
        self._log = log or logging.getLogger("coral")

        # Last good reading, seeded to the natural anchor so the system boots at
        # state 0 before the first poll lands. Guarded by a lock: the poll thread
        # writes it, the control loop reads it.
        self._temp = float(cfg_real.get("start_temp", 26.0))
        self._lock = threading.Lock()
        self._healthy = True  # de-duplicates logging across a failure streak

        self._session = session or _local_session()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # ``start_polling=False`` builds an inert instance for unit tests, which
        # drive _poll_once()/read() deterministically rather than racing a thread.
        if start_polling:
            self._thread = threading.Thread(
                target=self._run, name="sensor-poll", daemon=True
            )
            self._thread.start()
            self._log.info(
                "temperature: real — polling %s at %.1fHz timeout=%.1fs",
                self.url, poll_hz, self.timeout_s,
            )

    def read(self, dt: float) -> float:
        """Return the latest cached reading. Never blocks; never hits the network."""
        with self._lock:
            return self._temp

    # --------------------------------------------------------------- poll thread
    def _run(self) -> None:
        """Poll forever at the configured interval until :meth:`close` is called."""
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self.poll_interval)  # interruptible sleep

    def _poll_once(self) -> bool:
        """One GET + parse + cache update. Returns True on success. Never raises.

        A failure holds the previous reading (PROTOCOL.md §4). The first failure in
        a streak logs a warning and the return to health logs an info, so a sensor
        outage leaves a clear before/after in the log without flooding it 2×/second.
        """
        try:
            resp = self._session.get(self.url, timeout=self.timeout_s)
            resp.raise_for_status()
            temp = self._parse(resp.text)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            if self._healthy:
                self._log.warning(
                    "SENSOR read failed (%s): %s — holding last reading %.2f°C",
                    self.url, exc, self.read(0.0),
                )
                self._healthy = False
            return False
        with self._lock:
            self._temp = temp
        if not self._healthy:
            self._log.info("SENSOR recovered: %.2f°C from %s", temp, self.url)
            self._healthy = True
        return True

    @staticmethod
    def _parse(text: str) -> float:
        """Extract °C from an ESPHome web-server reply (PROTOCOL.md §4).

        ``{"id":"sensor-water_temperature","value":26.4,"state":"26.4 °C"}`` — the
        machine-readable ``value`` field is authoritative (the ``state`` string
        carries the unit and is for humans). A missing, null, or NaN value means a
        disconnected probe; it raises so the caller soft-fails and holds the last
        good reading rather than feeding garbage into the state machine.
        """
        data = json.loads(text)          # bad JSON -> JSONDecodeError (a ValueError)
        temp = float(data["value"])      # missing -> KeyError; null -> TypeError
        if temp != temp:                 # NaN (bad probe) -> hold last reading
            raise ValueError("sensor value is NaN")
        return temp

    # ----------------------------------------------------------------- lifecycle
    def close(self) -> None:
        """Stop the poll thread and release the HTTP session. Safe to call twice."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.timeout_s + 1.0)
        try:
            self._session.close()
        except Exception:  # noqa: BLE001 - a fake/session without close() is fine
            pass


def make_source(
    cfg_temp: dict,
    get_target: Callable[[], float],
    log: Optional[logging.Logger] = None,
) -> TemperatureSource:
    """Construct the temperature source selected by ``cfg_temp['mode']``.

    ``get_target`` feeds the simulated thermal model; ``log`` is used by the real
    source for its poll diagnostics. Both are ignored by the source that does not
    need them, so the caller wires the same two arguments in either mode.
    """
    mode = str(cfg_temp.get("mode", "simulated")).lower()
    if mode == "simulated":
        return SimulatedSource(cfg_temp["simulated"], get_target)
    if mode == "real":
        return RealSource(cfg_temp["real"], log)
    raise ValueError(f"unknown temperature.mode: {mode!r} (expected 'simulated' or 'real')")
