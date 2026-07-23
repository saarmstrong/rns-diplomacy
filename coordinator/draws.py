"""Draw proposal and voting.

A DRAW_PROPOSE counts as the proposer casting a "yes" DRAW_VOTE; both are
handled the same way — record the vote, then re-evaluate the outcome for
every joined player. The outcome rule (unanimous by default, or simple
majority) is configurable per match.
"""

from __future__ import annotations

from enum import Enum

from coordinator.persistence import MatchStore
from coordinator.phases import MatchState, end_match
from engine.map import FACTIONS
from protocol.messages import DrawPropose, DrawResult, DrawVote, MatchStatus


class DrawRule(Enum):
    """How a draw proposal is decided."""

    UNANIMOUS = "unanimous"
    MAJORITY = "majority"


def _evaluate(
    store: MatchStore, match_id: str, turn: int, *, sequence_number: int, timestamp: float, rule: DrawRule
) -> DrawResult:
    players = store.list_players(match_id)
    votes = store.get_draw_votes(match_id, turn)
    yes_votes = sum(1 for p in players if votes.get(p.public_key, False))
    total = len(players)

    if rule is DrawRule.UNANIMOUS:
        accepted = total > 0 and yes_votes == total
    else:
        accepted = yes_votes > total / 2

    votes_by_faction = {FACTIONS[p.faction_id].name: votes.get(p.public_key, False) for p in players}
    return DrawResult(
        sequence_number=sequence_number,
        timestamp=timestamp,
        match_id=match_id,
        turn=turn,
        accepted=accepted,
        votes=votes_by_faction,
    )


def _is_active(store: MatchStore, match_id: str) -> bool:
    match = store.get_match(match_id)
    return match is not None and match.state.status is MatchStatus.ACTIVE


def handle_draw_propose(
    store: MatchStore,
    match_id: str,
    public_key: bytes,
    request: DrawPropose,
    *,
    sequence_number: int,
    timestamp: float,
    rule: DrawRule = DrawRule.UNANIMOUS,
) -> DrawResult:
    if _is_active(store, match_id):
        store.record_draw_vote(match_id, request.turn, public_key, True, voted_at=timestamp)
    return _evaluate(store, match_id, request.turn, sequence_number=sequence_number, timestamp=timestamp, rule=rule)


def handle_draw_vote(
    store: MatchStore,
    match_id: str,
    public_key: bytes,
    request: DrawVote,
    *,
    sequence_number: int,
    timestamp: float,
    rule: DrawRule = DrawRule.UNANIMOUS,
) -> DrawResult:
    if _is_active(store, match_id):
        store.record_draw_vote(match_id, request.turn, public_key, request.vote, voted_at=timestamp)
    return _evaluate(store, match_id, request.turn, sequence_number=sequence_number, timestamp=timestamp, rule=rule)


def apply_result(store: MatchStore, match_id: str, state: MatchState, result: DrawResult) -> MatchState:
    """If the draw was accepted, end the match and persist the transition. Otherwise a no-op."""
    if not result.accepted:
        return state
    new_state = end_match(state)
    store.save_match_state(match_id, new_state)
    return new_state
