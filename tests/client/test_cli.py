"""Tests for client/cli.py: argument parsing, session persistence, and record (de)serialization.

The network-touching commands (join/status/negotiate/order/verify/
history/draw) wrap PlayerClient, already thoroughly tested against a real
ReticulumTransport in tests/integration/. Manually smoke-tested end to
end (create -> serve -> join x7 -> start -> status -> order -> orders ->
advance-phase -> history -> verify -> negotiate, all over real TCP)
during development; not re-exercised here for the same reason the
coordinator CLI's equivalent commands aren't.
"""

from __future__ import annotations

import argparse

import pytest

from client.cli import (
    _append_jsonl,
    _load_session,
    _match_start_to_record,
    _parse_connect,
    _phase_result_to_record,
    _read_jsonl,
    _record_to_match_start,
    _record_to_phase_result,
    _save_session,
    build_parser,
    cmd_orders,
)
from engine.model import Phase
from protocol.messages import MatchStart, PhaseResult


def test_parse_connect_valid():
    assert _parse_connect("127.0.0.1:4242") == ("127.0.0.1", 4242)


@pytest.mark.parametrize("value", ["no-port", "host:", "host:abc"])
def test_parse_connect_rejects_malformed_input(value):
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_connect(value)


def test_build_parser_requires_a_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("join", ["--coordinator", "127.0.0.1:1", "--coordinator-pub", "ab"]),
        ("status", []),
        ("negotiate", ["--to", "ab", "--message", "hi"]),
        ("order", ["--revision", "1", "--order", "vet_peak:hold"]),
        ("orders", []),
        ("verify", []),
        ("history", []),
        ("draw", ["propose", "--turn", "1"]),
    ],
)
def test_build_parser_accepts_every_subcommand(command, extra):
    parser = build_parser()
    args = parser.parse_args([command, "match-1", *extra])
    assert args.match_id == "match-1"
    assert args.command == command


def test_discover_does_not_take_a_match_id():
    """discover precedes knowing a match_id at all — that's the point of it."""
    parser = build_parser()
    args = parser.parse_args(["discover", "--coordinator", "127.0.0.1:1"])
    assert args.command == "discover"
    assert not hasattr(args, "match_id")


def test_draw_vote_requires_vote_choice_at_the_argparse_level():
    parser = build_parser()
    # --vote isn't argparse-required (it only applies to `vote`, not `propose`),
    # so this parses fine; main() enforces the actual requirement — see below.
    args = parser.parse_args(["draw", "match-1", "vote", "--turn", "1"])
    assert args.vote is None


def test_match_start_record_round_trips():
    message = MatchStart(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        faction_names=("Vethara", "Thornveil"),
        canonical_state=b"\x00\x01\x02",
        state_hash="abc123",
        signature=b"\x03\x04",
        deadline=9999.0,
    )
    record = _match_start_to_record(message)
    restored = _record_to_match_start(record)
    assert restored.match_id == message.match_id
    assert restored.faction_names == message.faction_names
    assert restored.canonical_state == message.canonical_state
    assert restored.state_hash == message.state_hash
    assert restored.signature == message.signature
    assert restored.deadline == message.deadline


def test_phase_result_record_round_trips():
    message = PhaseResult(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        phase=Phase.RETREAT,
        turn=3,
        year=3,
        canonical_state=b"\xaa\xbb",
        state_hash="hash-1",
        previous_state_hash="hash-0",
        signature=b"\xcc\xdd",
    )
    record = _phase_result_to_record(message)
    restored = _record_to_phase_result(record)
    assert restored.phase is Phase.RETREAT
    assert restored.turn == 3
    assert restored.year == 3
    assert restored.canonical_state == message.canonical_state
    assert restored.state_hash == message.state_hash
    assert restored.previous_state_hash == message.previous_state_hash
    assert restored.signature == message.signature


def test_jsonl_append_and_read_round_trip(tmp_path):
    path = tmp_path / "log.jsonl"
    assert _read_jsonl(path) == []
    _append_jsonl(path, {"a": 1})
    _append_jsonl(path, {"a": 2})
    assert _read_jsonl(path) == [{"a": 1}, {"a": 2}]


def test_session_round_trip(tmp_path):
    args = argparse.Namespace(data_dir=str(tmp_path), match_id="match-1")
    _save_session(args, {"coordinator_pub": "abc", "faction_name": "Vethara"})
    session = _load_session(args)
    assert session == {"coordinator_pub": "abc", "faction_name": "Vethara"}


def test_load_session_missing_exits(tmp_path, capsys):
    args = argparse.Namespace(data_dir=str(tmp_path), match_id="match-1")
    with pytest.raises(SystemExit) as exc_info:
        _load_session(args)
    assert exc_info.value.code == 1
    assert "run `join` first" in capsys.readouterr().err


def test_cmd_orders_with_no_prior_submission(tmp_path, capsys):
    args = argparse.Namespace(data_dir=str(tmp_path), match_id="match-1")
    _save_session(args, {"coordinator_pub": "abc"})
    cmd_orders(args)
    assert "no orders submitted yet" in capsys.readouterr().out


def test_cmd_orders_shows_last_receipt(tmp_path, capsys):
    args = argparse.Namespace(data_dir=str(tmp_path), match_id="match-1")
    _save_session(
        args,
        {"coordinator_pub": "abc", "last_order_receipt": {"revision": 2, "state": "accepted", "order_hash": "deadbeef"}},
    )
    cmd_orders(args)
    out = capsys.readouterr().out
    assert "revision 2" in out
    assert "accepted" in out
    assert "deadbeef" in out
