# Architecture

## Overview

rns-diplomacy is a hybrid-distributed system: Reticulum provides transport, identity, and
cryptography; one coordinator process is the single authoritative source of truth for a
match's game state; every other participant (a player's client) is a peer that can act
independently and verify the coordinator's claims. There is no consensus protocol and no
peer-to-peer state replication — if the coordinator disappears, the match stalls until it (or
a restarted instance loaded from the same database) comes back, but it cannot silently corrupt
history without a client being able to prove it.

```
                    ┌─────────────────────┐
                    │  MatchCoordinator    │
                    │  (coordinator/match)│
                    └──────────┬───────────┘
             ┌──────────────────┼──────────────────┐
             │                  │                  │
      ┌──────▼──────┐   ┌───────▼──────┐   ┌───────▼──────┐
      │ lobby.py    │   │ orders.py    │   │ draws.py     │
      │ join flow   │   │ order intake │   │ propose/vote │
      └──────┬──────┘   └───────┬──────┘   └───────┬──────┘
             └──────────────────┼──────────────────┘
                    ┌────────────▼────────────┐
                    │  persistence.py          │
                    │  (SQLite — MatchStore)   │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  engine/ (adjudicator)   │
                    │  pure function, no I/O   │
                    └──────────────────────────┘

      Transport (shared/transport.py): InMemoryTransport | ReticulumTransport
                                 │
             ┌───────────────────┼───────────────────┐
             │                   │                   │
      ┌──────▼──────┐    ┌───────▼──────┐    ┌───────▼──────┐
      │ PlayerClient │    │ PlayerClient │    │ PlayerClient │  (up to 7)
      │ (client/)    │◄──►│ (client/)    │◄──►│ (client/)    │  direct negotiation,
      └──────────────┘    └──────────────┘    └──────────────┘  never via coordinator
```

## Layering

The codebase is layered strictly bottom-up; each layer only depends on the ones below it,
never sideways or up:

| Layer | Packages | Depends on |
|---|---|---|
| Foundation | `engine/`, `protocol/`, `shared/` | nothing project-internal |
| Application | `coordinator/`, `client/` | `engine/`, `protocol/`, `shared/` — never each other |
| Interfaces | `coordinator/server.py`, `client/cli.py` | their own package only |

`coordinator/` and `client/` do **not** import from each other. Anything both need
(`validate_orders`, the `Order` type, canonicalization) lives in `engine/` or `shared/` — see
`engine/state.py::validate_orders`, moved there specifically so the client could reuse the
coordinator's validation logic without an inverted dependency.

## Components

### `engine/` — the rules, as a pure function

- `engine/model.py` — `Region`, `Faction`, `Unit`, `GameState`. Plain dataclasses, no behavior.
- `engine/map.py` — the default 7-faction map ("The Shattered Reach"): regions, adjacency
  graph, starting units/supply centers.
