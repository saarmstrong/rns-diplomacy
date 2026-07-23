"""Tests for the deterministic adjudication engine.

Uses the real Shattered Reach map (engine.map) so adjacency and terrain
rules are exercised exactly as they will be in production, but starts
from small, hand-built order sets isolated to a few regions per test so
each scenario is easy to reason about.
"""

from __future__ import annotations

from dataclasses import replace

from engine.adjudicator import (
    AdjustmentResult,
    BuildResult,
    DisbandResult,
    MovementResult,
    UnitOrderResult,
    apply_adjustment_result,
    apply_movement_result,
    apply_retreat_result,
    compute_adjustment_deltas,
    default_home_centers,
    get_retreat_options,
    resolve_adjustments,
    resolve_movement,
    resolve_retreats,
    update_supply_center_ownership,
)
from engine.map import create_default_map
from engine.model import Unit, UnitType
from engine.orders import (
    BuildOrder,
    DisbandOrder,
    HoldOrder,
    MoveOrder,
    RetreatOrder,
    SupportHoldOrder,
    SupportMoveOrder,
)

# ---------------------------------------------------------------------------
# Hold / missing / illegal orders
# ---------------------------------------------------------------------------


def test_hold_order_keeps_unit_in_place():
    state = create_default_map()
    orders = {"vet_peak": HoldOrder("vet_peak")}
    result = resolve_movement(state, orders)
    vet = _result_for(result, "vet_peak")
    assert vet.moved is False
    assert vet.final_region_id == "vet_peak"
    assert vet.dislodged is False
    assert vet.normalized is False


def test_missing_order_defaults_to_hold():
    state = create_default_map()
    result = resolve_movement(state, orders={})
    for r in result.unit_results:
        assert r.normalized is True
        assert isinstance(r.order, HoldOrder)
        assert r.moved is False
        assert r.final_region_id == r.region_id


def test_illegal_move_order_normalizes_to_hold():
    state = create_default_map()
    orders = {"vet_peak": MoveOrder("vet_peak", "irn_citadel")}  # not adjacent
    result = resolve_movement(state, orders)
    vet = _result_for(result, "vet_peak")
    assert vet.normalized is True
    assert isinstance(vet.order, HoldOrder)
    assert vet.moved is False


# ---------------------------------------------------------------------------
# Basic move / bounce
# ---------------------------------------------------------------------------


def test_unopposed_move_succeeds():
    state = create_default_map()
    orders = {"vet_peak": MoveOrder("vet_peak", "pale_ridge")}
    result = resolve_movement(state, orders)
    vet = _result_for(result, "vet_peak")
    assert vet.moved is True
    assert vet.final_region_id == "pale_ridge"
    assert vet.dislodged is False


def test_equal_strength_moves_into_same_region_both_bounce():
    state = create_default_map()
    orders = {
        "thr_heart": MoveOrder("thr_heart", "gloomfen"),
        "dsk_mire": MoveOrder("dsk_mire", "gloomfen"),
    }
    result = resolve_movement(state, orders)
    thr = _result_for(result, "thr_heart")
    dsk = _result_for(result, "dsk_mire")
    assert thr.moved is False
    assert dsk.moved is False
    assert thr.final_region_id == "thr_heart"
    assert dsk.final_region_id == "dsk_mire"
    assert "gloomfen" in result.contested_regions


# ---------------------------------------------------------------------------
# Support hold / support move / dislodgement
# ---------------------------------------------------------------------------


def test_support_hold_repels_lone_attacker():
    state = _add_unit(create_default_map(), UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "pale_ridge": HoldOrder("pale_ridge"),
        "thr_heart": SupportHoldOrder("thr_heart", "pale_ridge"),
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
    }
    result = resolve_movement(state, orders)
    defender = _result_for(result, "pale_ridge")
    attacker = _result_for(result, "vet_peak")
    assert defender.dislodged is False
    assert attacker.moved is False


