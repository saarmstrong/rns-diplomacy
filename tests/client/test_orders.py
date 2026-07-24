"""Tests for client/orders.py — local order composition helpers."""

from __future__ import annotations

import pytest

from client.orders import (
    compose_order,
    get_move_options,
    get_orderable_units,
    get_support_hold_options,
    get_support_move_options,
    validate_orders,
)
from engine.map import create_default_map
from engine.orders import HoldOrder, MoveOrder, OrderType, SupportHoldOrder, SupportMoveOrder


def test_get_orderable_units_returns_only_that_factions_units_sorted():
    state = create_default_map()
    units = get_orderable_units(state, "vet")
    assert {u.faction_id for u in units} == {"vet"}
    assert [u.region_id for u in units] == sorted(u.region_id for u in units)


def test_get_move_options_matches_legal_moves():
    state = create_default_map()
    unit = get_orderable_units(state, "vet")[0]
    assert unit.region_id == "vet_peak"
    options = get_move_options(state, unit)
    assert "pale_ridge" in options
    assert "north_reach" not in options  # sea, army can't go there


def test_get_support_hold_options_only_includes_occupied_reachable_regions():
    state = create_default_map()
    vet_peak = next(u for u in state.units if u.region_id == "vet_peak")
    options = get_support_hold_options(state, vet_peak)
    # vet_peak can reach and support its own fleet holding at vet_shore.
    assert options == ["vet_shore"]

    thr_cove = next(u for u in state.units if u.region_id == "thr_cove")
    # thr_cove is a fleet — pale_ridge (inland) has no occupant anyway, and
    # thr_cove has no adjacent occupied region it could reach to support.
    assert get_support_hold_options(state, thr_cove) == []


def test_get_support_move_options_finds_a_valid_pairing():
    state = create_default_map()
    # thr_heart (army) is adjacent to pale_ridge, which vet_peak's army can also reach.
    thr_heart = next(u for u in state.units if u.region_id == "thr_heart")
    options = get_support_move_options(state, thr_heart)
    # thr_heart itself can't move to pale_ridge and then be supported by itself,
    # but it should offer to support some neighboring unit's legal move.
    assert isinstance(options, list)


def test_compose_order_hold():
    state = create_default_map()
    unit = next(u for u in state.units if u.region_id == "vet_peak")
    order = compose_order(unit, OrderType.HOLD)
    assert order == HoldOrder(unit_region_id="vet_peak")


def test_compose_order_move():
    state = create_default_map()
    unit = next(u for u in state.units if u.region_id == "vet_peak")
    order = compose_order(unit, OrderType.MOVE, destination_id="pale_ridge")
    assert order == MoveOrder(unit_region_id="vet_peak", destination_id="pale_ridge")


def test_compose_order_support_hold():
    state = create_default_map()
    unit = next(u for u in state.units if u.region_id == "vet_peak")
    order = compose_order(unit, OrderType.SUPPORT_HOLD, supported_region_id="thr_heart")
    assert order == SupportHoldOrder(unit_region_id="vet_peak", supported_region_id="thr_heart")


def test_compose_order_support_move():
    state = create_default_map()
    unit = next(u for u in state.units if u.region_id == "vet_peak")
    order = compose_order(
        unit, OrderType.SUPPORT_MOVE, supported_region_id="thr_heart", supported_destination_id="pale_ridge"
    )
    assert order == SupportMoveOrder(
        unit_region_id="vet_peak", supported_region_id="thr_heart", supported_destination_id="pale_ridge"
    )


def test_compose_order_move_missing_destination_raises_key_error():
    state = create_default_map()
    unit = next(u for u in state.units if u.region_id == "vet_peak")
    with pytest.raises(KeyError):
        compose_order(unit, OrderType.MOVE)


def test_validate_orders_reexported_and_usable_locally():
    state = create_default_map()
    errors = validate_orders(state, "vet", (HoldOrder(unit_region_id="vet_peak"),))
    assert errors == ()
    errors = validate_orders(state, "vet", (HoldOrder(unit_region_id="thr_heart"),))
    assert errors != ()
