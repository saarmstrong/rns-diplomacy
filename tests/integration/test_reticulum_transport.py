"""Real cross-process TCP/IP test for ReticulumTransport.

Two separate OS processes, each running its own Reticulum instance,
exchange a real protocol message and an announcement over an actual TCP
loopback connection. Both sides run as subprocesses — not just the
"other" side — for two independent reasons:

1. Two identities sharing a single Reticulum/Transport instance don't
   reliably path-find to each other (confirmed while building
   shared/reticulum_transport.py) — a degenerate case real deployments
   never hit, since two real participants are always separate processes.
2. ``RNS.Reticulum()`` is a hard per-process singleton — a second call
   in the same process raises ``OSError: Attempt to reinitialise
   Reticulum``. Since this test suite runs many tests in one pytest
   process, and another test (test_reticulum_match_join.py) also needs
   its own Reticulum instance, the pytest process itself must never
   construct one — every RNS-touching side of every test here has to be
   a subprocess.

Slower than the rest of the suite (RNS's path-discovery/retry cycle runs
on multi-second intervals), but it's the one test that actually proves
the real transport works, not just the abstraction it implements.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKER = Path(__file__).resolve().parent / "_reticulum_worker.py"


def _free_port() -> int:
    """An ephemeral port free right now — avoids TIME_WAIT contention from reusing a fixed port across runs."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_reticulum_transport_send_receive_announce_over_real_tcp():
    port = _free_port()

    # Start the server first and wait for it to confirm it's listening (it only prints
    # once ReticulumTransport's TCPServerInterface is up) before starting the client —
    # otherwise the client's connection attempt can race the server's bind and fail.
    server = subprocess.Popen(
        [sys.executable, str(_WORKER), str(_REPO_ROOT), "server", str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    client = None
    try:
        server_pub = json.loads(server.stdout.readline())["pub"]

        client = subprocess.Popen(
            [sys.executable, str(_WORKER), str(_REPO_ROOT), "client", str(port)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        client_pub = json.loads(client.stdout.readline())["pub"]

        server.stdin.write(json.dumps({"peer_pub": client_pub}) + "\n")
        server.stdin.flush()
        client.stdin.write(json.dumps({"peer_pub": server_pub}) + "\n")
        client.stdin.flush()

        server_out, server_err = server.communicate(timeout=60)
        client_out, client_err = client.communicate(timeout=60)
    finally:
        for proc in (server, client):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()

    server_result = json.loads(server_out.strip().splitlines()[-1])
    client_result = json.loads(client_out.strip().splitlines()[-1])

    assert server_result["received_message_type"] == "DISCOVER_GAME", f"server stderr:\n{server_err}"
    assert client_result["received_message_type"] == "DISCOVER_GAME", f"client stderr:\n{client_err}"
    # Each side's announce should reach the *other* side.
    assert "client-announce" in server_result["announced"]
    assert "server-announce" in client_result["announced"]
