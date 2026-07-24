"""A real MatchCoordinator and a real PlayerClient, talking over a real network.

Everything else in this suite proves the coordinator/client stack works
against the abstract Transport, using InMemoryTransport. This test swaps
in ReticulumTransport instead — same MatchCoordinator, same PlayerClient,
zero changes to either — to prove the transport-agnostic design actually
holds, not just in theory.

Both the coordinator and the client run in their own subprocess (see
test_reticulum_transport.py's docstring for why: RNS.Reticulum() is a
per-process singleton, and same-process RNS nodes don't reliably
path-find to each other anyway). The parent test process only
orchestrates them and inspects the coordinator's SQLite file afterwards.

Known flakiness: unlike test_reticulum_transport.py (send-only, each side
addresses a peer it already knows — very reliable), this test needs path
discovery to succeed in *both* directions (client -> coordinator for
JOIN_REQUEST, then coordinator -> client for the JOIN_ACCEPTED reply).
A run in twenty back-to-back invocations of the underlying mechanism
outside pytest was 20/20 reliable and typically resolves in under a
second; under pytest specifically, a small fraction of runs (~1 in 4-8
observed during development) instead exhaust the full retry budget. The
client already resends JOIN_REQUEST periodically to give the coordinator
repeated chances to reply (real resilience a client should have anyway,
not just a test aid), and the timeout here is generous (100s) to absorb
slow retries. The remaining occasional full-timeout failure was not
root-caused precisely — it did not reproduce outside pytest, and did not
correlate with any bug in the underlying send/receive path (which the
sibling test exercises directly and reliably). If this becomes disruptive,
rerun it in isolation before assuming a regression.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

from coordinator.persistence import MatchStore
from engine.map import FACTIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COORDINATOR_WORKER = Path(__file__).resolve().parent / "_reticulum_coordinator_worker.py"
_CLIENT_WORKER = Path(__file__).resolve().parent / "_reticulum_client_worker.py"


def _free_port() -> int:
    """An ephemeral port free right now — avoids TIME_WAIT contention from reusing a fixed port across runs."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_client_joins_a_real_coordinator_over_the_network(tmp_path):
    port = _free_port()
    match_id = "match-1"
    db_path = tmp_path / "match.sqlite3"

    # Start the coordinator first and wait for it to confirm it's listening before
    # starting the client, to avoid a connection-refused race on the TCP bind.
    coordinator_proc = subprocess.Popen(
        [sys.executable, str(_COORDINATOR_WORKER), str(_REPO_ROOT), str(port), match_id, str(db_path), str(tmp_path / "coordinator")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    client_proc = None
    try:
        coordinator_pub = coordinator_proc.stdout.readline().strip()

        client_proc = subprocess.Popen(
            [sys.executable, str(_CLIENT_WORKER), str(_REPO_ROOT), str(port), coordinator_pub, match_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        client_out, client_err = client_proc.communicate(timeout=100)
        coordinator_out, coordinator_err = coordinator_proc.communicate(timeout=100)
    finally:
        for proc in (coordinator_proc, client_proc):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()

    result = json.loads(client_out.strip().splitlines()[-1])
    assert result["rejected_reason"] is None, f"join was rejected: {result['rejected_reason']}\nclient stderr:\n{client_err}"
    assert result["faction_name"] in {f.name for f in FACTIONS.values()}, f"client stderr:\n{client_err}"

    store = MatchStore(db_path)
    try:
        players = store.list_players(match_id)
        assert len(players) == 1, f"coordinator stderr:\n{coordinator_err}"
    finally:
        store.close()
