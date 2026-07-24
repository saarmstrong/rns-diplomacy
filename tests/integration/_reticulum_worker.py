"""Subprocess worker for tests/integration/test_reticulum_transport.py.

Both sides of that test run this script in their own process — see the
test module's docstring for why (RNS.Reticulum() is a process-wide
singleton, and same-process nodes don't reliably path-find to each
other anyway).

Protocol with the parent, over stdio:
  1. Print {"pub": "<own pubkey hex>"} and flush.
  2. Read one line of stdin: {"peer_pub": "<other side's pubkey hex>"}.
  3. Announce, then send a DISCOVER_GAME message to the peer.
  4. Poll for up to 25s for a message and an announcement to arrive.
  5. Print {"received_message_type": "...", "announced": [...]} and exit.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time

sys.path.insert(0, str(sys.argv[1]))  # repo root, so `shared`/`protocol` import

from protocol.encoding import decode_message, encode_message  # noqa: E402
from protocol.messages import DiscoverGame  # noqa: E402
from shared.identity import Identity  # noqa: E402
from shared.reticulum_transport import ReticulumTransport  # noqa: E402


def main() -> None:
    role = sys.argv[2]
    port = int(sys.argv[3])

    identity = Identity.generate()
    configdir = tempfile.mkdtemp(prefix="rns-diplomacy-worker-")
    if role == "server":
        transport = ReticulumTransport(identity, listen=("127.0.0.1", port), configdir=configdir)
    else:
        transport = ReticulumTransport(identity, connect_to=("127.0.0.1", port), configdir=configdir)

    print(json.dumps({"pub": transport.local_destination}), flush=True)

    line = sys.stdin.readline()
    peer_pub = json.loads(line)["peer_pub"]

    transport.announce(data=f"{role}-announce".encode())
    transport.send(peer_pub, encode_message(DiscoverGame(sequence_number=1, timestamp=time.time())))

    deadline = time.time() + 55
    received_type = None
    announced = []
    while time.time() < deadline and (received_type is None or not announced):
        for inbound in transport.receive():
            message = decode_message(inbound.data)
            received_type = message.message_type.value
        announced.extend(a.data for a in transport.discover())
        time.sleep(0.2)

    print(json.dumps({"received_message_type": received_type, "announced": [a.decode() for a in announced]}), flush=True)


if __name__ == "__main__":
    main()
