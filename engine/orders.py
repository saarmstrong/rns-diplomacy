"""Order types for unit commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union

from engine.model import UnitType


class OrderType(Enum):
    """The type of order a unit can receive."""

    HOLD = "hold"
    MOVE = "move"
    SUPPORT_HOLD = "support_hold"
    SUPPORT_MOVE = "support_move"
    # TODO: CONVOY deferred — will be added when sea transport mechanics are implemented


@dataclass(frozen=True)
class HoldOrder:
    """Order a unit to hold its current position."""

    unit_region_id: str
    order_type: OrderType = OrderType.HOLD


@dataclass(frozen=True)
class MoveOrder:
    """Order a unit to move to an adjacent region."""

    unit_region_id: str
    destination_id: str
    order_type: OrderType = OrderType.MOVE


@dataclass(frozen=True)
class SupportHoldOrder:
    """Order a unit to support another unit holding its position."""

    unit_region_id: str
    supported_region_id: str
    order_type: OrderType = OrderType.SUPPORT_HOLD


@dataclass(frozen=True)
class SupportMoveOrder:
    """Order a unit to support another unit's move."""

    unit_region_id: str
    supported_region_id: str
    supported_destination_id: str
    order_type: OrderType = OrderType.SUPPORT_MOVE


# Union of all order types for type annotations
Order = Union[HoldOrder, MoveOrder, SupportHoldOrder, SupportMoveOrder]

_ORDER_CLASSES_BY_TYPE: dict[OrderType, type] = {
    OrderType.HOLD: HoldOrder,
    OrderType.MOVE: MoveOrder,
    OrderType.SUPPORT_HOLD: SupportHoldOrder,
    OrderType.SUPPORT_MOVE: SupportMoveOrder,
}


def order_from_dict(raw: dict) -> Order:
    """Reconstruct a movement-phase Order from its canonicalized dict form.

    ``raw`` is the shape produced by canonicalizing an Order dataclass
    (e.g. via ``engine.hashing.canonicalize``): field names to values,
    with ``order_type`` holding the ``OrderType`` value string.
    """
    order_type = OrderType(raw["order_type"])
    cls = _ORDER_CLASSES_BY_TYPE[order_type]
    kwargs = {k: v for k, v in raw.items() if k != "order_type"}
    return cls(**kwargs)


@dataclass(frozen=True)
class RetreatOrder:
    """Order a dislodged unit to retreat, or disband if destination_id is None."""

    unit_region_id: str
    destination_id: str | None = None


@dataclass(frozen=True)
class BuildOrder:
    """Order to build a new unit at a controlled home supply center (adjustment phase)."""

    faction_id: str
    region_id: str
    unit_type: UnitType


@dataclass(frozen=True)
class DisbandOrder:
    """Order to disband an existing unit (adjustment phase)."""

    faction_id: str
    region_id: str