def test_support_move_enables_dislodgement():
    state = _add_unit(create_default_map(), UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "pale_ridge": HoldOrder("pale_ridge"),
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
    }
    result = resolve_movement(state, orders)
    attacker = _result_for(result, "vet_peak")
    defender = _result_for(result, "pale_ridge")
    assert attacker.moved is True
    assert attacker.final_region_id == "pale_ridge"
    assert defender.dislodged is True
    assert defender.dislodged_by == "vet_peak"


def test_equal_strength_conflict_both_bounce_with_matched_support():
    # Attacker (supported, strength 2) vs. defender (supported hold, strength 2).
    state = create_default_map()
    state = _add_unit(state, UnitType.ARMY, "ash", "pale_ridge")
    state = _add_unit(state, UnitType.ARMY, "ash", "the_crucible")
    orders = {
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
        "pale_ridge": HoldOrder("pale_ridge"),
        "the_crucible": SupportHoldOrder("the_crucible", "pale_ridge"),
    }
    result = resolve_movement(state, orders)
    attacker = _result_for(result, "vet_peak")
    defender = _result_for(result, "pale_ridge")
    assert attacker.moved is False
    assert defender.dislodged is False


def test_self_dislodgement_is_prevented():
    state = create_default_map()
    state = _add_unit(state, UnitType.ARMY, "vet", "pale_ridge")  # same faction as attacker
    orders = {
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
        "pale_ridge": HoldOrder("pale_ridge"),
    }
    result = resolve_movement(state, orders)
    attacker = _result_for(result, "vet_peak")
    defender = _result_for(result, "pale_ridge")
    assert attacker.moved is False  # cannot dislodge own unit even with superior strength
    assert defender.dislodged is False


# ---------------------------------------------------------------------------
# Cut support
# ---------------------------------------------------------------------------


def test_support_is_cut_by_attack_on_supporter():
    state = create_default_map()
    state = _add_unit(state, UnitType.ARMY, "ash", "pale_ridge")
    state = _add_unit(state, UnitType.ARMY, "dsk", "gloomfen")
    orders = {
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
        "pale_ridge": HoldOrder("pale_ridge"),
        "gloomfen": MoveOrder("gloomfen", "thr_heart"),  # attacks the supporter
    }
    result = resolve_movement(state, orders)
    supporter = _result_for(result, "thr_heart")
    attacker = _result_for(result, "vet_peak")
    defender = _result_for(result, "pale_ridge")
    assert supporter.support_cut is True
    assert attacker.moved is False  # lost its support, can no longer beat the defender
    assert defender.dislodged is False


def test_support_not_cut_by_attack_from_the_supported_destination():
    # Classic exception: the unit at the destination the support is aimed at
    # cannot cut that support by attacking the supporter.
    state = create_default_map()
    state = _add_unit(state, UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
        "pale_ridge": MoveOrder("pale_ridge", "thr_heart"),  # counterattacks the supporter
    }
    result = resolve_movement(state, orders)
    supporter = _result_for(result, "thr_heart")
    attacker = _result_for(result, "vet_peak")
    defender = _result_for(result, "pale_ridge")
    assert supporter.support_cut is False
    assert attacker.moved is True
    assert defender.dislodged is True
    assert defender.dislodged_by == "vet_peak"


# ---------------------------------------------------------------------------
# Head-to-head
# ---------------------------------------------------------------------------


def test_head_to_head_stronger_unit_dislodges_weaker():
    state = create_default_map()
    state = _add_unit(state, UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
        "pale_ridge": MoveOrder("pale_ridge", "vet_peak"),
    }
    result = resolve_movement(state, orders)
    winner = _result_for(result, "vet_peak")
    loser = _result_for(result, "pale_ridge")
    assert winner.moved is True
    assert winner.final_region_id == "pale_ridge"
    assert loser.moved is False
    assert loser.dislodged is True
    assert loser.dislodged_by == "vet_peak"


