"""Client CLI — play rns-diplomacy as a match-scoped pseudonymous player.

Subcommands: discover, join, status, negotiate, order, orders, verify,
history, draw.

Session state persists across invocations under
``<data-dir>/<match-id>/``: the player's identity (``identity.key``,
owner-only permissions — see ``client/identity.py``), a ``session.json``
with the coordinator's address/pubkey and a few small cached facts
(faction name, latest order receipt, an in-progress order draft), and
append-only JSON-lines logs of every PHASE_RESULT and NEGOTIATION this
client has ever observed. Each CLI invocation is a fresh process with an
empty ``PlayerClient``, so ``verify`` and ``history`` read from these
local logs rather than requiring the coordinator to replay everything
on demand — they're built up a little more each time any command polls.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from client.identity import create_identity, load_identity, save_identity
from client.orders import compose_order, get_orderable_units, validate_orders
from client.transport import PlayerClient, listen_for_announces
from client.verification import verify_history
from engine.hashing import game_state_from_canonical_bytes
from engine.model import Phase
from engine.orders import OrderType
from protocol.messages import MatchStart, PhaseResult
from shared.identity import PublicIdentity
from shared.logging import configure_logging, get_logger
from shared.reticulum_transport import ReticulumTransport

_POLL_SECONDS = 8.0

log = get_logger("client.cli")


# --- Session / local persistence helpers --------------------------------


def _parse_connect(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError("expected HOST:PORT, e.g. 127.0.0.1:4242")
    return host, int(port)


def _session_dir(args: argparse.Namespace) -> Path:
    path = Path(args.data_dir) / args.match_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _identity_path(args: argparse.Namespace) -> Path:
    return _session_dir(args) / "identity.key"


def _configdir(args: argparse.Namespace) -> Path:
    return _session_dir(args) / "reticulum"


def _session_file(args: argparse.Namespace) -> Path:
    return _session_dir(args) / "session.json"


def _phase_results_path(args: argparse.Namespace) -> Path:
    return _session_dir(args) / "phase_results.jsonl"


def _negotiations_path(args: argparse.Namespace) -> Path:
    return _session_dir(args) / "negotiations.jsonl"


def _load_session(args: argparse.Namespace) -> dict:
    path = _session_file(args)
    if not path.exists():
        print(f"no session for match {args.match_id!r} in {args.data_dir!r} (run `join` first)", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text())


def _save_session(args: argparse.Namespace, session: dict) -> None:
    _session_file(args).write_text(json.dumps(session, indent=2))


def _append_jsonl(path: Path, record: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _match_start_to_record(message: MatchStart) -> dict:
    return {
        "match_id": message.match_id,
        "faction_names": list(message.faction_names),
        "canonical_state": message.canonical_state.hex(),
        "state_hash": message.state_hash,
        "signature": message.signature.hex(),
        "deadline": message.deadline,
    }


def _record_to_match_start(record: dict) -> MatchStart:
    return MatchStart(
        sequence_number=0,
        timestamp=0.0,
        match_id=record["match_id"],
        faction_names=tuple(record["faction_names"]),
        canonical_state=bytes.fromhex(record["canonical_state"]),
        state_hash=record["state_hash"],
        signature=bytes.fromhex(record["signature"]),
        deadline=record["deadline"],
    )


def _phase_result_to_record(message: PhaseResult) -> dict:
    return {
        "turn": message.turn,
        "phase": message.phase.value,
        "year": message.year,
        "canonical_state": message.canonical_state.hex(),
        "state_hash": message.state_hash,
        "previous_state_hash": message.previous_state_hash,
        "signature": message.signature.hex(),
    }


def _record_to_phase_result(record: dict) -> PhaseResult:
    return PhaseResult(
        sequence_number=0,
        timestamp=0.0,
        match_id="",
        phase=Phase(record["phase"]),
        turn=record["turn"],
        year=record["year"],
        canonical_state=bytes.fromhex(record["canonical_state"]),
        state_hash=record["state_hash"],
        previous_state_hash=record["previous_state_hash"],
        signature=bytes.fromhex(record["signature"]),
    )


def _connect(args: argparse.Namespace, session: dict) -> PlayerClient:
    identity = load_identity(_identity_path(args))
    transport = ReticulumTransport(
        identity, connect_to=(session["coordinator_host"], session["coordinator_port"]), configdir=_configdir(args)
    )
    # The coordinator enforces a persistently strictly-increasing sequence number per
    # sender (replay protection). Each CLI invocation is a fresh process with a fresh
    # PlayerClient, so the counter has to be carried forward via the session file, or
    # every command after the first would be rejected as a replay — see
    # client/transport.py::PlayerClient's initial_sequence_number docstring.
    return PlayerClient(
        identity,
        transport,
        session["coordinator_pub"],
        args.match_id,
        initial_sequence_number=session.get("last_sequence_number", 0),
    )


def _persist_sequence(args: argparse.Namespace, session: dict, client: PlayerClient) -> None:
    session["last_sequence_number"] = client.sequence_number
    _save_session(args, session)


def _poll_and_log(client: PlayerClient, args: argparse.Namespace, seconds: float = _POLL_SECONDS) -> None:
    """Poll for a while, appending any newly observed PHASE_RESULT/NEGOTIATION to the local log."""
    deadline = time.time() + seconds
    seen_hashes = {r["state_hash"] for r in _read_jsonl(_phase_results_path(args))}
    while time.time() < deadline:
        client.poll()
        for result in client.phase_results:
            if result.state_hash not in seen_hashes:
                _append_jsonl(_phase_results_path(args), _phase_result_to_record(result))
                seen_hashes.add(result.state_hash)
        time.sleep(0.2)
    for entry in client.negotiations:
        _append_jsonl(
            _negotiations_path(args),
            {"sender": entry.sender_destination, "content": entry.message.content, "timestamp": entry.message.timestamp},
        )


# --- Commands ------------------------------------------------------------


def cmd_discover(args: argparse.Namespace) -> None:
    identity = create_identity()
    transport = ReticulumTransport(
        identity, connect_to=_parse_connect(args.coordinator), configdir=Path(args.data_dir) / "_discover"
    )
    print(f"listening for match announces via {args.coordinator} for {args.seconds:.0f}s...")
    found: dict[str, str] = {}
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        for coordinator_pub, match_id in listen_for_announces(transport):
            found[match_id] = coordinator_pub
        time.sleep(0.3)

    if not found:
        print("no matches found")
        return
    for match_id, coordinator_pub in found.items():
        print(f"{match_id}  coordinator={coordinator_pub}")


def cmd_join(args: argparse.Namespace) -> None:
    identity_path = _identity_path(args)
    identity = load_identity(identity_path) if identity_path.exists() else create_identity()
    save_identity(identity, identity_path)

    transport = ReticulumTransport(identity, connect_to=_parse_connect(args.coordinator), configdir=_configdir(args))
    client = PlayerClient(identity, transport, args.coordinator_pub, args.match_id)
    client.join(display_name=args.display_name)

    deadline = time.time() + _POLL_SECONDS
    while time.time() < deadline and client.faction_name is None and client.join_rejected_reason is None:
        client.poll()
        time.sleep(0.2)

    if client.join_rejected_reason is not None:
        print(f"join rejected: {client.join_rejected_reason}", file=sys.stderr)
        sys.exit(1)
    if client.faction_name is None:
        print("no response from coordinator (timed out)", file=sys.stderr)
        sys.exit(1)

    host, port = _parse_connect(args.coordinator)
    _save_session(
        args,
        {
            "coordinator_pub": args.coordinator_pub,
            "coordinator_host": host,
            "coordinator_port": port,
            "faction_name": client.faction_name,
            "match_parameters": client.match_parameters,
            "last_sequence_number": client.sequence_number,
        },
    )
    print(f"joined match {args.match_id!r} as {client.faction_name}")


def cmd_status(args: argparse.Namespace) -> None:
    session = _load_session(args)
    client = _connect(args, session)
    client.request_state()
    _poll_and_log(client, args)
    _persist_sequence(args, session, client)

    print(f"match:    {args.match_id}")
    print(f"faction:  {session['faction_name']}")
    if not client.state_responses:
        print("state:    no response from coordinator (is a `serve` process running?)")
        return

    latest = client.state_responses[-1]
    state = game_state_from_canonical_bytes(latest.canonical_state)
    faction_id = next(
        (fid for fid, faction in state.factions.items() if faction.name == session["faction_name"]), None
    )
    print(f"turn:     {latest.turn}")
    # STATE_RESPONSE's canonical_state doesn't itself track the coordinator's current
    # phase (that's coordinator-side MatchState, not engine-side GameState) — use a
    # PHASE_START actually observed this session, if the poll window caught one.
    print(f"phase:    {client.phase.value if client.phase is not None else 'unknown (no PHASE_START observed this session)'}")
    if client.deadline is not None:
        remaining = client.deadline - time.time()
        print(f"deadline: {'expired' if remaining <= 0 else f'{remaining:.0f}s remaining'}")
    else:
        print("deadline: unknown (no PHASE_START/MATCH_START observed this session)")

    print("your units:")
    your_units = get_orderable_units(state, faction_id) if faction_id else []
    if not your_units:
        print("  (none)")
    for unit in your_units:
        print(f"  {unit.unit_type.value:<6} {unit.region_id}")

    your_centers = sorted(r for r, f in state.supply_centers.items() if f == faction_id)
    print(f"your supply centers: {len(your_centers)} ({', '.join(your_centers) or 'none'})")

    print("control centers by faction:")
    counts: dict[str, int] = {}
    for owner in state.supply_centers.values():
        if owner:
            counts[owner] = counts.get(owner, 0) + 1
    for owner_faction_id, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        name = state.factions[owner_faction_id].name if owner_faction_id in state.factions else owner_faction_id
        print(f"  {name:<12} {count}")
    uncontrolled = sum(1 for f in state.supply_centers.values() if not f)
    if uncontrolled:
        print(f"  (uncontrolled) {uncontrolled}")


def cmd_negotiate(args: argparse.Namespace) -> None:
    session = _load_session(args)
    client = _connect(args, session)
    client.send_negotiation(args.to, args.message)
    print(f"sent to {args.to}: {args.message}")
    _poll_and_log(client, args, seconds=2.0)
    _persist_sequence(args, session, client)


def cmd_order(args: argparse.Namespace) -> None:
    session = _load_session(args)
    client = _connect(args, session)
    client.request_state()
    _poll_and_log(client, args)
    _persist_sequence(args, session, client)

    if not client.state_responses:
        print("no state available from coordinator yet — cannot compose orders", file=sys.stderr)
        sys.exit(1)

    state = game_state_from_canonical_bytes(client.state_responses[-1].canonical_state)
    faction_id = next(
        (fid for fid, faction in state.factions.items() if faction.name == session["faction_name"]), None
    )
    if faction_id is None:
        print(f"could not find faction {session['faction_name']!r} in current state", file=sys.stderr)
        sys.exit(1)

    units_by_region = {u.region_id: u for u in get_orderable_units(state, faction_id)}
    orders = []
    for spec in args.order:
        parts = spec.split(":")
        region_id, order_type_name, *rest = parts
        unit = units_by_region.get(region_id)
        if unit is None:
            print(f"'{region_id}' is not one of your units", file=sys.stderr)
            sys.exit(1)
        order_type = OrderType(order_type_name)
        if order_type is OrderType.MOVE:
            orders.append(compose_order(unit, order_type, destination_id=rest[0]))
        elif order_type is OrderType.SUPPORT_HOLD:
            orders.append(compose_order(unit, order_type, supported_region_id=rest[0]))
        elif order_type is OrderType.SUPPORT_MOVE:
            orders.append(compose_order(unit, order_type, supported_region_id=rest[0], supported_destination_id=rest[1]))
        else:
            orders.append(compose_order(unit, order_type))

    errors = validate_orders(state, faction_id, tuple(orders))
    if errors:
        for error in errors:
            print(f"invalid: {error}", file=sys.stderr)
        sys.exit(1)

    client.submit_orders(tuple(orders), revision=args.revision)
    deadline = time.time() + _POLL_SECONDS
    while time.time() < deadline and not client.order_receipts:
        client.poll()
        time.sleep(0.2)
    _persist_sequence(args, session, client)

    if not client.order_receipts:
        print("no receipt from coordinator (timed out)", file=sys.stderr)
        sys.exit(1)

    receipt = client.order_receipts[-1]
    session["last_order_receipt"] = {"revision": receipt.revision, "state": receipt.state.value, "order_hash": receipt.order_hash}
    _save_session(args, session)
    print(f"orders {receipt.state.value}: revision {receipt.revision}, hash {receipt.order_hash}")


def cmd_orders(args: argparse.Namespace) -> None:
    session = _load_session(args)
    receipt = session.get("last_order_receipt")
    if receipt is None:
        print("no orders submitted yet this match (from this client)")
        return
    print(f"revision {receipt['revision']}: {receipt['state']} (hash {receipt['order_hash']})")


def cmd_verify(args: argparse.Namespace) -> None:
    session = _load_session(args)
    if "match_start" not in session:
        # Try to catch a live MATCH_START broadcast to anchor the chain at genesis.
        # If this client joined in an earlier, separate invocation and only starts
        # polling now, it may simply never have seen it — that's fine: verify_history
        # falls back to verifying whatever chain segment is actually in the local
        # log for internal consistency, which just can't prove anything predating it.
        client = _connect(args, session)
        client.request_state()
        _poll_and_log(client, args)
        _persist_sequence(args, session, client)
        if client.match_start is not None:
            session["match_start"] = _match_start_to_record(client.match_start)
            _save_session(args, session)

    match_start = _record_to_match_start(session["match_start"]) if "match_start" in session else None
    phase_results = [_record_to_phase_result(r) for r in _read_jsonl(_phase_results_path(args))]
    if not phase_results and match_start is None:
        print("no phase history observed yet — nothing to verify", file=sys.stderr)
        sys.exit(1)

    coordinator_public = PublicIdentity(bytes.fromhex(session["coordinator_pub"]))
    report = verify_history(coordinator_public, phase_results, match_start=match_start)

    print(f"checked {report.checked} link(s) in the hash chain")
    if report.ok:
        print("OK — signatures and hash chain check out")
    else:
        print("MISMATCH DETECTED:")
        for issue in report.issues:
            print(f"  turn {issue.turn} {issue.phase.value}: {issue.reason}")
        sys.exit(1)


def cmd_history(args: argparse.Namespace) -> None:
    session = _load_session(args)
    client = _connect(args, session)
    _poll_and_log(client, args, seconds=2.0)
    _persist_sequence(args, session, client)

    print("phase history:")
    results = _read_jsonl(_phase_results_path(args))
    if not results:
        print("  none observed yet")
    for record in results:
        print(f"  turn {record['turn']:<3} {record['phase']:<10} hash={record['state_hash'][:16]}...")

    print("negotiation history:")
    negotiations = _read_jsonl(_negotiations_path(args))
    if not negotiations:
        print("  none observed yet")
    for record in negotiations:
        print(f"  from {record['sender'][:16]}...: {record['content']}")


def cmd_draw(args: argparse.Namespace) -> None:
    session = _load_session(args)
    client = _connect(args, session)
    if args.action == "propose":
        client.propose_draw(turn=args.turn)
    else:
        client.vote_draw(turn=args.turn, vote=args.vote == "yes")
    _poll_and_log(client, args, seconds=3.0)
    _persist_sequence(args, session, client)
    if client.draw_results:
        result = client.draw_results[-1]
        print(f"draw {'accepted' if result.accepted else 'not (yet) accepted'}: {dict(result.votes)}")
    else:
        print("draw request sent (no result observed yet)")


# --- Argument parsing ------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rns-diplomacy-client", description="Play rns-diplomacy as a player.")
    parser.add_argument("--data-dir", default=".rns-diplomacy-client", help="directory holding session state")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_discover = subparsers.add_parser("discover", help="listen for match announces")
    p_discover.add_argument("--coordinator", required=True, help="HOST:PORT of a known network entry point")
    p_discover.add_argument("--seconds", type=float, default=10.0)
    p_discover.set_defaults(func=cmd_discover)

    p_join = subparsers.add_parser("join", help="join a match")
    p_join.add_argument("match_id")
    p_join.add_argument("--coordinator", required=True, type=str, help="HOST:PORT")
    p_join.add_argument("--coordinator-pub", required=True, help="coordinator's public key (hex), from `discover`")
    p_join.add_argument("--display-name", default=None)
    p_join.set_defaults(func=cmd_join)

    p_status = subparsers.add_parser("status", help="show current game state, your units, control centers")
    p_status.add_argument("match_id")
    p_status.set_defaults(func=cmd_status)

    p_negotiate = subparsers.add_parser("negotiate", help="send a negotiation message directly to another player")
    p_negotiate.add_argument("match_id")
    p_negotiate.add_argument("--to", required=True, help="recipient's public key (hex)")
    p_negotiate.add_argument("--message", required=True)
    p_negotiate.set_defaults(func=cmd_negotiate)

    p_order = subparsers.add_parser("order", help="compose and submit orders for the current phase")
    p_order.add_argument("match_id")
    p_order.add_argument("--revision", type=int, required=True)
    p_order.add_argument(
        "--order",
        action="append",
        required=True,
        metavar="REGION:TYPE[:ARG1[:ARG2]]",
        help='e.g. "vet_peak:hold", "vet_peak:move:pale_ridge", '
        '"thr_heart:support_move:vet_peak:pale_ridge" (repeatable, one per unit)',
    )
    p_order.set_defaults(func=cmd_order)

    p_orders = subparsers.add_parser("orders", help="review your last submitted orders and their status")
    p_orders.add_argument("match_id")
    p_orders.set_defaults(func=cmd_orders)

    p_verify = subparsers.add_parser("verify", help="verify the coordinator's state against local adjudication")
    p_verify.add_argument("match_id")
    p_verify.set_defaults(func=cmd_verify)

    p_history = subparsers.add_parser("history", help="view phase history and hash chain")
    p_history.add_argument("match_id")
    p_history.set_defaults(func=cmd_history)

    p_draw = subparsers.add_parser("draw", help="propose or vote on a draw")
    p_draw.add_argument("match_id")
    p_draw.add_argument("action", choices=["propose", "vote"])
    p_draw.add_argument("--turn", type=int, required=True)
    p_draw.add_argument("--vote", choices=["yes", "no"], help="required for `vote`")
    p_draw.set_defaults(func=cmd_draw)

    return parser


def main() -> None:
    """Entry point for rns-diplomacy-client."""
    configure_logging()
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "draw" and args.action == "vote" and args.vote is None:
        parser.error("--vote is required for `draw vote`")
    args.func(args)


if __name__ == "__main__":
    main()
