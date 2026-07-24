"""Tests for canonical serialization and state hashing (engine/hashing.py)."""

from __future__ import annotations

from dataclasses import replace

from engine.hashing import (
    canonical_bytes,
    canonicalize,
    game_state_from_canonical_bytes,
    hash_game_state,
    hash_game_state_hex,
    hash_orders,
    verify_chain,
)
from engine.map import create_default_map
from engine.model import GameState, RegionType, Unit
from engine.orders import HoldOrder, MoveOrder


def test_hash_is_deterministic_for_identical_states():
    state = create_default_map()
    assert hash_game_state(state) == hash_game_state(create_default_map())


def test_hash_is_independent_of_unit_list_order():
    state = create_default_map()
    reordered = replace(state, units=list(reversed(state.units)))
    assert hash_game_state(state) == hash_game_state(reordered)


def test_hash_changes_when_a_unit_moves():
    state = create_default_map()
    moved = replace(
        state,
        units=[
            Unit(u.unit_type, u.faction_id, "pale_ridge") if u.region_id == "vet_peak" else u
            for u in state.units
        ],
    )
    assert hash_game_state(state) != hash_game_state(moved)


def test_hash_changes_with_previous_hash_chain_link():
    state = create_default_map()
    h1 = hash_game_state(state, previous_hash=None)
    h2 = hash_game_state(state, previous_hash=b"some-prior-hash")
    assert h1 != h2


def test_hash_hex_matches_digest():
    state = create_default_map()
    assert hash_game_state_hex(state) == hash_game_state(state).hex()


def test_canonicalize_sorts_dict_keys():
    state = create_default_map()
    canonical = canonicalize(state)
    assert list(canonical["regions"].keys()) == sorted(canonical["regions"].keys())
    assert list(canonical["factions"].keys()) == sorted(canonical["factions"].keys())


def test_canonical_bytes_round_trips_through_msgpack():
    import msgpack

    state = create_default_map()
    payload = canonical_bytes(state)
    unpacked = msgpack.unpackb(payload, raw=False)
    assert unpacked["turn"] == 1
    assert len(unpacked["units"]) == len(state.units)


def test_verify_chain_accepts_correctly_chained_hashes():
    state0 = create_default_map()
    state1 = replace(state0, turn=2)
    h0 = hash_game_state(state0, previous_hash=None)
    h1 = hash_game_state(state1, previous_hash=h0)
    assert verify_chain([state0, state1], [h0, h1]) is True


def test_verify_chain_rejects_tampered_state():
    state0 = create_default_map()
    state1 = replace(state0, turn=2)
    h0 = hash_game_state(state0, previous_hash=None)
    h1 = hash_game_state(state1, previous_hash=h0)
    tampered = replace(state1, turn=99)
    assert verify_chain([state0, tampered], [h0, h1]) is False


def test_canonicalize_handles_raw_lists_and_enums():
    assert canonicalize([1, "a", RegionType.SEA]) == [1, "a", "sea"]
    assert canonicalize(RegionType.LAND) == "land"


def test_verify_chain_rejects_mismatched_lengths():
    state0 = create_default_map()
    h0 = hash_game_state(state0)
    assert verify_chain([state0], [h0, b"extra"]) is False


def test_hash_orders_is_deterministic():
    orders = (HoldOrder(unit_region_id="vet_peak"), MoveOrder(unit_region_id="thr_heart", destination_id="pale_ridge"))
    assert hash_orders(orders) == hash_orders(orders)


def test_hash_orders_changes_when_orders_differ():
    orders_a = (HoldOrder(unit_region_id="vet_peak"),)
    orders_b = (MoveOrder(unit_region_id="vet_peak", destination_id="pale_ridge"),)
    assert hash_orders(orders_a) != hash_orders(orders_b)


def test_hash_orders_of_empty_set_is_stable():
    assert hash_orders(()) == hash_orders([])


def test_canonical_bytes_round_trips_for_an_empty_state():
    empty = GameState()
    reconstructed = game_state_from_canonical_bytes(canonical_bytes(empty))
    assert reconstructed.regions == {}
    assert reconstructed.factions == {}
    assert reconstructed.units == []
    assert reconstructed.supply_centers == {}
    assert hash_game_state(reconstructed) == hash_game_state(empty)


def test_game_state_from_canonical_bytes_round_trips_semantically():
    state = create_default_map()
    reconstructed = game_state_from_canonical_bytes(canonical_bytes(state))

    assert reconstructed.regions == state.regions
    assert reconstructed.factions == state.factions
    assert reconstructed.supply_centers == state.supply_centers
    assert reconstructed.phase == state.phase
    assert reconstructed.turn == state.turn
    assert reconstructed.year == state.year
    assert sorted(reconstructed.units, key=lambda u: u.region_id) == sorted(state.units, key=lambda u: u.region_id)
    assert hash_game_state(reconstructed) == hash_game_state(state)
