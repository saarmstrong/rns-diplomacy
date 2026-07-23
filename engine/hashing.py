"""Deterministic hashing of game state for integrity verification.

Produces a canonical hash of the full game state so that all participants
can independently verify they agree on the current board position.
"""

# TODO: Implement state hashing
#   - hash_game_state(state) -> bytes
#   - Canonical serialization order for determinism
#   - Include turn number, phase, all unit positions, ownership
