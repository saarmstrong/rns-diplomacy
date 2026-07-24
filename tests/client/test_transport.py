"""Tests for client/transport.py's PlayerClient, exercised against a real MatchCoordinator."""

from __future__ import annotations

import pytest

from client.identity import create_identity
from client.transport import PlayerClient, listen_for_announces
from coordinator.match import MatchCoordinator
from coordinator.persistence import MatchStore
from engine.map import FACTIONS, create_default_map
from engine.model import Phase
from engine.orders import HoldOrder
from protocol.messages import MatchStatus, OrderState
from shared.transport import InboundMessage, InMemoryNetwork, InMemoryTransport


@pytest.fixture
def network():
    return InMemoryNetwork()


@pytest.fixture
def coordinator(network, tmp_path):
    store = MatchStore(tmp_path / "match.sqlite3")
    coordinator_transport = InMemoryTransport("coordinator", network)
    coord = MatchCoordinator.create("match-1", store, coordinator_transport, now=1000.0)
    yield coord
    store.close()


def _make_client(network, match_id="match-1") -> PlayerClient:
    identity = create_identity()
    transport = InMemoryTransport(identity.public_bytes.hex(), network)
    return PlayerClient(identity, transport, "coordinator", match_id)


def _join_all(coordinator, network, n=7) -> list[PlayerClient]:
    clients = []
    for _ in range(n):
        client = _make_client(network)
        client.join(display_name="Ambassador")
        coordinator.poll()
        client.poll()
        clients.append(client)
    return clients


def test_discover_populates_game_info(coordinator, network):
    client = _make_client(network)
    client.discover()
    coordinator.poll()
    processed = client.poll()

    assert client.game_info is not None
    assert client.game_info.status is MatchStatus.LOBBY
    assert client.game_info.player_count == 0
    assert len(processed) == 1


def test_listen_for_announces_finds_an_announced_match(coordinator, network):
    scout_transport = InMemoryTransport("scout", network)

    coordinator.announce()
    found = listen_for_announces(scout_transport)

    assert found == ["match-1"]
    # A second poll with no new announcement finds nothing further.
    assert listen_for_announces(scout_transport) == []


def test_join_decrypts_faction_assignment(coordinator, network):
    client = _make_client(network)
    client.join(display_name="Ambassador")
    coordinator.poll()
    client.poll()

    assert client.faction_name in {f.name for f in FACTIONS.values()}
    assert len(client.faction_names) == 7
    assert client.join_rejected_reason is None
    assert client.match_parameters["max_players"] == 7


def test_join_rejected_when_match_full(coordinator, network):
    _join_all(coordinator, network)

    latecomer = _make_client(network)
    latecomer.join()
    coordinator.poll()
    latecomer.poll()

    assert latecomer.faction_name is None
    assert latecomer.join_rejected_reason == "match full"


def test_start_match_updates_client_phase_and_deadline(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.poll()

    for client in clients:
        assert client.match_status is MatchStatus.ACTIVE
        assert client.match_start is not None
        assert client.phase is Phase.ORDERS
        assert client.turn == 1
        assert client.deadline is not None


def test_submit_orders_receives_accepted_receipt(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.poll()

    alice = clients[0]
    faction_id = coordinator.store.get_player_faction("match-1", alice.identity.public_bytes)
    own_unit = next(u for u in create_default_map().units if u.faction_id == faction_id)

    alice.submit_orders((HoldOrder(unit_region_id=own_unit.region_id),), revision=1)
    coordinator.poll()
    alice.poll()

    assert len(alice.order_receipts) == 1
    assert alice.order_receipts[-1].state is OrderState.ACCEPTED


def test_full_turn_all_holds_client_receives_phase_result(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.poll()

    for client in clients:
        faction_id = coordinator.store.get_player_faction("match-1", client.identity.public_bytes)
        units = [u for u in create_default_map().units if u.faction_id == faction_id]
        client.submit_orders(tuple(HoldOrder(unit_region_id=u.region_id) for u in units), revision=1)
        coordinator.poll()
        client.poll()

    coordinator.advance_phase(now=300.0)
    for client in clients:
        client.poll()

    identity = coordinator.store.load_coordinator_identity("match-1")
    for client in clients:
        # One PHASE_RESULT for the turn-1 seed link (broadcast at start_match), one for
        # the actual turn-1 -> turn-2 resolution.
        assert len(client.phase_results) == 2
        result = client.phase_results[-1]
        assert client.phase is Phase.ORDERS
        assert client.turn == 2
        assert identity.public().verify(result.signature, result.state_hash.encode("utf-8"))


def test_draw_propose_and_vote_produce_draw_result(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.poll()

    clients[0].propose_draw(turn=1)
    coordinator.poll()
    for client in clients:
        client.poll()

    for client in clients[1:]:
        client.vote_draw(turn=1, vote=True)
        coordinator.poll()
        for c in clients:
            c.poll()

    for client in clients:
        assert client.draw_results
        assert client.draw_results[-1].accepted is True
    assert clients[0].match_end is not None
    assert clients[0].match_status is MatchStatus.COMPLETED


def test_state_request_returns_state_response(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.poll()

    alice = clients[0]
    alice.request_state()
    coordinator.poll()
    alice.poll()

    assert len(alice.state_responses) == 1
    assert alice.state_responses[0].turn == 1


def test_malformed_bytes_do_not_crash_poll(coordinator, network):
    client = _make_client(network)
    garbage = InboundMessage(sender="coordinator", data=b"\xff\xff\xff")
    client.transport.network.deliver(client.transport.local_destination, garbage)
    processed = client.poll()
    assert processed == []
