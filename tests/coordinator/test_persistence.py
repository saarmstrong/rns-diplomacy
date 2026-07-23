"""Round-trip and restart-recovery tests for coordinator/persistence.py."""

from __future__ import annotations

import sqlite3

import pytest

from coordinator.persistence import MatchStore
from coordinator.phases import MatchState, advance_phase, start_match
from engine.model import Phase
from protocol.messages import MatchStatus
from shared.identity import Identity


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "match.sqlite3"
    match_store = MatchStore(db_path)
    yield match_store
    match_store.close()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "match.sqlite3"


def test_create_and_get_match(store: MatchStore) -> None:
    identity = Identity.generate()
    store.create_match("match-1", identity, created_at=1000.0)

    record = store.get_match("match-1")
    assert record is not None
    assert record.match_id == "match-1"
    assert record.state == MatchState()
    assert record.created_at == 1000.0


def test_get_match_returns_none_for_unknown_match(store: MatchStore) -> None:
    assert store.get_match("no-such-match") is None


def test_load_coordinator_identity_round_trips(store: MatchStore) -> None:
    identity = Identity.generate()
    store.create_match("match-1", identity, created_at=1000.0)

    loaded = store.load_coordinator_identity("match-1")
    assert loaded.public_bytes == identity.public_bytes
    signature = loaded.sign(b"phase result hash")
    assert identity.public().verify(signature, b"phase result hash")


