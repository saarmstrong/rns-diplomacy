"""Client-core flow over the in-memory transport."""

from __future__ import annotations

from client.core import PlayerClient
from coordinator.match import MatchCoordinator
from coordinator.persistence import MatchStore
from engine.map import create_default_map
from engine.orders import HoldOrder
from shared.identity import Identity
from shared.transport import InMemoryNetwork, InMemoryTransport


def test_discover_join_receive_state_and_submit_orders(tmp_path):
    network = InMemoryNetwork()
    coordinator_transport = InMemoryTransport("coordinator", network)
    store = MatchStore(tmp_path / "match.sqlite")
    coordinator = MatchCoordinator.create("test", store, coordinator_transport, now=100.0)
    # The transport destination must be the same key advertised in JOIN_REQUEST.
    clients = []
    for _ in range(7):
        identity = Identity.generate()
        clients.append(PlayerClient.create(identity, InMemoryTransport(identity.public_bytes.hex(), network)))

    for index, client in enumerate(clients):
        [(announcement, info)] = client.discover()
        client.join(announcement, info, timestamp=101.0 + index)
        coordinator.poll()
        client.poll()
        assert client.faction_name is not None

    coordinator.start_match()
    for client in clients:
        client.poll()
        assert client.game_state is not None
        assert client.current_phase is not None
        faction_id = next(f.id for f in client.game_state.factions.values() if f.name == client.faction_name)
        orders = tuple(HoldOrder(unit.region_id) for unit in create_default_map().units if unit.faction_id == faction_id)
        client.submit_orders(orders, timestamp=200.0)
        coordinator.poll()
        client.poll()
        assert client.submission.status is not None
    store.close()