def test_head_to_head_equal_strength_both_bounce():
    state = create_default_map()
    state = _add_unit(state, UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "pale_ridge": MoveOrder("pale_ridge", "vet_peak"),
    }
    result = resolve_movement(state, orders)
    a = _result_for(result, "vet_peak")
    b = _result_for(result, "pale_ridge")
    assert a.moved is False
    assert b.moved is False
    assert a.dislodged is False
    assert b.dislodged is False


# ---------------------------------------------------------------------------
# Circular movement (bonus: CLAUDE.md's "supported resolutions" list)
# ---------------------------------------------------------------------------


def test_circular_movement_all_succeed_with_no_external_opposition():
    state = _add_unit(create_default_map(), UnitType.FLEET, "ash", "bleakwater")
    orders = {
        "kho_spire": MoveOrder("kho_spire", "kho_harbor"),
        "kho_harbor": MoveOrder("kho_harbor", "bleakwater"),
        "bleakwater": MoveOrder("bleakwater", "kho_spire"),
    }
    result = resolve_movement(state, orders)
    for region in ("kho_spire", "kho_harbor", "bleakwater"):
        r = _result_for(result, region)
        assert r.moved is True, f"{region} should have moved"


# ---------------------------------------------------------------------------
# Retreats: eligibility, destruction, collisions
# ---------------------------------------------------------------------------


def test_retreat_options_exclude_occupied_contested_and_attacker_origin():
    state = create_default_map()
    dislodged = UnitOrderResult(
        region_id="pale_ridge",
        faction_id="ash",
        unit_type=UnitType.ARMY,
        order=HoldOrder("pale_ridge"),
        normalized=False,
        moved=False,
        final_region_id="pale_ridge",
        dislodged=True,
        dislodged_by="vet_peak",
        support_cut=False,
    )
    survivor = UnitOrderResult(
        region_id="thr_heart",
        faction_id="thr",
        unit_type=UnitType.ARMY,
        order=HoldOrder("thr_heart"),
        normalized=False,
        moved=False,
        final_region_id="thr_heart",
        dislodged=False,
        dislodged_by=None,
        support_cut=False,
    )
    result = MovementResult(
        unit_results=(dislodged, survivor),
        contested_regions=frozenset({"the_crucible"}),
    )
    legal = get_retreat_options(state, result)["pale_ridge"]
    assert "vet_peak" not in legal  # attacker's origin
    assert "thr_heart" not in legal  # occupied
    assert "the_crucible" not in legal  # contested this turn
    assert legal == ["vet_shore"]


def test_retreat_options_exclude_terrain_illegal_regions():
    # A dislodged army must not be offered a sea region as a retreat option,
    # even though it's adjacent and otherwise unoccupied/uncontested.
    state = create_default_map()
    dislodged = UnitOrderResult(
        region_id="vet_shore",
        faction_id="ash",
        unit_type=UnitType.ARMY,
        order=HoldOrder("vet_shore"),
        normalized=False,
        moved=False,
        final_region_id="vet_shore",
        dislodged=True,
        dislodged_by="pale_ridge",
        support_cut=False,
    )
    result = MovementResult(unit_results=(dislodged,), contested_regions=frozenset())
    legal = get_retreat_options(state, result)["vet_shore"]
    assert "north_reach" not in legal  # sea region, illegal for an army
    assert "vet_peak" in legal


def test_dislodged_unit_retreats_to_chosen_legal_destination():
    state = _add_unit(create_default_map(), UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "pale_ridge": HoldOrder("pale_ridge"),
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
    }
    movement_result = resolve_movement(state, orders)
    options = get_retreat_options(state, movement_result)["pale_ridge"]
    assert "the_crucible" in options

    retreat_result = resolve_retreats(
        state, movement_result, {"pale_ridge": RetreatOrder("pale_ridge", "the_crucible")}
    )
    outcome = retreat_result.results[0]
    assert outcome.destroyed is False
    assert outcome.destination_id == "the_crucible"


