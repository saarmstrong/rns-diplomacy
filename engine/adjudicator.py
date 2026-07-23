"""Order adjudication engine.

Resolves submitted orders for a phase: detects conflicts, computes
support strengths, resolves battles, and produces the resulting state
delta. Implements the core Diplomacy resolution algorithm adapted for
the custom map (convoys deferred, see engine/orders.py).

Every function here is a pure function of its inputs: no I/O, no
randomness, no clocks. Given the same GameState and orders, the result
is always identical, so any client can reproduce the coordinator's
adjudication and verify it independently.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace

from engine.map import ADJACENCY, STARTING_SUPPLY_CENTERS
from engine.model import GameState, RegionType, Unit, UnitType
from engine.orders import (
    BuildOrder,
    DisbandOrder,
    HoldOrder,
    MoveOrder,
    Order,
    RetreatOrder,
    SupportHoldOrder,
    SupportMoveOrder,
)
from engine.state import can_move_to, get_supply_center_count, get_units_for_faction

# ---------------------------------------------------------------------------
# Movement phase — result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitOrderResult:
    """The adjudicated outcome for a single unit's order in the movement phase."""

    region_id: str
    faction_id: str
    unit_type: UnitType
    order: Order
    normalized: bool
    moved: bool
    final_region_id: str
    dislodged: bool
    dislodged_by: str | None
    support_cut: bool


@dataclass(frozen=True)
class MovementResult:
    """Complete adjudication result for a movement phase."""

    unit_results: tuple[UnitOrderResult, ...]
    contested_regions: frozenset[str]

    def dislodged(self) -> list[UnitOrderResult]:
        return [r for r in self.unit_results if r.dislodged]


# ---------------------------------------------------------------------------
# Movement phase — order normalization
# ---------------------------------------------------------------------------


def _normalize_order(state: GameState, unit: Unit, order: Order | None) -> tuple[Order, bool]:
    """Return (effective_order, was_normalized). Illegal or missing orders become Hold."""
    if order is None:
        return HoldOrder(unit.region_id), True
    if order.unit_region_id != unit.region_id:
        return HoldOrder(unit.region_id), True

    if isinstance(order, HoldOrder):
        return order, False

    if isinstance(order, MoveOrder):
        if can_move_to(state, unit, order.destination_id):
            return order, False
        return HoldOrder(unit.region_id), True

    if isinstance(order, SupportHoldOrder):
        supported = _unit_at(state, order.supported_region_id)
        if supported is None:
            return HoldOrder(unit.region_id), True
        if not can_move_to(state, unit, order.supported_region_id):
            return HoldOrder(unit.region_id), True
        return order, False

    if isinstance(order, SupportMoveOrder):
        supported = _unit_at(state, order.supported_region_id)
        if supported is None:
            return HoldOrder(unit.region_id), True
        if not can_move_to(state, unit, order.supported_destination_id):
            return HoldOrder(unit.region_id), True
        return order, False

    return HoldOrder(unit.region_id), True


def _unit_at(state: GameState, region_id: str) -> Unit | None:
    for u in state.units:
        if u.region_id == region_id:
            return u
    return None


# ---------------------------------------------------------------------------
# Movement phase — resolution
# ---------------------------------------------------------------------------


