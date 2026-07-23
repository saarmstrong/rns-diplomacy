"""Small terminal client utility for local match identity management."""

from __future__ import annotations

import argparse
from pathlib import Path

from client.identity import create_identity, get_display_hash, load_identity, save_identity


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rns-diplomacy-client")
    parser.add_argument("--identity", type=Path, default=Path("match.identity"))
    commands = parser.add_subparsers(dest="command", required=True)
    join = commands.add_parser("join", help="create or show the match-scoped identity")
    join.add_argument("--new", action="store_true", help="replace any existing identity")
    commands.add_parser("identity", help="show local identity fingerprint")
    for name in ("discover", "status", "orders", "verify", "history", "draw", "negotiate", "order"):
        commands.add_parser(name, help=f"{name} requires a connected transport embedding")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the basic client command interface."""
    args = _parser().parse_args(argv)
    if args.command == "join":
        if args.new or not args.identity.exists():
            identity = create_identity()
            save_identity(identity, args.identity)
        else:
            identity = load_identity(args.identity)
        print(f"match identity: {get_display_hash(identity)}")
        return
    if args.command == "identity":
        print(get_display_hash(load_identity(args.identity)))
        return
    raise SystemExit(f"{args.command} is available through the programmatic ClientTransport API")


if __name__ == "__main__":
    main()