def test_retreat_to_illegal_destination_normalizes_to_disband():
    state = _add_unit(create_default_map(), UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "pale_ridge": HoldOrder("pale_ridge"),
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
    }
    movement_result = resolve_movement(state, orders)
    retreat_result = resolve_retreats(
        state, movement_result, {"pale_ridge": RetreatOrder("pale_ridge", "vet_peak")}
    )
    outcome = retreat_result.results[0]
    assert outcome.destroyed is True  # vet_peak is the attacker's origin, illegal


def test_missing_retreat_order_defaults_to_disband():
    state = _add_unit(create_default_map(), UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "pale_ridge": HoldOrder("pale_ridge"),
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
    }
    movement_result = resolve_movement(state, orders)
    retreat_result = resolve_retreats(state, movement_result, {})
    assert retreat_result.results[0].destroyed is True


def test_destruction_when_no_legal_retreat_exists():
    dislodged = UnitOrderResult(
        region_id="pale_ridge",
        faction_id="ash",
        unit_type=UnitType.ARMY,
        order=HoldOrder("pale_ridge"),
        normalized=False,
        moved=False,
        final_region_id="pale_ridge",
        dislodged=True,
        dislodged_by="vet_peak",
        support_cut=False,
    )

    def _occupied(region: str, faction: str) -> UnitOrderResult:
        return UnitOrderResult(
            region_id=region,
            faction_id=faction,
            unit_type=UnitType.ARMY,
            order=HoldOrder(region),
            normalized=False,
            moved=False,
            final_region_id=region,
            dislodged=False,
            dislodged_by=None,
            support_cut=False,
        )

    result = MovementResult(
        unit_results=(
            dislodged,
            _occupied("vet_shore", "vet"),
            _occupied("thr_heart", "thr"),
        ),
        contested_regions=frozenset({"the_crucible"}),
    )
    state = create_default_map()
    assert get_retreat_options(state, result)["pale_ridge"] == []
    retreat_result = resolve_retreats(state, result, {})
    assert retreat_result.results[0].destroyed is True


def test_two_dislodged_units_retreating_to_same_region_both_destroyed():
    d1 = UnitOrderResult(
        region_id="pale_ridge",
        faction_id="ash",
        unit_type=UnitType.ARMY,
        order=HoldOrder("pale_ridge"),
        normalized=False,
        moved=False,
        final_region_id="pale_ridge",
        dislodged=True,
        dislodged_by="vet_peak",
        support_cut=False,
    )
    d2 = UnitOrderResult(
        region_id="thr_heart",
        faction_id="dsk",
        unit_type=UnitType.ARMY,
        order=HoldOrder("thr_heart"),
        normalized=False,
        moved=False,
        final_region_id="thr_heart",
        dislodged=True,
        dislodged_by="thr_cove",
        support_cut=False,
    )
    result = MovementResult(unit_results=(d1, d2), contested_regions=frozenset())
    state = create_default_map()
    # Both can legally retreat to "the_crucible" (adjacent to both, unoccupied, uncontested).
    retreat_orders = {
        "pale_ridge": RetreatOrder("pale_ridge", "the_crucible"),
        "thr_heart": RetreatOrder("thr_heart", "the_crucible"),
    }
    retreat_result = resolve_retreats(state, result, retreat_orders)
    for outcome in retreat_result.results:
        assert outcome.destroyed is True


# ---------------------------------------------------------------------------
# apply_* helpers / full movement+retreat integration
# ---------------------------------------------------------------------------


def test_apply_movement_result_removes_dislodged_units():
    state = _add_unit(create_default_map(), UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "pale_ridge": HoldOrder("pale_ridge"),
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
    }
    movement_result = resolve_movement(state, orders)
    new_state = apply_movement_result(state, movement_result)
    by_region = {u.region_id: u for u in new_state.units}
    assert by_region["pale_ridge"].faction_id == "vet"  # now occupied by the victorious vet unit
    assert len(new_state.units) == len(state.units) - 1  # dislodged ash unit is gone (pending retreat)


