"""Deterministic hashing of game state for integrity verification.

Produces a canonical hash of the full game state so that all participants
can independently verify they agree on the current board position. The
coordinator signs these hashes and chains each to the previous one; any
client can recompute the chain locally and compare.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any

import msgpack

from engine.model import Faction, GameState, Region, Unit

Canonical = Any


def _unit_sort_key(unit: Unit) -> tuple[str, str, str]:
    return (unit.region_id, unit.faction_id, unit.unit_type.value)


def canonicalize(value: Any) -> Canonical:
    """Convert a value into a canonical, order-independent, JSON/msgpack-safe structure.

    Dict keys are sorted, unit lists are sorted by region_id, and enums are
    reduced to their string value — so two states that are logically equal
    always canonicalize to byte-identical structures.
    """
    if isinstance(value, GameState):
        return {
            "regions": canonicalize(value.regions),
            "factions": canonicalize(value.factions),
            "units": [canonicalize(u) for u in sorted(value.units, key=_unit_sort_key)],
            "supply_centers": canonicalize(value.supply_centers),
            "phase": value.phase.value,
            "turn": value.turn,
            "year": value.year,
        }
    if isinstance(value, Unit):
        return {
            "unit_type": value.unit_type.value,
            "faction_id": value.faction_id,
            "region_id": value.region_id,
        }
    if isinstance(value, Region):
        return {
            "id": value.id,
            "name": value.name,
            "region_type": value.region_type.value,
            "is_supply_center": value.is_supply_center,
            "abbreviation": value.abbreviation,
        }
    if isinstance(value, Faction):
        return {
            "id": value.id,
            "name": value.name,
            "color": value.color,
            "abbreviation": value.abbreviation,
        }
    if isinstance(value, dict):
        return {str(k): canonicalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [canonicalize(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    return value


def canonical_bytes(state: GameState) -> bytes:
    """Serialize a game state to canonical, deterministic bytes."""
    return msgpack.packb(canonicalize(state), use_bin_type=True)


def hash_game_state(state: GameState, previous_hash: bytes | None = None) -> bytes:
    """Compute the SHA-256 hash of a state's canonical bytes, chained to the previous hash.

    ``previous_hash`` should be the hash of the prior phase result (or
    None for the first state in a match), forming an append-only chain.
    """
    hasher = hashlib.sha256()
    hasher.update(previous_hash or b"")
    hasher.update(canonical_bytes(state))
    return hasher.digest()


def hash_game_state_hex(state: GameState, previous_hash: bytes | None = None) -> str:
    """Hex-encoded convenience form of ``hash_game_state``."""
    return hash_game_state(state, previous_hash).hex()


def verify_chain(states: list[GameState], hashes: list[bytes]) -> bool:
    """Verify that ``hashes[i]`` is the correct chained hash of ``states[i]``.

    Each hash must chain from the previous entry's hash (or None for the
    first). Returns False on any mismatch or length mismatch.
    """
    if len(states) != len(hashes):
        return False
    previous: bytes | None = None
    for state, expected in zip(states, hashes):
        if hash_game_state(state, previous) != expected:
            return False
        previous = expected
    return True
