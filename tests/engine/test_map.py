"""Map integrity tests: adjacency symmetry, starting positions, supply centers."""

from __future__ import annotations

from engine.map import (
    ADJACENCY,
    FACTIONS,
    REGIONS,
    STARTING_SUPPLY_CENTERS,
    STARTING_UNITS,
    create_default_graph,
    create_default_map,
)
from engine.model import Phase, RegionType, UnitType
from engine.state import get_legal_moves


def test_seven_factions():
    assert len(FACTIONS) == 7
    for faction_id, faction in FACTIONS.items():
        assert faction.id == faction_id


def test_default_map_graph_validates_and_exposes_neighbors():
    graph = create_default_graph()
    assert graph.neighbors("vet_peak") == frozenset(ADJACENCY["vet_peak"])


def test_region_count_in_expected_range():
    assert 25 <= len(REGIONS) <= 35


def test_supply_center_count_in_expected_range():
    supply_centers = [r for r in REGIONS.values() if r.is_supply_center]
    assert 10 <= len(supply_centers) <= 15


def test_adjacency_is_symmetric():
    for region_id, neighbors in ADJACENCY.items():
        for neighbor_id in neighbors:
            assert region_id in ADJACENCY.get(neighbor_id, set()), (
                f"{region_id} -> {neighbor_id} is not reciprocated"
            )


def test_adjacency_has_no_self_loops():
    for region_id, neighbors in ADJACENCY.items():
        assert region_id not in neighbors


def test_all_adjacency_keys_and_neighbors_are_known_regions():
    for region_id, neighbors in ADJACENCY.items():
        assert region_id in REGIONS
        for neighbor_id in neighbors:
            assert neighbor_id in REGIONS


def test_every_region_appears_in_adjacency():
    for region_id in REGIONS:
        assert region_id in ADJACENCY


def test_every_region_has_at_least_one_neighbor():
    for region_id, neighbors in ADJACENCY.items():
        assert len(neighbors) > 0, f"{region_id} is isolated"


def test_coastal_regions_touch_at_least_one_sea():
    sea_ids = {r.id for r in REGIONS.values() if r.region_type == RegionType.SEA}
    for region in REGIONS.values():
        if region.region_type == RegionType.COASTAL:
            assert ADJACENCY[region.id] & sea_ids, f"{region.id} is coastal but touches no sea"


def test_sea_regions_never_touch_land_only():
    # Sea zones must never be adjacent to a pure LAND (inland) region.
    land_ids = {r.id for r in REGIONS.values() if r.region_type == RegionType.LAND}
    for region in REGIONS.values():
        if region.region_type == RegionType.SEA:
            assert not (ADJACENCY[region.id] & land_ids), f"{region.id} touches inland region"


def test_seven_factions_have_starting_units():
    factions_with_units = {u.faction_id for u in STARTING_UNITS}
    assert factions_with_units == set(FACTIONS.keys())


def test_starting_units_placed_on_legal_terrain():
    for unit in STARTING_UNITS:
        region = REGIONS[unit.region_id]
        if unit.unit_type == UnitType.ARMY:
            assert region.region_type != RegionType.SEA
        else:
            assert region.region_type != RegionType.LAND


def test_each_faction_has_a_viable_starting_position():
    state = create_default_map()
    for faction_id in FACTIONS:
        units = [unit for unit in state.units if unit.faction_id == faction_id]
        assert len(units) == 2
        assert any(get_legal_moves(state, unit) for unit in units)


def test_home_metadata_matches_starting_supply_centers():
    for faction_id, faction in FACTIONS.items():
        assert set(faction.home_centers) == {
            region_id for region_id, owner in STARTING_SUPPLY_CENTERS.items() if owner == faction_id
        }
        assert set(faction.home_regions) == set(faction.home_centers)
        for region_id in faction.home_regions:
            assert REGIONS[region_id].home_faction == faction_id


def test_starting_units_two_per_faction():
    counts: dict[str, int] = {}
    for unit in STARTING_UNITS:
        counts[unit.faction_id] = counts.get(unit.faction_id, 0) + 1
    for faction_id in FACTIONS:
        assert counts.get(faction_id, 0) == 2


def test_starting_supply_centers_owned_by_correct_faction_and_occupied():
    unit_region_ids = {u.region_id for u in STARTING_UNITS}
    for region_id, faction_id in STARTING_SUPPLY_CENTERS.items():
        assert region_id in REGIONS
        assert REGIONS[region_id].is_supply_center
        assert region_id in unit_region_ids

    # Each faction owns exactly its own two starting units' regions.
    for unit in STARTING_UNITS:
        assert STARTING_SUPPLY_CENTERS[unit.region_id] == unit.faction_id


def test_create_default_map_returns_fresh_state():
    state1 = create_default_map()
    state2 = create_default_map()
    assert state1 is not state2
    assert state1.units is not state2.units
    assert state1.phase == Phase.DIPLOMACY
    assert state1.turn == 1
    assert len(state1.units) == len(STARTING_UNITS)
    assert state1.supply_centers == STARTING_SUPPLY_CENTERS
