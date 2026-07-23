"""Verification of signed, hash-chained coordinator state."""

from __future__ import annotations

from dataclasses import dataclass

from engine import adjudicator
from engine.hashing import game_state_from_canonical_bytes, hash_game_state_hex
from engine.model import GameState
from engine.orders import Order
from protocol.messages import PhaseResult, StateResponse
from shared.identity import PublicIdentity


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    reason: str = ""
    state: GameState | None = None


def verify_state(
    received: PhaseResult | StateResponse,
    coordinator: PublicIdentity,
    previous_hash: str | None = None,
) -> VerificationResult:
    """Verify a received state hash, chain pointer, and coordinator signature."""
    try:
        state = game_state_from_canonical_bytes(received.canonical_state)
        expected = hash_game_state_hex(state, bytes.fromhex(received.previous_state_hash) if received.previous_state_hash else None)
    except (ValueError, TypeError, KeyError) as exc:
        return VerificationResult(False, f"invalid canonical state: {exc}")
    if received.previous_state_hash != previous_hash:
        return VerificationResult(False, "hash-chain discontinuity", state)
    if expected != received.state_hash:
        return VerificationResult(False, "state hash mismatch", state)
    if not coordinator.verify(received.signature, received.state_hash.encode("utf-8")):
        return VerificationResult(False, "invalid coordinator signature", state)
    return VerificationResult(True, state=state)


def verify_movement_result(before: GameState, orders: dict[str, Order], received_state: GameState) -> bool:
    """Re-adjudicate movement and compare the resulting board state exactly."""
    result = adjudicator.resolve_movement(before, orders)
    expected = adjudicator.update_supply_center_ownership(adjudicator.apply_movement_result(before, result))
    return hash_game_state_hex(expected) == hash_game_state_hex(received_state)
