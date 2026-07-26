"""Unit tests for the ingestion seam (PROTOCOL.md §4, src/temperature.py).

These need no network and no hardware: a recording fake stands in for the HTTP
session, and RealSource is built with ``start_polling=False`` so its parse/hold/
fail-soft policy is driven deterministically instead of racing the poll thread. One
test does exercise the live thread against a fake session to prove read() converges
and close() stops cleanly. SimulatedSource is checked for the drift direction the
whole state machine assumes.
"""
import math
import time

import pytest
import requests

from temperature import RealSource, SimulatedSource, make_source


# --------------------------------------------------------------- fake sessions
class FakeResponse:
    def __init__(self, text='{"id":"sensor-water_temperature","value":26.4,"state":"26.4 °C"}',
                 ok=True) -> None:
        self.text = text
        self._ok = ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise requests.HTTPError("500 Server Error")


class FlakySession:
    """Serves a Tasmota/ESPHome-shaped reply; flip ``fail`` to simulate an outage."""

    def __init__(self, text=FakeResponse().text) -> None:
        self.text = text
        self.fail = False
        self.ok = True
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        if self.fail:
            raise requests.ConnectionError("sensor unreachable")
        return FakeResponse(self.text, ok=self.ok)

    def close(self) -> None:
        pass


def make_real(session, **over) -> RealSource:
    cfg = {"sensor_url": "http://sensor/sensor/water_temperature",
           "poll_hz": 2, "timeout_s": 0.5, "start_temp": 26.0}
    cfg.update(over)
    return RealSource(cfg, session=session, start_polling=False)


# ------------------------------------------------------------------ _parse
def test_parse_reads_the_value_field():
    assert RealSource._parse('{"id":"x","value":27.35,"state":"27.35 °C"}') == 27.35


def test_parse_rejects_malformed_replies():
    with pytest.raises(ValueError):          # not JSON at all
        RealSource._parse("<html>oops</html>")
    with pytest.raises(KeyError):            # no value field
        RealSource._parse('{"state":"26 °C"}')
    with pytest.raises(TypeError):           # disconnected probe -> null
        RealSource._parse('{"value":null}')
    with pytest.raises(ValueError):          # bad probe -> NaN
        RealSource._parse('{"value":NaN}')


# ------------------------------------------------------------ read / poll
def test_reads_start_temp_before_first_poll():
    src = make_real(FlakySession(), start_temp=25.5)
    assert src.read(0.0) == 25.5  # boots at the natural anchor -> state 0


def test_poll_updates_the_cached_reading():
    src = make_real(FlakySession())
    assert src._poll_once() is True
    assert src.read(0.0) == 26.4


def test_failure_holds_last_reading_and_never_raises():
    sess = FlakySession()
    src = make_real(sess)
    src._poll_once()                 # good: cache -> 26.4
    sess.fail = True
    assert src._poll_once() is False  # outage: soft-failed, not raised
    assert src.read(0.0) == 26.4      # last good reading held (PROTOCOL.md §4)


def test_http_error_status_is_soft_failed():
    sess = FlakySession()
    sess.ok = False                   # every reply raises_for_status()
    src = make_real(sess)
    assert src._poll_once() is False
    assert src.read(0.0) == 26.0      # still the seed; nothing bad written


def test_failure_then_recovery_logs_once_each(caplog):
    sess = FlakySession()
    src = make_real(sess)
    src._poll_once()                  # healthy
    sess.fail = True
    with caplog.at_level("INFO", logger="coral"):
        src._poll_once()              # first failure -> one WARNING
        src._poll_once()              # still failing -> silent (de-duplicated)
        sess.fail = False
        src._poll_once()              # back -> one INFO "recovered"
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    recovered = [r for r in caplog.records if "recovered" in r.getMessage()]
    assert len(warnings) == 1
    assert len(recovered) == 1


# ------------------------------------------------------------ live thread
def test_poll_thread_converges_and_close_stops_it():
    sess = FlakySession()
    src = RealSource(
        {"sensor_url": "http://sensor/x", "poll_hz": 50, "timeout_s": 0.5,
         "start_temp": 20.0},
        session=sess,
    )
    try:
        deadline = time.monotonic() + 2.0
        while src.read(0.0) == 20.0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert src.read(0.0) == 26.4  # the thread polled and cached the reading
    finally:
        src.close()
    assert not (src._thread and src._thread.is_alive())


# ------------------------------------------------------------ ingestion guard
def test_non_http_ingestion_is_a_clear_error():
    with pytest.raises(NotImplementedError):
        RealSource({"sensor_url": "x", "ingestion": "mqtt"}, start_polling=False)


# ------------------------------------------------------------ make_source
def test_make_source_dispatches_on_mode():
    sim = make_source({"mode": "simulated",
                       "simulated": {"start_temp": 26.0, "heat_rate": 0.02,
                                     "cool_rate": 0.01, "noise": 0.0}},
                      get_target=lambda: 28.0)
    assert isinstance(sim, SimulatedSource)

    real = make_source({"mode": "real",
                        "real": {"sensor_url": "http://127.0.0.1:9/x", "poll_hz": 1,
                                 "timeout_s": 0.2, "start_temp": 26.0}},
                       get_target=lambda: 26.0)
    try:
        assert isinstance(real, RealSource)
    finally:
        real.close()

    with pytest.raises(ValueError):
        make_source({"mode": "bogus"}, get_target=lambda: 26.0)


# ------------------------------------------------------------ simulated drift
def test_simulated_drifts_toward_target():
    target = {"v": 28.0}
    sim = SimulatedSource({"start_temp": 26.0, "heat_rate": 1.0, "cool_rate": 1.0,
                           "noise": 0.0}, get_target=lambda: target["v"])
    warmed = sim.read(0.5)            # +0.5°C toward 28
    assert math.isclose(warmed, 26.5)
    target["v"] = 26.0
    cooled = sim.read(0.25)           # -0.25°C toward 26
    assert math.isclose(cooled, 26.25)


def test_simulated_close_is_a_noop():
    sim = SimulatedSource({"start_temp": 26.0, "heat_rate": 1.0, "cool_rate": 1.0,
                           "noise": 0.0}, get_target=lambda: 26.0)
    sim.close()  # inherited no-op; must not raise
