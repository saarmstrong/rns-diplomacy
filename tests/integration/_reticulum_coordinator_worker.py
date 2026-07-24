"""Subprocess worker for tests/integration/test_reticulum_match_join.py.

Runs a real MatchCoordinator — the exact same class used against
InMemoryTransport everywhere else in this test suite — over a real
ReticulumTransport. Must run in its own process: RNS.Reticulum() is a
per-process singleton, and the pytest process already hosts other tests
that need one too (see test_reticulum_transport.py's docstring).

Persists to the SQLite path given on argv, so the parent test process
can inspect match state after this process exits.
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, sys.argv[1])  # repo root

from coordinator.match import MatchCoordinator  # noqa: E402
from coordinator.persistence import MatchStore  # noqa: E402
from shared.identity import Identity  # noqa: E402
from shared.reticulum_transport import ReticulumTransport  # noqa: E402


def main() -> None:
    port = int(sys.argv[2])
    match_id = sys.argv[3]
    db_path = sys.argv[4]

    store = MatchStore(db_path)
    transport = ReticulumTransport(Identity.generate(), listen=("127.0.0.1", port), configdir=sys.argv[5])
    coordinator = MatchCoordinator.create(match_id, store, transport, now=time.time())

    print(transport.local_destination, flush=True)

    deadline = time.time() + 90
    while time.time() < deadline:
        coordinator.poll()
        if store.list_players(match_id):
            break
        time.sleep(0.2)

    store.close()


if __name__ == "__main__":
    main()
