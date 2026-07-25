"""Integration tests for match-lifecycle behaviors not covered by the full 7-client flow:
multi-turn repetition, coordinator restart/recovery, deadline-driven default orders, and
the order revision/cancellation flow — all exercised through PlayerClient + MatchCoordinator
over InMemoryTransport, not just the underlying coordinator/orders.py unit functions.
"""

from __future__ import annotations

from client.identity import create_identity
from client.transport import PlayerClient
from coordinator.match import MatchCoordinator
from coordinator.persistence import MatchStore
from engine.hashing import game_state_from_canonical_bytes
from engine.map import create_default_map
from engine.model import Phase
from engine.orders import HoldOrder, MoveOrder
from engine.state import get_legal_moves
from protocol.messages import OrderState
from shared.transport import InMemoryNetwork, InMemoryTransport


def _make_client(network: InMemoryNetwork, match_id: str = "match-1") -> PlayerClient:
    identity = create_identity()
    transport = InMemoryTransport(identity.public_bytes.hex(), network)
    return PlayerClient(identity, transport, "coordinator", match_id)


def _join_all(coordinator: MatchCoordinator, network: InMemoryNetwork) -> list[PlayerClient]:
    clients = [_make_client(network) for _ in range(7)]
    for client in clients:
        client.join(display_name="Ambassador")
        coordinator.poll()
        client.poll()
    return clients


def _submit_all_holds(coordinator: MatchCoordinator, clients: list[PlayerClient], revision: int) -> None:
    game_state = create_default_map()
    for client in clients:
        faction_id = coordinator.store.get_player_faction("match-1", client.identity.public_bytes)
        units = [u for u in game_state.units if u.faction_id == faction_id]
        client.submit_orders(tuple(HoldOrder(unit_region_id=u.region_id) for u in units), revision=revision)
        coordinator.poll()
        client.poll()


def test_multi_turn_lifecycle_repeats_discover_join_negotiate_order_adjudicate(tmp_path):
    """The full cycle isn't a one-shot: turns repeat, each with its own signed, chained result."""
    network = InMemoryNetwork()
    store = MatchStore(tmp_path / "match.sqlite3")
    coordinator_transport = InMemoryTransport("coordinator", network)
    coordinator = MatchCoordinator.create("match-1", store, coordinator_transport, now=1000.0)

    try:
        clients = _join_all(coordinator, network)
        coordinator.start_match()
        for client in clients:
            client.poll()

        # Negotiation can happen every turn, independent of the coordinator.
        alice, bob = clients[0], clients[1]

        for turn in range(1, 4):
            alice.send_negotiation(bob.transport.local_destination, f"Turn {turn}: still friends?")
            [received] = bob.poll()
            assert received.content == f"Turn {turn}: still friends?"

            _submit_all_holds(coordinator, clients, revision=turn)
            coordinator.advance_phase()  # real time, so it doesn't collide with coordinator.poll()'s real-time deadline checks
            for client in clients:
                client.poll()

            for client in clients:
                assert client.turn == turn + 1
                assert client.phase is Phase.ORDERS

        chain = store.get_hash_chain("match-1")
        # start_match() seeds two links (the DIPLOMACY genesis + the turn-1 ORDERS
        # row), plus one more PHASE_RESULT per completed turn (3 turns run above).
        assert len(chain) == 2 + 3
        identity = store.load_coordinator_identity("match-1")
        for link in chain:
            assert identity.public().verify(link.signature, link.state_hash.encode("utf-8"))
    finally:
        store.close()


def test_coordinator_restart_and_recovery_mid_match(tmp_path):
    """A coordinator process can crash and restart, and pick up exactly where it left off."""
    network = InMemoryNetwork()
    db_path = tmp_path / "match.sqlite3"
    store = MatchStore(db_path)
    coordinator_transport = InMemoryTransport("coordinator", network)
    coordinator = MatchCoordinator.create("match-1", store, coordinator_transport, now=1000.0)

    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.poll()
    _submit_all_holds(coordinator, clients, revision=1)
    coordinator.advance_phase()
    for client in clients:
        client.poll()

    pre_restart_record = store.get_match("match-1")
    pre_restart_chain_len = len(store.get_hash_chain("match-1"))
    store.close()  # simulate the process exiting

    # "Restart": a fresh MatchStore + MatchCoordinator over the same database file,
    # with a fresh (but same-destination) transport, exactly as a real restarted
    # process would reconnect.
    recovered_store = MatchStore(db_path)
    try:
        recovered_transport = InMemoryTransport("coordinator", network)
        recovered_coordinator = MatchCoordinator("match-1", recovered_store, recovered_transport)

        recovered_record = recovered_store.get_match("match-1")
        assert recovered_record.state == pre_restart_record.state
        assert len(recovered_store.get_hash_chain("match-1")) == pre_restart_chain_len

        # The recovered coordinator can keep driving the match forward correctly.
        _submit_all_holds(recovered_coordinator, clients, revision=2)
        recovered_coordinator.advance_phase()
        for client in clients:
            client.poll()

        final_record = recovered_store.get_match("match-1")
        assert final_record.state.turn == 3
        chain = recovered_store.get_hash_chain("match-1")
        assert len(chain) == pre_restart_chain_len + 1
        identity = recovered_store.load_coordinator_identity("match-1")
        for link in chain:
            assert identity.public().verify(link.signature, link.state_hash.encode("utf-8"))
    finally:
        recovered_store.close()


