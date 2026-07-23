"""Tests for the coordinator match state machine (lifecycle + phase transitions)."""

from __future__ import annotations

import pytest

from coordinator.phases import InvalidTransitionError, MatchState, advance_phase, end_match, start_match
from engine.adjudicator import compute_adjustment_deltas, resolve_movement
from engine.map import create_default_map
from engine.model import Phase
from engine.orders import HoldOrder
from protocol.messages import MatchStatus


def test_default_match_state_starts_in_lobby() -> None:
    state = MatchState()
    assert state.status is MatchStatus.LOBBY
    assert state.phase is Phase.DIPLOMACY
    assert state.turn == 1
    assert state.year == 1


def test_start_match_transitions_lobby_to_active() -> None:
    state = start_match(MatchState())
    assert state.status is MatchStatus.ACTIVE
    assert state.phase is Phase.DIPLOMACY
    assert state.turn == 1
    assert state.year == 1


@pytest.mark.parametrize("status", [MatchStatus.ACTIVE, MatchStatus.COMPLETED])
def test_start_match_rejects_non_lobby_status(status: MatchStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        start_match(MatchState(status=status))


def test_end_match_transitions_active_to_completed() -> None:
    state = end_match(MatchState(status=MatchStatus.ACTIVE, phase=Phase.RESOLUTION, turn=3, year=3))
    assert state.status is MatchStatus.COMPLETED
    # Phase/turn/year are preserved as a historical record of where the match ended.
    assert state.phase is Phase.RESOLUTION
    assert state.turn == 3


@pytest.mark.parametrize("status", [MatchStatus.LOBBY, MatchStatus.COMPLETED])
def test_end_match_rejects_non_active_status(status: MatchStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        end_match(MatchState(status=status))


@pytest.mark.parametrize("status", [MatchStatus.LOBBY, MatchStatus.COMPLETED])
def test_advance_phase_rejects_non_active_status(status: MatchStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        advance_phase(MatchState(status=status))


def test_advance_phase_diplomacy_to_orders() -> None:
    state = advance_phase(MatchState(status=MatchStatus.ACTIVE, phase=Phase.DIPLOMACY))
    assert state.phase is Phase.ORDERS
    assert state.turn == 1 and state.year == 1


def test_advance_phase_orders_to_resolution() -> None:
    state = advance_phase(MatchState(status=MatchStatus.ACTIVE, phase=Phase.ORDERS))
    assert state.phase is Phase.RESOLUTION


def test_resolution_skips_retreat_and_build_when_neither_needed() -> None:
    state = advance_phase(
        MatchState(status=MatchStatus.ACTIVE, phase=Phase.RESOLUTION, turn=1, year=1),
        has_dislodgements=False,
        adjustment_needed=False,
    )
    assert state.phase is Phase.DIPLOMACY
    assert state.turn == 2
    assert state.year == 2


def test_resolution_enters_retreat_when_dislodgements_exist() -> None:
    state = advance_phase(
        MatchState(status=MatchStatus.ACTIVE, phase=Phase.RESOLUTION),
        has_dislodgements=True,
        adjustment_needed=True,
    )
    assert state.phase is Phase.RETREAT


def test_resolution_enters_build_when_no_dislodgements_but_adjustment_needed() -> None:
    state = advance_phase(
        MatchState(status=MatchStatus.ACTIVE, phase=Phase.RESOLUTION),
        has_dislodgements=False,
        adjustment_needed=True,
    )
    assert state.phase is Phase.BUILD


def test_retreat_enters_build_when_adjustment_needed() -> None:
    state = advance_phase(
        MatchState(status=MatchStatus.ACTIVE, phase=Phase.RETREAT, turn=1, year=1),
        adjustment_needed=True,
    )
    assert state.phase is Phase.BUILD
    assert state.turn == 1  # not yet a new turn


def test_retreat_advances_turn_when_no_adjustment_needed() -> None:
    state = advance_phase(
        MatchState(status=MatchStatus.ACTIVE, phase=Phase.RETREAT, turn=1, year=1),
        adjustment_needed=False,
    )
    assert state.phase is Phase.DIPLOMACY
    assert state.turn == 2
    assert state.year == 2


def test_build_always_advances_turn() -> None:
    state = advance_phase(MatchState(status=MatchStatus.ACTIVE, phase=Phase.BUILD, turn=5, year=5))
    assert state.phase is Phase.DIPLOMACY
    assert state.turn == 6
    assert state.year == 6


def test_full_cycle_without_retreat_or_build_advances_turn_by_exactly_one() -> None:
    state = MatchState(status=MatchStatus.ACTIVE)
    state = advance_phase(state)  # -> ORDERS
    state = advance_phase(state)  # -> RESOLUTION
    state = advance_phase(state, has_dislodgements=False, adjustment_needed=False)  # -> next DIPLOMACY
    assert state.phase is Phase.DIPLOMACY
    assert state.turn == 2
    assert state.year == 2


def test_full_cycle_with_retreat_and_build_advances_turn_by_exactly_one() -> None:
    state = MatchState(status=MatchStatus.ACTIVE)
    state = advance_phase(state)  # -> ORDERS
    state = advance_phase(state)  # -> RESOLUTION
    state = advance_phase(state, has_dislodgements=True, adjustment_needed=True)  # -> RETREAT
    assert state.phase is Phase.RETREAT
    state = advance_phase(state, adjustment_needed=True)  # -> BUILD
    assert state.phase is Phase.BUILD
    state = advance_phase(state)  # -> next DIPLOMACY
    assert state.phase is Phase.DIPLOMACY
    assert state.turn == 2
    assert state.year == 2


def test_matches_engine_output_no_dislodgements_no_adjustment_needed() -> None:
    """A turn where every unit holds should skip both retreat and build."""
    initial = create_default_map()
    orders = {u.region_id: HoldOrder(unit_region_id=u.region_id) for u in initial.units}
    movement_result = resolve_movement(initial, orders)
    has_dislodgements = bool(movement_result.dislodged())
    adjustment_needed = any(delta != 0 for delta in compute_adjustment_deltas(initial).values())

    assert has_dislodgements is False
    assert adjustment_needed is False

    state = advance_phase(
        MatchState(status=MatchStatus.ACTIVE, phase=Phase.RESOLUTION),
        has_dislodgements=has_dislodgements,
        adjustment_needed=adjustment_needed,
    )
    assert state.phase is Phase.DIPLOMACY
