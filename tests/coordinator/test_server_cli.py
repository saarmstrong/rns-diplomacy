"""Tests for coordinator/server.py's CLI: argument parsing and the DB-only commands.

start/advance-phase/pause/resume/end only touch the database (they
enqueue an admin command for a running `serve` process to pick up), so
they're covered here via the enqueue/wait path. `serve` itself wraps
MatchCoordinator, already thoroughly tested against a real
ReticulumTransport in tests/integration/, and was manually smoke-tested
end to end (create -> serve -> 7 real client joins -> start ->
advance-phase -> pause -> resume -> end, all over real TCP) during
development; not re-exercised here to avoid duplicating the slower
network tests for logic that isn't CLI-specific.
"""

from __future__ import annotations

import argparse
import json
import threading
import time

import pytest

from coordinator.persistence import MatchStore
from coordinator.server import (
    _parse_listen,
    build_parser,
    cmd_create,
    cmd_end,
    cmd_list_players,
    cmd_pause,
    cmd_start,
    cmd_status,
)
from shared.identity import Identity


def test_parse_listen_valid():
    assert _parse_listen("127.0.0.1:4242") == ("127.0.0.1", 4242)
    assert _parse_listen("0.0.0.0:80") == ("0.0.0.0", 80)


@pytest.mark.parametrize("value", ["not-a-listen-spec", "127.0.0.1", "127.0.0.1:", ":4242abc", "host:abc"])
def test_parse_listen_rejects_malformed_input(value):
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_listen(value)


def test_build_parser_requires_a_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("create", []),
        ("status", []),
        ("list-players", []),
        ("start", []),
        ("advance-phase", []),
        ("pause", []),
        ("resume", []),
        ("end", ["--reason", "x"]),
        ("serve", ["--listen", "127.0.0.1:1"]),
    ],
)
def test_build_parser_accepts_every_subcommand(command, extra):
    parser = build_parser()
    args = parser.parse_args([command, "match-1", *extra])
    assert args.match_id == "match-1"
    assert args.command == command


def test_serve_requires_listen_argument():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["serve", "match-1"])


def test_end_requires_reason_argument():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["end", "match-1"])


def _args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(data_dir=str(kwargs.pop("data_dir")), match_id="match-1", **kwargs)


def test_cmd_create_creates_a_match_database(tmp_path, capsys):
    cmd_create(_args(data_dir=tmp_path))
    out = capsys.readouterr().out
    assert (tmp_path / "match-1.sqlite3").exists()
    assert "created match 'match-1'" in out
    assert "coordinator public key: " in out


def test_cmd_create_refuses_to_overwrite_an_existing_match(tmp_path, capsys):
    cmd_create(_args(data_dir=tmp_path))
    with pytest.raises(SystemExit) as exc_info:
        cmd_create(_args(data_dir=tmp_path))
    assert exc_info.value.code == 1
    assert "already exists" in capsys.readouterr().err


def test_cmd_status_unknown_match_exits_with_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cmd_status(_args(data_dir=tmp_path))
    assert exc_info.value.code == 1
    assert "run `create` first" in capsys.readouterr().err


def test_cmd_status_reports_lobby_state(tmp_path, capsys):
    cmd_create(_args(data_dir=tmp_path))
    created_out = capsys.readouterr().out
    created_pubkey = created_out.splitlines()[-1].removeprefix("coordinator public key: ")

    cmd_status(_args(data_dir=tmp_path))
    out = capsys.readouterr().out
    assert "status:   lobby" in out
    assert "players:  0/7" in out
    assert "deadline: none" in out
    assert f"pubkey:   {created_pubkey}" in out


def test_cmd_list_players_empty(tmp_path, capsys):
    cmd_create(_args(data_dir=tmp_path))
    capsys.readouterr()
    cmd_list_players(_args(data_dir=tmp_path))
    assert "no players joined yet" in capsys.readouterr().out


def test_cmd_list_players_shows_joined_players(tmp_path, capsys):
    cmd_create(_args(data_dir=tmp_path))
    store = MatchStore(tmp_path / "match-1.sqlite3")
    try:
        store.add_player("match-1", Identity.generate().public_bytes, "vet", display_name="Ambassador", joined_at=1.0)
    finally:
        store.close()

    capsys.readouterr()
    cmd_list_players(_args(data_dir=tmp_path))
    out = capsys.readouterr().out
    assert "Vethara" in out
    assert "Ambassador" in out


def test_cmd_start_enqueues_an_admin_command(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("coordinator.server._ADMIN_COMMAND_WAIT_SECONDS", 0.2)
    cmd_create(_args(data_dir=tmp_path))
    capsys.readouterr()

    cmd_start(_args(data_dir=tmp_path))
    out, err = capsys.readouterr()

    store = MatchStore(tmp_path / "match-1.sqlite3")
    try:
        pending = store.get_pending_admin_commands("match-1")
    finally:
        store.close()
    assert len(pending) == 1
    assert pending[0].command == "start"
    assert pending[0].payload == "{}"
    # No `serve` process is running in this test to pick it up, so it should
    # time out and say so rather than hang or silently claim success.
    assert "queued" in err


def test_cmd_end_enqueues_with_reason_payload(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("coordinator.server._ADMIN_COMMAND_WAIT_SECONDS", 0.2)
    cmd_create(_args(data_dir=tmp_path))
    capsys.readouterr()

    cmd_end(_args(data_dir=tmp_path, reason="operator abandoned"))
    capsys.readouterr()

    store = MatchStore(tmp_path / "match-1.sqlite3")
    try:
        pending = store.get_pending_admin_commands("match-1")
    finally:
        store.close()
    assert pending[0].command == "end"
    assert json.loads(pending[0].payload) == {"reason": "operator abandoned"}


def test_cmd_reports_done_once_a_concurrent_serve_process_marks_it_processed(tmp_path, capsys, monkeypatch):
    """Exercises _enqueue_and_wait's real polling loop against a concurrent 'processor'."""
    monkeypatch.setattr("coordinator.server._ADMIN_COMMAND_WAIT_SECONDS", 5.0)
    cmd_create(_args(data_dir=tmp_path))
    capsys.readouterr()

    def process_pending_command_shortly() -> None:
        deadline = time.time() + 3.0
        store = MatchStore(tmp_path / "match-1.sqlite3")
        try:
            while time.time() < deadline:
                pending = store.get_pending_admin_commands("match-1")
                if pending:
                    store.mark_admin_command_processed(pending[0].id, processed_at=time.time())
                    return
                time.sleep(0.05)
        finally:
            store.close()

    worker = threading.Thread(target=process_pending_command_shortly)
    worker.start()
    cmd_pause(_args(data_dir=tmp_path))
    worker.join(timeout=5.0)

    out, err = capsys.readouterr()
    assert "pause: done" in out
    assert err == ""
