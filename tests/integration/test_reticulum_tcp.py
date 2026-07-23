"""Opt-in end-to-end test of ReticulumTransport over a real TCP socket.

It deliberately uses two Python processes: a single RNS process can route a
packet locally without exercising TCP. Enable with
``RUN_LIVE_RETICULUM_TESTS=1 pytest tests/integration/test_reticulum_tcp.py``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live_reticulum,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_RETICULUM_TESTS") != "1",
        reason="set RUN_LIVE_RETICULUM_TESTS=1 to run live Reticulum TCP tests",
    ),
]


_PROCESS = r'''
import sys
import time
from pathlib import Path
import RNS
from shared.identity import Identity
from shared.reticulum_transport import ReticulumTransport

role, config_dir, ready_file, received_file = sys.argv[1:]
RNS.Reticulum(configdir=config_dir, loglevel=RNS.LOG_ERROR)
transport = ReticulumTransport(Identity.generate(), "game")
if role == "server":
    Path(ready_file).write_text(transport.local_destination, encoding="ascii")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        transport.announce(b"rns-diplomacy-tcp-test")
        for message in transport.receive():
            if message.data == b"tcp-proof":
                Path(received_file).write_bytes(message.data)
                raise SystemExit(0)
        time.sleep(0.2)
    raise SystemExit("server timed out waiting for packet")

server_destination = Path(ready_file).read_text(encoding="ascii")
deadline = time.monotonic() + 12
while time.monotonic() < deadline:
    if any(a.destination == server_destination for a in transport.discover()):
        transport.send(server_destination, b"tcp-proof")
        raise SystemExit(0)
    time.sleep(0.1)
raise SystemExit("client did not receive server announce")
'''


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_rns_config(path: Path, *, server: bool, port: int) -> None:
    interface = (
        f"""[[loopback tcp]]
type = TCPServerInterface
enabled = yes
listen_ip = 127.0.0.1
listen_port = {port}
"""
        if server
        else f"""[[loopback tcp]]
type = TCPClientInterface
enabled = yes
target_host = 127.0.0.1
target_port = {port}
"""
    )
    path.mkdir()
    (path / "config").write_text(
        "[reticulum]\nenable_transport = no\nshare_instance = no\n\n[interfaces]\n" + interface,
        encoding="utf-8",
    )


def _wait_for(path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def test_reticulum_transport_delivers_over_local_tcp(tmp_path: Path) -> None:
    """A TCP client discovers a server announce then sends an opaque packet."""
    port = _free_tcp_port()
    server_config = tmp_path / "server-rns"
    client_config = tmp_path / "client-rns"
    _write_rns_config(server_config, server=True, port=port)
    _write_rns_config(client_config, server=False, port=port)
    ready, received = tmp_path / "ready", tmp_path / "received"
    environment = {**os.environ, "PYTHONPATH": str(Path.cwd())}
    server = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(_PROCESS), "server", str(server_config), str(ready), str(received)],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_for(ready, 8)
        client = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(_PROCESS), "client", str(client_config), str(ready), str(received)],
            env=environment, capture_output=True, text=True, timeout=15,
        )
        assert client.returncode == 0, client.stderr or client.stdout
        _wait_for(received, 8)
        assert received.read_bytes() == b"tcp-proof"
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
