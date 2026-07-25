"""Tests for the MatchCoordinator orchestrator (coordinator/match.py)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from coordinator.match import MatchCoordinator
from coordinator.persistence import MatchStore
from coordinator.phases import InvalidTransitionError, MatchState
from engine.hashing import (
    game_state_from_canonical_bytes,
    verify_chain,
)
from engine.map import FACTIONS, create_default_map
from engine.model import GameState, Phase, Unit, UnitType
from engine.orders import HoldOrder, MoveOrder, SupportMoveOrder
from protocol.encoding import decode_message, encode_message
from protocol.messages import (
    DiscoverGame,
    GameInfo,
    JoinAccepted,
    JoinRequest,
    MatchEnd,
    MatchStart,
    MatchStatus,
    OrderReceipt,
    OrderState,
    OrderSubmit,
    PhaseResult,
    PhaseStart,
)
from shared.identity import Identity
from shared.transport import InMemoryNetwork, InMemoryTransport


class Client:
    """A minimal test double standing in for a player's client + transport."""

    def __init__(self, network: InMemoryNetwork) -> None:
        self.identity = Identity.generate()
        self.transport = InMemoryTransport(self.identity.public_bytes.hex(), network)

    def send(self, message) -> None:
        self.transport.send("coordinator", encode_message(message))

    def receive(self) -> list:
        return [decode_message(m.data) for m in self.transport.receive()]


@pytest.fixture
def network():
    return InMemoryNetwork()


@pytest.fixture
def coordinator(network, tmp_path):
    store = MatchStore(tmp_path / "match.sqlite3")
    coordinator_transport = InMemoryTransport("coordinator", network)
    coordinator = MatchCoordinator.create("match-1", store, coordinator_transport, now=1000.0)
    yield coordinator
    store.close()


def _join_all(coordinator, network, n=7) -> list[Client]:
    clients = []
    for i in range(n):
        client = Client(network)
        client.send(
            JoinRequest(
                sequence_number=1,
                timestamp=100.0 + i,
                match_id="match-1",
                player_public_key=client.identity.public_bytes,
                display_name=f"Player{i}",
            )
        )
        coordinator.poll()
        client.receive()  # drain the JOIN_ACCEPTED
        clients.append(client)
    return clients


def test_discover_game_returns_game_info(coordinator, network):
    scout = Client(network)
    scout.send(DiscoverGame(sequence_number=1, timestamp=100.0))
    coordinator.poll()
    [response] = scout.receive()
    assert isinstance(response, GameInfo)
    assert response.status is MatchStatus.LOBBY
    assert response.player_count == 0


def test_join_flow_over_transport(coordinator, network):
    client = Client(network)
    client.send(
        JoinRequest(
            sequence_number=1,
            timestamp=100.0,
            match_id="match-1",
            player_public_key=client.identity.public_bytes,
            display_name="Ambassador",
        )
    )
    coordinator.poll()
    [response] = client.receive()
    assert isinstance(response, JoinAccepted)
    decrypted = client.identity.decrypt(response.encrypted_faction)
    assert decrypted.decode("utf-8") in {f.name for f in FACTIONS.values()}


