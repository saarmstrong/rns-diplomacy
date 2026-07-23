"""Tests for coordinator order submission handling (coordinator/orders.py)."""

from __future__ import annotations

import pytest

from coordinator.orders import (
    decode_orders,
    encode_orders,
    get_effective_orders,
    handle_order_cancel,
    handle_order_status,
    handle_order_submit,
    validate_orders,
)
from coordinator.persistence import MatchStore
from engine.map import create_default_map
from engine.model import Phase
from engine.orders import HoldOrder, MoveOrder
from protocol.messages import OrderCancel, OrderState, OrderSubmit
from shared.identity import Identity


@pytest.fixture
def store():
    match_store = MatchStore(":memory:")
    match_store.create_match("match-1", Identity.generate(), created_at=1000.0)
    yield match_store
    match_store.close()


@pytest.fixture
def game_state():
    return create_default_map()


def _add_player(store, faction_id: str) -> bytes:
    public_key = Identity.generate().public_bytes
    store.add_player("match-1", public_key, faction_id, display_name=None, joined_at=10.0)
    return public_key


def test_encode_decode_orders_round_trip():
    orders = (HoldOrder(unit_region_id="vet_peak"), MoveOrder(unit_region_id="thr_heart", destination_id="pale_ridge"))
    assert decode_orders(encode_orders(orders)) == orders


def test_validate_orders_accepts_own_units(game_state):
    errors = validate_orders(game_state, "vet", (HoldOrder(unit_region_id="vet_peak"),))
    assert errors == ()


def test_validate_orders_rejects_other_factions_unit(game_state):
    errors = validate_orders(game_state, "vet", (HoldOrder(unit_region_id="thr_heart"),))
    assert len(errors) == 1
    assert "thr_heart" in errors[0]


def test_validate_orders_rejects_duplicate_unit_orders(game_state):
    orders = (HoldOrder(unit_region_id="vet_peak"), MoveOrder(unit_region_id="vet_peak", destination_id="pale_ridge"))
    errors = validate_orders(game_state, "vet", orders)
    assert any("duplicate" in e for e in errors)


def test_submit_accepted_and_persisted(store, game_state):
    alice = _add_player(store, "vet")
    request = OrderSubmit(
        sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
        orders=(HoldOrder(unit_region_id="vet_peak"),),
    )
    receipt = handle_order_submit(
        store, game_state, "match-1", alice, request,
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=1, timestamp=100.0,
    )
    assert receipt.state is OrderState.ACCEPTED
    assert receipt.revision == 1

    latest = store.get_latest_order("match-1", alice, 1)
    assert latest.status == OrderState.ACCEPTED.value
    assert latest.order_hash == receipt.order_hash


def test_submit_rejected_when_player_unknown(store, game_state):
    stranger = Identity.generate().public_bytes
    request = OrderSubmit(
        sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
        orders=(HoldOrder(unit_region_id="vet_peak"),),
    )
    receipt = handle_order_submit(
        store, game_state, "match-1", stranger, request,
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=1, timestamp=100.0,
    )
    assert receipt.state is OrderState.REJECTED


def test_submit_rejected_when_not_orders_phase(store, game_state):
    alice = _add_player(store, "vet")
    request = OrderSubmit(
        sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
        orders=(HoldOrder(unit_region_id="vet_peak"),),
    )
    receipt = handle_order_submit(
        store, game_state, "match-1", alice, request,
        current_turn=1, current_phase=Phase.DIPLOMACY, sequence_number=1, timestamp=100.0,
    )
    assert receipt.state is OrderState.REJECTED
    assert store.get_latest_order("match-1", alice, 1) is None


def test_submit_rejected_for_non_increasing_revision(store, game_state):
    alice = _add_player(store, "vet")
    first = OrderSubmit(
        sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
        orders=(HoldOrder(unit_region_id="vet_peak"),),
    )
    handle_order_submit(
        store, game_state, "match-1", alice, first,
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=1, timestamp=100.0,
    )
    repeat = OrderSubmit(
        sequence_number=2, timestamp=101.0, match_id="match-1", revision=1,
        orders=(HoldOrder(unit_region_id="vet_peak"),),
    )
    receipt = handle_order_submit(
        store, game_state, "match-1", alice, repeat,
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=2, timestamp=101.0,
    )
    assert receipt.state is OrderState.REJECTED
    # The original accepted revision must be untouched.
    latest = store.get_latest_order("match-1", alice, 1)
    assert latest.revision == 1
    assert latest.status == OrderState.ACCEPTED.value


