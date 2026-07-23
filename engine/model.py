"""Core domain models for RNS Diplomacy."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RegionType(Enum):
    """Type of map region, governing which units can occupy it."""

    LAND = "land"
    SEA = "sea"
    COASTAL = "coastal"


class UnitType(Enum):
    """Type of military unit."""

    ARMY = "army"
    FLEET = "fleet"


class Phase(Enum):
    """Game phase within a turn."""

    DIPLOMACY = "diplomacy"
    ORDERS = "orders"
    RESOLUTION = "resolution"
    RETREAT = "retreat"
    BUILD = "build"


@dataclass(frozen=True)
class Region:
    """A region on the game map."""

    id: str
    name: str
    region_type: RegionType
    is_supply_center: bool = False
    abbreviation: str = ""
    home_faction: str | None = None


@dataclass(frozen=True)
class Faction:
    """A player faction and its fixed home territories and centers."""

    id: str
    name: str
    color: str
    abbreviation: str
    home_regions: tuple[str, ...] = ()
    home_centers: tuple[str, ...] = ()


@dataclass(frozen=True)
class MapGraph:
    """Immutable map topology with validated, symmetric adjacency."""

    regions: dict[str, Region]
    adjacency: dict[str, frozenset[str]]

    def neighbors(self, region_id: str) -> frozenset[str]:
        """Return the neighboring region IDs, or raise for an unknown region."""
        if region_id not in self.regions:
            raise KeyError(region_id)
        return self.adjacency.get(region_id, frozenset())

    def validate(self) -> None:
        """Raise ValueError if topology or terrain invariants are violated.

        Coastal territories are land that fleets may occupy. They therefore
        need both a sea-capable neighbour and a land-capable neighbour; this
        catches accidental isolated coast definitions while allowing island
        chains made of coastal territories.
        """
        for region_id, region in self.regions.items():
            neighbors = self.adjacency.get(region_id, frozenset())
            if region.region_type is RegionType.SEA and region.is_supply_center:
                raise ValueError(f"sea region {region_id!r} cannot be a supply center")
            if region.region_type is RegionType.COASTAL:
                neighbor_types = {self.regions[n].region_type for n in neighbors if n in self.regions}
                if RegionType.SEA not in neighbor_types:
                    raise ValueError(f"coastal region {region_id!r} has no sea neighbour")
                if not neighbor_types.intersection({RegionType.LAND, RegionType.COASTAL}):
                    raise ValueError(f"coastal region {region_id!r} has no land neighbour")
        for region_id, neighbors in self.adjacency.items():
            if region_id not in self.regions:
                raise ValueError(f"adjacency has unknown region {region_id!r}")
            for neighbor in neighbors:
                if neighbor not in self.regions:
                    raise ValueError(f"{region_id!r} has unknown neighbor {neighbor!r}")
                if neighbor == region_id:
                    raise ValueError(f"{region_id!r} is adjacent to itself")
                if region_id not in self.adjacency.get(neighbor, frozenset()):
                    raise ValueError(f"edge {region_id!r}-{neighbor!r} is not symmetric")


@dataclass(frozen=True)
class Unit:
    """A military unit on the board."""

    unit_type: UnitType
    faction_id: str
    region_id: str


@dataclass
class GameState:
    """Complete snapshot of the game at a point in time."""

    regions: dict[str, Region] = field(default_factory=dict)
    factions: dict[str, Faction] = field(default_factory=dict)
    units: list[Unit] = field(default_factory=list)
    supply_centers: dict[str, str] = field(default_factory=dict)  # region_id -> faction_id
    phase: Phase = Phase.DIPLOMACY
    turn: int = 1
    year: int = 1  # In-game year for flavor
