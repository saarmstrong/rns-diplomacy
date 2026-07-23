"""Domain model basics."""

from __future__ import annotations

import pytest

from engine.model import Faction, GameState, Phase, Region, RegionType, Unit, UnitType


def test_region_is_frozen_and_hashable():
    region = Region(id="a", name="A", region_type=RegionType.LAND)
    with pytest.raises(Exception):
        region.name = "B"  # type: ignore[misc]
    assert hash(region) is not None


def test_region_defaults():
    region = Region(id="a", name="A", region_type=RegionType.LAND)
    assert region.is_supply_center is False
    assert region.abbreviation == ""


def test_faction_is_frozen():
    faction = Faction(id="f", name="F", color="red", abbreviation="F")
    with pytest.raises(Exception):
        faction.name = "G"  # type: ignore[misc]


def test_unit_is_frozen_and_equal_by_value():
    u1 = Unit(unit_type=UnitType.ARMY, faction_id="f", region_id="a")
    u2 = Unit(unit_type=UnitType.ARMY, faction_id="f", region_id="a")
    assert u1 == u2
    assert u1 is not u2
    with pytest.raises(Exception):
        u1.region_id = "b"  # type: ignore[misc]


def test_game_state_defaults_are_independent_per_instance():
    s1 = GameState()
    s2 = GameState()
    s1.units.append(Unit(UnitType.ARMY, "f", "a"))
    assert s2.units == []
    assert s1.phase == Phase.DIPLOMACY
    assert s1.turn == 1
    assert s1.year == 1


def test_game_state_holds_regions_factions_and_supply_centers():
    region = Region(id="a", name="A", region_type=RegionType.LAND, is_supply_center=True)
    faction = Faction(id="f", name="F", color="red", abbreviation="F")
    unit = Unit(UnitType.ARMY, "f", "a")
    state = GameState(
        regions={"a": region},
        factions={"f": faction},
        units=[unit],
        supply_centers={"a": "f"},
    )
    assert state.regions["a"].is_supply_center
    assert state.factions["f"].name == "F"
    assert state.units == [unit]
    assert state.supply_centers["a"] == "f"


@pytest.mark.parametrize(
    "phase",
    [Phase.DIPLOMACY, Phase.ORDERS, Phase.RESOLUTION, Phase.RETREAT, Phase.BUILD],
)
def test_all_phases_are_valid_enum_members(phase):
    assert Phase(phase.value) is phase


@pytest.mark.parametrize("unit_type", [UnitType.ARMY, UnitType.FLEET])
def test_all_unit_types_are_valid_enum_members(unit_type):
    assert UnitType(unit_type.value) is unit_type


@pytest.mark.parametrize(
    "region_type", [RegionType.LAND, RegionType.SEA, RegionType.COASTAL]
)
def test_all_region_types_are_valid_enum_members(region_type):
    assert RegionType(region_type.value) is region_type
