"""Match state machine — lifecycle and phase transitions.

Tracks a match's lifecycle (lobby -> active -> completed) and, while
active, the sequence of phases within a turn: diplomacy -> orders ->
resolution -> retreat (conditional on dislodgements) -> build
(conditional on adjustment need) -> the next turn's diplomacy.

Pure state transitions only — no I/O, no timers, no persistence. Callers
supply the outcome of adjudication (``has_dislodgements``,
``adjustment_needed``) to resolve the conditional branches; deadline
scheduling and order handling are built on top of this elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from engine.model import Phase
from protocol.messages import MatchStatus


class InvalidTransitionError(Exception):
    """Raised when a state transition is attempted from an illegal state."""


@dataclass(frozen=True)
class MatchState:
    """The coordinator's current lifecycle/phase position for one match."""

    status: MatchStatus = MatchStatus.LOBBY
    phase: Phase = Phase.DIPLOMACY
    turn: int = 1
    year: int = 1


def start_match(state: MatchState) -> MatchState:
    """Transition lobby -> active, entering turn 1's diplomacy phase."""
    if state.status is not MatchStatus.LOBBY:
        raise InvalidTransitionError(f"cannot start match from status {state.status.value}")
    return replace(state, status=MatchStatus.ACTIVE, phase=Phase.DIPLOMACY, turn=1, year=1)


def end_match(state: MatchState) -> MatchState:
    """Transition active -> completed, from any phase."""
    if state.status is not MatchStatus.ACTIVE:
        raise InvalidTransitionError(f"cannot end match from status {state.status.value}")
    return replace(state, status=MatchStatus.COMPLETED)


def advance_phase(
    state: MatchState,
    *,
    has_dislodgements: bool = False,
    adjustment_needed: bool = False,
) -> MatchState:
    """Advance to the next phase of an active match.

    ``has_dislodgements`` gates the retreat phase and is only consulted
    when leaving RESOLUTION. ``adjustment_needed`` gates the build phase
    and is only consulted when leaving RESOLUTION (if retreat was
    skipped) or RETREAT.
    """
    if state.status is not MatchStatus.ACTIVE:
        raise InvalidTransitionError(f"cannot advance phase from status {state.status.value}")

    if state.phase is Phase.DIPLOMACY:
        return replace(state, phase=Phase.ORDERS)
    if state.phase is Phase.ORDERS:
        return replace(state, phase=Phase.RESOLUTION)
    if state.phase is Phase.RESOLUTION:
        if has_dislodgements:
            return replace(state, phase=Phase.RETREAT)
        if adjustment_needed:
            return replace(state, phase=Phase.BUILD)
        return _next_turn(state)
    if state.phase is Phase.RETREAT:
        if adjustment_needed:
            return replace(state, phase=Phase.BUILD)
        return _next_turn(state)
    if state.phase is Phase.BUILD:
        return _next_turn(state)

    raise InvalidTransitionError(f"unknown phase {state.phase}")  # pragma: no cover


def _next_turn(state: MatchState) -> MatchState:
    """One full diplomacy..build cycle is one game-year: advance both counters."""
    return replace(state, phase=Phase.DIPLOMACY, turn=state.turn + 1, year=state.year + 1)