def test_full_movement_and_retreat_cycle():
    state = _add_unit(create_default_map(), UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "pale_ridge": HoldOrder("pale_ridge"),
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
    }
    movement_result = resolve_movement(state, orders)
    state_after_movement = apply_movement_result(state, movement_result)
    retreat_result = resolve_retreats(
        state, movement_result, {"pale_ridge": RetreatOrder("pale_ridge", "the_crucible")}
    )
    final_state = apply_retreat_result(state_after_movement, retreat_result)

    by_region = {u.region_id: u for u in final_state.units}
    assert by_region["pale_ridge"].faction_id == "vet"
    assert by_region["the_crucible"].faction_id == "ash"
    assert len(final_state.units) == len(state.units)  # nobody was destroyed


# ---------------------------------------------------------------------------
# Supply center ownership
# ---------------------------------------------------------------------------


def test_supply_center_ownership_transfers_on_occupation():
    state = create_default_map()
    new_units = [
        Unit(u.unit_type, u.faction_id, "vet_peak") if u.region_id == "dsk_mire" else u
        for u in state.units
    ]
    state = replace(state, units=new_units)
    updated = update_supply_center_ownership(state)
    assert updated.supply_centers["vet_peak"] == "dsk"


def test_supply_center_ownership_unchanged_when_unoccupied():
    state = create_default_map()
    new_units = [u for u in state.units if u.region_id != "vet_peak"]
    state = replace(state, units=new_units)
    updated = update_supply_center_ownership(state)
    assert updated.supply_centers["vet_peak"] == "vet"


# ---------------------------------------------------------------------------
# Adjustment phase: build / disband
# ---------------------------------------------------------------------------


def _build_state():
    state = create_default_map()
    remaining = [u for u in state.units if u.region_id != "vet_shore"]
    centers = dict(state.supply_centers)
    centers["irn_citadel"] = "vet"
    return replace(state, units=remaining, supply_centers=centers)


def _disband_state():
    state = create_default_map()
    centers = dict(state.supply_centers)
    centers["thr_heart"] = "vet"
    return replace(state, supply_centers=centers)


def test_compute_adjustment_deltas():
    state = _build_state()
    deltas = compute_adjustment_deltas(state)
    assert deltas["vet"] == 2  # 3 centers, 1 unit


def test_build_accepted_at_owned_unoccupied_home_center():
    state = _build_state()
    result = resolve_adjustments(state, [BuildOrder("vet", "vet_shore", UnitType.FLEET)], [])
    assert result.builds == (BuildResult("vet", "vet_shore", UnitType.FLEET, True, ""),)


def test_build_rejected_when_not_a_home_center():
    state = _build_state()
    result = resolve_adjustments(state, [BuildOrder("vet", "irn_citadel", UnitType.FLEET)], [])
    outcome = result.builds[0]
    assert outcome.accepted is False
    assert outcome.reason == "not a home center"


def test_build_rejected_when_region_occupied():
    state = _build_state()
    result = resolve_adjustments(state, [BuildOrder("vet", "vet_peak", UnitType.ARMY)], [])
    outcome = result.builds[0]
    assert outcome.accepted is False
    assert outcome.reason == "region occupied"


def test_build_rejected_when_region_not_a_supply_center():
    state = _build_state()
    result = resolve_adjustments(state, [BuildOrder("vet", "pale_ridge", UnitType.ARMY)], [])
    outcome = result.builds[0]
    assert outcome.accepted is False
    assert outcome.reason == "not a supply center"


def test_build_rejected_when_home_center_not_currently_controlled():
    state = _build_state()
    state = replace(state, supply_centers={**state.supply_centers, "vet_shore": "dsk"})
    result = resolve_adjustments(state, [BuildOrder("vet", "vet_shore", UnitType.FLEET)], [])
    outcome = result.builds[0]
    assert outcome.accepted is False
    assert outcome.reason == "center not controlled"


