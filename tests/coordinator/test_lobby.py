"""Tests for the coordinator join flow (coordinator/lobby.py)."""

from __future__ import annotations

import random

import pytest

from coordinator.lobby import can_start_match, handle_join_request, start_match
from coordinator.persistence import MatchStore
from coordinator.phases import InvalidTransitionError
from engine.hashing import hash_game_state_hex
from engine.map import FACTIONS, create_default_map
from protocol.messages import JoinAccepted, JoinRejected, JoinRequest, MatchStatus
from shared.identity import Identity


@pytest.fixture
def store():
    match_store = MatchStore(":memory:")
    yield match_store
    match_store.close()


@pytest.fixture
def coordinator_identity():
    return Identity.generate()


@pytest.fixture
def match_id(store, coordinator_identity):
    store.create_match("match-1", coordinator_identity, created_at=1000.0)
    return "match-1"


def _join(store, match_id, *, seq=1, ts=100.0, rng=None, display_name="Ambassador"):
    player = Identity.generate()
    request = JoinRequest(
        sequence_number=seq,
        timestamp=ts,
        match_id=match_id,
        player_public_key=player.public_bytes,
        display_name=display_name,
    )
    response = handle_join_request(store, match_id, request, sequence_number=seq, timestamp=ts, rng=rng)
    return player, response


def test_first_join_is_accepted_and_persisted(store, match_id) -> None:
    player, response = _join(store, match_id)

    assert isinstance(response, JoinAccepted)
    assert response.match_id == match_id
    assert len(response.faction_names) == 7
    assert set(response.faction_names) == {f.name for f in FACTIONS.values()}

    faction_id = store.get_player_faction(match_id, player.public_bytes)
    assert faction_id in FACTIONS

    decrypted = player.decrypt(response.encrypted_faction).decode("utf-8")
    assert decrypted == FACTIONS[faction_id].name


def test_match_parameters_are_populated(store, match_id) -> None:
    _, response = _join(store, match_id)
    assert response.match_parameters["max_players"] == 7
    assert response.match_parameters["movement_phase_seconds"] > 0


def test_rejoin_with_same_key_is_idempotent(store, match_id) -> None:
    player, first = _join(store, match_id, seq=1)
    request = JoinRequest(
        sequence_number=2,
        timestamp=101.0,
        match_id=match_id,
        player_public_key=player.public_bytes,
        display_name="Ambassador",
    )
    second = handle_join_request(store, match_id, request, sequence_number=2, timestamp=101.0)

    assert isinstance(second, JoinAccepted)
    assert store.get_player_faction(match_id, player.public_bytes) == store.get_player_faction(
        match_id, player.public_bytes
    )
    # Only one player row exists — the rejoin didn't insert a duplicate.
    assert len(store.list_players(match_id)) == 1
    decrypted_first = player.decrypt(first.encrypted_faction).decode("utf-8")
    decrypted_second = player.decrypt(second.encrypted_faction).decode("utf-8")
    assert decrypted_first == decrypted_second


def test_seventh_join_fills_match_eighth_is_rejected(store, match_id) -> None:
    rng = random.Random(42)
    for i in range(7):
        _, response = _join(store, match_id, seq=i + 1, ts=100.0 + i, rng=rng)
        assert isinstance(response, JoinAccepted)

    assert len(store.list_players(match_id)) == 7

    _, eighth = _join(store, match_id, seq=8, ts=200.0, rng=rng)
    assert isinstance(eighth, JoinRejected)
    assert eighth.reason == "match full"


def test_join_rejected_when_match_unknown(store) -> None:
    player = Identity.generate()
    request = JoinRequest(
        sequence_number=1, timestamp=100.0, match_id="no-such-match", player_public_key=player.public_bytes
    )
    response = handle_join_request(store, "no-such-match", request, sequence_number=1, timestamp=100.0)
    assert isinstance(response, JoinRejected)
    assert response.reason == "no such match"


def test_join_rejected_once_match_active(store, match_id, coordinator_identity) -> None:
    rng = random.Random(7)
    for i in range(7):
        _join(store, match_id, seq=i + 1, ts=100.0 + i, rng=rng)
    start_match(store, match_id, coordinator_identity, sequence_number=100, timestamp=500.0, deadline=1000.0)

    new_player = Identity.generate()
    request = JoinRequest(
        sequence_number=200, timestamp=600.0, match_id=match_id, player_public_key=new_player.public_bytes
    )
    response = handle_join_request(store, match_id, request, sequence_number=200, timestamp=600.0)
    assert isinstance(response, JoinRejected)
    assert response.reason == "match already started"


def test_can_start_match_false_until_full(store, match_id) -> None:
    assert can_start_match(store, match_id) is False
    rng = random.Random(1)
    for i in range(6):
        _join(store, match_id, seq=i + 1, ts=100.0 + i, rng=rng)
    assert can_start_match(store, match_id) is False
    _join(store, match_id, seq=7, ts=110.0, rng=rng)
    assert can_start_match(store, match_id) is True


def test_start_match_raises_when_not_full(store, match_id, coordinator_identity) -> None:
    with pytest.raises(InvalidTransitionError):
        start_match(store, match_id, coordinator_identity, sequence_number=1, timestamp=100.0, deadline=200.0)


def test_start_match_raises_for_unknown_match(store, coordinator_identity) -> None:
    with pytest.raises(InvalidTransitionError):
        start_match(store, "no-such-match", coordinator_identity, sequence_number=1, timestamp=100.0, deadline=200.0)


def test_start_match_transitions_state_and_broadcasts(store, match_id, coordinator_identity) -> None:
    rng = random.Random(3)
    for i in range(7):
        _join(store, match_id, seq=i + 1, ts=100.0 + i, rng=rng)

    result = start_match(store, match_id, coordinator_identity, sequence_number=100, timestamp=500.0, deadline=86500.0)

    record = store.get_match(match_id)
    assert record.state.status is MatchStatus.ACTIVE
    assert record.state.turn == 1
    assert record.state.year == 1

    assert can_start_match(store, match_id) is False  # no longer in lobby
    assert store.get_deadline(match_id) == 86500.0

    assert len(result.faction_names) == 7
    expected_hash = hash_game_state_hex(create_default_map(), previous_hash=None)
    assert result.state_hash == expected_hash
    assert coordinator_identity.public().verify(result.signature, result.state_hash.encode("utf-8"))

    chain = store.get_hash_chain(match_id)
    assert len(chain) == 1
    assert chain[0].state_hash == expected_hash
    assert chain[0].previous_state_hash is None


def test_start_match_twice_raises(store, match_id, coordinator_identity) -> None:
    rng = random.Random(9)
    for i in range(7):
        _join(store, match_id, seq=i + 1, ts=100.0 + i, rng=rng)
    start_match(store, match_id, coordinator_identity, sequence_number=1, timestamp=500.0, deadline=1000.0)

    with pytest.raises(InvalidTransitionError):
        start_match(store, match_id, coordinator_identity, sequence_number=2, timestamp=600.0, deadline=2000.0)
