# rns-diplomacy

A pseudonymous, asynchronous, multiplayer strategy game for 7 players, played over [Reticulum](https://reticulum.network/). It implements a Diplomacy-style negotiation and territory-control game with:

- **Match-scoped identities** — players generate a fresh Reticulum identity per match. No accounts, no persistent profiles.
- **One authoritative coordinator per match**, which adjudicates orders and publishes signed results. There is no consensus protocol.
- **Deterministic adjudication** — given the same map state and orders, the resolver always produces the same result, so any client can independently re-run it and verify the coordinator's results.
- **Hash-chained, signed state** — every phase result is signed by the coordinator and chained to the previous state hash, so a dishonest coordinator can be caught with cryptographic proof.
- **Direct player-to-player negotiation** over LXMF, end-to-end encrypted and never routed through the coordinator.

TCP/IP is the initial Reticulum transport target; other Reticulum-supported media (LoRa, packet radio, ...) should work without protocol changes.

See [CLAUDE.md](CLAUDE.md) for the full project specification (protocol messages, identity model, adjudication rules, threat model, and build order).

## Status

All 17 build-order steps in `CLAUDE.md` are implemented and tested (526 tests; see
[Known limitations](#known-limitations) below for the honest gaps). Full detail on each area
lives in `docs/`; this section is a summary.

- **`engine/`** — domain model, the default 7-faction map ("The Shattered Reach"), and the deterministic adjudication engine (movement, support, bounces, cuts, dislodgement, retreats, adjustments, canonical state hashing/hash-chaining).
- **`protocol/`** — all ~25 message types, canonical msgpack encoding/decoding, and validation.
- **`shared/`** — the `Transport` abstraction with two implementations: `InMemoryTransport` (used by nearly the whole suite) and `ReticulumTransport` (real network I/O over RNS/LXMF — see below); a Reticulum identity wrapper (signing, verification, asymmetric encryption); deadline utilities; structured logging.
- **`coordinator/`** — the match state machine, SQLite persistence with restart recovery, the join/lobby flow, order submission and revision handling, draw proposal/voting, and the `MatchCoordinator` orchestrator tying all of it together (phase advancement, signed hash-chained results, deadline tracking, pause/resume, manual dev-mode advance, replay protection, join rate limiting). `coordinator/server.py` is the operator-facing CLI (`rns-diplomacy-coordinator`).
- **`client/`** — match-scoped identity generation/storage, the `PlayerClient` session (discovery via announces, join, order submission/revision/cancellation, state requests, draw voting), local order composition and pre-submission validation, direct player-to-player negotiation (bypassing the coordinator entirely), and independent client-side verification (signature checks, hash-chain continuity, and local re-adjudication against the coordinator's claimed result). `client/cli.py` is the player-facing CLI (`rns-diplomacy-client`).
- `tests/integration/` drives all of the above together, including a full 7-client discover → join → negotiate → order → adjudicate → verify → draw flow over `InMemoryTransport`, multi-turn lifecycle tests, coordinator restart/recovery mid-match, and default-hold-on-deadline behavior.

**Real network transport is wired in and driven by both CLIs.** `shared/reticulum_transport.py` implements `Transport` over actual RNS/LXMF — `MatchCoordinator` and `PlayerClient` run against it completely unmodified, proving the transport-agnostic design holds in practice, not just in theory (`tests/integration/test_reticulum_match_join.py`, and both CLIs' subprocess-based tests). Delivery is via LXMF rather than raw RNS packets, since LXMF transparently handles messages larger than one packet's MDU (a `PHASE_RESULT` carrying a full canonical game state would not reliably fit in a single raw packet) and gives end-to-end encryption to the recipient's identity for free — negotiation content is encrypted in transit with no extra application-layer step. Addressing reuses the same convention as `InMemoryTransport`: a destination is the hex-encoded public key of the identity to reach; LXMF's own fixed `lxmf.delivery` destination namespace means there's no need for the separate `rns_diplomacy.game`/`.player` aspect split the spec describes (see the module docstring for the reasoning).

Two things worth knowing if you touch `shared/reticulum_transport.py`:
- Two identities sharing one process's `RNS.Reticulum()` instance don't reliably path-find to each other, and a second `RNS.Reticulum()` call in the same process raises outright (`RNS.Reticulum` is a hard per-process singleton). Every RNS-touching test therefore spawns genuinely separate subprocesses — the same way two real machines would talk — rather than constructing two nodes in-process.
- On first contact, a receiver may not yet be able to resolve a sender's full identity from just the transport (`LXMessage.get_source()` can be `None` until an announce has propagated) — real mesh-network behavior, not a bug. `JOIN_REQUEST` already carries the player's public key in the message body for exactly this reason; `MatchCoordinator` replies to that field rather than assuming the transport always knows who sent something.

Only `serve` (the coordinator CLI's long-running subcommand) holds a live network connection at
a time — the other coordinator subcommands (`start`, `advance-phase`, `pause`, `resume`, `end`)
are short-lived processes that enqueue an admin command in the shared SQLite database for a
running `serve` to pick up and execute, since a second `ReticulumTransport` bound to a different
port has no path to reach clients already connected to `serve`'s port. See
`coordinator/server.py`'s module docstring and `docs/development.md`'s manual walkthrough.

### Known limitations

Scope-limited gaps, each flagged in the code and covered in more detail in `docs/architecture.md`'s Known Limitations section and `docs/threat-model.md`'s Known gaps section:

- Retreat and adjustment orders are not yet collected from players over the wire (no discriminator field yet on `RetreatOrder`/`BuildOrder`/`DisbandOrder`); those phases currently resolve with the engine's deterministic defaults (forced disband on missed retreats, no build/civil-disorder disband on missed adjustments).
- Client-side re-adjudication (`client/verification.py`) needs the full order set for a phase to fully reproduce it, but `ORDER_SUBMIT` is a private client↔coordinator message — a client only ever sees its own orders. Signature and hash-chain verification (which every client *can* always do unilaterally) are what actually make a dishonest coordinator provably dishonest per the threat model; full local re-adjudication is available for audit tooling that does have the full order set.
- The client CLI's `order` command is flag-based (`--order REGION:TYPE[:ARGS]`), not an interactive builder walking through valid destinations; the data layer for valid-option queries (`client/orders.py`) exists and is tested, just not surfaced interactively.
- Negotiation is addressed by raw public key, not faction name — there's no in-protocol directory translating a faction name into a destination, since direct P2P negotiation never goes through the coordinator (which is the only party that knows the identity↔faction mapping).
- `tests/integration/test_reticulum_match_join.py` has a known, intermittent full-timeout failure (roughly 1 run in 4–8 under pytest); rerun in isolation before assuming a regression — see `docs/development.md`.

## Quick start

Requires Python 3.12+.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Run the test suite:

```bash
.venv/bin/pytest
```

Lint:

```bash
.venv/bin/ruff check .
```

Play a match against real Reticulum/LXMF (two console scripts, installed by the steps above):
`rns-diplomacy-coordinator` and `rns-diplomacy-client`. See `docs/development.md`'s "Manually
running a match end to end" section for a full walkthrough.

## Dependencies

| Package | Purpose |
|---|---|
| [`rns`](https://reticulum.network/) | Transport, identity, and cryptography |
| [`lxmf`](https://github.com/markqvist/LXMF) | Player-to-player negotiation messaging |
| `msgpack` | Canonical, deterministic message/state serialization |
| `pydantic` | Reserved for schema validation (dataclasses are used throughout for now, for consistency) |

Dev-only: `pytest`, `pytest-cov`, `ruff`.

## Repository layout

```
protocol/       Message definitions, canonical encoding, validation
engine/         Adjudication engine, game rules, the default map
coordinator/    Match state machine, persistence, join/order/draw handling, orchestration, CLI
client/         Player client: identity, session, local order composition, negotiation, verification, CLI
shared/         Transport abstraction, identity wrapper, time/deadline utilities, logging
tests/          Mirrors the package layout above, plus tests/integration/
docs/           Architecture, protocol, identity model, threat model, and development docs
```

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — system design, component interactions, data flow, known limitations.
- [`docs/protocol.md`](docs/protocol.md) — complete message format reference.
- [`docs/identity-model.md`](docs/identity-model.md) — identity concepts, lifecycle, privacy properties.
- [`docs/threat-model.md`](docs/threat-model.md) — security analysis, attack vectors, mitigations.
- [`docs/development.md`](docs/development.md) — setup, testing, manual end-to-end walkthrough.
- `TODO.md` tracks the detailed build checklist against `CLAUDE.md`'s 17-step build order; `docs/PLAN.md` has the phase-by-phase plan it was built from.

## Development

- All coordinator/client logic is written against the abstract `Transport` interface (`shared/transport.py`) so the full stack is testable with `InMemoryTransport` — no live Reticulum network required.
- The adjudication engine (`engine/adjudicator.py`) is a pure function of `(GameState, orders)`: no I/O, no randomness, no clock reads.