def resolve_movement(state: GameState, orders: dict[str, Order]) -> MovementResult:
    """Adjudicate a single movement phase.

    ``orders`` maps a unit's current region_id to its submitted order.
    Units with no entry default to Hold. Illegal orders are normalized
    to Hold before resolution.
    """
    units_by_region = {u.region_id: u for u in sorted(state.units, key=lambda u: u.region_id)}

    effective_orders: dict[str, Order] = {}
    normalized_flags: dict[str, bool] = {}
    for region_id, unit in units_by_region.items():
        effective_order, was_normalized = _normalize_order(state, unit, orders.get(region_id))
        effective_orders[region_id] = effective_order
        normalized_flags[region_id] = was_normalized

    support_hold_orders = {
        r: o for r, o in effective_orders.items() if isinstance(o, SupportHoldOrder)
    }
    support_move_orders = {
        r: o for r, o in effective_orders.items() if isinstance(o, SupportMoveOrder)
    }
    all_support_orders: dict[str, SupportHoldOrder | SupportMoveOrder] = {
        **support_hold_orders,
        **support_move_orders,
    }

    # --- Cut support: any move order into a supporter's region cuts it,
    # unless that move's origin is exactly the region the support-move was
    # aimed at (a unit cannot cut the support of an attack against itself).
    cut: dict[str, bool] = {}
    for supporter_region, sorder in all_support_orders.items():
        is_cut = False
        for attacker_region, order in effective_orders.items():
            if not isinstance(order, MoveOrder):
                continue
            if order.destination_id != supporter_region:
                continue
            if attacker_region == supporter_region:
                continue
            if (
                isinstance(sorder, SupportMoveOrder)
                and sorder.supported_destination_id == attacker_region
            ):
                continue
            is_cut = True
            break
        cut[supporter_region] = is_cut

    # --- Attack strength for each move order.
    attack_strength: dict[str, int] = {}
    for region, order in effective_orders.items():
        if not isinstance(order, MoveOrder):
            continue
        supports = sum(
            1
            for s in support_move_orders.values()
            if s.supported_region_id == region
            and s.supported_destination_id == order.destination_id
            and not cut[s.unit_region_id]
        )
        attack_strength[region] = 1 + supports

    # --- Hold strength for stationary units (Hold / Support*), matched
    # only against units whose order is literally Hold: a unit that
    # attempted a move and failed does NOT retroactively benefit from a
    # support-hold order aimed at it — it defends with base strength 1.
    hold_strength: dict[str, int] = {}
    for region, order in effective_orders.items():
        if isinstance(order, MoveOrder):
            continue
        supports = sum(
            1
            for s in support_hold_orders.values()
            if s.supported_region_id == region and not cut[s.unit_region_id]
        )
        hold_strength[region] = 1 + supports

    resolved: dict[str, bool] = {}
    in_progress: set[str] = set()

    def resolve_move(region: str) -> bool:
        if region in resolved:
            return resolved[region]
        if region in in_progress:
            # Circular movement (A->B->C->A): assume success while the
            # cycle is being traced. Correct for unopposed rings; genuine
            # paradoxes (an opposed cycle) are not guaranteed DATC-exact.
            return True
        in_progress.add(region)

        order = effective_orders[region]
        assert isinstance(order, MoveOrder)
        dest = order.destination_id
        my_strength = attack_strength[region]
        my_faction = units_by_region[region].faction_id

        defender_strength: int | None = None
        defender_faction: str | None = None
        head_to_head_strength: int | None = None

        occupant = units_by_region.get(dest)
        if occupant is not None:
            occ_order = effective_orders.get(dest)
            if isinstance(occ_order, MoveOrder):
                if occ_order.destination_id == region:
                    head_to_head_strength = attack_strength[dest]
                else:
                    if not resolve_move(dest):
                        defender_strength = 1
                        defender_faction = occupant.faction_id
            else:
                defender_strength = hold_strength[dest]
                defender_faction = occupant.faction_id

        other_attackers = [
            attack_strength[r]
            for r, o in effective_orders.items()
            if isinstance(o, MoveOrder) and o.destination_id == dest and r != region
        ]

        success = True
        if defender_strength is not None:
            if defender_faction == my_faction or my_strength <= defender_strength:
                success = False
        if head_to_head_strength is not None and my_strength <= head_to_head_strength:
            success = False
        if any(my_strength <= s for s in other_attackers):
            success = False

        in_progress.discard(region)
        resolved[region] = success
        return success

    for region, order in sorted(effective_orders.items()):
        if isinstance(order, MoveOrder):
            resolve_move(region)

    # --- Assemble results.
    winners: dict[str, str] = {}
    for region, order in effective_orders.items():
        if isinstance(order, MoveOrder) and resolved.get(region):
            winners[order.destination_id] = region

    move_targets = Counter(
        o.destination_id for o in effective_orders.values() if isinstance(o, MoveOrder)
    )
    contested_regions = frozenset(dest for dest, count in move_targets.items() if count >= 2)

    unit_results: list[UnitOrderResult] = []
    for region in sorted(effective_orders.keys()):
        order = effective_orders[region]
        unit = units_by_region[region]

        if isinstance(order, MoveOrder) and resolved.get(region):
            moved = True
            final_region = order.destination_id
        else:
            moved = False
            final_region = region

        dislodged = not moved and region in winners
        dislodged_by = winners[region] if dislodged else None

        support_cut = (
            cut.get(region, False) if isinstance(order, (SupportHoldOrder, SupportMoveOrder)) else False
        )

        unit_results.append(
            UnitOrderResult(
                region_id=region,
                faction_id=unit.faction_id,
                unit_type=unit.unit_type,
                order=order,
                normalized=normalized_flags[region],
                moved=moved,
                final_region_id=final_region,
                dislodged=dislodged,
                dislodged_by=dislodged_by,
                support_cut=support_cut,
            )
        )

    return MovementResult(
        unit_results=tuple(unit_results),
        contested_regions=contested_regions,
    )


