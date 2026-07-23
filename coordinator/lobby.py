"""Lobby management — join flow, faction assignment, and match start.

Handles the pre-game phase where players discover and join a match:
capacity checks, encrypted faction assignment, and the transition into
active play once every seat is filled and the operator starts the match.
"""

from __future__ import annotations

import random

from coordinator.persistence import MatchStore
from coordinator.phases import InvalidTransitionError
from coordinator.phases import start_match as _start_match_fsm
from engine.hashing import canonical_bytes, hash_game_state_hex
from engine.map import FACTIONS, create_default_map
from protocol.constants import (
    DEFAULT_ADJUSTMENT_PHASE_SECONDS,
    DEFAULT_MOVEMENT_PHASE_SECONDS,
    DEFAULT_RETREAT_PHASE_SECONDS,
    MAX_PLAYERS,
)
from protocol.messages import JoinAccepted, JoinRejected, JoinRequest, MatchStart, MatchStatus
from shared.identity import Identity, PublicIdentity

_FACTION_NAMES = tuple(FACTIONS[fid].name for fid in sorted(FACTIONS))


def handle_join_request(
    store: MatchStore,
    match_id: str,
    request: JoinRequest,
    *,
    sequence_number: int,
    timestamp: float,
    rng: random.Random | None = None,
) -> JoinAccepted | JoinRejected:
    """Process a JOIN_REQUEST, returning the JOIN_ACCEPTED/JOIN_REJECTED to send back.

    Re-joining with a public key that already has a faction assignment
    (e.g. after a dropped connection) is idempotent: it returns the same
    assignment rather than erroring or reassigning.
    """
    match = store.get_match(match_id)
    if match is None:
        return _rejected(match_id, sequence_number, timestamp, "no such match")
    if match.state.status is not MatchStatus.LOBBY:
        return _rejected(match_id, sequence_number, timestamp, "match already started")

    existing_faction = store.get_player_faction(match_id, request.player_public_key)
    players = store.list_players(match_id)

    if existing_faction is not None:
        faction_id = existing_faction
    else:
        if len(players) >= MAX_PLAYERS:
            return _rejected(match_id, sequence_number, timestamp, "match full")
        taken = {p.faction_id for p in players}
        available = sorted(fid for fid in FACTIONS if fid not in taken)
        if not available:
            return _rejected(match_id, sequence_number, timestamp, "match full")
        chooser = rng if rng is not None else random
        faction_id = chooser.choice(available)
        store.add_player(
            match_id,
            request.player_public_key,
            faction_id,
            display_name=request.display_name,
            joined_at=timestamp,
        )

    faction_name = FACTIONS[faction_id].name
    encrypted_faction = PublicIdentity(request.player_public_key).encrypt(faction_name.encode("utf-8"))

    return JoinAccepted(
        sequence_number=sequence_number,
        timestamp=timestamp,
        match_id=match_id,
        encrypted_faction=encrypted_faction,
        faction_names=_FACTION_NAMES,
        match_parameters={
            "max_players": MAX_PLAYERS,
            "movement_phase_seconds": DEFAULT_MOVEMENT_PHASE_SECONDS,
            "retreat_phase_seconds": DEFAULT_RETREAT_PHASE_SECONDS,
            "adjustment_phase_seconds": DEFAULT_ADJUSTMENT_PHASE_SECONDS,
        },
    )


def _rejected(match_id: str, sequence_number: int, timestamp: float, reason: str) -> JoinRejected:
    return JoinRejected(sequence_number=sequence_number, timestamp=timestamp, match_id=match_id, reason=reason)


def can_start_match(store: MatchStore, match_id: str) -> bool:
    """True once the match is full and still in the lobby — ready for the operator to start it."""
    match = store.get_match(match_id)
    if match is None or match.state.status is not MatchStatus.LOBBY:
        return False
    return len(store.list_players(match_id)) == MAX_PLAYERS


def start_match(
    store: MatchStore,
    match_id: str,
    coordinator_identity: Identity,
    *,
    sequence_number: int,
    timestamp: float,
    deadline: float,
) -> MatchStart:
    """Transition lobby -> active and produce the MATCH_START broadcast.

    Requires exactly ``MAX_PLAYERS`` joined players; raises
    ``InvalidTransitionError`` otherwise, or if the match isn't in the
    lobby. Seeds the hash chain with the signed initial game state.
    """
    match = store.get_match(match_id)
    if match is None:
        raise InvalidTransitionError(f"no such match: {match_id!r}")
    if len(store.list_players(match_id)) != MAX_PLAYERS:
        raise InvalidTransitionError(f"cannot start match {match_id!r} with fewer than {MAX_PLAYERS} players")

    new_state = _start_match_fsm(match.state)
    store.save_match_state(match_id, new_state)

    initial_state = create_default_map()
    canonical = canonical_bytes(initial_state)
    state_hash = hash_game_state_hex(initial_state, previous_hash=None)
    signature = coordinator_identity.sign(state_hash.encode("utf-8"))

    store.record_phase_result(
        match_id,
        turn=new_state.turn,
        phase=new_state.phase,
        canonical_state=canonical,
        state_hash=state_hash,
        previous_state_hash=None,
        signature=signature,
        resolved_at=timestamp,
    )
    store.set_deadline(match_id, deadline)

    players = store.list_players(match_id)
    faction_names = tuple(FACTIONS[p.faction_id].name for p in players)

    return MatchStart(
        sequence_number=sequence_number,
        timestamp=timestamp,
        match_id=match_id,
        faction_names=faction_names,
        canonical_state=canonical,
        state_hash=state_hash,
        signature=signature,
        deadline=deadline,
    )
