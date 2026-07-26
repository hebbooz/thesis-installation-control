#!/usr/bin/env python3
"""discover_buttons.py — find the gamepad button indices for the arcade encoder.

The USB "zero delay" encoder enumerates as a **gamepad/joystick**, not a keyboard
(CLAUDE.md, HARDWARE.md). Its button *indices* vary by unit, so run this once, press
each arcade button, and read off the index — then record them in ``config.yaml``:

    input:
      device_index: 0     # the device number printed below
      warm_button: <the index printed when you press the WARM (red) button>
      cool_button: <the index printed when you press the COOL (blue) button>

It reports buttons, hats, and axes so you can see exactly how your unit presents
each control. The two wired arcade buttons come through as **buttons**; hats/axes are
shown only to help you recognise anything wired to the encoder's joystick header.

    python tools/discover_buttons.py     # Ctrl-C to quit

This reads state with ``joystick.get_button`` — the identical call the server's
``src/inputs.py`` uses — so anything this tool sees, the server will read too. It is
a diagnostic, not part of the exhibition runtime.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Keep pygame's import banner off stdout, then import it.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

# Reuse the one config loader so we can show the current mapping (as other tools do).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from config import load_config  # noqa: E402

POLL_INTERVAL_S = 0.02  # 50 Hz — well under human press duration, no missed edges


def _open_all() -> dict[int, pygame.joystick.JoystickType]:
    """Open every currently-connected joystick, keyed by index."""
    joysticks: dict[int, pygame.joystick.JoystickType] = {}
    for i in range(pygame.joystick.get_count()):
        js = pygame.joystick.Joystick(i)
        js.init()
        joysticks[i] = js
    return joysticks


def _describe(js: pygame.joystick.JoystickType, index: int) -> str:
    return (f"  device {index}: {js.get_name()!r}  "
            f"buttons={js.get_numbuttons()}  hats={js.get_numhats()}  "
            f"axes={js.get_numaxes()}")


def _current_mapping() -> str:
    """A reminder line showing what config.yaml maps today (best-effort)."""
    try:
        cfg = load_config()["input"]
        return (f"config.yaml currently maps: device {cfg.get('device_index')}, "
                f"warm=button {cfg.get('warm_button')}, cool=button {cfg.get('cool_button')}")
    except Exception:  # noqa: BLE001 - a missing/odd config must not stop discovery
        return "config.yaml not loaded — record indices manually."


def run() -> None:
    pygame.init()
    pygame.joystick.init()

    print("discover_buttons — press each arcade button; note its index.")
    print(f"  {_current_mapping()}")
    print("  Ctrl-C to quit.\n")

    joysticks = _open_all()
    if joysticks:
        print("Connected devices:")
        for i, js in joysticks.items():
            print(_describe(js, i))
        print()
    else:
        print("No gamepad detected — plug in the encoder (it will attach live)...\n")

    # Edge-detected state so a held button prints once, not every frame.
    prev_buttons: dict[tuple[int, int], bool] = {}
    prev_hats: dict[tuple[int, int], tuple[int, int]] = {}
    prev_axes: dict[tuple[int, int], float] = {}
    last_count = len(joysticks)

    while True:
        pygame.event.pump()

        # Hotplug: re-enumerate when the device count changes (plug/unplug).
        count = pygame.joystick.get_count()
        if count != last_count:
            joysticks = _open_all()
            last_count = count
            prev_buttons.clear(); prev_hats.clear(); prev_axes.clear()
            if joysticks:
                print("\n[device change] now connected:")
                for i, js in joysticks.items():
                    print(_describe(js, i))
                print()
            else:
                print("\n[device change] no gamepad connected.\n")

        for idx, js in list(joysticks.items()):
            try:
                for b in range(js.get_numbuttons()):
                    down = bool(js.get_button(b))
                    if down != prev_buttons.get((idx, b), False):
                        if down:
                            print(f"  device {idx}  BUTTON {b}  pressed   "
                                  f"→ use {b} as warm_button or cool_button")
                        prev_buttons[(idx, b)] = down
                for h in range(js.get_numhats()):
                    val = js.get_hat(h)
                    if val != prev_hats.get((idx, h), (0, 0)):
                        print(f"  device {idx}  HAT {h}  → {val}  (a 4-way / d-pad)")
                        prev_hats[(idx, h)] = val
                for a in range(js.get_numaxes()):
                    val = round(js.get_axis(a), 2)
                    # Only report a decisive throw, and only on change, to stay quiet.
                    if abs(val) > 0.6 and val != prev_axes.get((idx, a)):
                        print(f"  device {idx}  AXIS {a}  → {val:+.2f}  (an analog stick/trigger)")
                        prev_axes[(idx, a)] = val
                    elif abs(val) <= 0.6:
                        prev_axes[(idx, a)] = 0.0
            except pygame.error:
                # Device removed between the count check and the read — the next loop
                # re-enumerates. Drop it now so we don't keep poking a dead handle.
                joysticks.pop(idx, None)

        time.sleep(POLL_INTERVAL_S)


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        pass
    finally:
        pygame.joystick.quit()
        pygame.quit()
        print("\ndiscover_buttons stopped.")


if __name__ == "__main__":
    main()
