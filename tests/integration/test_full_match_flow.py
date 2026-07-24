"""Full 7-client integration test: discover, join, negotiate, order, adjudicate, verify, draw.

Exercises the whole client + coordinator stack together over
InMemoryTransport — no unit is mocked out. This is the acceptance-level
test for "a full 7-client simulation runs end-to-end" from CLAUDE.md's
testing strategy.
"""

from __future__ import annotations

from client.identity import create_identity
from client.orders import compose_order, get_orderable_units
from client.transport import PlayerClient
from client.verification import verify_history
from coordinator.match import MatchCoordinator
from coordinator.persistence import MatchStore
from engine.map import FACTIONS, create_default_map
from engine.model import Phase
from engine.orders import OrderType
from protocol.messages import JoinAccepted, MatchStatus, OrderState
from shared.transport import InMemoryNetwork, InMemoryTransport


def _make_client(network: InMemoryNetwork, match_id: str) -> PlayerClient:
    identity = create_identity()
    transport = InMemoryTransport(identity.public_bytes.hex(), network)
    return PlayerClient(identity, transport, "coordinator", match_id)


def test_full_seven_client_match_flow(tmp_path):
    network = InMemoryNetwork()
    store = MatchStore(tmp_path / "match.sqlite3")
    coordinator_transport = InMemoryTransport("coordinator", network)
    coordinator = MatchCoordinator.create("match-1", store, coordinator_transport, now=1_000_000.0)

    try:
        # --- Discovery -----------------------------------------------------
        scout = _make_client(network, "match-1")
        scout.discover()
        coordinator.poll()
        scout.poll()
        assert scout.game_info is not None
        assert scout.game_info.status is MatchStatus.LOBBY
        assert scout.game_info.player_count == 0

        # --- Join, seven players --------------------------------------------
        clients = [_make_client(network, "match-1") for _ in range(7)]
        for i, client in enumerate(clients):
            client.join(display_name=f"Player{i}")
            coordinator.poll()
            [response] = client.poll()
            assert isinstance(response, JoinAccepted)
            assert client.faction_name in {f.name for f in FACTIONS.values()}

        assert len({c.faction_name for c in clients}) == 7  # every faction assigned exactly once

        # --- Direct player-to-player negotiation, bypassing the coordinator ---
        alice, bob = clients[0], clients[1]
        alice.send_negotiation(bob.transport.local_destination, "Non-aggression pact?")
        [received] = bob.poll()
        assert received.content == "Non-aggression pact?"
        bob.ack_negotiation(alice.transport.local_destination, received.sequence_number)
        alice.poll()
        assert alice.negotiation_acks[-1].acked_sequence_number == received.sequence_number
        assert coordinator_transport.receive() == []  # coordinator never saw any of it

        # --- Operator starts the match --------------------------------------
        coordinator.start_match()
        for client in clients:
            client.poll()
        for client in clients:
            assert client.match_status is MatchStatus.ACTIVE
            assert client.phase is Phase.ORDERS
            assert client.turn == 1

        # --- Each player composes and submits orders locally -----------------
        game_state = create_default_map()
        for client in clients:
            faction_id = coordinator.store.get_player_faction("match-1", client.identity.public_bytes)
            units = get_orderable_units(game_state, faction_id)
            orders = tuple(compose_order(u, OrderType.HOLD) for u in units)
            client.submit_orders(orders, revision=1)
            coordinator.poll()
            client.poll()
            assert client.order_receipts[-1].state is OrderState.ACCEPTED

        # --- Coordinator resolves the phase; every client verifies the result -
        coordinator.advance_phase(now=1_000_300.0)
        for client in clients:
            client.poll()

        coordinator_public = store.load_coordinator_identity("match-1").public()
        for client in clients:
            assert client.turn == 2
            assert client.phase is Phase.ORDERS
            report = verify_history(coordinator_public, client.match_start, client.phase_results)
            assert report.ok is True, report.issues

        # --- Draw proposal and unanimous vote ends the match ------------------
        clients[0].propose_draw(turn=2)
        coordinator.poll()
        for client in clients:
            client.poll()

        for client in clients[1:]:
            client.vote_draw(turn=2, vote=True)
            coordinator.poll()
            for c in clients:
                c.poll()

        for client in clients:
            assert client.draw_results
            assert client.draw_results[-1].accepted is True
            assert client.match_status is MatchStatus.COMPLETED
            assert client.match_end is not None

        record = store.get_match("match-1")
        assert record.state.status is MatchStatus.COMPLETED
    finally:
        store.close()