- `engine/orders.py` — the four movement order types (`Hold`, `Move`, `SupportHold`,
  `SupportMove`) plus `RetreatOrder`/`BuildOrder`/`DisbandOrder` (engine-level only — see the
  Known Limitations note below on why these three aren't collectible over the wire yet).
- `engine/adjudicator.py` — `resolve_movement`, `resolve_retreats`, `resolve_adjustments` and
  their `apply_*_result` counterparts. Each is a pure function of `(GameState, orders) ->
  result`: no I/O, no randomness, no clock reads, so any party can reproduce a result exactly.
- `engine/hashing.py` — canonical serialization (`canonicalize`, `canonical_bytes`) and the
  hash chain (`hash_game_state`, `verify_chain`). Canonicalization is what makes two logically
  identical states hash identically regardless of dict/list ordering.

### `protocol/` — the wire format

- `protocol/messages.py` — all ~25 message dataclasses, a shared `Message` envelope
  (`protocol_version`, `sequence_number`, `timestamp`), and `MESSAGE_TYPES` for decode
  dispatch.
- `protocol/encoding.py` — `encode_message`/`decode_message`, built on the same canonicalize
  approach as `engine/hashing.py` (msgpack, sorted keys).
- `protocol/validation.py` — envelope and per-message-type validation, applied by both the
  coordinator and clients before acting on anything received.

### `shared/` — infrastructure both sides need

- `shared/transport.py` — the abstract `Transport` interface (`send`, `receive`, `announce`,
  `discover`) plus `InMemoryTransport`, used by nearly the entire test suite.
- `shared/reticulum_transport.py` — `ReticulumTransport`, the real implementation over RNS/LXMF.
  Addressing convention: a destination string is the hex-encoded Curve25519 public key of the
  identity to reach — the *same* convention `InMemoryTransport` uses, which is why
  `MatchCoordinator` and `PlayerClient` run against either one unmodified.
- `shared/identity.py` — `Identity`/`PublicIdentity`, a thin wrapper over `RNS.Identity` for
  signing, verifying, and asymmetric encryption, usable standalone (no live network needed).
- `shared/time.py`, `shared/logging.py` — deadline math and the shared log namespace.

### `coordinator/` — the authority

- `coordinator/persistence.py` (`MatchStore`) — SQLite is the single source of truth. Every
  mutation is wrapped in a transaction. The coordinator's signing identity is stored inside the
  database and doubles as its network address, so it's stable across process restarts.
- `coordinator/phases.py` — the match lifecycle/phase state machine (lobby → active →
  completed; diplomacy → orders → resolution → retreat (conditional) → build (conditional) →
  next turn), as pure transition functions guarded against illegal moves.
- `coordinator/lobby.py`, `coordinator/orders.py`, `coordinator/draws.py` — join flow, order
  intake/revision/cancellation, and draw proposal/voting, each a set of functions operating on
  a `MatchStore` plus the inbound message.
- `coordinator/match.py` (`MatchCoordinator`) — the orchestrator. Dispatches inbound messages
  to the modules above, drives phase advancement (running `engine/adjudicator` and signing +
  hash-chaining the result), tracks deadlines, and broadcasts outbound messages over whatever
  `Transport` it was given.
- `coordinator/server.py` — the CLI. See "Coordinator CLI process model" below.

### `client/` — a player

- `client/identity.py` — match-scoped identity generation and file persistence (a fresh
  identity per match, never reused — see `docs/identity-model.md`).
- `client/transport.py` (`PlayerClient`) — the client-side mirror of `MatchCoordinator`: sends
  requests, and a typed `poll()` dispatch updates tracked state (faction, phase, deadline,
  order receipts, phase-result history) as messages arrive.
- `client/orders.py` — local order composition and pre-submission validation (a convenience;
  the coordinator does its own authoritative validation regardless).
- `client/negotiations.py` — direct player-to-player messaging, entirely bypassing the
  coordinator.
- `client/verification.py` — independent verification: signature checks, hash-chain
  continuity, and (when the full order set for a phase is known) local re-adjudication compared
  against the coordinator's claim. See "Verification model" below and `docs/threat-model.md`.
- `client/cli.py` — the CLI. Session state (identity, coordinator address, and append-only
  local logs of every phase result and negotiation observed) persists across invocations under
  `<data-dir>/<match-id>/`, since each CLI command is a fresh, short-lived process.

## Data flow: one phase transition

1. Players send `ORDER_SUBMIT` (client → coordinator only; never seen by other players).
   `MatchCoordinator._on_order_submit` calls `coordinator/orders.py::handle_order_submit`,
   which validates ownership (`engine/state.py::validate_orders`) and persists the revision.
2. The coordinator's deadline expires, or an operator manually triggers an advance (`serve`
   picks up an admin command — see below). `MatchCoordinator.advance_phase()` runs:
   - Loads the turn-start `GameState` from the last persisted phase result.
   - Gathers each player's latest *accepted* order revision (missing/rejected/cancelled
     players' units default to Hold — `engine.adjudicator` handles this natively).
   - Calls the appropriate pure `engine.adjudicator` function.
   - Canonicalizes and hashes the resulting state, chaining from the previous link's hash
     (`engine/hashing.py`), and signs the hash with the coordinator's identity.
   - Persists the new `(turn, phase)` row and broadcasts `PHASE_RESULT` (the signed result)
     followed by `PHASE_START` (the new phase's deadline) to every joined player.
3. Each `PlayerClient` that's currently polling appends the new `PhaseResult` to its history;
   `client/cli.py` additionally logs it locally so a later, separate CLI invocation can still
   see it (`client verify`, `client history`).

## Coordinator CLI process model

Only one process — `coordinator serve` — holds a live network connection. This matters: a
second `ReticulumTransport` bound to a different port, as a separate short-lived CLI
invocation would be, has no path to reach clients already connected to `serve`'s port (RNS
routes across a connection graph, not by identity alone; two processes with the same signing
identity but different sockets are not interchangeable at the network layer). So
`start`/`advance-phase`/`pause`/`resume`/`end` never touch the network directly — they enqueue
an admin command in the shared SQLite database (`admin_commands` table) and wait briefly for
`serve`'s loop to pick it up and execute it. `create`/`status`/`list-players` are pure database
reads/writes and don't need `serve` running at all. See `coordinator/server.py`'s module
docstring for the full reasoning, including the same-process-RNS-instance constraint that
motivated it.

