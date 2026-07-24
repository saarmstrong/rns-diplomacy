"""Subprocess worker for tests/integration/test_reticulum_match_join.py.

Runs a real PlayerClient — the exact same class used against
InMemoryTransport everywhere else in this test suite — over a real
ReticulumTransport, joining a MatchCoordinator running in the parent
process. Proves the coordinator/client stack is genuinely
transport-agnostic, not just abstractly designed to be.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time

sys.path.insert(0, sys.argv[1])  # repo root

from client.identity import create_identity  # noqa: E402
from client.transport import PlayerClient  # noqa: E402
from shared.reticulum_transport import ReticulumTransport  # noqa: E402


def main() -> None:
    coordinator_port = int(sys.argv[2])
    coordinator_pub = sys.argv[3]
    match_id = sys.argv[4]

    identity = create_identity()
    transport = ReticulumTransport(
        identity, connect_to=("127.0.0.1", coordinator_port), configdir=tempfile.mkdtemp(prefix="rns-diplomacy-client-")
    )
    client = PlayerClient(identity, transport, coordinator_pub, match_id)

    client.join(display_name="RealNetworkPlayer")

    # LXMF caps delivery attempts on a single outbound message; if the underlying
    # path hasn't resolved by the time that cap is hit, the message is dropped for
    # good, not retried indefinitely. Resending JOIN_REQUEST periodically (harmless:
    # the coordinator's join handling is idempotent for a repeat request from the
    # same key) is real resilience a client should have anyway, not just a test hack.
    deadline = time.time() + 90
    next_resend = time.time() + 10
    while time.time() < deadline and client.faction_name is None and client.join_rejected_reason is None:
        client.poll()
        if time.time() > next_resend:
            client.join(display_name="RealNetworkPlayer")
            next_resend = time.time() + 10
        time.sleep(0.2)

    print(json.dumps({"faction_name": client.faction_name, "rejected_reason": client.join_rejected_reason}), flush=True)


if __name__ == "__main__":
    main()
