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


def test_bundle_addresses_and_types():
    """/coral/state is int32; /coral/intensity and /coral/temp are float32."""
    data = _build_bundle(state=2, intensity=0.5, temp=27.3)
    msgs = {t.message.address: t.message.params[0] for t in OscPacket(data).messages}

    assert set(msgs) == {"/coral/state", "/coral/intensity", "/coral/temp"}
    assert msgs["/coral/state"] == 2 and isinstance(msgs["/coral/state"], int)
    assert isinstance(msgs["/coral/intensity"], float)
    assert msgs["/coral/intensity"] == 0.5
    assert isinstance(msgs["/coral/temp"], float)
    # float32 round-trip: within single-precision tolerance of the input
    assert abs(msgs["/coral/temp"] - 27.3) < 1e-4
