# rns-diplomacy — Development Plan

## Phase 1: Foundation

**Goal**: Repository scaffolding, domain models, and the original game map.

### Milestones

- Repository initialized with pyproject.toml, directory structure, git, linting config
- Core domain models defined: Region, Faction, Unit, UnitType, MapGraph, Adjacency
- Original fictional map designed: 25–35 regions, 10–15 control centers, 7 factions with home territories
- Adjacency graph fully defined with land/sea/coastal region types
- Map is importable and unit-testable (can query neighbors, validate paths)

### Complexity Hotspots

- **Map design**: Balancing 7 factions on an original map is a game-design problem, not just engineering. Asymmetric starting positions can make the game unfair. Expect iteration.
- **Adjacency correctness**: Every missing or wrong edge is a game-breaking bug. Need thorough tests and ideally a visual verification step.

### Risks

- Map balance issues won't surface until playtesting — accept this and design for easy map editing.
- Fictional naming is creative work; don't let it block engineering progress. Placeholder names are fine initially.

---

## Phase 2: Game Engine

**Goal**: A fully tested, deterministic adjudication engine.

### Milestones

- Adjudicator resolves all order types: hold, move, support hold, support move
- Bounce resolution (equal strength standoffs)
- Support cutting (attacking a supporting unit)
- Dislodgement and retreat resolution
- Adjustment phase (build/disband based on center count)
- Circular movement resolution (A→B→C→A)
- Self-dislodgement prevention
- Canonical serialization (MessagePack/CBOR) with deterministic encoding
- State hashing with hash-chain construction
- Comprehensive adjudication test suite passing (50+ test cases minimum)

### Complexity Hotspots

- **Adjudication algorithm**: The resolution of simultaneous moves with support, cuts, and bounces is the hardest algorithmic problem in the project. Diplomacy adjudication has well-known edge cases (e.g., the "beleaguered garrison", convoy paradoxes). Study existing implementations (like the DATC test cases).
- **Determinism guarantees**: Must avoid dict iteration order issues, floating point, and any source of nondeterminism. Requires discipline and explicit testing.
- **Canonical serialization**: Getting MessagePack/CBOR to produce byte-identical output for the same logical data. Sorted keys, canonical integer encoding, no optional fields with default values differing.

### Risks

- Adjudication bugs are subtle and can hide for a long time. Invest heavily in tests.
- If convoy support is attempted, it introduces convoy paradoxes — a known hard problem. Deferring convoys is the safe play.

---

## Phase 3: Protocol & Transport

**Goal**: Message definitions, encoding pipeline, and both transport implementations.

### Milestones

- All ~25 message types defined as dataclasses/Pydantic models
- Serialization/deserialization for all message types
- Message validation (schema checks, size limits, type checking)
- Protocol versioning in every message
- Replay protection fields (sequence numbers, timestamps)
- `Transport` abstract interface defined
- `InMemoryTransport` implemented (for tests)
- `ReticulumTransport` implemented — **TCP/IP first**
- Transport round-trip tests passing

### Complexity Hotspots

- **Message schema design**: Getting the right fields and types for ~25 message types requires careful thought. Changes after coordinator/client are built are expensive.
- **ReticulumTransport**: Reticulum's API for destinations, announces, links, and packets has its own learning curve. TCP/IP interface mode is the simplest starting point.

### Risks

- Reticulum API may have quirks or undocumented behaviors. Budget time for exploration.
- Message format changes after this phase ripple through coordinator and client.

---

## Phase 4: Coordinator

**Goal**: A fully functional match coordinator with persistence, signing, and lifecycle management.

### Milestones

- State machine: lobby → active → completed, with proper transition guards
- SQLite schema designed and implemented (matches, players, orders, phases, results)
- Atomic transactions for all state mutations
- Lobby/join flow: accept JOIN_REQUEST, assign factions, send encrypted JOIN_ACCEPTED/JOIN_REJECTED
- Phase management: start phases, track deadlines, advance on deadline or manual trigger
- Order acceptance: validate orders, store with revision tracking, send ORDER_RECEIPT with hash
- Adjudication integration: run engine at phase end, produce signed results
- Hash chain: sign each phase result, chain to previous hash
- Default hold orders on deadline expiry for non-submitting players
- Restart recovery: load full state from SQLite, resume match
- Draw proposal/vote handling

### Complexity Hotspots

- **State machine correctness**: Ensuring the coordinator never enters an invalid state (e.g., accepting orders for a phase that hasn't started, advancing when orders are being processed). Needs careful design and testing.
- **Signing and hash chaining**: Integrating Reticulum's signing primitives with the hash chain. Must be correct — verification depends on it.
- **Restart recovery**: Reconstructing in-memory state (timers, pending operations) from SQLite after a crash.

### Risks

- Concurrency issues if multiple requests arrive while processing (SQLite serializes, but application logic must handle ordering).
- Deadline timer management across restarts.

---

## Phase 5: Client

**Goal**: A working player client with identity management, order composition, negotiation, and verification.

### Milestones

- Identity management: generate fresh per-match identity, store keypair locally
- Match discovery: listen for coordinator announces, display available matches
- Join flow: send JOIN_REQUEST, receive and decrypt faction assignment
- Order composition: interactive order builder with validation against current state
- Order submission and tracking: send orders, receive receipts, handle revisions
- Result verification: re-run adjudication locally, verify signatures, verify hash chain
- Negotiation via LXMF: send/receive encrypted messages to/from other factions
- Delivery receipts for negotiation
- Game state display: current map, units, control centers, phase info

### Complexity Hotspots

- **LXMF integration**: Setting up LXMF for direct messaging with delivery receipts. LXMF has its own message lifecycle and propagation model.
- **Order composition UX**: Making it easy to compose valid orders in a terminal UI. Players need to see their units, valid destinations, and support options.
- **Verification logic**: Re-running adjudication and comparing against signed results. Must handle the full hash chain walk.

### Risks

- LXMF may have limitations or quirks for direct messaging (it's primarily designed for propagation-based delivery).
- Terminal UI for order composition may be clunky. Accept this for v1 — a better UI is a future enhancement.

---

## Phase 6: Integration & Polish

**Goal**: CLIs, end-to-end tests, documentation, and a working demo.

### Milestones

- Coordinator CLI: create, start, status, advance-phase, list-players, pause/resume, end
- Client CLI: discover, join, status, negotiate, order, orders, verify, history, draw
- 7-client integration test: full game simulation using InMemoryTransport
- Restart recovery integration test
- End-to-end demo script (can run a full match)
- Documentation complete: README, architecture.md, protocol.md, identity-model.md, threat-model.md, development.md
- Threat model document with all attack vectors and mitigations
- Code cleanup, consistent error handling, logging

### Complexity Hotspots

- **7-client integration test**: Orchestrating 7 independent clients through a full game with negotiation, orders, retreats, and adjustments. Needs careful sequencing and assertion design.
- **Documentation quality**: Writing accurate, complete documentation that stays in sync with the implementation.

### Risks

- Integration tests may reveal bugs in any layer. Budget time for debugging across the stack.
- Demo may expose UX issues that need quick fixes.