def test_submit_rejected_for_foreign_unit_but_still_persisted(store, game_state):
    alice = _add_player(store, "vet")
    request = OrderSubmit(
        sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
        orders=(HoldOrder(unit_region_id="thr_heart"),),
    )
    receipt = handle_order_submit(
        store, game_state, "match-1", alice, request,
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=1, timestamp=100.0,
    )
    assert receipt.state is OrderState.REJECTED
    latest = store.get_latest_order("match-1", alice, 1)
    assert latest is not None
    assert latest.status == OrderState.REJECTED.value


def test_new_valid_revision_supersedes_previous(store, game_state):
    alice = _add_player(store, "vet")
    rev1 = OrderSubmit(
        sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
        orders=(HoldOrder(unit_region_id="vet_peak"),),
    )
    handle_order_submit(
        store, game_state, "match-1", alice, rev1,
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=1, timestamp=100.0,
    )
    rev2 = OrderSubmit(
        sequence_number=2, timestamp=110.0, match_id="match-1", revision=2,
        orders=(MoveOrder(unit_region_id="vet_peak", destination_id="pale_ridge"),),
    )
    receipt = handle_order_submit(
        store, game_state, "match-1", alice, rev2,
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=2, timestamp=110.0,
    )
    assert receipt.state is OrderState.ACCEPTED

    revisions = store.get_order_revisions("match-1", alice, 1)
    assert [(r.revision, r.status) for r in revisions] == [
        (1, OrderState.SUPERSEDED.value),
        (2, OrderState.ACCEPTED.value),
    ]


def test_cancel_supersedes_and_clears_orders(store, game_state):
    alice = _add_player(store, "vet")
    rev1 = OrderSubmit(
        sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
        orders=(HoldOrder(unit_region_id="vet_peak"),),
    )
    handle_order_submit(
        store, game_state, "match-1", alice, rev1,
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=1, timestamp=100.0,
    )
    cancel_request = OrderCancel(sequence_number=2, timestamp=120.0, match_id="match-1", revision=2)
    receipt = handle_order_cancel(
        store, "match-1", alice, cancel_request,
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=2, timestamp=120.0,
    )
    assert receipt.state is OrderState.SUPERSEDED

    revisions = store.get_order_revisions("match-1", alice, 1)
    assert [r.status for r in revisions] == [OrderState.SUPERSEDED.value, OrderState.SUPERSEDED.value]
    assert decode_orders(revisions[-1].orders_blob) == ()


def test_order_status_before_any_submission(store):
    alice = _add_player(store, "vet")
    status = handle_order_status(store, "match-1", alice, 1, sequence_number=1, timestamp=100.0)
    assert status.revision == 0
    assert status.state is OrderState.SENT
    assert status.order_hash is None


def test_order_status_after_submission(store, game_state):
    alice = _add_player(store, "vet")
    request = OrderSubmit(
        sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
        orders=(HoldOrder(unit_region_id="vet_peak"),),
    )
    receipt = handle_order_submit(
        store, game_state, "match-1", alice, request,
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=1, timestamp=100.0,
    )
    status = handle_order_status(store, "match-1", alice, 1, sequence_number=2, timestamp=101.0)
    assert status.revision == 1
    assert status.state is OrderState.ACCEPTED
    assert status.order_hash == receipt.order_hash


def test_get_effective_orders_includes_only_accepted_latest_revisions(store, game_state):
    alice = _add_player(store, "vet")
    bob = _add_player(store, "thr")

    handle_order_submit(
        store, game_state, "match-1", alice,
        OrderSubmit(sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
                    orders=(HoldOrder(unit_region_id="vet_peak"),)),
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=1, timestamp=100.0,
    )
    # Bob's only submission is invalid (references someone else's unit) — rejected, so excluded.
    handle_order_submit(
        store, game_state, "match-1", bob,
        OrderSubmit(sequence_number=1, timestamp=100.0, match_id="match-1", revision=1,
                    orders=(HoldOrder(unit_region_id="vet_peak"),)),
        current_turn=1, current_phase=Phase.ORDERS, sequence_number=1, timestamp=100.0,
    )

    effective = get_effective_orders(store, "match-1", 1)
    assert set(effective.keys()) == {"vet_peak"}
    assert effective["vet_peak"] == HoldOrder(unit_region_id="vet_peak")
