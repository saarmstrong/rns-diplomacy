"""Order types for unit commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union


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