## Verification model

Every client can unilaterally do three things without any special access: verify a
`PHASE_RESULT`'s signature against the coordinator's known public key
(`client/verification.py::verify_signature`), verify that the claimed `state_hash` really is
the hash of the claimed `canonical_state` (`verify_hash_matches_state`), and verify hash-chain
continuity across everything it has personally observed (`verify_history`). These three are
what make a dishonest coordinator *provably* dishonest — a mismatch is cryptographic evidence,
not just a claim.

Full local re-adjudication (`reproduce_and_compare`) — actually re-running the engine and
comparing outputs — requires the complete order set for a phase, which an ordinary client
doesn't have (`ORDER_SUBMIT` is a private client↔coordinator message; a player never sees
other players' orders). It's available for audit tooling or tests that do have the full set.
`verify_history` doesn't require an unbroken chain back to `MATCH_START` either: a client that
starts observing mid-match verifies whatever segment it has seen for internal consistency,
which is weaker than verifying the whole match but still catches any tampering within the
window it was actually watching.

## Known limitations

- **Retreat/adjustment orders aren't collectible over the wire yet.** `RetreatOrder`,
  `BuildOrder`, and `DisbandOrder` have no `order_type` discriminator (unlike the four movement
  orders), so `ORDER_SUBMIT`'s `Order` union can't carry them. Both phases currently always
  resolve with the engine's deterministic defaults (forced disband on a missed retreat,
  no-build/civil-disorder-disband on a missed adjustment) — correct, tested engine behavior,
  just not yet backed by real player choice over the network.
- **Negotiation on true first contact.** If two players have never exchanged anything and
  neither has announced, the recipient of a `NEGOTIATION` message may only get a one-way
  destination hash, not the sender's full public key, and can't reply yet. See
  `client/negotiations.py`'s module docstring.
- **Negotiation is addressed by raw public key, not faction name.** CLAUDE.md's identity model
  keeps the identity→faction mapping coordinator-and-player-only, but direct P2P negotiation
  (by design, never routed through the coordinator) still needs a concrete destination address
  to send to. There's no in-protocol directory translating "message Ashenmere" into a pubkey —
  a player currently has to learn a negotiation partner's raw public key out-of-band (e.g.
  reading it out of an announce, or a partner volunteering it in a first message) and pass it
  to `client negotiate --to <pubkey>`. The local `history` log records negotiations by sender
  pubkey prefix for the same reason, not by faction name.
- **`shared/reticulum_transport.py` test coverage requires separate processes.** Two identities
  sharing one Python process's `RNS.Reticulum()` instance don't reliably path-find to each
  other, and a second `RNS.Reticulum()` call in the same process raises outright — it's a
  hard per-process singleton. Every RNS-touching test spawns real subprocesses (see
  `tests/integration/test_reticulum_transport.py`).
