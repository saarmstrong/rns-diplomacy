"""Tests for client/verification.py."""

from __future__ import annotations

from dataclasses import replace

import pytest

from client.identity import create_identity
from client.transport import PlayerClient
from client.verification import (
    reproduce_and_compare,
    verify_hash_matches_state,
    verify_history,
    verify_signature,
)
from coordinator.match import MatchCoordinator
from coordinator.persistence import MatchStore
from engine import adjudicator
from engine.hashing import game_state_from_canonical_bytes
from engine.map import create_default_map
from engine.orders import HoldOrder
from shared.identity import Identity
from shared.transport import InMemoryNetwork, InMemoryTransport


@pytest.fixture
def network():
    return InMemoryNetwork()


@pytest.fixture
def coordinator(network, tmp_path):
    store = MatchStore(tmp_path / "match.sqlite3")
    coordinator_transport = InMemoryTransport("coordinator", network)
    coord = MatchCoordinator.create("match-1", store, coordinator_transport, now=1000.0)
    yield coord
    store.close()


def _make_client(network) -> PlayerClient:
    identity = create_identity()
    transport = InMemoryTransport(identity.public_bytes.hex(), network)
    return PlayerClient(identity, transport, "coordinator", "match-1")


def _run_one_all_hold_turn(coordinator, network) -> PlayerClient:
    clients = [_make_client(network) for _ in range(7)]
    for client in clients:
        client.join(display_name="Ambassador")
        coordinator.poll()
        client.poll()
    coordinator.start_match()
    for client in clients:
        client.poll()

    for client in clients:
        faction_id = coordinator.store.get_player_faction("match-1", client.identity.public_bytes)
        units = [u for u in create_default_map().units if u.faction_id == faction_id]
        client.submit_orders(tuple(HoldOrder(unit_region_id=u.region_id) for u in units), revision=1)
        coordinator.poll()
        client.poll()

    coordinator.advance_phase(now=300.0)
    for client in clients:
        client.poll()
    return clients[0]


def test_verify_signature_true_for_genuine_result(coordinator, network):
    client = _run_one_all_hold_turn(coordinator, network)
    coordinator_public = coordinator.store.load_coordinator_identity("match-1").public()
    assert verify_signature(coordinator_public, client.match_start) is True
    for result in client.phase_results:
        assert verify_signature(coordinator_public, result) is True


def test_verify_signature_false_for_wrong_identity(coordinator, network):
    client = _run_one_all_hold_turn(coordinator, network)
    impostor_public = Identity.generate().public()
    assert verify_signature(impostor_public, client.match_start) is False


def test_verify_hash_matches_state_true_for_genuine_result(coordinator, network):
    client = _run_one_all_hold_turn(coordinator, network)
    for result in client.phase_results:
        assert verify_hash_matches_state(result) is True


def test_verify_hash_matches_state_false_when_canonical_state_tampered(coordinator, network):
    client = _run_one_all_hold_turn(coordinator, network)
    tampered = replace(client.phase_results[0], canonical_state=b"not-the-real-state")
    assert verify_hash_matches_state(tampered) is False


def test_verify_history_ok_for_honest_chain(coordinator, network):
    client = _run_one_all_hold_turn(coordinator, network)
    coordinator_public = coordinator.store.load_coordinator_identity("match-1").public()
    report = verify_history(coordinator_public, client.match_start, client.phase_results)
    assert report.ok is True
    assert report.issues == ()
    assert report.checked == len(client.phase_results) + 1


def test_verify_history_detects_tampered_state(coordinator, network):
    client = _run_one_all_hold_turn(coordinator, network)
    coordinator_public = coordinator.store.load_coordinator_identity("match-1").public()

    tampered_results = list(client.phase_results)
    tampered_results[-1] = replace(tampered_results[-1], canonical_state=b"forged")

    report = verify_history(coordinator_public, client.match_start, tampered_results)
    assert report.ok is False
    assert any("state_hash does not match" in issue.reason for issue in report.issues)


def test_verify_history_detects_broken_chain_link(coordinator, network):
    client = _run_one_all_hold_turn(coordinator, network)
    coordinator_public = coordinator.store.load_coordinator_identity("match-1").public()

    tampered_results = list(client.phase_results)
    tampered_results[-1] = replace(tampered_results[-1], previous_state_hash="0" * 64)

    report = verify_history(coordinator_public, client.match_start, tampered_results)
    assert report.ok is False
    assert any("hash chain broken" in issue.reason for issue in report.issues)


def test_verify_history_detects_forged_signature(coordinator, network):
    client = _run_one_all_hold_turn(coordinator, network)
    impostor = Identity.generate()
    coordinator_public = coordinator.store.load_coordinator_identity("match-1").public()

    forged = replace(client.phase_results[-1], signature=impostor.sign(client.phase_results[-1].state_hash.encode("utf-8")))
    tampered_results = list(client.phase_results)
    tampered_results[-1] = forged

    report = verify_history(coordinator_public, client.match_start, tampered_results)
    assert report.ok is False
    assert any("signature invalid" in issue.reason for issue in report.issues)


def test_reproduce_and_compare_matches_for_correct_all_hold_turn(coordinator, network):
    client = _run_one_all_hold_turn(coordinator, network)
    pre_state = create_default_map()
    orders = {u.region_id: HoldOrder(unit_region_id=u.region_id) for u in pre_state.units}
    claimed_state = game_state_from_canonical_bytes(client.phase_results[-1].canonical_state)

    matches, computed = reproduce_and_compare(pre_state, orders, claimed_state)
    assert matches is True
    assert computed.units == claimed_state.units


def test_reproduce_and_compare_flags_a_wrong_claimed_state(coordinator, network):
    pre_state = create_default_map()
    orders = {u.region_id: HoldOrder(unit_region_id=u.region_id) for u in pre_state.units}

    movement_result = adjudicator.resolve_movement(pre_state, orders)
    honest_state = adjudicator.apply_movement_result(pre_state, movement_result)
    forged_state = replace(honest_state, turn=99)  # a dishonest coordinator claiming a different outcome

    matches, _ = reproduce_and_compare(pre_state, orders, forged_state)
    assert matches is False
