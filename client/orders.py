"""Local order construction, validation, and submission bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.model import GameState, Unit
from engine.orders import HoldOrder, MoveOrder, Order, OrderType, SupportHoldOrder, SupportMoveOrder
from engine.state import can_move_to
from protocol.messages import OrderReceipt, OrderState


def compose_order(unit: Unit, order_type: OrderType, *targets: str) -> Order:
    """Construct an order, raising ValueError when its required targets are absent."""
    if order_type is OrderType.HOLD and not targets:
        return HoldOrder(unit.region_id)
    if order_type is OrderType.MOVE and len(targets) == 1:
        return MoveOrder(unit.region_id, targets[0])
    if order_type is OrderType.SUPPORT_HOLD and len(targets) == 1:
        return SupportHoldOrder(unit.region_id, targets[0])
    if order_type is OrderType.SUPPORT_MOVE and len(targets) == 2:
        return SupportMoveOrder(unit.region_id, targets[0], targets[1])
    raise ValueError(f"invalid targets for {order_type.value}")


def available_moves(unit: Unit, state: GameState) -> tuple[str, ...]:
    """Return legal destinations in deterministic order."""
    return tuple(region_id for region_id in sorted(state.regions) if can_move_to(state, unit, region_id))


def validate_order_locally(order: Order, state: GameState, faction_id: str | None = None) -> bool:
    """Validate basic ownership and geometry before sending an order.

    The engine remains authoritative: this intentionally does not try to
    predict simultaneous resolution or support cuts.
    """
    unit = next((u for u in state.units if u.region_id == order.unit_region_id), None)
    if unit is None or (faction_id is not None and unit.faction_id != faction_id):
        return False
    if isinstance(order, HoldOrder):
        return True
    if isinstance(order, MoveOrder):
        return can_move_to(state, unit, order.destination_id)
    supported = next((u for u in state.units if u.region_id == order.supported_region_id), None)
    if supported is None:
        return False
    if isinstance(order, SupportHoldOrder):
        return can_move_to(state, unit, order.supported_region_id)
    return can_move_to(state, unit, order.supported_destination_id)


@dataclass
class SubmissionTracker:
    """Tracks a player's monotonically increasing submission revisions."""

    revision: int = 0
    receipts: dict[int, OrderReceipt] = field(default_factory=dict)

    def next_revision(self) -> int:
        self.revision += 1
        return self.revision

    def record_receipt(self, receipt: OrderReceipt) -> None:
        self.revision = max(self.revision, receipt.revision)
        self.receipts[receipt.revision] = receipt

    @property
    def status(self) -> OrderState | None:
        receipt = self.receipts.get(self.revision)
        return receipt.state if receipt else None
