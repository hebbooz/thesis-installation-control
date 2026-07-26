"""Input plane — the physical arcade buttons over USB HID (PROTOCOL.md §5, Phase 6).

Two illuminated arcade buttons are wired to a USB "zero delay" encoder that
enumerates as a **gamepad/joystick**, NOT a keyboard (CLAUDE.md, HARDWARE.md). This
module reads that device with ``pygame.joystick`` and answers one question each
tick: *which buttons are down right now?* — returning ``(warm, cool)``.

Those two booleans funnel into the **exact same** set-target path the no-hardware
harness drives over OSC (``/sim/warm`` / ``/sim/cool`` → ``state.apply_input``), so
the state machine is byte-identical whether input arrives from the encoder or the
keyboard rig (PROTOCOL.md §5). Cool-wins-on-conflict and the idle-timer reset live
downstream in ``apply_input``; this module only reports button levels.

Design choices, and why they match the rest of the system:

* **In-loop, not a thread.** Reading a local HID device is non-blocking, unlike the
  sensor and plugs (which are threaded *only* because they do blocking network I/O).
  So input is polled inline on the server's single-threaded fixed-tick loop — the
  KISS choice, and it keeps the loop's determinism (CLAUDE.md).
* **Level-based and idempotent.** While a button is held it reports ``True`` every
  tick; ``apply_input`` is idempotent, so a held press simply re-asserts the same
  target and keeps the piece "awake". No edge state to get wrong.
* **Fail-soft (Phase 6 acceptance test).** A missing, unplugged, or mid-run-removed
  encoder reports ``(False, False)`` and never raises: the server keeps broadcasting
  and the idle reset eventually returns the piece to Natural. A re-plugged encoder is
  re-acquired automatically. The pygame backend is isolated behind a small seam so
  this policy is unit-tested with a fake and zero hardware (tests/test_inputs.py).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable, Optional, Protocol, Tuple

# Keep pygame's import banner off stdout — the server's console is for the log.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


class Device(Protocol):
    """The subset of ``pygame.joystick.Joystick`` this module uses.

    A real pygame Joystick satisfies it directly; the tests supply a fake with the
    same shape, which is what lets the input policy be verified without hardware.
    """

    def get_name(self) -> str: ...
    def get_numbuttons(self) -> int: ...
    def get_button(self, index: int) -> int: ...


class Backend(Protocol):
    """The joystick access seam: everything pygame-specific lives behind this."""

    def pump(self) -> None: ...
    def count(self) -> int: ...
    def open(self, index: int) -> Optional[Device]: ...
    def close(self) -> None: ...


class PygameBackend:
    """The real backend: SDL joysticks via pygame. Constructed only when input is
    enabled, so a pure-simulation run that never touches input pays nothing for it.

    ``pygame.init()`` brings up the whole library (video included): on macOS the SDL
    event pump that refreshes joystick state must run with the event subsystem up and
    on the main thread — which is exactly where the server loop calls :meth:`read`.
    ``init()`` never raises on a subsystem that fails to start (it reports it), and
    :class:`ButtonReader` wraps construction in a try/except regardless, so a headless
    or display-less host degrades to "no buttons" instead of crashing.
    """

    def __init__(self) -> None:
        import pygame  # local import: only paid for when input.enabled is true

        self._pygame = pygame
        pygame.init()
        pygame.joystick.init()

    def pump(self) -> None:
        self._pygame.event.pump()

    def count(self) -> int:
        return self._pygame.joystick.get_count()

    def open(self, index: int) -> Optional[Device]:
        if index < 0 or index >= self._pygame.joystick.get_count():
            return None
        js = self._pygame.joystick.Joystick(index)
        js.init()
        return js

    def close(self) -> None:
        self._pygame.joystick.quit()
        self._pygame.quit()


class ButtonReader:
    """Reads the warm/cool arcade buttons and reports their levels each tick.

    Construct once from ``cfg['input']``; call :meth:`read` every tick for the
    ``(warm, cool)`` pair to feed ``state.apply_input``. Disabled, or when no encoder
    is attached, it simply reports ``(False, False)`` and the OSC harness still drives
    input — so the same build runs with or without the physical buttons wired.
    """

    # Throttle device (re)open attempts so a run with no encoder plugged in doesn't
    # probe SDL 20×/second. Structural, not a knob — the reacquire cadence is not part
    # of the installation's tunable behaviour.
    REACQUIRE_INTERVAL_S = 1.0

    def __init__(
        self,
        cfg_input: dict,
        log: Optional[logging.Logger] = None,
        *,
        backend: Optional[Backend] = None,
        backend_factory: Callable[[], Backend] = PygameBackend,
    ) -> None:
        self.enabled = bool(cfg_input.get("enabled", False))
        self.device_index = int(cfg_input.get("device_index", 0))
        self.warm_button = int(cfg_input.get("warm_button", 0))
        self.cool_button = int(cfg_input.get("cool_button", 1))
        self._log = log or logging.getLogger("coral")

        self._device: Optional[Device] = None
        self._next_acquire = 0.0  # monotonic time before which not to retry open()
        self._backend: Optional[Backend] = None

        if not self.enabled:
            self._log.info("input: disabled — harness/OSC (/sim/*) drives input")
            return

        # ``backend`` (a fake) wins for tests; otherwise build the real pygame backend.
        # A failure here (no display, SDL missing) must degrade, never crash the server.
        try:
            self._backend = backend if backend is not None else backend_factory()
        except Exception as exc:  # noqa: BLE001 - fail soft, exactly like the sensor/plugs
            self._log.warning(
                "input: could not initialise gamepad backend (%s) — continuing "
                "without physical buttons (OSC harness still works)", exc,
            )
            self._backend = None
            return

        self._log.info(
            "input: enabled — warm=button %d, cool=button %d, device %d",
            self.warm_button, self.cool_button, self.device_index,
        )
        self._acquire(force=True)  # attempt once now; harmless if not yet plugged in
        if self._device is None:
            self._log.info(
                "input: no gamepad detected yet — will attach automatically on plug-in"
            )

    # ------------------------------------------------------------------ per tick
    def read(self) -> Tuple[bool, bool]:
        """Return ``(warm_pressed, cool_pressed)`` for this tick. Never raises.

        Reports ``(False, False)`` when input is disabled, the backend failed to
        start, or no encoder is currently attached — in every one of those cases the
        server keeps running and the OSC harness path is unaffected.
        """
        if self._backend is None:
            return (False, False)
        try:
            self._backend.pump()  # refresh SDL's view of the buttons
        except Exception:  # noqa: BLE001 - a pump hiccup must not stall the loop
            return (False, False)

        if self._device is None:
            self._acquire()  # throttled; a re-plugged encoder reconnects here
            if self._device is None:
                return (False, False)

        try:
            warm = self._is_down(self.warm_button)
            cool = self._is_down(self.cool_button)
        except Exception as exc:  # noqa: BLE001 - device yanked mid-read (pygame.error)
            self._on_lost(exc)
            return (False, False)
        return (warm, cool)

    def _is_down(self, index: int) -> bool:
        """True if button ``index`` is pressed; out-of-range indices are simply False.

        A misconfigured index (e.g. beyond the encoder's button count) must not crash
        the read — it just never registers a press, which is visible and recoverable.
        """
        assert self._device is not None
        return 0 <= index < self._device.get_numbuttons() and bool(
            self._device.get_button(index)
        )

    # ----------------------------------------------------------------- lifecycle
    def _acquire(self, force: bool = False) -> None:
        """Try to open the configured device. Throttled unless ``force``. Never raises."""
        now = time.monotonic()
        if not force and now < self._next_acquire:
            return
        self._next_acquire = now + self.REACQUIRE_INTERVAL_S
        assert self._backend is not None
        try:
            if self._backend.count() <= self.device_index:
                return  # nothing plugged in at that index yet
            device = self._backend.open(self.device_index)
        except Exception:  # noqa: BLE001 - transient during hotplug; retry next interval
            return
        if device is None:
            return
        self._device = device
        self._log.info(
            "INPUT encoder connected: %r (device %d, %d buttons)",
            _safe(device.get_name, "?"), self.device_index,
            _safe(device.get_numbuttons, 0),
        )

    def _on_lost(self, exc: Exception) -> None:
        """Handle an encoder disconnected mid-run: drop it, log once, keep serving."""
        self._device = None
        self._next_acquire = time.monotonic() + self.REACQUIRE_INTERVAL_S
        self._log.warning(
            "INPUT encoder disconnected (%s) — continuing without buttons; the idle "
            "reset will return the piece to Natural. Re-plug to reconnect.", exc,
        )

    def close(self) -> None:
        """Release the backend. Safe when disabled or already closed."""
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:  # noqa: BLE001 - a fake/backend without close() is fine
                pass
            self._backend = None


def _safe(fn: Callable, default):
    """Call ``fn`` for a log field, tolerating a device that fails mid-query."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default
