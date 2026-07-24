"""Client-side state verification.

Independently checks what a coordinator claims: signatures, hash-chain
continuity, and (when the orders behind a transition are actually known
to the caller) that re-running adjudication locally reproduces the
claimed result exactly.

Order privacy is real: ORDER_SUBMIT is a private client<->coordinator
message, never broadcast. A client only ever sees its *own* submitted
orders, not the whole board's, so it generally cannot single-handedly
reproduce a full movement resolution the way the coordinator can.
``reproduce_and_compare`` covers the case where the full order set *is*
available (audit tooling, tests, or a future protocol extension that
publishes orders once a phase has closed) — see the TODO on
``coordinator/orders.py``. Signature and hash-chain verification, which
every client can always do unilaterally, are what actually make a
dishonest coordinator provably dishonest per the threat model.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine import adjudicator
from engine.hashing import game_state_from_canonical_bytes, hash_game_state_hex
from engine.model import GameState, Phase
from engine.orders import Order
from protocol.messages import MatchStart, PhaseResult
from shared.identity import PublicIdentity


@dataclass(frozen=True)
class VerificationIssue:
    """One specific thing that failed to check out, with enough detail to act as evidence."""

    index: int
    turn: int
    phase: Phase
    reason: str


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    checked: int
    issues: tuple[VerificationIssue, ...]


def verify_signature(coordinator_public_key: PublicIdentity, result: PhaseResult | MatchStart) -> bool:
    """Check that ``result`` was actually signed by the coordinator's known identity."""
    return coordinator_public_key.verify(result.signature, result.state_hash.encode("utf-8"))


def verify_hash_matches_state(result: PhaseResult | MatchStart) -> bool:
    """Check that ``state_hash`` is really the hash of ``canonical_state`` chained from ``previous_state_hash``.

    Catches a coordinator broadcasting a state and a hash that don't
    actually correspond to each other — including outright malformed
    ``canonical_state`` bytes, which fail this check rather than raising.
    ``MatchStart`` has no ``previous_state_hash`` field: it's always the
    genesis link, chained from ``None``.
    """
    try:
        state = game_state_from_canonical_bytes(result.canonical_state)
    except Exception:
        return False
    previous_hash = getattr(result, "previous_state_hash", None)
    previous = bytes.fromhex(previous_hash) if previous_hash else None
    return hash_game_state_hex(state, previous_hash=previous) == result.state_hash


def verify_history(coordinator_public_key: PublicIdentity, match_start: MatchStart, phase_results: list[PhaseResult]) -> VerificationReport:
    """Verify every link a client has actually received: MATCH_START plus every PHASE_RESULT since.

    For each link, checks the coordinator's signature, that the claimed
    hash really matches the claimed state, and that it correctly chains
    from the previous link's hash. Returns every failure found — not just
    the first — so a dispute has full evidence.
    """
    issues: list[VerificationIssue] = []

    if not verify_signature(coordinator_public_key, match_start):
        issues.append(VerificationIssue(index=0, turn=0, phase=Phase.DIPLOMACY, reason="MATCH_START signature invalid"))
    if not verify_hash_matches_state(match_start):
        issues.append(
            VerificationIssue(index=0, turn=0, phase=Phase.DIPLOMACY, reason="MATCH_START state_hash does not match canonical_state")
        )

    previous_hash = match_start.state_hash
    for i, result in enumerate(phase_results, start=1):
        if not verify_signature(coordinator_public_key, result):
            issues.append(VerificationIssue(index=i, turn=result.turn, phase=result.phase, reason="signature invalid"))
        if not verify_hash_matches_state(result):
            issues.append(
                VerificationIssue(
                    index=i, turn=result.turn, phase=result.phase, reason="state_hash does not match canonical_state"
                )
            )
        if result.previous_state_hash != previous_hash:
            issues.append(
                VerificationIssue(
                    index=i,
                    turn=result.turn,
                    phase=result.phase,
                    reason=f"hash chain broken: expected previous_state_hash {previous_hash!r}, got {result.previous_state_hash!r}",
                )
            )
        previous_hash = result.state_hash

    return VerificationReport(ok=not issues, checked=len(phase_results) + 1, issues=tuple(issues))


def reproduce_and_compare(
    pre_state: GameState, orders: dict[str, Order], claimed_state: GameState
) -> tuple[bool, GameState]:
    """Independently re-run movement adjudication and compare against a claimed result.

    Requires the full order set for the phase (see the module docstring
    on why a client doesn't normally have this for other players' orders).
    Returns ``(matches, locally_computed_state)`` — the computed state is
    included so a mismatch can be inspected, not just flagged.
    """
    movement_result = adjudicator.resolve_movement(pre_state, orders)
    computed = adjudicator.apply_movement_result(pre_state, movement_result)
    computed = adjudicator.update_supply_center_ownership(computed)
    return hash_game_state_hex(computed) == hash_game_state_hex(claimed_state), computed
