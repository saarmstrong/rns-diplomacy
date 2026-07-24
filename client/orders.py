"""Local order composition — helps a player build valid orders before submitting them.

This is a convenience layer only: the coordinator does its own
authoritative validation on receipt (``coordinator/orders.py``), and the
adjudication engine normalizes any remaining illegal order to Hold at
resolution time. Catching mistakes here just saves a round trip.
"""

from __future__ import annotations

from engine.map import ADJACENCY
from engine.model import GameState, Unit
from engine.orders import HoldOrder, MoveOrder, Order, OrderType, SupportHoldOrder, SupportMoveOrder
from engine.state import can_move_to, get_legal_moves, get_unit_at, get_units_for_faction, validate_orders

__all__ = [
    "compose_order",
    "get_move_options",
    "get_orderable_units",
    "get_support_hold_options",
    "get_support_move_options",
    "validate_orders",
]


def get_orderable_units(state: GameState, faction_id: str) -> list[Unit]:
    """A faction's units, in a stable display order."""
    return sorted(get_units_for_faction(state, faction_id), key=lambda u: u.region_id)


def get_move_options(state: GameState, unit: Unit) -> list[str]:
    """Region IDs ``unit`` could legally move to."""
    return get_legal_moves(state, unit)


def get_support_hold_options(state: GameState, unit: Unit) -> list[str]:
    """Region IDs holding a unit that ``unit`` could legally support to hold."""
    return [
        region_id
        for region_id in sorted(ADJACENCY.get(unit.region_id, set()))
        if get_unit_at(state, region_id) is not None and can_move_to(state, unit, region_id)
    ]


def get_support_move_options(state: GameState, unit: Unit) -> list[tuple[str, str]]:
    """(supported_region_id, destination_id) pairs ``unit`` could legally support a move into."""
    options: list[tuple[str, str]] = []
    for supported_region_id in sorted(ADJACENCY.get(unit.region_id, set())):
        supported_unit = get_unit_at(state, supported_region_id)
        if supported_unit is None:
            continue
        for destination_id in get_legal_moves(state, supported_unit):
            if can_move_to(state, unit, destination_id):
                options.append((supported_region_id, destination_id))
    return options


def compose_order(unit: Unit, order_type: OrderType, **kwargs: str) -> Order:
    """Build an Order for ``unit`` of the given type.

    ``kwargs`` supplies the target fields beyond ``unit_region_id``:
    MOVE needs ``destination_id``; SUPPORT_HOLD needs
    ``supported_region_id``; SUPPORT_MOVE needs ``supported_region_id``
    and ``supported_destination_id``.
    """
    if order_type is OrderType.HOLD:
        return HoldOrder(unit_region_id=unit.region_id)
    if order_type is OrderType.MOVE:
        return MoveOrder(unit_region_id=unit.region_id, destination_id=kwargs["destination_id"])
    if order_type is OrderType.SUPPORT_HOLD:
        return SupportHoldOrder(unit_region_id=unit.region_id, supported_region_id=kwargs["supported_region_id"])
    if order_type is OrderType.SUPPORT_MOVE:
        return SupportMoveOrder(
            unit_region_id=unit.region_id,
            supported_region_id=kwargs["supported_region_id"],
            supported_destination_id=kwargs["supported_destination_id"],
        )
    raise ValueError(f"unsupported order type: {order_type}")