def test_start_match_broadcasts_match_start_and_phase_start(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()

    for client in clients:
        messages = client.receive()
        assert any(isinstance(m, MatchStart) for m in messages)
        assert any(isinstance(m, PhaseStart) and m.phase is Phase.ORDERS for m in messages)

    record = coordinator.store.get_match("match-1")
    assert record.state.status is MatchStatus.ACTIVE
    assert record.state.phase is Phase.ORDERS
    assert record.state.turn == 1


def test_order_submit_receives_accepted_receipt(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.receive()  # drain MATCH_START / PHASE_START

    alice = clients[0]
    faction_id = coordinator.store.get_player_faction("match-1", alice.identity.public_bytes)
    own_unit = next(u for u in create_default_map().units if u.faction_id == faction_id)
    alice.send(
        OrderSubmit(
            sequence_number=2,
            timestamp=200.0,
            match_id="match-1",
            revision=1,
            orders=(HoldOrder(unit_region_id=own_unit.region_id),),
        )
    )
    coordinator.poll()
    [receipt] = alice.receive()
    assert isinstance(receipt, OrderReceipt)
    assert receipt.state is OrderState.ACCEPTED


def test_full_turn_with_all_holds_produces_signed_chained_result(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.receive()

    for client in clients:
        faction_id = coordinator.store.get_player_faction("match-1", client.identity.public_bytes)
        units = [u for u in create_default_map().units if u.faction_id == faction_id]
        client.send(
            OrderSubmit(
                sequence_number=2,
                timestamp=200.0,
                match_id="match-1",
                revision=1,
                orders=tuple(HoldOrder(unit_region_id=u.region_id) for u in units),
            )
        )
        coordinator.poll()
        client.receive()  # drain receipt

    coordinator.advance_phase(now=300.0)

    record = coordinator.store.get_match("match-1")
    # Holding never dislodges or changes supply-center ownership, so the turn completes cleanly.
    assert record.state.phase is Phase.ORDERS
    assert record.state.turn == 2

    chain = coordinator.store.get_hash_chain("match-1")
    states = [game_state_from_canonical_bytes(r.canonical_state) for r in chain]
    hashes = [bytes.fromhex(r.state_hash) for r in chain]
    assert verify_chain(states, hashes) is True

    identity = coordinator.store.load_coordinator_identity("match-1")
    for r in chain:
        assert identity.public().verify(r.signature, r.state_hash.encode("utf-8"))

    saw_phase_result = False
    for client in clients:
        for message in client.receive():
            if isinstance(message, PhaseResult):
                saw_phase_result = True
                assert message.state_hash == chain[-1].state_hash
    assert saw_phase_result


def test_replay_protection_rejects_non_increasing_sequence_number(coordinator, network):
    client = Client(network)
    request = JoinRequest(
        sequence_number=5, timestamp=100.0, match_id="match-1", player_public_key=client.identity.public_bytes
    )
    client.send(request)
    coordinator.poll()
    [first_response] = client.receive()
    assert isinstance(first_response, JoinAccepted)

    # Resend with a sequence number that doesn't strictly increase — should be silently
    # dropped, not processed a second time.
    replay = JoinRequest(
        sequence_number=5, timestamp=101.0, match_id="match-1", player_public_key=client.identity.public_bytes,
        display_name="ShouldBeIgnored",
    )
    client.send(replay)
    coordinator.poll()
    assert client.receive() == []


def test_replay_protection_is_independent_per_sender(coordinator, network):
    alice, bob = Client(network), Client(network)
    for c in (alice, bob):
        c.send(JoinRequest(sequence_number=1, timestamp=100.0, match_id="match-1", player_public_key=c.identity.public_bytes))
        coordinator.poll()
        [response] = c.receive()
        assert isinstance(response, JoinAccepted)


def test_join_request_rate_limiting_drops_excess_attempts(coordinator, network):
    client = Client(network)
    responded = 0
    for i in range(8):
        client.send(
            JoinRequest(
                sequence_number=i + 1, timestamp=100.0 + i, match_id="match-1",
                player_public_key=client.identity.public_bytes,
            )
        )
        coordinator.poll()
        if client.receive():
            responded += 1

    # Re-joining with the same key is idempotent (JOIN_ACCEPTED again), so the first
    # _JOIN_RATE_LIMIT_MAX_ATTEMPTS (5) all get a response; the rest are dropped silently.
    assert responded == 5


def test_manual_advance_bypasses_deadline(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.receive()

    deadline_before = coordinator.store.get_deadline("match-1")
    coordinator.advance_phase(now=100.0)  # far before the real deadline — manual/dev-mode advance
    deadline_after = coordinator.store.get_deadline("match-1")
    assert deadline_after != deadline_before


def test_end_match_broadcasts_match_end_and_completes(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.receive()

    coordinator.end_match("operator abandoned the match", now=500.0)

    record = coordinator.store.get_match("match-1")
    assert record.state.status is MatchStatus.COMPLETED

    for client in clients:
        messages = client.receive()
        ends = [m for m in messages if isinstance(m, MatchEnd)]
        assert len(ends) == 1
        assert ends[0].reason == "operator abandoned the match"
        assert ends[0].final_state_hash is not None


def test_end_match_raises_when_not_active(coordinator, network):
    with pytest.raises(InvalidTransitionError):
        coordinator.end_match("too early")


def test_pause_freezes_deadline_and_resume_restores_remaining_time(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.receive()

    expires_at = coordinator.store.get_deadline("match-1")
    pause_time = expires_at - 1000.0
    coordinator.pause(now=pause_time)

    assert coordinator.store.get_deadline("match-1") is None
    assert coordinator.store.get_paused_remaining_seconds("match-1") == 1000.0

    # Advancing far past the original deadline while paused does nothing.
    coordinator.check_deadline(now=expires_at + 500.0)
    record = coordinator.store.get_match("match-1")
    assert record.state.turn == 1

    resume_time = pause_time + 100.0
    coordinator.resume(now=resume_time)
    assert coordinator.store.get_paused_remaining_seconds("match-1") is None
    assert coordinator.store.get_deadline("match-1") == resume_time + 1000.0


def test_pause_is_a_noop_without_an_active_deadline(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.receive()

    coordinator.pause(now=100.0)
    assert coordinator.store.get_paused_remaining_seconds("match-1") is not None
    coordinator.pause(now=200.0)  # already paused — should not overwrite with 0
    assert coordinator.store.get_paused_remaining_seconds("match-1") > 0


def test_resume_is_a_noop_when_not_paused(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.receive()

    expires_at = coordinator.store.get_deadline("match-1")
    coordinator.resume(now=100.0)
    assert coordinator.store.get_deadline("match-1") == expires_at


def test_check_deadline_advances_once_expired(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.receive()

    expires_at = coordinator.store.get_deadline("match-1")
    coordinator.check_deadline(now=expires_at + 1.0)

    record = coordinator.store.get_match("match-1")
    assert record.state.turn == 2


def test_check_deadline_sends_warning_once(coordinator, network):
    clients = _join_all(coordinator, network)
    coordinator.start_match()
    for client in clients:
        client.receive()

    expires_at = coordinator.store.get_deadline("match-1")
    warn_time = expires_at - 100.0
    coordinator.check_deadline(now=warn_time)
    coordinator.check_deadline(now=warn_time + 1.0)  # still in the window — should not warn again

    warning_count = 0
    for client in clients:
        for message in client.receive():
            if type(message).__name__ == "PhaseDeadlineWarning":
                warning_count += 1
    assert warning_count == len(clients)


def _seed_dislodgement_scenario(coordinator: MatchCoordinator) -> GameState:
    """A minimal 3-unit state where two vet armies combine to dislodge thr's army.

    Supply centers are trimmed to just what these three units occupy, so
    the other five (unit-less) factions don't register phantom adjustment
    deltas from their untouched home centers.
    """
    state = create_default_map()
    custom_units = [
        Unit(UnitType.ARMY, "vet", "vet_peak"),
        Unit(UnitType.ARMY, "vet", "vet_shore"),
        Unit(UnitType.ARMY, "thr", "pale_ridge"),
    ]
    custom_centers = {"vet_peak": "vet", "vet_shore": "vet"}
    scenario = replace(state, units=custom_units, supply_centers=custom_centers, phase=Phase.ORDERS, turn=1, year=1)

    identity = coordinator.store.load_coordinator_identity(coordinator.match_id)
    coordinator.store.save_match_state(coordinator.match_id, MatchState(status=MatchStatus.ACTIVE, phase=Phase.ORDERS, turn=1, year=1))
    coordinator._persist_phase_result(1, Phase.ORDERS, scenario, identity, 100.0)
    return scenario


def _add_two_players(coordinator, network) -> tuple[Client, Client]:
    vet = Client(network)
    thr = Client(network)
    coordinator.store.add_player("match-1", vet.identity.public_bytes, "vet", display_name=None, joined_at=10.0)
    coordinator.store.add_player("match-1", thr.identity.public_bytes, "thr", display_name=None, joined_at=11.0)
    return vet, thr


def test_dislodgement_enters_retreat_phase(coordinator, network):
    _seed_dislodgement_scenario(coordinator)
    vet, thr = _add_two_players(coordinator, network)

    vet.send(
        OrderSubmit(
            sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
            orders=(
                MoveOrder(unit_region_id="vet_peak", destination_id="pale_ridge"),
                SupportMoveOrder(unit_region_id="vet_shore", supported_region_id="vet_peak", supported_destination_id="pale_ridge"),
            ),
        )
    )
    coordinator.poll()
    vet.receive()

    coordinator.advance_phase(now=200.0)

    record = coordinator.store.get_match("match-1")
    assert record.state.phase is Phase.RETREAT
    assert record.state.turn == 1

    retreat_snapshot = coordinator.store.get_phase_result("match-1", 1, Phase.RETREAT)
    assert retreat_snapshot is not None
    state = game_state_from_canonical_bytes(retreat_snapshot.canonical_state)
    # The dislodged thr army had no legal retreat orders submitted, so it was disbanded by default.
    assert all(u.faction_id != "thr" for u in state.units)
    assert any(u.region_id == "pale_ridge" and u.faction_id == "vet" for u in state.units)


def test_retreat_phase_advances_to_next_turn_when_no_adjustment_needed(coordinator, network):
    _seed_dislodgement_scenario(coordinator)
    vet, thr = _add_two_players(coordinator, network)

    vet.send(
        OrderSubmit(
            sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
            orders=(
                MoveOrder(unit_region_id="vet_peak", destination_id="pale_ridge"),
                SupportMoveOrder(unit_region_id="vet_shore", supported_region_id="vet_peak", supported_destination_id="pale_ridge"),
            ),
        )
    )
    coordinator.poll()
    vet.receive()
    coordinator.advance_phase(now=200.0)  # ORDERS -> RETREAT

    record = coordinator.store.get_match("match-1")
    assert record.state.phase is Phase.RETREAT

    coordinator.advance_phase(now=300.0)  # RETREAT -> next turn's ORDERS (no adjustment: units == centers is untouched here)

    record = coordinator.store.get_match("match-1")
    assert record.state.phase is Phase.ORDERS
    assert record.state.turn == 2

    chain = coordinator.store.get_hash_chain("match-1")
    assert [r.phase for r in chain] == [Phase.ORDERS, Phase.RETREAT, Phase.ORDERS]


def test_build_phase_applies_deterministic_adjustment(coordinator, network):
    state = create_default_map()
    # vet controls an extra supply center it has no unit for — a build is owed.
    scenario = replace(
        state,
        units=[Unit(UnitType.ARMY, "vet", "vet_peak")],
        supply_centers={**state.supply_centers, "vet_shore": "vet"},
        phase=Phase.BUILD,
        turn=1,
        year=1,
    )
    identity = coordinator.store.load_coordinator_identity(coordinator.match_id)
    coordinator.store.save_match_state(coordinator.match_id, MatchState(status=MatchStatus.ACTIVE, phase=Phase.ORDERS, turn=1, year=1))
    coordinator._persist_phase_result(1, Phase.ORDERS, scenario, identity, 100.0)
    coordinator.store.save_match_state(coordinator.match_id, MatchState(status=MatchStatus.ACTIVE, phase=Phase.BUILD, turn=1, year=1))
    coordinator._persist_phase_result(1, Phase.BUILD, scenario, identity, 150.0)

    vet = Client(network)
    coordinator.store.add_player("match-1", vet.identity.public_bytes, "vet", display_name=None, joined_at=10.0)

    coordinator.advance_phase(now=200.0)

    record = coordinator.store.get_match("match-1")
    assert record.state.phase is Phase.ORDERS
    assert record.state.turn == 2

    build_result = coordinator.store.get_phase_result("match-1", 2, Phase.ORDERS)
    final_state = game_state_from_canonical_bytes(build_result.canonical_state)
    # vet owed a build but never submitted one — the deterministic default is "no build", not an error.
    assert len(final_state.units) == 1
    assert final_state.units[0].region_id == "vet_peak"