def test_default_hold_orders_applied_on_deadline_expiry(tmp_path):
    """Players who never submit orders don't block the match — their units default to Hold."""
    network = InMemoryNetwork()
    store = MatchStore(tmp_path / "match.sqlite3")
    coordinator_transport = InMemoryTransport("coordinator", network)
    coordinator = MatchCoordinator.create("match-1", store, coordinator_transport, now=1000.0)

    try:
        clients = _join_all(coordinator, network)
        coordinator.start_match()
        for client in clients:
            client.poll()

        # Only the first 5 players submit orders; the last 2 never do.
        game_state = create_default_map()
        for client in clients[:5]:
            faction_id = coordinator.store.get_player_faction("match-1", client.identity.public_bytes)
            units = [u for u in game_state.units if u.faction_id == faction_id]
            client.submit_orders(tuple(HoldOrder(unit_region_id=u.region_id) for u in units), revision=1)
            coordinator.poll()
            client.poll()

        expires_at = coordinator.store.get_deadline("match-1")
        coordinator.check_deadline(now=expires_at + 1.0)  # deadline expiry triggers the auto-advance
        for client in clients:
            client.poll()

        record = coordinator.store.get_match("match-1")
        assert record.state.turn == 2  # the match proceeded despite two players never ordering

        # The non-submitters' units are exactly where they started (defaulted to Hold).
        result = store.get_latest_phase_result("match-1")

        final_state = game_state_from_canonical_bytes(result.canonical_state)
        non_submitter_faction_ids = {
            coordinator.store.get_player_faction("match-1", c.identity.public_bytes) for c in clients[5:]
        }
        for faction_id in non_submitter_faction_ids:
            original_regions = {u.region_id for u in game_state.units if u.faction_id == faction_id}
            final_regions = {u.region_id for u in final_state.units if u.faction_id == faction_id}
            assert final_regions == original_regions
    finally:
        store.close()


def test_order_revision_and_cancellation_flow(tmp_path):
    """A revised order supersedes the original; a cancelled order defaults that unit to Hold."""
    network = InMemoryNetwork()
    store = MatchStore(tmp_path / "match.sqlite3")
    coordinator_transport = InMemoryTransport("coordinator", network)
    coordinator = MatchCoordinator.create("match-1", store, coordinator_transport, now=1000.0)

    try:
        clients = _join_all(coordinator, network)
        coordinator.start_match()
        for client in clients:
            client.poll()

        game_state = create_default_map()
        alice = clients[0]
        alice_faction = coordinator.store.get_player_faction("match-1", alice.identity.public_bytes)
        alice_units = [u for u in game_state.units if u.faction_id == alice_faction]

        # Revision 1: hold everywhere.
        alice.submit_orders(tuple(HoldOrder(unit_region_id=u.region_id) for u in alice_units), revision=1)
        coordinator.poll()
        alice.poll()
        assert alice.order_receipts[-1].state is OrderState.ACCEPTED

        # Revision 2: revise to move the first unit somewhere legal, holding the rest.
        move_target = get_legal_moves(game_state, alice_units[0])[0]
        revised_orders = (MoveOrder(unit_region_id=alice_units[0].region_id, destination_id=move_target),) + tuple(
            HoldOrder(unit_region_id=u.region_id) for u in alice_units[1:]
        )
        alice.update_orders(revised_orders, revision=2)
        coordinator.poll()
        alice.poll()
        assert alice.order_receipts[-1].state is OrderState.ACCEPTED

        revisions = coordinator.store.get_order_revisions("match-1", alice.identity.public_bytes, turn=1)
        assert [(r.revision, r.status) for r in revisions] == [(1, "superseded"), (2, "accepted")]

        # Bob cancels entirely — his units should default to Hold.
        bob = clients[1]
        bob_faction = coordinator.store.get_player_faction("match-1", bob.identity.public_bytes)
        bob_units = [u for u in game_state.units if u.faction_id == bob_faction]
        bob.submit_orders(tuple(HoldOrder(unit_region_id=u.region_id) for u in bob_units), revision=1)
        coordinator.poll()
        bob.poll()
        bob.cancel_orders(revision=2)
        coordinator.poll()
        bob.poll()
        assert bob.order_receipts[-1].state is OrderState.SUPERSEDED

        # Everyone else just holds.
        for client in clients[2:]:
            faction_id = coordinator.store.get_player_faction("match-1", client.identity.public_bytes)
            units = [u for u in game_state.units if u.faction_id == faction_id]
            client.submit_orders(tuple(HoldOrder(unit_region_id=u.region_id) for u in units), revision=1)
            coordinator.poll()
            client.poll()

        coordinator.advance_phase()
        for client in clients:
            client.poll()

        result = store.get_latest_phase_result("match-1")

        final_state = game_state_from_canonical_bytes(result.canonical_state)
        # Alice's revised move actually took effect.
        assert any(u.region_id == move_target and u.faction_id == alice_faction for u in final_state.units)
        # Bob's units are untouched (cancelled -> defaulted to Hold, same as never submitting).
        bob_final_regions = {u.region_id for u in final_state.units if u.faction_id == bob_faction}
        assert bob_final_regions == {u.region_id for u in bob_units}
    finally:
        store.close()
