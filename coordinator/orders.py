"""Order submission handling — validation, revisions, and receipts.

Accepts ORDER_SUBMIT/ORDER_UPDATE, tracks revisions per player per turn,
and answers ORDER_CANCEL / ORDER_STATUS. Only movement-phase orders
travel over these messages — see engine.orders.Order (Hold/Move/
SupportHold/SupportMove). Retreat and adjustment orders are collected
separately (TODO: RetreatOrder/BuildOrder/DisbandOrder have no wire
discriminator yet; the coordinator currently applies the engine's
deterministic defaults for those phases instead of collecting them).

Full move-legality (adjacency, unit type, occupied destinations, ...) is
deliberately not re-checked here — the adjudication engine already
normalizes illegal orders to Hold at resolution time
(``engine/adjudicator.py``). This layer only guards the one thing that
must be rejected before persistence: a player ordering a unit that isn't
theirs.
"""

from __future__ import annotations

import msgpack

from coordinator.persistence import MatchStore
from engine.hashing import canonicalize, hash_orders
from engine.model import GameState, Phase
from engine.orders import Order, order_from_dict
from engine.state import get_units_for_faction
from protocol.messages import OrderCancel, OrderReceipt, OrderState, OrderStatus, OrderSubmit, OrderUpdate


def encode_orders(orders: tuple[Order, ...]) -> bytes:
    """Canonical msgpack encoding of an order set, for persistence."""
    return msgpack.packb([canonicalize(o) for o in orders], use_bin_type=True)


def decode_orders(blob: bytes) -> tuple[Order, ...]:
    """Reverse of ``encode_orders``."""
    return tuple(order_from_dict(item) for item in msgpack.unpackb(blob, raw=False))


def validate_orders(state: GameState, faction_id: str, orders: tuple[Order, ...]) -> tuple[str, ...]:
    """Structural validation: every order must reference one of the faction's own units, at most once."""
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


def _rejected_receipt(request: OrderSubmit | OrderUpdate, sequence_number: int, timestamp: float) -> OrderReceipt:
    return OrderReceipt(
        sequence_number=sequence_number,
        timestamp=timestamp,
        match_id=request.match_id,
        revision=request.revision,
        order_hash=hash_orders(request.orders),
        state=OrderState.REJECTED,
    )


def handle_order_submit(
    store: MatchStore,
    game_state: GameState,
    match_id: str,
    public_key: bytes,
    request: OrderSubmit | OrderUpdate,
    *,
    current_turn: int,
    current_phase: Phase,
    sequence_number: int,
    timestamp: float,
) -> OrderReceipt:
    """Handle ORDER_SUBMIT or ORDER_UPDATE: validate, persist a new revision, and receipt it.

    Rejected submissions (wrong phase, unknown player, non-increasing
    revision, or orders that reference another faction's units) are still
    persisted for the audit trail when a player is known, but never
    supersede the player's last accepted revision.
    """
    faction_id = store.get_player_faction(match_id, public_key)
    if faction_id is None or current_phase is not Phase.ORDERS:
        return _rejected_receipt(request, sequence_number, timestamp)

    previous = store.get_latest_order(match_id, public_key, current_turn)
    if previous is not None and request.revision <= previous.revision:
        return _rejected_receipt(request, sequence_number, timestamp)

    order_hash = hash_orders(request.orders)
    errors = validate_orders(game_state, faction_id, request.orders)
    status = OrderState.REJECTED if errors else OrderState.ACCEPTED

    if status is OrderState.ACCEPTED and previous is not None:
        store.update_order_status(match_id, public_key, current_turn, previous.revision, OrderState.SUPERSEDED.value)

    store.record_order_submission(
        match_id,
        public_key,
        current_turn,
        request.revision,
        encode_orders(request.orders),
        order_hash,
        status=status.value,
        submitted_at=timestamp,
    )

    return OrderReceipt(
        sequence_number=sequence_number,
        timestamp=timestamp,
        match_id=match_id,
        revision=request.revision,
        order_hash=order_hash,
        state=status,
    )


def handle_order_cancel(
    store: MatchStore,
    match_id: str,
    public_key: bytes,
    request: OrderCancel,
    *,
    current_turn: int,
    current_phase: Phase,
    sequence_number: int,
    timestamp: float,
) -> OrderReceipt:
    """Handle ORDER_CANCEL: mark a player's current-turn orders as cancelled.

    A cancelled player is treated the same as one who never submitted —
    their units default to Hold at adjudication.
    """
    faction_id = store.get_player_faction(match_id, public_key)
    if faction_id is None or current_phase is not Phase.ORDERS:
        return OrderReceipt(
            sequence_number=sequence_number,
            timestamp=timestamp,
            match_id=match_id,
            revision=request.revision,
            order_hash="",
            state=OrderState.REJECTED,
        )

    previous = store.get_latest_order(match_id, public_key, current_turn)
    if previous is not None and previous.status == OrderState.ACCEPTED.value:
        store.update_order_status(match_id, public_key, current_turn, previous.revision, OrderState.SUPERSEDED.value)

    empty_order_hash = hash_orders(())
    store.record_order_submission(
        match_id,
        public_key,
        current_turn,
        request.revision,
        encode_orders(()),
        empty_order_hash,
        status=OrderState.SUPERSEDED.value,
        submitted_at=timestamp,
    )

    return OrderReceipt(
        sequence_number=sequence_number,
        timestamp=timestamp,
        match_id=match_id,
        revision=request.revision,
        order_hash=empty_order_hash,
        state=OrderState.SUPERSEDED,
    )


def handle_order_status(
    store: MatchStore,
    match_id: str,
    public_key: bytes,
    turn: int,
    *,
    sequence_number: int,
    timestamp: float,
) -> OrderStatus:
    """Answer an ORDER_STATUS request with the player's latest revision for ``turn``."""
    latest = store.get_latest_order(match_id, public_key, turn)
    if latest is None:
        return OrderStatus(
            sequence_number=sequence_number,
            timestamp=timestamp,
            match_id=match_id,
            revision=0,
            state=OrderState.SENT,
            order_hash=None,
        )
    return OrderStatus(
        sequence_number=sequence_number,
        timestamp=timestamp,
        match_id=match_id,
        revision=latest.revision,
        state=OrderState(latest.status),
        order_hash=latest.order_hash,
    )


def get_effective_orders(store: MatchStore, match_id: str, turn: int) -> dict[str, Order]:
    """The accepted orders for ``turn``, keyed by unit region_id, for adjudication.

    Players with no accepted revision (never submitted, cancelled, or
    only ever rejected) are simply absent — the adjudicator defaults
    missing orders to Hold.
    """
    effective: dict[str, Order] = {}
    for record in store.get_latest_orders_for_turn(match_id, turn):
        if record.status != OrderState.ACCEPTED.value:
            continue
        for order in decode_orders(record.orders_blob):
            effective[order.unit_region_id] = order
    return effective
