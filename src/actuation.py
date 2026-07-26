"""Actuation plane — Tasmota smart-plug control over local HTTP (PROTOCOL.md §3).

This is the point-to-point, must-succeed counterpart to the fan-out event plane.
The server derives a desired power state for each plug from the current *target*
and coral *state*, and this module gates the plugs accordingly (PROTOCOL.md §3):

    target warm  -> heater ON,  fan OFF     (warming toward the heater's setpoint)
    target cool  -> heater OFF, fan ON      (active cooling)
    lamp         -> per lamp_mode (a dramatic blackout at the bleach latch)

The server never assumes the heater's internal state — the heater has its own
thermostat. This module only gates mains power and logs the outcome.

Two properties are non-negotiable, and are why this is not just a `requests.get`:

1. **Edge-driven.** A plug is commanded only when its *desired* state changes, so
   an entire visitor arc costs a handful of GETs, not five per second. A failed
   command is deliberately *not* retried until the next real edge (PROTOCOL.md §3:
   "retry on the next actuation change") — the cool button flipping the target back
   naturally supersedes a failed warm command.
2. **Off-loop and fail-soft.** A dead plug can block an HTTP call until timeout;
   doing that on the 5 Hz control loop would stall the heartbeat every button
   press. So commands are handed to a single background worker thread over a queue.
   The worker owns *all* network I/O; the control loop only ever enqueues (a dict
   compare, never a socket). Because the worker consumes immutable command tuples
   and touches no shared mutable state, the main loop stays effectively
   single-threaded and lock-free — the determinism CLAUDE.md requires is intact.
   An unreachable plug is logged and skipped; it never crashes the server.

Actuation is inert unless ``plugs.enabled`` is true, so this is safe to construct
in every mode. It gates real hardware, so it lives *below* the event plane and
knows nothing about subscribers.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

import requests

# Roles this controller knows how to gate. Order is the startup command order.
ROLES = ("heater", "fan", "lamp")

# Sentinel enqueued by close() to wake the worker so it can exit its blocking get().
_SHUTDOWN = None


def _local_session() -> requests.Session:
    """A requests session pinned to the isolated LAN.

    ``trust_env = False`` stops requests from honouring any system HTTP(S)_PROXY /
    .netrc configuration. The exhibition Mac may carry a university/VPN proxy;
    without this, plug commands to ``http://192.168.x.y`` would be routed off-LAN
    and fail, violating the local-only principle (CLAUDE.md #4). Plug traffic must
    always go direct.
    """
    session = requests.Session()
    session.trust_env = False
    return session


class PlugController:
    """Gate the Tasmota plugs from (target, state), off the control loop.

    Construct once from ``cfg['plugs']`` and the cool target, then call
    :meth:`update` every tick with the current ``(target, state)``. Only plugs that
    both have an address configured *and* have changed desired state are commanded,
    and the actual HTTP happens on a background worker so the loop never blocks.
    """

    def __init__(
        self,
        cfg_plugs: dict,
        cool_target: float,
        log: Optional[logging.Logger] = None,
        *,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.enabled = bool(cfg_plugs.get("enabled", False))
        self.timeout_s = float(cfg_plugs.get("timeout_s", 2.0))
        self.cool_target = float(cool_target)
        self.lamp_mode = str(cfg_plugs.get("lamp_mode", "on_until_bleach")).lower()
        self._log = log or logging.getLogger("coral")

        # role -> base URL, for each role that has an address in config. A role with
        # no URL is simply absent from the map and is never commanded.
        self._urls: dict[str, str] = {}
        for role in ROLES:
            url = cfg_plugs.get(role)
            if url:
                self._urls[role] = str(url).rstrip("/")

        # Last power we *commanded* per role (None = never sent). Edge detection
        # compares desired against this. Only the main thread ever touches it, so no
        # lock is needed; the worker never reads or writes it.
        self._commanded: dict[str, Optional[bool]] = {r: None for r in self._urls}

        self._queue: "queue.Queue[Optional[tuple[str, str, bool]]]" = queue.Queue()
        self._session = session or _local_session()
        self._worker: Optional[threading.Thread] = None
        if self.enabled and self._urls:
            self._worker = threading.Thread(
                target=self._run, name="plug-actuation", daemon=True
            )
            self._worker.start()
            self._log.info(
                "actuation: enabled, plugs=%s lamp_mode=%s timeout=%.1fs",
                sorted(self._urls), self.lamp_mode, self.timeout_s,
            )
        else:
            self._log.info(
                "actuation: disabled (enabled=%s, %d plug(s) configured)",
                self.enabled, len(self._urls),
            )

    # ----------------------------------------------------------------- policy
    def _lamp_on(self, state: int) -> Optional[bool]:
        """Desired lamp power for a coral state, or None to leave the lamp alone.

        ``on_until_bleach`` (default) keeps the lamp lit while the coral is alive
        (states 0/1) and dark once bleaching has latched (states 2/3), so the light
        returns only when life does. ``always_on`` holds it on; ``off`` disables
        lamp control entirely (the plug is never commanded).
        """
        if self.lamp_mode == "off":
            return None
        if self.lamp_mode == "always_on":
            return True
        return state < 2  # on_until_bleach

    def desired(self, target: float, state: int) -> dict[str, Optional[bool]]:
        """Map ``(target, state)`` to a desired power per *configured* plug.

        Heater/fan follow the target (warming vs cooling); the lamp follows the
        state so it can go dark the instant bleaching latches. A ``None`` value
        means "leave this plug alone" (only the lamp, and only in ``off`` mode).
        """
        warming = target > self.cool_target
        want: dict[str, Optional[bool]] = {}
        if "heater" in self._urls:
            want["heater"] = warming
        if "fan" in self._urls:
            want["fan"] = not warming
        if "lamp" in self._urls:
            want["lamp"] = self._lamp_on(state)
        return want

    # ------------------------------------------------------------- main thread
    def update(self, target: float, state: int) -> None:
        """Enqueue a command for every plug whose desired state changed this tick.

        Cheap and safe to call on every tick: when nothing changed (the common
        case) it does a few dict comparisons and returns. Optimistically records the
        new desired state *before* the HTTP succeeds — that is what makes a failure
        wait for the next edge instead of being retried on the loop (PROTOCOL.md §3).
        """
        if not self.enabled:
            return
        for role, want in self.desired(target, state).items():
            if want is None:
                continue  # lamp_mode: off — this plug is not ours to drive
            if self._commanded[role] != want:
                self._commanded[role] = want
                self._queue.put((role, self._urls[role], want))

    # ----------------------------------------------------------- worker thread
    def _run(self) -> None:
        """Drain the command queue, issuing one blocking HTTP call at a time."""
        while True:
            item = self._queue.get()
            try:
                if item is _SHUTDOWN:
                    return
                self._send(*item)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive no matter what
                self._log.error("actuation worker error: %s", exc)
            finally:
                self._queue.task_done()

    def _send(self, role: str, base: str, want: bool) -> None:
        """Issue one Tasmota power command. Failure is logged, never raised."""
        word = "On" if want else "Off"
        url = f"{base}/cm?cmnd=Power%20{word}"  # %20 encodes the space
        try:
            resp = self._session.get(url, timeout=self.timeout_s)
            resp.raise_for_status()
            self._log.info("ACTUATE %s %s -> %s", role, word, resp.text.strip())
        except requests.RequestException as exc:
            # Fail soft: the plug may be unplugged or the LAN down. Log and move on;
            # the next real edge re-commands it (PROTOCOL.md §3).
            self._log.warning("ACTUATE %s %s FAILED (%s): %s", role, word, base, exc)

    # ------------------------------------------------------------------ lifecycle
    def wait_idle(self, timeout: Optional[float] = None) -> None:
        """Block until every queued command has been processed (tests/diagnostics)."""
        if timeout is None:
            self._queue.join()
            return
        # queue.join() has no timeout; poll unfinished_tasks so tests can't hang.
        import time
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)

    def close(self) -> None:
        """Stop the worker and release the HTTP session. Safe to call when disabled."""
        if self._worker and self._worker.is_alive():
            self._queue.put(_SHUTDOWN)
            self._worker.join(timeout=self.timeout_s + 1.0)
        try:
            self._session.close()
        except Exception:  # noqa: BLE001 - a fake/session without close() is fine
            pass