def test_build_rejected_for_army_into_sea_terrain():
    # The real map never makes a sea region a supply center, so this branch is
    # defensive; exercise it directly with a synthetic sea "supply center".
    from engine.model import Faction, GameState, Region, RegionType

    sea_center = Region("open_sea", "Open Sea", RegionType.SEA, is_supply_center=True)
    state = GameState(
        regions={"open_sea": sea_center},
        factions={"vet": Faction(id="vet", name="Vethara", color="crimson", abbreviation="VET")},
        units=[],
        supply_centers={"open_sea": "vet"},
    )
    result = resolve_adjustments(
        state,
        [BuildOrder("vet", "open_sea", UnitType.ARMY)],
        [],
        home_centers={"vet": {"open_sea"}},
    )
    outcome = result.builds[0]
    assert outcome.accepted is False
    assert outcome.reason == "invalid terrain"


def test_build_rejected_for_fleet_into_land_terrain():
    state = create_default_map()
    state = replace(
        state,
        units=[u for u in state.units if u.region_id != "thr_heart"],
        supply_centers={**state.supply_centers, "irn_citadel": "thr"},
    )
    result = resolve_adjustments(state, [BuildOrder("thr", "thr_heart", UnitType.FLEET)], [])
    outcome = result.builds[0]
    assert outcome.accepted is False
    assert outcome.reason == "invalid terrain"


def test_explicit_disband_orders_beyond_required_count_are_capped():
    state = _disband_state()
    result = resolve_adjustments(
        state,
        [],
        [DisbandOrder("thr", "thr_heart"), DisbandOrder("thr", "thr_cove")],
    )
    assert len(result.disbands) == 1  # only 1 disband required despite 2 orders submitted


def test_build_rejected_beyond_available_count():
    state = create_default_map()  # every faction has delta == 0
    result = resolve_adjustments(state, [BuildOrder("vet", "vet_shore", UnitType.FLEET)], [])
    outcome = result.builds[0]
    assert outcome.accepted is False
    assert outcome.reason == "build limit reached"


def test_explicit_disband_order_is_honored():
    state = _disband_state()
    result = resolve_adjustments(state, [], [DisbandOrder("thr", "thr_heart")])
    assert result.disbands == (DisbandResult("thr", "thr_heart"),)


def test_missing_disband_order_is_chosen_deterministically():
    state = _disband_state()
    result1 = resolve_adjustments(state, [], [])
    result2 = resolve_adjustments(state, [], [])
    assert result1.disbands == result2.disbands
    assert len(result1.disbands) == 1
    assert result1.disbands[0].faction_id == "thr"


def test_apply_adjustment_result_updates_units():
    state = _build_state()
    result = AdjustmentResult(
        builds=(BuildResult("vet", "vet_shore", UnitType.FLEET, True),),
        disbands=(),
    )
    new_state = apply_adjustment_result(state, result)
    regions = {u.region_id for u in new_state.units}
    assert "vet_shore" in regions


def test_apply_adjustment_result_removes_disbanded_units():
    state = _disband_state()
    result = AdjustmentResult(builds=(), disbands=(DisbandResult("thr", "thr_heart"),))
    new_state = apply_adjustment_result(state, result)
    regions = {u.region_id for u in new_state.units}
    assert "thr_heart" not in regions


def test_default_home_centers_matches_starting_setup():
    homes = default_home_centers()
    assert homes["vet"] == {"vet_peak", "vet_shore"}


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_resolve_movement_is_deterministic_across_runs():
    state = _add_unit(create_default_map(), UnitType.ARMY, "ash", "pale_ridge")
    orders = {
        "pale_ridge": HoldOrder("pale_ridge"),
        "vet_peak": MoveOrder("vet_peak", "pale_ridge"),
        "thr_heart": SupportMoveOrder("thr_heart", "vet_peak", "pale_ridge"),
    }
    result1 = resolve_movement(state, orders)
    result2 = resolve_movement(state, orders)
    assert result1 == result2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result_for(result: MovementResult, region_id: str) -> UnitOrderResult:
    for r in result.unit_results:
        if r.region_id == region_id:
            return r
    raise AssertionError(f"no result for {region_id}")


def _add_unit(state, unit_type: UnitType, faction_id: str, region_id: str):
    return replace(state, units=[*state.units, Unit(unit_type, faction_id, region_id)])
