"""End-to-end tests over real UDP/OSC against the actual server.py process.

Each server runs from an isolated, fast config on a free port (via CORAL_CONFIG),
so these are independent of the developer's local config.yaml and of any server
they may have running. Covers the wire contract, registration, the warm arc,
pruning, and startup-order independence.
"""
import os
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import yaml
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_packet import OscPacket

from config import EXAMPLE_CONFIG, REPO_ROOT

SERVER_PY = REPO_ROOT / "src" / "server.py"


# --------------------------------------------------------------------- helpers
def osc(address, *args):
    builder = OscMessageBuilder(address=address)
    for arg in args:
        builder.add_arg(arg)
    return builder.build().dgram


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for(pred, timeout=6.0, interval=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False


def start_server(tmp_path, **sim_overrides):
    """Launch server.py on a free port with a fast, isolated config.

    Returns a namespace with .port, .lines (captured stdout), and .stop().
    """
    cfg = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    port = free_port()
    cfg["broadcast"]["listen_port"] = port
    cfg["broadcast"]["client_timeout_s"] = 2          # prune quickly
    cfg["temperature"]["simulated"]["heat_rate"] = 0.3  # reach state 1 in ~1 s
    cfg["temperature"]["simulated"].update(sim_overrides)
    cfg["logging"]["dir"] = str(tmp_path / "logs")
    cfg["logging"]["log_temperature_samples"] = False
    cfg_path = tmp_path / "server-config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))

    env = dict(os.environ, CORAL_CONFIG=str(cfg_path))
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PY)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    lines: list[str] = []
    threading.Thread(
        target=lambda: lines.extend(iter(proc.stdout.readline, "")), daemon=True
    ).start()

    def stop():
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    srv = SimpleNamespace(proc=proc, port=port, lines=lines, stop=stop)
    # Ready once the OSC listener is bound (logged right after bind succeeds).
    assert wait_for(lambda: any("mode=" in ln for ln in lines)), "server did not start"
    return srv


def client_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.setblocking(False)
    return sock


def collect_snapshots(sock, seconds):
    """Gather {address: value} dicts from broadcast bundles for `seconds`."""
    snaps = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            data, _ = sock.recvfrom(4096)
        except (BlockingIOError, OSError):
            time.sleep(0.01)
            continue
        snap = {t.message.address: t.message.params[0] for t in OscPacket(data).messages}
        if snap:
            snaps.append(snap)
    return snaps


@pytest.fixture
def server(tmp_path):
    srv = start_server(tmp_path)
    yield srv
    srv.stop()


# ----------------------------------------------------------------------- tests
def test_broadcast_contract_and_registration(server):
    sock = client_socket()
    target = ("127.0.0.1", server.port)
    sock.sendto(osc("/client/hello", "probe"), target)

    assert wait_for(lambda: any("CLIENT registered" in l and "probe" in l for l in server.lines))

    snaps = collect_snapshots(sock, 1.5)
    assert snaps, "no broadcasts received"
    last = snaps[-1]
    assert set(last) == {"/coral/state", "/coral/intensity", "/coral/temp",
                         "/coral/bed", "/coral/latch"}
    assert isinstance(last["/coral/state"], int)
    assert isinstance(last["/coral/intensity"], float)
    assert isinstance(last["/coral/temp"], float)
    assert isinstance(last["/coral/bed"], int)
    assert isinstance(last["/coral/latch"], float)
    assert last["/coral/state"] == 0          # cold start
    assert last["/coral/bed"] == 0            # quantise disabled -> bed tracks state
    assert last["/coral/latch"] == 0.0        # nothing pending
    assert 25.5 < last["/coral/temp"] < 26.5
    sock.close()


def test_warm_command_drives_the_arc(server):
    sock = client_socket()
    target = ("127.0.0.1", server.port)
    sock.sendto(osc("/client/hello", "probe"), target)
    wait_for(lambda: any("CLIENT registered" in l for l in server.lines))

    reached_state1 = False
    last = None
    end = time.time() + 8
    while time.time() < end and not reached_state1:
        sock.sendto(osc("/sim/warm"), target)
        for snap in collect_snapshots(sock, 0.4):
            last = snap
            reached_state1 = reached_state1 or snap["/coral/state"] == 1
    assert last is not None
    assert reached_state1
    assert last["/coral/temp"] > 26.2      # crossed the rise threshold
    assert last["/coral/intensity"] > 0.05  # and intensity followed it up
    sock.close()


def test_silent_client_is_pruned(server):
    sock = client_socket()
    target = ("127.0.0.1", server.port)
    sock.sendto(osc("/client/hello", "probe"), target)
    assert wait_for(lambda: any("CLIENT registered" in l and "probe" in l for l in server.lines))

    # Go silent; client_timeout_s is 2 in the test config.
    assert wait_for(
        lambda: any("CLIENT pruned" in l and "probe" in l for l in server.lines),
        timeout=6.0,
    )
    sock.close()


def test_startup_order_independence(tmp_path):
    """A client already sending hello converges once the server appears."""
    port_holder = {}
    sock = client_socket()
    stop = threading.Event()

    # Start hello-ing to a port that isn't listening yet.
    provisional_port = free_port()

    def hello_loop():
        while not stop.is_set():
            try:
                sock.sendto(osc("/client/hello", "early"), ("127.0.0.1", port_holder.get("p", provisional_port)))
            except OSError:
                pass
            time.sleep(0.5)

    threading.Thread(target=hello_loop, daemon=True).start()
    time.sleep(1.0)  # client is talking to a dead port

    srv = start_server(tmp_path)
    port_holder["p"] = srv.port  # redirect the client to the real port
    try:
        assert wait_for(
            lambda: any("CLIENT registered" in l and "early" in l for l in srv.lines),
            timeout=6.0,
        )
        # And it now receives broadcasts.
        assert collect_snapshots(sock, 1.0), "did not converge after server start"
    finally:
        stop.set()
        srv.stop()
        sock.close()
