"""Coordinator command-line lifecycle management.

Network hosting is deliberately kept separate from the durable match logic:
this entry point creates and administers a match database and can be used by
an embedding Reticulum transport process.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from coordinator.match import MatchCoordinator
from coordinator.persistence import MatchStore
from coordinator.phases import MatchState
from protocol.messages import MatchStatus
from shared.transport import InMemoryTransport


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rns-diplomacy-coordinator")
    parser.add_argument("--database", default="rns-diplomacy.sqlite")
    parser.add_argument("--match-id", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create")
    commands.add_parser("start")
    commands.add_parser("status")
    commands.add_parser("advance-phase")
    commands.add_parser("list-players")
    commands.add_parser("pause")
    commands.add_parser("resume")
    end = commands.add_parser("end")
    end.add_argument("--reason", default="ended by operator")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Administer a durable coordinator match from the command line."""
    args = _parser().parse_args(argv)
    with MatchStore(Path(args.database)) as store:
        transport = InMemoryTransport("coordinator")
        coordinator = MatchCoordinator(args.match_id, store, transport)
        record = store.get_match(args.match_id)
        if args.command == "create":
            if record is not None:
                raise SystemExit(f"match already exists: {args.match_id}")
            MatchCoordinator.create(args.match_id, store, transport)
            print(f"created {args.match_id}")
            return
        if record is None:
            raise SystemExit(f"unknown match: {args.match_id}")
        if args.command == "start":
            coordinator.start_match()
        elif args.command == "advance-phase":
            coordinator.advance_phase()
        elif args.command == "list-players":
            for player in store.list_players(args.match_id):
                print(f"{player.faction_id}\t{player.public_key.hex()}\t{player.display_name or ''}")
            return
        elif args.command == "pause":
            coordinator.pause()
        elif args.command == "resume":
            coordinator.resume()
        elif args.command == "end":
            store.save_match_state(args.match_id, MatchState(status=MatchStatus.COMPLETED, phase=record.state.phase, turn=record.state.turn, year=record.state.year))
            print(args.reason)
        record = store.get_match(args.match_id)
        print(f"{record.match_id}: {record.state.status.value}, {record.state.phase.value}, turn {record.state.turn}")


if __name__ == "__main__":
    main()
