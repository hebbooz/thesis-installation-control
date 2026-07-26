"""Unit tests for the actuation plane (PROTOCOL.md §3, src/actuation.py).

These need no network and no hardware: a recording fake stands in for the HTTP
session, so we assert exactly which Tasmota commands the controller issues, that it
issues them only on an *edge*, and that a dead plug is soft-failed rather than
fatal. The pure policy (`desired`) is tested directly, with no worker thread.
"""
import requests

from actuation import PlugController

# A cool target of 26.0, mirroring config; "warm" is anything above it.
COOL = 26.0


def make_cfg(**over) -> dict:
    cfg = {
        "enabled": True,
        "timeout_s": 0.5,
        "heater": "http://plug/heater",
        "fan": "http://plug/fan",
        "lamp": "http://plug/lamp",
        "lamp_mode": "on_until_bleach",
    }
    cfg.update(over)
    return cfg


class FakeResponse:
    def __init__(self, text='{"POWER":"ON"}') -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class RecordingSession:
    """Records every GET as (url, timeout); returns a benign Tasmota-shaped reply."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        return FakeResponse()

    def close(self) -> None:
        pass

    # Convenience for assertions: just the "role On/Off" essence of each call.
    def commands(self) -> list[str]:
        out = []
        for url, _ in self.calls:
            base, _, cmnd = url.partition("/cm?cmnd=Power%20")
            role = base.rsplit("/", 1)[-1]
            out.append(f"{role} {cmnd}")
        return out


class FailingSession(RecordingSession):
    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        raise requests.ConnectionError("plug unreachable")


# --------------------------------------------------------------------- policy
def test_desired_maps_target_and_state():
    pc = PlugController(make_cfg(enabled=False), COOL)  # policy needs no worker
    warm = pc.desired(target=28.0, state=1)
    assert warm == {"heater": True, "fan": False, "lamp": True}
    cool = pc.desired(target=26.0, state=0)
    assert cool == {"heater": False, "fan": True, "lamp": True}


def test_lamp_goes_dark_when_bleached():
    pc = PlugController(make_cfg(enabled=False), COOL)
    assert pc.desired(28.0, 1)["lamp"] is True   # alive -> lit
    assert pc.desired(28.0, 2)["lamp"] is False  # bleach latched -> dark
    assert pc.desired(26.0, 3)["lamp"] is False  # still bleached through recovery


def test_lamp_mode_always_on_and_off():
    always = PlugController(make_cfg(enabled=False, lamp_mode="always_on"), COOL)
    assert always.desired(28.0, 2)["lamp"] is True

    off = PlugController(make_cfg(enabled=False, lamp_mode="off"), COOL)
    assert off.desired(28.0, 2)["lamp"] is None  # None -> never commanded


def test_only_configured_plugs_appear():
    pc = PlugController({"enabled": False, "fan": "http://plug/fan"}, COOL)
    assert set(pc.desired(28.0, 1)) == {"fan"}


# ------------------------------------------------------------ edge behaviour
def test_cold_start_drives_all_plugs_once():
    sess = RecordingSession()
    pc = PlugController(make_cfg(), COOL, session=sess)
    pc.update(target=COOL, state=0)  # cold start: heater off, fan on, lamp on
    pc.wait_idle(timeout=2.0)
    pc.close()
    assert set(sess.commands()) == {"heater Off", "fan On", "lamp On"}


def test_commands_only_on_edge():
    sess = RecordingSession()
    pc = PlugController(make_cfg(), COOL, session=sess)
    pc.update(COOL, 0)     # initial: 3 commands
    pc.update(COOL, 0)     # unchanged: nothing
    pc.update(COOL, 1)     # still cool, lamp still on: nothing
    pc.wait_idle(timeout=2.0)
    n_after_settle = len(sess.calls)
    assert n_after_settle == 3

    pc.update(28.0, 1)     # warm: heater On, fan Off (lamp unchanged)
    pc.wait_idle(timeout=2.0)
    pc.close()
    assert sess.commands()[3:] == ["heater On", "fan Off"]


def test_full_arc_including_bleach_blackout():
    sess = RecordingSession()
    pc = PlugController(make_cfg(), COOL, session=sess)
    pc.update(COOL, 0)     # rest
    pc.update(28.0, 1)     # warm
    pc.update(28.0, 2)     # bleach latches -> lamp blacks out
    pc.update(COOL, 3)     # cool for recovery -> heater off, fan on (lamp still off)
    pc.update(COOL, 0)     # recovered -> lamp back on
    pc.wait_idle(timeout=2.0)
    pc.close()
    assert sess.commands() == [
        "heater Off", "fan On", "lamp On",   # cold start
        "heater On", "fan Off",              # warm
        "lamp Off",                          # bleach blackout
        "heater Off", "fan On",              # recovery cooling
        "lamp On",                           # healed -> relit
    ]


# --------------------------------------------------------------- fail-soft
def test_failure_is_logged_not_raised():
    sess = FailingSession()
    pc = PlugController(make_cfg(), COOL, session=sess)
    pc.update(COOL, 0)          # every command will raise inside the worker
    pc.wait_idle(timeout=2.0)   # must not hang or crash
    pc.close()
    assert len(sess.calls) == 3  # all attempted, none fatal


def test_failure_retried_on_next_edge_only():
    """A failed command is not retried until the desired state next changes."""
    sess = FailingSession()
    pc = PlugController(make_cfg(), COOL, session=sess)
    pc.update(28.0, 1)          # warm -> heater On (fails), fan Off (fails), lamp On (fails)
    pc.update(28.0, 1)          # unchanged -> no retry despite the failures
    pc.wait_idle(timeout=2.0)
    assert len(sess.calls) == 3
    pc.update(COOL, 1)          # cool -> heater Off, fan On are fresh edges
    pc.wait_idle(timeout=2.0)
    pc.close()
    assert sess.commands()[3:] == ["heater Off", "fan On"]


# ---------------------------------------------------------------- disabled
def test_disabled_never_touches_the_network():
    sess = RecordingSession()
    pc = PlugController(make_cfg(enabled=False), COOL, session=sess)
    pc.update(28.0, 2)
    pc.update(COOL, 0)
    pc.wait_idle(timeout=0.2)
    pc.close()
    assert sess.calls == []