def apply_movement_result(state: GameState, result: MovementResult) -> GameState:
    """Return the new state after movement, omitting dislodged units (pending retreat)."""
    new_units = [
        Unit(unit_type=r.unit_type, faction_id=r.faction_id, region_id=r.final_region_id)
        for r in result.unit_results
        if not r.dislodged
    ]
    new_units.sort(key=lambda u: u.region_id)
    return replace(state, units=new_units)


# ---------------------------------------------------------------------------
# Retreat phase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetreatUnitResult:
    region_id: str
    faction_id: str
    unit_type: UnitType
    destination_id: str | None
    destroyed: bool


@dataclass(frozen=True)
class RetreatResult:
    results: tuple[RetreatUnitResult, ...]


def get_retreat_options(state: GameState, movement_result: MovementResult) -> dict[str, list[str]]:
    """Legal retreat destinations per dislodged unit's current (pre-retreat) region.

    Excludes regions occupied by a surviving unit, regions contested by a
    standoff this turn, and the attacker's origin region.
    """
    final_positions = {r.final_region_id for r in movement_result.unit_results if not r.dislodged}

    options: dict[str, list[str]] = {}
    for r in movement_result.unit_results:
        if not r.dislodged:
            continue
        unit = Unit(unit_type=r.unit_type, faction_id=r.faction_id, region_id=r.region_id)
        legal = []
        for candidate in sorted(ADJACENCY.get(r.region_id, set())):
            if not can_move_to(state, unit, candidate):
                continue
            if candidate in final_positions:
                continue
            if candidate in movement_result.contested_regions:
                continue
            if candidate == r.dislodged_by:
                continue
            legal.append(candidate)
        options[r.region_id] = legal
    return options


def resolve_retreats(
    state: GameState,
    movement_result: MovementResult,
    retreat_orders: dict[str, RetreatOrder],
) -> RetreatResult:
    """Adjudicate the retreat phase.

    Missing orders and orders to illegal destinations are normalized to
    disband. Two or more dislodged units retreating to the same region
    all fail and are destroyed instead.
    """
    options = get_retreat_options(state, movement_result)
    dislodged = [r for r in movement_result.unit_results if r.dislodged]

    chosen: dict[str, str | None] = {}
    for r in sorted(dislodged, key=lambda x: x.region_id):
        order = retreat_orders.get(r.region_id)
        legal = options[r.region_id]
        if order is None or order.destination_id is None:
            chosen[r.region_id] = None
        elif order.destination_id in legal:
            chosen[r.region_id] = order.destination_id
        else:
            chosen[r.region_id] = None

    dest_counts = Counter(d for d in chosen.values() if d is not None)

    results = []
    for r in sorted(dislodged, key=lambda x: x.region_id):
        dest = chosen[r.region_id]
        if dest is not None and dest_counts[dest] > 1:
            dest = None
        results.append(
            RetreatUnitResult(
                region_id=r.region_id,
                faction_id=r.faction_id,
                unit_type=r.unit_type,
                destination_id=dest,
                destroyed=(dest is None),
            )
        )
    return RetreatResult(results=tuple(results))


def apply_retreat_result(state: GameState, result: RetreatResult) -> GameState:
    """Return the new state with surviving retreaters placed on the board.

    ``state`` must already have dislodged units removed (i.e. be the
    output of ``apply_movement_result``).
    """
    new_units = list(state.units)
    for r in result.results:
        if not r.destroyed:
            new_units.append(Unit(unit_type=r.unit_type, faction_id=r.faction_id, region_id=r.destination_id))
    new_units.sort(key=lambda u: u.region_id)
    return replace(state, units=new_units)


# ---------------------------------------------------------------------------
# Supply center ownership
# ---------------------------------------------------------------------------


def update_supply_center_ownership(state: GameState) -> GameState:
    """Transfer ownership of any supply center currently occupied to the occupier."""
    new_centers = dict(state.supply_centers)
    for unit in sorted(state.units, key=lambda u: u.region_id):
        region = state.regions.get(unit.region_id)
        if region is not None and region.is_supply_center:
            new_centers[unit.region_id] = unit.faction_id
    return replace(state, supply_centers=new_centers)


# ---------------------------------------------------------------------------
# Adjustment phase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildResult:
    faction_id: str
    region_id: str
    unit_type: UnitType
    accepted: bool
    reason: str = ""


@dataclass(frozen=True)
class DisbandResult:
    faction_id: str
    region_id: str


@dataclass(frozen=True)
class AdjustmentResult:
    builds: tuple[BuildResult, ...]
    disbands: tuple[DisbandResult, ...]


