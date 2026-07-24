"""Game state queries and movement validation."""

from __future__ import annotations

from engine.map import ADJACENCY
from engine.model import GameState, RegionType, Unit, UnitType
from engine.orders import Order


def get_units_for_faction(state: GameState, faction_id: str) -> list[Unit]:
    """Return all units belonging to a faction."""
    return [u for u in state.units if u.faction_id == faction_id]


def get_supply_center_count(state: GameState, faction_id: str) -> int:
    """Return the number of supply centers controlled by a faction."""
    return sum(1 for owner in state.supply_centers.values() if owner == faction_id)


def get_unit_at(state: GameState, region_id: str) -> Unit | None:
    """Return the unit occupying a region, or None."""
    for u in state.units:
        if u.region_id == region_id:
            return u
    return None


def can_move_to(state: GameState, unit: Unit, destination_id: str) -> bool:
    """Check whether a unit can legally move to a destination.

    Validates:
        - Destination is adjacent to the unit's current region
        - Armies cannot enter sea regions
        - Fleets cannot enter inland (non-coastal, non-sea) regions
    """
    # Adjacency check
    neighbors = ADJACENCY.get(unit.region_id)
    if neighbors is None or destination_id not in neighbors:
        return False

    dest_region = state.regions.get(destination_id)
    if dest_region is None:
        return False

    # Unit type / terrain checks
    if unit.unit_type == UnitType.ARMY:
        # Armies can go to land or coastal, never sea
        if dest_region.region_type == RegionType.SEA:
            return False
    elif unit.unit_type == UnitType.FLEET:
        # Fleets can go to sea or coastal, never inland land
        if dest_region.region_type == RegionType.LAND:
            return False

    return True


def get_legal_moves(state: GameState, unit: Unit) -> list[str]:
    """Return all region IDs the unit can legally move to."""
    neighbors = ADJACENCY.get(unit.region_id, set())
    return [rid for rid in sorted(neighbors) if can_move_to(state, unit, rid)]


def validate_orders(state: GameState, faction_id: str, orders: tuple[Order, ...]) -> tuple[str, ...]:
    """Structural validation shared by the coordinator (before persisting) and the client
    (before submitting): every order must reference one of the faction's own units, at most
    once each. Deeper legality (adjacency, unit type, occupied destinations, ...) is
    deliberately not checked here — the adjudication engine normalizes illegal orders to
    Hold at resolution time (``engine/adjudicator.py``).
    """
    owned_regions = {u.region_id for u in get_units_for_faction(state, faction_id)}
    errors: list[str] = []
    seen: set[str] = set()
    for order in orders:
        region_id = order.unit_region_id
        if region_id not in owned_regions:
            errors.append(f"{region_id} is not one of {faction_id}'s units")
        if region_id in seen:
            errors.append(f"duplicate order for unit at {region_id}")
        seen.add(region_id)
    return tuple(errors)
