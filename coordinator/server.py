"""Coordinator CLI — operate an rns-diplomacy match.

Subcommands: create, status, list-players, start, advance-phase, pause,
resume, end, serve.

``MatchStore`` (SQLite) is the single source of truth across
invocations — each command is a short-lived process that opens the same
database file. The coordinator's signing identity is persisted inside
it (``coordinator/persistence.py``) and doubles as its network address.

Only ``serve`` holds a live network connection — it's the one process
that can actually broadcast to already-connected players (a second
``ReticulumTransport`` bound to a different port, as a separate
short-lived process would be, has no path to reach clients connected to
serve's port; RNS routes by identity across a connection graph, not by
identity alone). So start/advance-phase/pause/resume/end don't touch the
network at all: they enqueue an admin command in the shared database
(``admin_commands`` table) and wait briefly for confirmation that a
running ``serve`` process picked it up and executed it. If none is
running, the command sits queued until one starts.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from coordinator.match import MatchConfig, MatchCoordinator
from coordinator.persistence import AdminCommandRecord, MatchStore
from engine.map import FACTIONS
from protocol.constants import (
    DEFAULT_ADJUSTMENT_PHASE_SECONDS,
    DEFAULT_MOVEMENT_PHASE_SECONDS,
    DEFAULT_RETREAT_PHASE_SECONDS,
    MAX_PLAYERS,
)
from protocol.messages import MatchStatus
from shared.identity import Identity
from shared.logging import configure_logging, get_logger
from shared.reticulum_transport import ReticulumTransport

_ADMIN_COMMAND_WAIT_SECONDS = 10.0
_ANNOUNCE_INTERVAL_SECONDS = 30.0

log = get_logger("coordinator.cli")


def _parse_listen(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError("expected HOST:PORT, e.g. 0.0.0.0:4242")
    return host, int(port)


def _db_path(args: argparse.Namespace) -> Path:
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / f"{args.match_id}.sqlite3"


def _configdir(args: argparse.Namespace) -> Path:
    return Path(args.data_dir) / f"{args.match_id}-reticulum"


def _open_store(args: argparse.Namespace) -> MatchStore:
    db_path = _db_path(args)
    if not db_path.exists():
        print(f"no such match {args.match_id!r} in {args.data_dir!r} (run `create` first)", file=sys.stderr)
        sys.exit(1)
    return MatchStore(db_path)


def cmd_create(args: argparse.Namespace) -> None:
    db_path = _db_path(args)
    if db_path.exists():
        print(f"match {args.match_id!r} already exists at {db_path}", file=sys.stderr)
        sys.exit(1)
    identity = Identity.generate()
    store = MatchStore(db_path)
    try:
        store.create_match(args.match_id, identity, created_at=time.time())
    finally:
        store.close()
    print(f"created match {args.match_id!r} at {db_path}")
    print(f"coordinator public key: {identity.public_bytes.hex()}")


def cmd_status(args: argparse.Namespace) -> None:
    store = _open_store(args)
    try:
        record = store.get_match(args.match_id)
        players = store.list_players(args.match_id)
        deadline = store.get_deadline(args.match_id)
        paused = store.get_paused_remaining_seconds(args.match_id)
        pending = store.get_pending_admin_commands(args.match_id)
        coordinator_pub = store.load_coordinator_identity(args.match_id).public_bytes.hex()
    finally:
        store.close()

    print(f"match:    {args.match_id}")
    print(f"pubkey:   {coordinator_pub}")
    print(f"status:   {record.state.status.value}")
    print(f"phase:    {record.state.phase.value}")
    print(f"turn:     {record.state.turn}  year: {record.state.year}")
    print(f"players:  {len(players)}/{MAX_PLAYERS}")
    if record.state.status is MatchStatus.COMPLETED:
        pass  # a completed match has no meaningful deadline left to report
    elif paused is not None:
        print(f"deadline: paused ({paused:.0f}s remaining when resumed)")
    elif deadline is not None:
        remaining = deadline - time.time()
        print(f"deadline: {'expired' if remaining <= 0 else f'{remaining:.0f}s remaining'}")
    else:
        print("deadline: none")
    if pending:
        print(f"pending admin commands (no `serve` running?): {', '.join(p.command for p in pending)}")


def cmd_list_players(args: argparse.Namespace) -> None:
    store = _open_store(args)
    try:
        players = store.list_players(args.match_id)
    finally:
        store.close()

    if not players:
        print("no players joined yet")
        return
    for player in players:
        label = f" ({player.display_name})" if player.display_name else ""
        print(f"{FACTIONS[player.faction_id].name:<12} {player.public_key.hex()[:16]}...{label}")


def _enqueue_and_wait(args: argparse.Namespace, command: str, payload: dict) -> None:
    store = _open_store(args)
    try:
        command_id = store.enqueue_admin_command(args.match_id, command, json.dumps(payload), created_at=time.time())
        deadline = time.time() + _ADMIN_COMMAND_WAIT_SECONDS
        while time.time() < deadline:
            record = store.get_admin_command(command_id)
            if record is not None and record.processed_at is not None:
                print(f"{command}: done")
                return
            time.sleep(0.3)
        print(f"{command}: queued (no running `serve` process picked it up yet)", file=sys.stderr)
    finally:
        store.close()


def cmd_start(args: argparse.Namespace) -> None:
    _enqueue_and_wait(args, "start", {})


def cmd_advance_phase(args: argparse.Namespace) -> None:
    _enqueue_and_wait(args, "advance-phase", {})


def cmd_pause(args: argparse.Namespace) -> None:
    _enqueue_and_wait(args, "pause", {})


def cmd_resume(args: argparse.Namespace) -> None:
    _enqueue_and_wait(args, "resume", {})


def cmd_end(args: argparse.Namespace) -> None:
    _enqueue_and_wait(args, "end", {"reason": args.reason})


def _process_admin_command(coordinator: MatchCoordinator, record: AdminCommandRecord) -> None:
    payload = json.loads(record.payload)
    if record.command == "start":
        coordinator.start_match()
    elif record.command == "advance-phase":
        coordinator.advance_phase()
    elif record.command == "pause":
        coordinator.pause()
    elif record.command == "resume":
        coordinator.resume()
    elif record.command == "end":
        coordinator.end_match(payload["reason"])
    else:
        log.warning("unknown admin command %r (id=%s), skipping", record.command, record.id)


def cmd_serve(args: argparse.Namespace) -> None:
    store = _open_store(args)
    try:
        identity = store.load_coordinator_identity(args.match_id)
        transport = ReticulumTransport(identity, listen=_parse_listen(args.listen), configdir=_configdir(args))
        config = MatchConfig(
            movement_phase_seconds=args.movement_seconds,
            retreat_phase_seconds=args.retreat_seconds,
            adjustment_phase_seconds=args.adjustment_seconds,
        )
        coordinator = MatchCoordinator(args.match_id, store, transport, config=config)

        log.info(
            "serving match %s on %s (pubkey %s, Ctrl-C to stop)",
            args.match_id,
            args.listen,
            identity.public_bytes.hex(),
        )
        coordinator.announce()
        try:
            last_announce = time.monotonic()
            while True:
                coordinator.poll()
                for record in store.get_pending_admin_commands(args.match_id):
                    try:
                        _process_admin_command(coordinator, record)
                        log.info("processed admin command %r", record.command)
                    except Exception:
                        log.exception("admin command %r (id=%s) failed", record.command, record.id)
                    store.mark_admin_command_processed(record.id, processed_at=time.time())
                if time.monotonic() - last_announce >= _ANNOUNCE_INTERVAL_SECONDS:
                    coordinator.announce()
                    last_announce = time.monotonic()
                time.sleep(0.5)
        except KeyboardInterrupt:
            log.info("stopping")
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rns-diplomacy-coordinator", description="Operate an rns-diplomacy match.")
    parser.add_argument(
        "--data-dir", default=".rns-diplomacy", help="directory holding match databases and Reticulum state"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_create = subparsers.add_parser("create", help="create a new match")
    p_create.add_argument("match_id")
    p_create.set_defaults(func=cmd_create)

    p_status = subparsers.add_parser("status", help="show match status, phase, and deadline")
    p_status.add_argument("match_id")
    p_status.set_defaults(func=cmd_status)

    p_list = subparsers.add_parser("list-players", help="show joined players and their factions")
    p_list.add_argument("match_id")
    p_list.set_defaults(func=cmd_list_players)

    p_start = subparsers.add_parser("start", help="start the match once 7 players have joined (needs `serve` running)")
    p_start.add_argument("match_id")
    p_start.set_defaults(func=cmd_start)

    p_advance = subparsers.add_parser(
        "advance-phase", help="manually advance to the next phase (dev/testing; needs `serve` running)"
    )
    p_advance.add_argument("match_id")
    p_advance.set_defaults(func=cmd_advance_phase)

    p_pause = subparsers.add_parser("pause", help="freeze the current phase's deadline (needs `serve` running)")
    p_pause.add_argument("match_id")
    p_pause.set_defaults(func=cmd_pause)

    p_resume = subparsers.add_parser("resume", help="restore a paused deadline (needs `serve` running)")
    p_resume.add_argument("match_id")
    p_resume.set_defaults(func=cmd_resume)

    p_end = subparsers.add_parser("end", help="end the match immediately, with a reason (needs `serve` running)")
    p_end.add_argument("match_id")
    p_end.add_argument("--reason", required=True)
    p_end.set_defaults(func=cmd_end)

    p_serve = subparsers.add_parser("serve", help="run the persistent listening loop (required for players to connect)")
    p_serve.add_argument("match_id")
    p_serve.add_argument("--listen", required=True, type=str, help="HOST:PORT to listen on, e.g. 0.0.0.0:4242")
    p_serve.add_argument("--movement-seconds", type=float, default=DEFAULT_MOVEMENT_PHASE_SECONDS)
    p_serve.add_argument("--retreat-seconds", type=float, default=DEFAULT_RETREAT_PHASE_SECONDS)
    p_serve.add_argument("--adjustment-seconds", type=float, default=DEFAULT_ADJUSTMENT_PHASE_SECONDS)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main() -> None:
    """Entry point for rns-diplomacy-coordinator."""
    configure_logging()
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