def default_home_centers() -> dict[str, set[str]]:
    """Each faction's home supply centers, derived from the starting map."""
    homes: dict[str, set[str]] = defaultdict(set)
    for region_id, faction_id in STARTING_SUPPLY_CENTERS.items():
        homes[faction_id].add(region_id)
    return dict(homes)


def compute_adjustment_deltas(state: GameState) -> dict[str, int]:
    """Per faction: positive = builds available, negative = disbands required."""
    return {
        faction_id: get_supply_center_count(state, faction_id) - len(get_units_for_faction(state, faction_id))
        for faction_id in sorted(state.factions.keys())
    }


def resolve_adjustments(
    state: GameState,
    build_orders: list[BuildOrder],
    disband_orders: list[DisbandOrder],
    home_centers: dict[str, set[str]] | None = None,
) -> AdjustmentResult:
    """Adjudicate the adjustment phase: process builds up to each faction's
    available count, and disbands up to each faction's required count.

    Builds beyond the available count, or to an illegal region, are
    rejected. If a faction owes more disbands than it explicitly ordered,
    the shortfall is made up deterministically (units sorted by region_id
    descending) — the deadline-default policy for civil disorder.
    """
    if home_centers is None:
        home_centers = default_home_centers()

    deltas = compute_adjustment_deltas(state)
    occupied = {u.region_id for u in state.units}

    builds_by_faction: dict[str, list[BuildOrder]] = defaultdict(list)
    for b in build_orders:
        builds_by_faction[b.faction_id].append(b)

    builds: list[BuildResult] = []
    for faction_id in sorted(state.factions.keys()):
        allowed = max(deltas.get(faction_id, 0), 0)
        used_regions: set[str] = set()
        accepted_count = 0
        for order in builds_by_faction.get(faction_id, []):
            if accepted_count >= allowed:
                builds.append(BuildResult(faction_id, order.region_id, order.unit_type, False, "build limit reached"))
                continue
            region = state.regions.get(order.region_id)
            home = home_centers.get(faction_id, set())
            if region is None or not region.is_supply_center:
                builds.append(BuildResult(faction_id, order.region_id, order.unit_type, False, "not a supply center"))
                continue
            if order.region_id not in home:
                builds.append(BuildResult(faction_id, order.region_id, order.unit_type, False, "not a home center"))
                continue
            if state.supply_centers.get(order.region_id) != faction_id:
                builds.append(BuildResult(faction_id, order.region_id, order.unit_type, False, "center not controlled"))
                continue
            if order.region_id in occupied or order.region_id in used_regions:
                builds.append(BuildResult(faction_id, order.region_id, order.unit_type, False, "region occupied"))
                continue
            if order.unit_type == UnitType.ARMY and region.region_type == RegionType.SEA:
                builds.append(BuildResult(faction_id, order.region_id, order.unit_type, False, "invalid terrain"))
                continue
            if order.unit_type == UnitType.FLEET and region.region_type == RegionType.LAND:
                builds.append(BuildResult(faction_id, order.region_id, order.unit_type, False, "invalid terrain"))
                continue
            builds.append(BuildResult(faction_id, order.region_id, order.unit_type, True))
            used_regions.add(order.region_id)
            accepted_count += 1

    disbands_by_faction: dict[str, list[DisbandOrder]] = defaultdict(list)
    for d in disband_orders:
        disbands_by_faction[d.faction_id].append(d)

    disbands: list[DisbandResult] = []
    for faction_id in sorted(state.factions.keys()):
        required = max(-deltas.get(faction_id, 0), 0)
        if required == 0:
            continue
        unit_regions = {u.region_id for u in get_units_for_faction(state, faction_id)}
        chosen: list[str] = []
        for order in disbands_by_faction.get(faction_id, []):
            if len(chosen) >= required:
                break
            if order.region_id in unit_regions and order.region_id not in chosen:
                chosen.append(order.region_id)
        if len(chosen) < required:
            remaining = sorted((r for r in unit_regions if r not in chosen), reverse=True)
            for region_id in remaining:
                if len(chosen) >= required:
                    break
                chosen.append(region_id)
        for region_id in sorted(chosen):
            disbands.append(DisbandResult(faction_id, region_id))

    return AdjustmentResult(builds=tuple(builds), disbands=tuple(disbands))


def apply_adjustment_result(state: GameState, result: AdjustmentResult) -> GameState:
    """Return the new state with accepted builds added and disbands removed."""
    disbanded_regions = {d.region_id for d in result.disbands}
    new_units = [u for u in state.units if u.region_id not in disbanded_regions]
    for b in result.builds:
        if b.accepted:
            new_units.append(Unit(unit_type=b.unit_type, faction_id=b.faction_id, region_id=b.region_id))
    new_units.sort(key=lambda u: u.region_id)
    return replace(state, units=new_units)