def test_save_match_state_updates_status_and_phase(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    new_state = advance_phase(start_match(MatchState()))
    store.save_match_state("match-1", new_state)

    record = store.get_match("match-1")
    assert record.state == new_state
    assert record.state.status is MatchStatus.ACTIVE
    assert record.state.phase is Phase.ORDERS


def test_save_match_state_unknown_match_raises() -> None:
    with MatchStore(":memory:") as store:
        with pytest.raises(KeyError):
            store.save_match_state("no-such-match", MatchState())


def test_deadline_round_trip(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    assert store.get_deadline("match-1") is None

    store.set_deadline("match-1", 1086400.0)
    assert store.get_deadline("match-1") == 1086400.0

    store.set_deadline("match-1", None)
    assert store.get_deadline("match-1") is None


def test_add_and_list_players(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    alice = Identity.generate().public_bytes
    bob = Identity.generate().public_bytes

    store.add_player("match-1", alice, "vet", display_name="Alice", joined_at=10.0)
    store.add_player("match-1", bob, "thr", display_name=None, joined_at=20.0)

    players = store.list_players("match-1")
    assert [p.faction_id for p in players] == ["vet", "thr"]
    assert players[0].public_key == alice
    assert players[0].display_name == "Alice"
    assert players[1].display_name is None

    assert store.get_player_faction("match-1", alice) == "vet"
    assert store.get_player_faction("match-1", b"unknown-key") is None


def test_add_player_duplicate_raises_integrity_error(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    alice = Identity.generate().public_bytes
    store.add_player("match-1", alice, "vet", display_name="Alice", joined_at=10.0)

    with pytest.raises(sqlite3.IntegrityError):
        store.add_player("match-1", alice, "thr", display_name="Alice2", joined_at=11.0)


def test_order_submission_and_latest_lookup(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    alice = Identity.generate().public_bytes

    store.record_order_submission(
        "match-1", alice, turn=1, revision=1, orders_blob=b"orders-v1", order_hash="hash1",
        status="accepted", submitted_at=10.0,
    )
    store.record_order_submission(
        "match-1", alice, turn=1, revision=2, orders_blob=b"orders-v2", order_hash="hash2",
        status="accepted", submitted_at=20.0,
    )

    latest = store.get_latest_order("match-1", alice, turn=1)
    assert latest is not None
    assert latest.revision == 2
    assert latest.orders_blob == b"orders-v2"

    revisions = store.get_order_revisions("match-1", alice, turn=1)
    assert [r.revision for r in revisions] == [1, 2]

    assert store.get_latest_order("match-1", alice, turn=2) is None


def test_update_order_status(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    alice = Identity.generate().public_bytes
    store.record_order_submission(
        "match-1", alice, turn=1, revision=1, orders_blob=b"orders-v1", order_hash="hash1",
        status="accepted", submitted_at=10.0,
    )
    store.update_order_status("match-1", alice, turn=1, revision=1, status="superseded")

    latest = store.get_latest_order("match-1", alice, turn=1)
    assert latest.status == "superseded"


def test_update_order_status_unknown_raises(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    with pytest.raises(KeyError):
        store.update_order_status("match-1", b"nobody", turn=1, revision=1, status="superseded")


def test_get_latest_orders_for_turn_returns_one_per_player(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    alice = Identity.generate().public_bytes
    bob = Identity.generate().public_bytes

    store.record_order_submission(
        "match-1", alice, turn=1, revision=1, orders_blob=b"a-v1", order_hash="ha1",
        status="accepted", submitted_at=10.0,
    )
    store.record_order_submission(
        "match-1", alice, turn=1, revision=2, orders_blob=b"a-v2", order_hash="ha2",
        status="accepted", submitted_at=15.0,
    )
    store.record_order_submission(
        "match-1", bob, turn=1, revision=1, orders_blob=b"b-v1", order_hash="hb1",
        status="accepted", submitted_at=12.0,
    )

    latest = store.get_latest_orders_for_turn("match-1", turn=1)
    by_player = {r.public_key: r for r in latest}
    assert by_player[alice].orders_blob == b"a-v2"
    assert by_player[bob].orders_blob == b"b-v1"
    assert len(latest) == 2


def test_phase_result_hash_chain_round_trip(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)

    store.record_phase_result(
        "match-1", turn=1, phase=Phase.RESOLUTION,
        canonical_state=b"state-1", state_hash="hash-1", previous_state_hash=None,
        signature=b"sig-1", resolved_at=100.0,
    )
    store.record_phase_result(
        "match-1", turn=2, phase=Phase.RESOLUTION,
        canonical_state=b"state-2", state_hash="hash-2", previous_state_hash="hash-1",
        signature=b"sig-2", resolved_at=200.0,
    )

    chain = store.get_hash_chain("match-1")
    assert [r.state_hash for r in chain] == ["hash-1", "hash-2"]
    assert chain[1].previous_state_hash == "hash-1"
    assert chain[0].phase is Phase.RESOLUTION

    latest = store.get_latest_phase_result("match-1")
    assert latest.state_hash == "hash-2"


def test_get_phase_result_looks_up_by_turn_and_phase(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    store.record_phase_result(
        "match-1", turn=1, phase=Phase.ORDERS,
        canonical_state=b"state-orders", state_hash="hash-orders", previous_state_hash=None,
        signature=b"sig-orders", resolved_at=100.0,
    )
    store.record_phase_result(
        "match-1", turn=1, phase=Phase.RETREAT,
        canonical_state=b"state-retreat", state_hash="hash-retreat", previous_state_hash="hash-orders",
        signature=b"sig-retreat", resolved_at=200.0,
    )

    orders_result = store.get_phase_result("match-1", 1, Phase.ORDERS)
    assert orders_result is not None
    assert orders_result.canonical_state == b"state-orders"

    retreat_result = store.get_phase_result("match-1", 1, Phase.RETREAT)
    assert retreat_result is not None
    assert retreat_result.state_hash == "hash-retreat"

    assert store.get_phase_result("match-1", 1, Phase.BUILD) is None
    assert store.get_phase_result("match-1", 2, Phase.ORDERS) is None


def test_get_latest_phase_result_none_when_empty(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    assert store.get_latest_phase_result("match-1") is None


def test_draw_vote_round_trip_and_overwrite(store: MatchStore) -> None:
    store.create_match("match-1", Identity.generate(), created_at=1000.0)
    alice = Identity.generate().public_bytes
    bob = Identity.generate().public_bytes

    store.record_draw_vote("match-1", turn=3, public_key=alice, vote=True, voted_at=10.0)
    store.record_draw_vote("match-1", turn=3, public_key=bob, vote=False, voted_at=11.0)

    votes = store.get_draw_votes("match-1", turn=3)
    assert votes == {alice: True, bob: False}

    # Changing a vote overwrites, not duplicates.
    store.record_draw_vote("match-1", turn=3, public_key=alice, vote=False, voted_at=12.0)
    votes = store.get_draw_votes("match-1", turn=3)
    assert votes == {alice: False, bob: False}


def test_restart_recovery_reloads_full_state_from_disk(db_path) -> None:
    identity = Identity.generate()
    alice = Identity.generate().public_bytes

    store = MatchStore(db_path)
    store.create_match("match-1", identity, created_at=1000.0)
    store.add_player("match-1", alice, "vet", display_name="Alice", joined_at=10.0)
    active_state = advance_phase(start_match(MatchState()))
    store.save_match_state("match-1", active_state)
    store.set_deadline("match-1", 2000.0)
    store.record_order_submission(
        "match-1", alice, turn=1, revision=1, orders_blob=b"orders-v1", order_hash="hash1",
        status="accepted", submitted_at=15.0,
    )
    store.record_phase_result(
        "match-1", turn=1, phase=Phase.RESOLUTION,
        canonical_state=b"state-1", state_hash="hash-1", previous_state_hash=None,
        signature=identity.sign(b"state-1"), resolved_at=100.0,
    )
    store.close()

    # Simulate a coordinator restart: reopen a fresh MatchStore over the same file.
    recovered = MatchStore(db_path)
    try:
        record = recovered.get_match("match-1")
        assert record.state == active_state
        assert recovered.get_deadline("match-1") == 2000.0

        players = recovered.list_players("match-1")
        assert len(players) == 1
        assert players[0].faction_id == "vet"

        recovered_identity = recovered.load_coordinator_identity("match-1")
        assert recovered_identity.public_bytes == identity.public_bytes

        latest_order = recovered.get_latest_order("match-1", alice, turn=1)
        assert latest_order.orders_blob == b"orders-v1"

        chain = recovered.get_hash_chain("match-1")
        assert len(chain) == 1
        assert chain[0].state_hash == "hash-1"
        assert recovered_identity.public().verify(chain[0].signature, chain[0].canonical_state)
    finally:
        recovered.close()


def test_context_manager_closes_connection(tmp_path) -> None:
    db_path = tmp_path / "match.sqlite3"
    with MatchStore(db_path) as store:
        store.create_match("match-1", Identity.generate(), created_at=1000.0)
    with pytest.raises(sqlite3.ProgrammingError):
        store.get_match("match-1")
