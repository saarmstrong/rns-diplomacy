"""Tests for engine/state.py query and movement-validation helpers."""

from __future__ import annotations

from engine.map import create_default_map
from engine.model import Unit, UnitType
from engine.state import (
    can_move_to,
    get_legal_moves,
    get_supply_center_count,
    get_unit_at,
    get_units_for_faction,
)


def test_get_units_for_faction_returns_only_that_factions_units():
    state = create_default_map()
    units = get_units_for_faction(state, "vet")
    assert len(units) == 2
    assert all(u.faction_id == "vet" for u in units)


def test_get_units_for_faction_empty_for_unknown_faction():
    state = create_default_map()
    assert get_units_for_faction(state, "nope") == []


def test_get_supply_center_count_matches_starting_setup():
    state = create_default_map()
    for faction_id in state.factions:
        assert get_supply_center_count(state, faction_id) == 2


def test_get_unit_at_returns_unit_when_present():
    state = create_default_map()
    unit = get_unit_at(state, "vet_peak")
    assert unit is not None
    assert unit.faction_id == "vet"
    assert unit.unit_type == UnitType.ARMY


def test_get_unit_at_returns_none_when_empty():
    state = create_default_map()
    assert get_unit_at(state, "pale_ridge") is None


def test_can_move_to_true_for_legal_adjacent_move():
    state = create_default_map()
    army = Unit(UnitType.ARMY, "vet", "vet_peak")
    assert can_move_to(state, army, "pale_ridge") is True


def test_can_move_to_false_for_non_adjacent_region():
    state = create_default_map()
    army = Unit(UnitType.ARMY, "vet", "vet_peak")
    assert can_move_to(state, army, "irn_citadel") is False


def test_army_cannot_enter_sea():
    state = create_default_map()
    army = Unit(UnitType.ARMY, "vet", "vet_shore")
    assert can_move_to(state, army, "north_reach") is False


def test_fleet_cannot_enter_inland_land():
    state = create_default_map()
    fleet = Unit(UnitType.FLEET, "thr", "thr_cove")
    assert can_move_to(state, fleet, "gloomfen") is False


def test_fleet_can_enter_coastal_region():
    state = create_default_map()
    fleet = Unit(UnitType.FLEET, "dsk", "dsk_port")
    assert can_move_to(state, fleet, "dsk_mire") is False  # dsk_mire is LAND, not coastal
    assert can_move_to(state, fleet, "the_narrows") is True  # coastal


def test_can_move_to_false_for_unknown_destination():
    state = create_default_map()
    army = Unit(UnitType.ARMY, "vet", "vet_peak")
    assert can_move_to(state, army, "nowhere") is False


def test_get_legal_moves_only_returns_valid_destinations_sorted():
    state = create_default_map()
    army = Unit(UnitType.ARMY, "vet", "vet_peak")
    moves = get_legal_moves(state, army)
    assert moves == sorted(moves)
    assert "north_reach" not in moves  # sea, army can't go there
    assert "pale_ridge" in moves
    assert "stormveil" in moves  # coastal is fine for an army
