"""Unit tests for the event plane that need no sockets: the client registry and
the OSC bundle wire format (PROTOCOL.md §1-2)."""
from pythonosc.osc_packet import OscPacket

from broadcast import ClientRegistry, _build_bundle


def test_registry_insert_refresh_prune():
    reg = ClientRegistry(timeout_s=15)
    assert reg.hello("phone1", "10.0.0.5", 9001, now=100.0) is True   # new
    assert reg.hello("phone1", "10.0.0.5", 9001, now=101.0) is False  # refresh
    assert len(reg) == 1
    assert reg.addresses() == [("10.0.0.5", 9001)]

    assert reg.prune(now=110.0) == []          # 9s < 15s: kept
    assert reg.prune(now=120.0) == ["phone1"]  # 19s > 15s: pruned
    assert len(reg) == 0


def test_registry_uses_packet_source_port():
    """Two clients on one host (localhost testing) are told apart by source port."""
    reg = ClientRegistry(timeout_s=15)
    reg.hello("a", "127.0.0.1", 5001, now=0.0)
    reg.hello("b", "127.0.0.1", 5002, now=0.0)
    assert set(reg.addresses()) == {("127.0.0.1", 5001), ("127.0.0.1", 5002)}


def _unpack(data: bytes) -> dict:
    return {t.message.address: t.message.params[0] for t in OscPacket(data).messages}


def test_bundle_addresses_and_types():
    """/coral/state and /coral/cue are int32; the rest are float32."""
    data = _build_bundle(state=2, intensity=0.5, temp=27.3, cue=1, latch=0.25)
    msgs = _unpack(data)

    assert set(msgs) == {"/coral/state", "/coral/intensity", "/coral/temp",
                         "/coral/cue", "/coral/latch"}
    assert msgs["/coral/state"] == 2 and isinstance(msgs["/coral/state"], int)
    assert msgs["/coral/cue"] == 1 and isinstance(msgs["/coral/cue"], int)
    assert isinstance(msgs["/coral/intensity"], float)
    assert msgs["/coral/intensity"] == 0.5
    assert isinstance(msgs["/coral/temp"], float)
    assert isinstance(msgs["/coral/latch"], float)
    assert msgs["/coral/latch"] == 0.25
    # float32 round-trip: within single-precision tolerance of the input
    assert abs(msgs["/coral/temp"] - 27.3) < 1e-4


def test_cue_defaults_to_state():
    """An omitted cue makes /coral/cue a copy of /coral/state, so subscribers can
    map it unconditionally whether or not quantisation is switched on."""
    msgs = _unpack(_build_bundle(state=3, intensity=0.4, temp=26.9))
    assert msgs["/coral/cue"] == 3
    assert msgs["/coral/latch"] == 0.0
