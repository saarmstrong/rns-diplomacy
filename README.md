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

Currently implemented and tested (399 tests passing):

- **`engine/`** — domain model, the default 7-faction map ("The Shattered Reach"), and the deterministic adjudication engine (movement, support, bounces, cuts, dislodgement, retreats, adjustments, canonical state hashing/hash-chaining).
- **`protocol/`** — all ~25 message types, canonical msgpack encoding/decoding, and validation.
- **`shared/`** — the `Transport` abstraction (with an in-memory implementation for tests), a Reticulum identity wrapper (signing, verification, asymmetric encryption), and deadline utilities.
- **`coordinator/`** — the match state machine, SQLite persistence with restart recovery, the join/lobby flow, order submission and revision handling, draw proposal/voting, and the `MatchCoordinator` orchestrator tying all of it together (phase advancement, signed hash-chained results, deadline tracking, manual dev-mode advance).

Not yet implemented: the `client/` package (identity management, order composition, negotiation, verification), the coordinator/client CLIs, the `ReticulumTransport` (real network transport — everything above is tested against `InMemoryTransport`), and LXMF-based negotiation.

Retreat and adjustment orders are not yet collected from players over the wire (no discriminator field yet on `RetreatOrder`/`BuildOrder`/`DisbandOrder`); those phases currently resolve with the engine's deterministic defaults (forced disband on missed retreats, no build/civil-disorder disband on missed adjustments).

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
coordinator/    Match state machine, persistence, join/order/draw handling, orchestration
client/         Player client (not yet implemented)
shared/         Transport abstraction, identity wrapper, time/deadline utilities
tests/          Mirrors the package layout above, plus tests/integration/
docs/           Architecture, protocol, and planning notes
```

## Development

- `TODO.md` tracks the detailed build checklist; `docs/PLAN.md` has the phase-by-phase plan.
- All coordinator/client logic is written against the abstract `Transport` interface (`shared/transport.py`) so the full stack is testable with `InMemoryTransport` — no live Reticulum network required.
- The adjudication engine (`engine/adjudicator.py`) is a pure function of `(GameState, orders)`: no I/O, no randomness, no clock reads.
