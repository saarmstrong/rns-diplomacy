# rns-diplomacy — Project Specification

> **NOTE: Target TCP/IP transport first.** Since this is Reticulum-based, any medium (LoRa, packet radio, etc.) should work, but get TCP/IP working as the initial transport.

---

## Product Goal

rns-diplomacy is a **pseudonymous, asynchronous, multiplayer strategy game** played over [Reticulum](https://reticulum.network/). It implements a Diplomacy-style negotiation and territory-control game for **7 players** with the following core properties:

- **Pseudonymous match-scoped identities** — each player generates a fresh identity per match; there are no accounts, no persistent profiles, no global identity linkage.
- **No accounts** — joining a match requires only generating a Reticulum identity and sending a join request.
- **Coordinator adjudicates** — one authoritative coordinator per match resolves orders deterministically.
- **Clients verify** — every client can independently re-run adjudication against the hash-chained signed state to detect coordinator dishonesty.

---

## Core Architecture

**Hybrid distributed** design:

- **Reticulum provides transport, identity, and cryptography.** All communication happens over Reticulum destinations. Identity is Reticulum identity (Curve25519 keypairs). Encryption and signing use Reticulum's built-in primitives.
- **One authoritative coordinator per match.** The coordinator manages match lifecycle, accepts orders, runs adjudication, signs and publishes results. There is no consensus protocol — the coordinator is the single source of truth for game state.
- **Deterministic adjudication.** The adjudication engine is a pure function: given the same map state and set of orders, it always produces the same result. No randomness, no time-dependence, no external inputs.
- **Hash-chained signed state.** Every phase result is signed by the coordinator and includes a hash of the previous state, forming an append-only verifiable history.
- **Direct player-to-player negotiation.** Negotiation messages go directly between players via LXMF, never through the coordinator. The coordinator cannot read or censor negotiations.

---

## Technology Stack

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| Network transport | Reticulum (RNS) |
| Messaging | LXMF (for negotiation) |
| Serialization | MessagePack or CBOR (deterministic) |
| Persistence | SQLite |
| Testing | pytest |
| Type safety | Type hints throughout |
| Data models | dataclasses or Pydantic |
| UI | Minimal terminal UI (CLI) |

---

## Repository Structure

```
rns-diplomacy/
├── protocol/          # Message definitions, encoding, validation
├── engine/            # Adjudication engine, game rules, map
├── coordinator/       # Match coordinator (server-side logic)
├── client/            # Player client (CLI + logic)
├── shared/            # Shared utilities, types, constants
├── tests/             # All test modules
│   ├── test_protocol/
│   ├── test_engine/
│   ├── test_coordinator/
│   └── test_integration/
└── docs/              # Architecture, protocol, threat model docs
```

---

## Identity Model

Three distinct identity concepts:

### 1. Match Coordinator Identity

- A Reticulum identity (Curve25519 keypair) created or loaded when the coordinator starts a match.
- **Signs everything**: phase results, state hashes, join responses, all coordinator-originated messages.
- Players use the coordinator's public key to verify all signed state.

### 2. Match-Scoped Player Identity

- A **fresh Reticulum identity generated per match**. Players do NOT reuse identities across matches.
- No global accounts, no persistent profiles. A player's identity is their keypair for this match only.
- The coordinator knows the player only by their match-scoped public key.
- This prevents cross-match identity correlation at the protocol level.

### 3. Public Faction Identity

- Players are assigned a **faction** (e.g., "Valdoria", "Kelmarch") upon joining.
- Other players see **faction names**, not identity hashes or public keys.
- The mapping of identity → faction is known only to the coordinator (and the player themselves).
- Faction assignment is **encrypted per-player** in the JOIN_ACCEPTED response.

---

## Reticulum Destinations

Two destination aspects:

| Aspect | Name | Purpose |
|---|---|---|
| Coordinator | `rns_diplomacy.game` | Match management, order submission, state publication |
| Player | `rns_diplomacy.player` | Receiving match updates, join responses, negotiation |

---

## Match Discovery & Join Flow

### Discovery

1. Coordinator **announces** the match on its `rns_diplomacy.game` destination (via Reticulum announce).
2. Clients **discover** available matches by listening for announces or querying known coordinator destinations.

### Join Flow

1. **Client → Coordinator**: `JOIN_REQUEST` — includes the player's match-scoped public key and optional display preferences.
2. **Coordinator → Client**: `JOIN_ACCEPTED` — includes the assigned faction (encrypted to the player's key), match parameters, and the list of faction names (but not the identity-to-faction mapping for other players). OR `JOIN_REJECTED` — includes a reason (match full, already started, banned key, etc.).
3. The coordinator stores the mapping of player identity → faction internally.
4. Once 7 players have joined and the coordinator starts the match, all players receive a `MATCH_START` message with the initial game state.

---

## Protocol Messages

Approximately 25 message types, organized by category:

### Discovery & Join

| Message | Direction | Description |
|---|---|---|
| `DISCOVER_GAME` | Client → Coordinator | Request match info |
| `GAME_INFO` | Coordinator → Client | Match parameters, status, player count |
| `JOIN_REQUEST` | Client → Coordinator | Request to join with match-scoped identity |
| `JOIN_ACCEPTED` | Coordinator → Client | Faction assignment (encrypted), match params |
| `JOIN_REJECTED` | Coordinator → Client | Rejection with reason |

### Negotiation

| Message | Direction | Description |
|---|---|---|
| `NEGOTIATION` | Player → Player | Free-form negotiation message (via LXMF, E2E encrypted) |
| `NEGOTIATION_ACK` | Player → Player | Delivery receipt |

### Orders

| Message | Direction | Description |
|---|---|---|
| `ORDER_SUBMIT` | Client → Coordinator | Submit orders for current phase |
| `ORDER_RECEIPT` | Coordinator → Client | Acknowledgement with order hash |
| `ORDER_UPDATE` | Client → Coordinator | Revise previously submitted orders |
| `ORDER_CANCEL` | Client → Coordinator | Cancel submitted orders |
| `ORDER_STATUS` | Coordinator → Client | Current order status |

### Phase Management

| Message | Direction | Description |
|---|---|---|
| `PHASE_START` | Coordinator → Client | New phase begins, deadline info |
| `PHASE_RESULT` | Coordinator → Client | Adjudication results, signed state |
| `PHASE_DEADLINE_WARNING` | Coordinator → Client | Deadline approaching |

### State & Verification

| Message | Direction | Description |
|---|---|---|
| `STATE_REQUEST` | Client → Coordinator | Request current or historical state |
| `STATE_RESPONSE` | Coordinator → Client | Signed state with hash chain |
| `STATE_HASH` | Coordinator → Client | Current state hash for quick verification |

### Draw & Meta

| Message | Direction | Description |
|---|---|---|
| `DRAW_PROPOSE` | Client → Coordinator | Propose a draw |
| `DRAW_VOTE` | Client → Coordinator | Vote on draw proposal |
| `DRAW_RESULT` | Coordinator → Client | Draw vote outcome |
| `MATCH_START` | Coordinator → Client | Match begins, initial state |
| `MATCH_END` | Coordinator → Client | Match over, final results |
| `ERROR` | Either direction | Error with code and description |

### Protocol Properties

- **Versioning**: Every message includes a protocol version field.
- **Validation**: All messages are validated against schema on receipt.
- **Replay protection**: Messages include timestamps and sequence numbers; the coordinator rejects duplicates.

---

## Serialization

- **Format**: MessagePack or CBOR — both are compact binary formats suitable for constrained links.
- **Deterministic encoding**: Serialization must be canonical/deterministic so that hashing the serialized form produces consistent results. Map keys are sorted, no duplicate keys, canonical integer encoding.
- **Validation**: All deserialized messages are validated against expected schema before processing.
- **Size limits**: Messages have maximum size limits to prevent DoS via oversized payloads.

---

## Negotiation

- **Transport**: LXMF (Lightweight Extensible Message Format) over Reticulum.
- **Encryption**: End-to-end encrypted between players using their match-scoped Reticulum identities.
- **Routing**: Direct player-to-player. Negotiation messages never pass through the coordinator.
- **Content**: Free-form text. The protocol does not interpret negotiation content.
- **Async**: Messages are stored-and-forwarded via LXMF; players do not need to be online simultaneously.
- **Delivery receipts**: `NEGOTIATION_ACK` confirms delivery (not reading).

---

## Order Submission

Structured transactional protocol:

### Order Lifecycle

1. **Draft** — composed locally by the client, not yet sent.
2. **Sent** — transmitted to the coordinator.
3. **Delivered** — coordinator acknowledges receipt.
4. **Accepted** — coordinator validates and accepts the orders.
5. **Rejected** — coordinator rejects (invalid orders, wrong phase, etc.).
6. **Superseded** — replaced by a newer submission from the same player.

### Properties

- **Revision numbers**: Each submission has a monotonically increasing revision number. The coordinator tracks the latest revision per player.
- **Validation**: Orders are validated against the current game state (correct unit types, valid destinations, player owns the units, etc.).
- **ORDER_RECEIPT**: The coordinator responds with a receipt containing a hash of the accepted orders, allowing the client to verify what the coordinator recorded.
- **Atomicity**: Orders are submitted as a complete set for a phase. Partial submissions replace previous ones entirely.

---

## Game Rules

### Map

- **Original fictional map** with 25–35 regions, 10–15 control centers, designed for 7 factions.
- Regions have fictional names (not historical Diplomacy names).
- Each faction starts with a home territory containing their initial units and home control centers.
- The map is a graph: regions are nodes, adjacency relationships are edges.
- Region types: **land**, **sea**, **coastal** (land regions adjacent to sea regions).

### Units

- **Army**: Moves on land. Cannot enter sea regions.
- **Fleet**: Moves on sea and coastal regions. Cannot move to inland regions.

### Phases

1. **Movement phase**: Players submit orders for their units.
2. **Retreat phase**: Dislodged units must retreat or disband. Only occurs if there are dislodged units.
3. **Adjustment phase**: Players build or disband units based on control center count. Only occurs after a movement+retreat cycle (typically once per game-year).

### Order Types

- **Hold**: Unit stays in place, defends at strength 1.
- **Move**: Unit attempts to move to an adjacent region.
- **Support Hold**: Unit supports another unit holding in an adjacent region (+1 strength to holder).
- **Support Move**: Unit supports another unit's move into an adjacent region (+1 strength to mover).
- **Convoy**: Deferred if complex; if implemented, fleets in sea regions transport armies across sea.

### Resolution

- Moves succeed if the mover has more support than the defender.
- Equal strength = bounce (both stay).
- Support is **cut** if the supporting unit is attacked (from a direction other than the one it's supporting into).
- A unit that is overpowered is **dislodged** and must retreat.
- Retreat: dislodged unit can move to an unoccupied, non-contested adjacent region, or disband.
- Adjustment: if a player controls more centers than units, they build; if fewer, they disband.

### Victory

- A player who controls a majority of control centers (typically >50%) wins.
- Players may also agree to a draw via the draw proposal/vote mechanism.

---

## Deterministic Adjudication Engine

The adjudication engine has the following properties:

- **Pure function**: `adjudicate(map_state, orders) → result`. No side effects, no I/O, no randomness.
- **Deterministic**: Same inputs always produce the same output. No hash maps with random iteration order, no floating point, no time-dependent logic.
- **Time-independent**: The engine does not read clocks or timestamps. Phase timing is managed externally by the coordinator.
- **Reproducible**: Any party with the same map state and orders can independently reproduce the result.

### Supported Resolutions

- Hold vs. move (strength comparison)
- Move vs. move (head-to-head, bounces)
- Support hold (adding defensive strength)
- Support move (adding offensive strength)
- Cutting support (attacking the supporter)
- Dislodgement (overpowered defender must retreat)
- Retreat resolution (retreat conflicts)
- Adjustment (build/disband based on center count vs. unit count)
- Circular movement (A→B→C→A all succeed if no external opposition)
- Self-dislodgement prevention (can't dislodge your own units)
- Convoy disruption (if convoys are implemented)

---

## State Hashing & Verification

- Every phase result is **hashed**: `hash(canonical_serialize(phase_result))`.
- The hash includes the **previous state hash**, forming a hash chain.
- The coordinator **signs** the hash with its identity key.
- Clients receive the signed hash with each phase result.
- **Client verification**: clients can verify the signature (using the coordinator's known public key), verify the hash chain (each hash links to the previous), and re-run adjudication locally to confirm the result matches.
- If a client detects a mismatch, the coordinator is provably dishonest — the signed incorrect result is cryptographic evidence.

---

## Coordinator Persistence

- **Database**: SQLite, one database per match.
- **Stored state**: All match state is persisted — match configuration, player identities and faction assignments, all submitted orders (every revision), all phase results and signed hashes, the complete hash chain, deadlines, and match status.
- **Atomic transactions**: All state mutations (accepting orders, advancing phases, recording results) are wrapped in SQLite transactions.
- **Restart recovery**: The coordinator can crash and restart, loading full state from SQLite. No in-memory-only state that would be lost.

---

## Deadline Model

- **Coordinator-authoritative**: The coordinator is the sole authority on deadlines. Clients display them but cannot override.
- **Configurable durations**: Phase durations are set at match creation (e.g., 24h for movement, 12h for retreat, 12h for adjustment).
- **Manual advance for development**: A CLI command allows the coordinator operator to manually advance the phase, bypassing the deadline. This is essential for testing and development.
- **Deadline warnings**: The coordinator sends `PHASE_DEADLINE_WARNING` messages as deadlines approach.
- **Default orders on deadline**: If a player has not submitted orders when the deadline expires, their units default to **hold**.

---

## CLI Interfaces

### Coordinator CLI

Commands for the match operator:

- `create` — Create a new match with configuration parameters.
- `start` — Start the match (requires 7 players joined).
- `status` — Display current match status, phase, players, deadlines.
- `advance-phase` — Manually advance to the next phase (dev/testing).
- `list-players` — Show joined players (faction assignments).
- `pause` / `resume` — Pause/resume deadlines.
- `end` — End the match (with reason).

### Client CLI

Commands for players:

- `discover` — Discover available matches on the network.
- `join` — Join a match (generates fresh identity, sends JOIN_REQUEST).
- `status` — Show current game state, your units, control centers.
- `negotiate` — Send a negotiation message to another faction.
- `order` — Compose and submit orders for the current phase.
- `orders` — Review submitted orders and their status.
- `verify` — Verify the coordinator's state against local adjudication.
- `history` — View phase history and hash chain.
- `draw` — Propose or vote on a draw.

---

## Testing Strategy

### Test Categories

1. **Protocol tests**: Serialization round-trips, message validation, encoding/decoding, size limits, version handling.
2. **Engine tests**: All adjudication cases — every order type, every resolution scenario, bounces, cuts, dislodgements, retreats, adjustments, circular moves, edge cases. This is the most critical test suite.
3. **Coordinator tests**: State machine transitions, join flow, order acceptance/rejection, phase advancement, deadline handling, persistence and recovery, signing and hash chaining.
4. **Integration tests**: Full 7-client simulation — clients discover, join, negotiate, submit orders, receive results, verify state. This is the end-to-end acceptance test.

### Transport Abstraction

- **`InMemoryTransport`**: A mock transport that delivers messages directly in-process. Used for all unit and integration tests. No actual Reticulum network required for testing.
- **`ReticulumTransport`**: The real transport using Reticulum. Only used in live testing and production.
- Both implement a common `Transport` interface, allowing the coordinator and client to be transport-agnostic.

---

## Security & Threat Model

### Threats & Mitigations

| Threat | Mitigation |
|---|---|
| **Stolen keys** | Match-scoped identities limit damage to one match. No persistent identity to steal. |
| **Replay attacks** | Sequence numbers and timestamps on all messages. Coordinator rejects duplicates. |
| **Malformed payloads** | Strict validation on all deserialized messages. Size limits. Type checking. |
| **Dishonest coordinator** | Hash-chained signed state allows client-side verification. Mismatch is cryptographic proof of dishonesty. |
| **Traffic analysis** | LXMF provides some store-and-forward obfuscation. Full traffic analysis resistance is out of scope. |
| **Identity correlation** | Fresh identity per match. No protocol-level linkage across matches. Application-layer correlation (play style, timing) is out of scope. |
| **DoS** | Message size limits. Rate limiting on join requests. Coordinator can reject/ban keys. |
| **Sybil attacks** | Coordinator limits match to 7 players. No benefit to multiple identities beyond filling slots. Match operator can require out-of-band invitation. |
| **Filesystem permissions** | SQLite databases and key material should have restricted filesystem permissions. |

---

## Documentation

The following documentation files should be produced:

| Document | Location | Content |
|---|---|---|
| README | `README.md` | Project overview, quick start, dependencies |
| Architecture | `docs/architecture.md` | System design, component interactions, data flow |
| Protocol | `docs/protocol.md` | Complete message format reference |
| Identity Model | `docs/identity-model.md` | Identity concepts, lifecycle, privacy properties |
| Threat Model | `docs/threat-model.md` | Security analysis, attack vectors, mitigations |
| Development | `docs/development.md` | Setup, testing, contribution guide |

---

## Development Order

The following 17 steps define the recommended build order:

1. **Repository setup** — pyproject.toml, directory structure, git init, dependencies, linting.
2. **Domain models** — Region, Faction, Unit, MapGraph, adjacency definitions, the original map.
3. **Map design** — Define the 25–35 region map with 7 factions, home territories, control centers, adjacency graph. Fictional names.
4. **Adjudication engine** — Implement the deterministic resolver for hold, move, support hold, support move, bounces, cut support, dislodgement.
5. **Adjudication tests** — Comprehensive test suite for all resolution cases. This is the most important test suite in the project.
6. **Retreat and adjustment** — Extend the engine for retreat resolution and build/disband adjustment phase.
7. **Canonical serialization** — Implement deterministic MessagePack/CBOR encoding. Verify round-trip and hash stability.
8. **State hashing** — Implement hash chaining and state serialization for verification.
9. **Protocol message models** — Define all ~25 message types as dataclasses/Pydantic models.
10. **Protocol encoding/validation** — Serialization, deserialization, validation, size limits for all message types.
11. **Transport abstraction** — Define the Transport interface. Implement InMemoryTransport.
12. **Coordinator state machine** — Match lifecycle: lobby → active → completed. Phase transitions, deadline tracking.
13. **Coordinator persistence** — SQLite schema and data layer. Store/load all match state. Atomic transactions.
14. **Coordinator join flow & order handling** — JOIN_REQUEST processing, faction assignment, ORDER_SUBMIT handling, receipts, signing.
15. **Client core** — Identity management, match discovery, order composition, submission, tracking, result verification.
16. **Negotiation** — LXMF integration for player-to-player messaging. E2E encryption. Delivery receipts.
17. **CLIs and integration tests** — Coordinator CLI, Client CLI, 7-client integration test, end-to-end demo.

---

## Acceptance Criteria

All of the following must work for the project to be considered complete:

1. A coordinator can create and start a 7-player match over Reticulum (TCP/IP transport).
2. Seven clients can discover, join, and receive faction assignments.
3. Players can negotiate directly via LXMF with E2E encryption.
4. Players can compose and submit orders with revision tracking.
5. The coordinator deterministically adjudicates orders and publishes signed results.
6. Phase results are hash-chained; clients can verify the full chain.
7. Clients can independently re-run adjudication and detect mismatches.
8. Retreat and adjustment phases work correctly.
9. The coordinator persists all state to SQLite and recovers from restart.
10. The draw proposal and voting mechanism works.
11. Deadlines are enforced; default hold orders are applied for missing submissions.
12. All adjudication edge cases pass (bounces, cuts, dislodgements, circular moves).
13. A full 7-client integration test runs end-to-end with InMemoryTransport.
14. CLI tools provide usable interfaces for both coordinator and player.

---

## Agent Working Rules

- **Build runnable code.** Every module should be importable and testable from the start. No stub files with `pass` everywhere.
- **Test after milestones.** Run the test suite after completing each development step. Don't accumulate untested code.
- **Small, tested modules.** Keep modules focused. Each file should have a clear single responsibility.
- **Explicit TODOs only for deferred features.** Mark convoy (if deferred) and other explicitly deferred features with `# TODO: <description>`. Do not use TODO for things that should be implemented now.
- **Type hints everywhere.** All function signatures, all return types, all class attributes.
- **No premature optimization.** Get it working and correct first. Optimize only if profiling shows a problem.
