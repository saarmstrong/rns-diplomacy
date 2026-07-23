"""Tests for draw proposal/voting handling (coordinator/draws.py)."""

from __future__ import annotations

import pytest

from coordinator.draws import DrawRule, apply_result, handle_draw_propose, handle_draw_vote
from coordinator.persistence import MatchStore
from coordinator.phases import MatchState
from engine.map import FACTIONS
from engine.model import Phase
from protocol.messages import DrawPropose, DrawVote, MatchStatus
from shared.identity import Identity


@pytest.fixture
def store():
    match_store = MatchStore(":memory:")
    match_store.create_match("match-1", Identity.generate(), created_at=1000.0)
    yield match_store
    match_store.close()


def _activate(store, match_id="match-1"):
    store.save_match_state(match_id, MatchState(status=MatchStatus.ACTIVE, phase=Phase.DIPLOMACY, turn=1, year=1))


def _add_player(store, faction_id: str, match_id="match-1") -> bytes:
    public_key = Identity.generate().public_bytes
    store.add_player(match_id, public_key, faction_id, display_name=None, joined_at=10.0)
    return public_key


def test_propose_by_sole_player_is_unanimously_accepted(store):
    _activate(store)
    alice = _add_player(store, "vet")
    request = DrawPropose(sequence_number=1, timestamp=100.0, match_id="match-1", turn=1)
    result = handle_draw_propose(store, "match-1", alice, request, sequence_number=1, timestamp=100.0)
    assert result.accepted is True
    assert result.votes == {"Vethara": True}


def test_unanimous_requires_every_player(store):
    _activate(store)
    alice = _add_player(store, "vet")
    bob = _add_player(store, "thr")
    carol = _add_player(store, "dsk")

    propose = DrawPropose(sequence_number=1, timestamp=100.0, match_id="match-1", turn=1)
    result = handle_draw_propose(store, "match-1", alice, propose, sequence_number=1, timestamp=100.0)
    assert result.accepted is False

    vote_yes = DrawVote(sequence_number=2, timestamp=101.0, match_id="match-1", turn=1, vote=True)
    result = handle_draw_vote(store, "match-1", bob, vote_yes, sequence_number=2, timestamp=101.0)
    assert result.accepted is False

    result = handle_draw_vote(store, "match-1", carol, vote_yes, sequence_number=3, timestamp=102.0)
    assert result.accepted is True
    assert result.votes == {"Vethara": True, "Thornveil": True, "Duskhollow": True}


def test_majority_rule_accepts_without_unanimity(store):
    _activate(store)
    alice = _add_player(store, "vet")
    bob = _add_player(store, "thr")
    _add_player(store, "dsk")  # carol never votes

    propose = DrawPropose(sequence_number=1, timestamp=100.0, match_id="match-1", turn=1)
    handle_draw_propose(store, "match-1", alice, propose, sequence_number=1, timestamp=100.0, rule=DrawRule.MAJORITY)

    vote_yes = DrawVote(sequence_number=2, timestamp=101.0, match_id="match-1", turn=1, vote=True)
    result = handle_draw_vote(
        store, "match-1", bob, vote_yes, sequence_number=2, timestamp=101.0, rule=DrawRule.MAJORITY
    )
    assert result.accepted is True


def test_changing_vote_to_no_updates_result(store):
    _activate(store)
    alice = _add_player(store, "vet")
    bob = _add_player(store, "thr")

    vote_yes = DrawVote(sequence_number=1, timestamp=100.0, match_id="match-1", turn=1, vote=True)
    handle_draw_vote(store, "match-1", alice, vote_yes, sequence_number=1, timestamp=100.0)
    result = handle_draw_vote(store, "match-1", bob, vote_yes, sequence_number=2, timestamp=101.0)
    assert result.accepted is True

    vote_no = DrawVote(sequence_number=3, timestamp=102.0, match_id="match-1", turn=1, vote=False)
    result = handle_draw_vote(store, "match-1", alice, vote_no, sequence_number=3, timestamp=102.0)
    assert result.accepted is False
    assert result.votes["Vethara"] is False


def test_propose_and_vote_are_noop_before_match_is_active(store):
    alice = _add_player(store, "vet")  # match still in LOBBY — never activated
    propose = DrawPropose(sequence_number=1, timestamp=100.0, match_id="match-1", turn=1)
    result = handle_draw_propose(store, "match-1", alice, propose, sequence_number=1, timestamp=100.0)
    assert result.accepted is False
    assert store.get_draw_votes("match-1", 1) == {}


def test_apply_result_ends_match_when_accepted(store):
    _activate(store)
    alice = _add_player(store, "vet")
    propose = DrawPropose(sequence_number=1, timestamp=100.0, match_id="match-1", turn=1)
    result = handle_draw_propose(store, "match-1", alice, propose, sequence_number=1, timestamp=100.0)
    assert result.accepted is True

    active_state = store.get_match("match-1").state
    new_state = apply_result(store, "match-1", active_state, result)

    assert new_state.status is MatchStatus.COMPLETED
    assert store.get_match("match-1").state.status is MatchStatus.COMPLETED


def test_apply_result_is_noop_when_not_accepted(store):
    _activate(store)
    alice = _add_player(store, "vet")
    _add_player(store, "thr")
    propose = DrawPropose(sequence_number=1, timestamp=100.0, match_id="match-1", turn=1)
    result = handle_draw_propose(store, "match-1", alice, propose, sequence_number=1, timestamp=100.0)
    assert result.accepted is False

    active_state = store.get_match("match-1").state
    new_state = apply_result(store, "match-1", active_state, result)

    assert new_state == active_state
    assert store.get_match("match-1").state.status is MatchStatus.ACTIVE


def test_votes_keyed_by_faction_name_not_public_key(store):
    _activate(store)
    alice = _add_player(store, "kho")
    propose = DrawPropose(sequence_number=1, timestamp=100.0, match_id="match-1", turn=1)
    result = handle_draw_propose(store, "match-1", alice, propose, sequence_number=1, timestamp=100.0)
    assert set(result.votes.keys()) == {FACTIONS["kho"].name}
