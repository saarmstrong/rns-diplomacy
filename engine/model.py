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


@dataclass(frozen=True)
class Faction:
    """A player faction."""

    id: str
    name: str
    color: str
    abbreviation: str


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
