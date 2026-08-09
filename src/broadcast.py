"""Event plane — OSC broadcast and the client registry (PROTOCOL.md §1-2).

The server hands this module a (state, intensity, temp) triple 5×/second; it
fans that out as one OSC bundle to every static subscriber (Ableton, projection)
and every registered AR client. The same UDP socket also receives the inbound
plane: ``/client/hello`` registrations, and — for the no-hardware harness — the
``/sim/warm`` / ``/sim/cool`` button events fake_rig sends in place of the (yet
to be wired) arcade encoder.

The broadcast is simultaneously the data plane and the heartbeat: a lost datagram
is corrected 200 ms later, and a client that (re)joins converges within one
interval. There is no separate keep-alive anywhere in the system.
"""
from __future__ import annotations

import socket

from pythonosc.osc_bundle_builder import IMMEDIATELY, OscBundleBuilder
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_packet import OscPacket

HELLO_ADDRESS = "/client/hello"


class ClientRegistry:
    """Application-layer service discovery for transient AR devices.

    Keyed by client id. Each entry stores the ``(ip, port)`` the ``hello`` packet
    arrived from and the time it was last seen. Taking the *port* from the packet
    source (not just the IP) is what lets many clients share one host during
    localhost testing, and is correct for real phones too as long as they send
    ``hello`` from the socket they also listen on.
    """

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = float(timeout_s)
        self._clients: dict[str, tuple[str, int, float]] = {}

    def hello(self, client_id: str, ip: str, port: int, now: float) -> bool:
        """Insert or refresh a client. Returns True if this is a new registration."""
        is_new = client_id not in self._clients
        self._clients[client_id] = (ip, port, now)
        return is_new

    def prune(self, now: float) -> list[str]:
        """Drop entries silent for longer than ``timeout_s``. Returns pruned ids."""
        dead = [cid for cid, (_, _, seen) in self._clients.items()
                if now - seen > self.timeout_s]
        for cid in dead:
            del self._clients[cid]
        return dead

    def addresses(self) -> list[tuple[str, int]]:
        return [(ip, port) for (ip, port, _) in self._clients.values()]

    def __len__(self) -> int:
        return len(self._clients)


class Broadcaster:
    """Owns the OSC UDP socket: sends the fan-out bundle, receives the inbound plane."""

    def __init__(self, cfg_broadcast: dict) -> None:
        self.bind_host = cfg_broadcast["bind_host"]          # 0.0.0.0, never 127.0.0.1
        self.listen_port = int(cfg_broadcast["listen_port"])
        self.static = [
            (s["host"], int(s["port"]), s.get("name", "?"))
            for s in cfg_broadcast.get("static_subscribers", [])
        ]
        self.registry = ClientRegistry(cfg_broadcast["client_timeout_s"])

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((self.bind_host, self.listen_port))
        except OSError as exc:
            raise RuntimeError(
                f"cannot bind OSC listener on {self.bind_host}:{self.listen_port} "
                f"({exc}). Is another server already running?"
            ) from exc
        self._sock.setblocking(False)

    def poll_inbound(self, now: float) -> tuple[list[tuple[str, list]], list[str]]:
        """Drain all pending inbound datagrams.

        Consumes ``/client/hello`` into the registry; returns any other messages as
        ``(address, params)`` extras for the server to interpret (the harness button
        events), plus the ids of newly-registered clients. Malformed packets are
        ignored — a bad datagram must never disturb the loop.
        """
        extras: list[tuple[str, list]] = []
        newly: list[str] = []
        while True:
            try:
                data, addr = self._sock.recvfrom(4096)
            except (BlockingIOError, OSError):
                break
            ip, port = addr
            try:
                packet = OscPacket(data)
            except Exception:
                continue
            for timed in packet.messages:
                msg = timed.message
                if msg.address == HELLO_ADDRESS:
                    cid = str(msg.params[0]) if msg.params else ip
                    if self.registry.hello(cid, ip, port, now):
                        newly.append(cid)
                else:
                    extras.append((msg.address, list(msg.params)))
        return extras, newly

    def emit(self, state: int, intensity: float, temp: float,
             bed: int | None = None, latch: float = 0.0) -> None:
        """Send one bundle to every static subscriber and every registered client."""
        dgram = _build_bundle(state, intensity, temp, bed, latch)
        targets = [(host, port) for (host, port, _) in self.static]
        targets += self.registry.addresses()
        for target in targets:
            try:
                self._sock.sendto(dgram, target)
            except OSError:
                pass  # a dead subscriber must not stall the fan-out

    def close(self) -> None:
        self._sock.close()


def _build_bundle(state: int, intensity: float, temp: float,
                  bed: int | None = None, latch: float = 0.0) -> bytes:
    """Build the canonical OSC bundle (PROTOCOL.md §1).

    ``bed`` is the bar-quantised twin of ``state`` used by the soundscape. It
    defaults to ``state``, so the address is always present and a subscriber can
    map it unconditionally whether or not quantisation is switched on.

    ``latch`` is progress toward the bleach latch — the one forward-looking value
    in the protocol.
    """
    bundle = OscBundleBuilder(IMMEDIATELY)
    bundle.add_content(_message("/coral/state", int(state)))        # int32
    bundle.add_content(_message("/coral/intensity", float(intensity)))  # float32
    bundle.add_content(_message("/coral/temp", float(temp)))        # float32
    bundle.add_content(_message("/coral/bed", int(state if bed is None else bed)))  # int32
    bundle.add_content(_message("/coral/latch", float(latch)))      # float32
    return bundle.build().dgram


def _message(address: str, value):
    builder = OscMessageBuilder(address=address)
    builder.add_arg(value)  # int -> 'i' (int32), float -> 'f' (float32)
    return builder.build()
