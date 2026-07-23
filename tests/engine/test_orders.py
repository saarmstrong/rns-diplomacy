"""Tests for order dataclasses and normalization (validation) of illegal orders."""

from __future__ import annotations

import pytest

from engine.adjudicator import _normalize_order
from engine.map import create_default_map
from engine.model import Unit, UnitType
from engine.orders import (
    BuildOrder,
    DisbandOrder,
    HoldOrder,
    MoveOrder,
    OrderType,
    RetreatOrder,
    SupportHoldOrder,
    SupportMoveOrder,
)


def test_hold_order_defaults():
    order = HoldOrder(unit_region_id="vet_peak")
    assert order.order_type == OrderType.HOLD


def test_move_order_defaults():
    order = MoveOrder(unit_region_id="vet_peak", destination_id="pale_ridge")
    assert order.order_type == OrderType.MOVE


def test_support_hold_order_defaults():
    order = SupportHoldOrder(unit_region_id="a", supported_region_id="b")
    assert order.order_type == OrderType.SUPPORT_HOLD


def test_support_move_order_defaults():
    order = SupportMoveOrder(unit_region_id="a", supported_region_id="b", supported_destination_id="c")
    assert order.order_type == OrderType.SUPPORT_MOVE


def test_orders_are_frozen():
    order = HoldOrder(unit_region_id="a")
    with pytest.raises(Exception):
        order.unit_region_id = "b"  # type: ignore[misc]


def test_retreat_order_defaults_to_disband():
    order = RetreatOrder(unit_region_id="a")
    assert order.destination_id is None


def test_build_and_disband_orders_hold_faction_and_region():
    build = BuildOrder(faction_id="vet", region_id="vet_peak", unit_type=UnitType.ARMY)
    disband = DisbandOrder(faction_id="vet", region_id="vet_peak")
    assert build.faction_id == "vet"
    assert disband.region_id == "vet_peak"


# --- Normalization (illegal-order handling) ---------------------------------


def test_legal_hold_order_is_not_normalized():
    state = create_default_map()
    unit = Unit(UnitType.ARMY, "vet", "vet_peak")
    order, normalized = _normalize_order(state, unit, HoldOrder("vet_peak"))
    assert normalized is False
    assert order == HoldOrder("vet_peak")


def test_missing_order_normalizes_to_hold():
    state = create_default_map()
    unit = Unit(UnitType.ARMY, "vet", "vet_peak")
    order, normalized = _normalize_order(state, unit, None)
    assert normalized is True
    assert order == HoldOrder("vet_peak")


def test_order_for_wrong_unit_normalizes_to_hold():
    state = create_default_map()
    unit = Unit(UnitType.ARMY, "vet", "vet_peak")
    mismatched = HoldOrder("vet_shore")
    order, normalized = _normalize_order(state, unit, mismatched)
    assert normalized is True
    assert order == HoldOrder("vet_peak")


def test_move_to_non_adjacent_region_normalizes_to_hold():
    state = create_default_map()
    unit = Unit(UnitType.ARMY, "vet", "vet_peak")
    order, normalized = _normalize_order(state, unit, MoveOrder("vet_peak", "irn_citadel"))
    assert normalized is True
    assert order == HoldOrder("vet_peak")


def test_army_move_into_sea_normalizes_to_hold():
    state = create_default_map()
    unit = Unit(UnitType.ARMY, "vet", "vet_shore")
    order, normalized = _normalize_order(state, unit, MoveOrder("vet_shore", "north_reach"))
    assert normalized is True
    assert order == HoldOrder("vet_shore")


def test_legal_move_order_is_not_normalized():
    state = create_default_map()
    unit = Unit(UnitType.ARMY, "vet", "vet_peak")
    order, normalized = _normalize_order(state, unit, MoveOrder("vet_peak", "pale_ridge"))
    assert normalized is False
    assert order == MoveOrder("vet_peak", "pale_ridge")


def test_support_hold_with_no_unit_at_target_normalizes_to_hold():
    state = create_default_map()
    unit = Unit(UnitType.ARMY, "vet", "vet_peak")
    order, normalized = _normalize_order(state, unit, SupportHoldOrder("vet_peak", "pale_ridge"))
    assert normalized is True
    assert order == HoldOrder("vet_peak")


def test_support_hold_supporter_cannot_reach_target_normalizes_to_hold():
    state = create_default_map()
    # thr_heart's neighbors don't include irn_citadel; irn is occupied.
    unit = Unit(UnitType.ARMY, "thr", "thr_heart")
    order, normalized = _normalize_order(state, unit, SupportHoldOrder("thr_heart", "irn_citadel"))
    assert normalized is True
    assert order == HoldOrder("thr_heart")


def test_support_move_with_no_unit_at_supported_region_normalizes_to_hold():
    state = create_default_map()
    unit = Unit(UnitType.ARMY, "vet", "vet_peak")
    order, normalized = _normalize_order(
        state, unit, SupportMoveOrder("vet_peak", "pale_ridge", "thr_heart")
    )
    assert normalized is True
    assert order == HoldOrder("vet_peak")


def test_support_move_supporter_cannot_reach_destination_normalizes_to_hold():
    state = create_default_map()
    # vet_peak army supporting thr's army moving somewhere vet_peak can't reach.
    unit = Unit(UnitType.ARMY, "vet", "vet_peak")
    order, normalized = _normalize_order(
        state, unit, SupportMoveOrder("vet_peak", "thr_heart", "gloomfen")
    )
    assert normalized is True
    assert order == HoldOrder("vet_peak")
