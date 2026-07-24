# rns-diplomacy — Development Checklist

## Phase 1: Foundation

### Setup

- [x] Create pyproject.toml with project metadata and dependencies
- [x] Set up directory structure: protocol/, engine/, coordinator/, client/, shared/, tests/, docs/
- [x] Initialize git repository
- [x] Add .gitignore (Python, SQLite, __pycache__, .rns/)
- [x] Configure linting (ruff or flake8) and formatting (black or ruff format)
- [x] Add pytest configuration
- [x] Pin dependency versions: reticulum, lxmf, msgpack/cbor2, pydantic

### Domain Models

- [x] Define Region model (name, type: land/sea/coastal, is_control_center, home_faction)
- [x] Define Faction model (name, home_regions, home_centers)
- [x] Define Unit model (type: army/fleet, faction, region)
- [x] Define UnitType enum (ARMY, FLEET)
- [x] Define MapGraph (regions, adjacency edges, neighbor queries)
- [x] Implement adjacency validation (coastal connects to sea and land, armies can't enter sea, etc.)
- [x] Define GameState model (units, ownership, control_centers, phase, turn)

### Map Design

- [x] Design 25–35 region map with 7 balanced factions
- [x] Name all regions (fictional names)
- [x] Define all adjacency relationships
- [x] Assign 10–15 control centers across the map
- [x] Assign home territories and starting units for each faction
- [x] Verify map connectivity (no isolated regions)
- [x] Write map validation tests (neighbor symmetry, region type consistency)
- [x] Test that each faction has a viable starting position

## Phase 2: Game Engine

### Adjudication — Core Orders

- [x] Define Order types: Hold, Move, SupportHold, SupportMove
- [x] Implement order validation (unit exists, owns unit, valid destination, adjacency)
- [x] Implement hold resolution (strength 1 defense)
- [x] Implement move resolution (strength comparison)
- [x] Implement support hold (adds +1 defensive strength)
- [x] Implement support move (adds +1 offensive strength)

### Adjudication — Conflict Resolution

- [x] Implement bounce resolution (equal strength → both stay)
- [x] Implement head-to-head battle (two units moving into each other)
- [x] Implement support cutting (attacking a supporter cancels support)
- [x] Implement dislodgement (overpowered unit marked for retreat)
- [x] Implement self-dislodgement prevention
- [x] Implement circular movement (A→B→C→A all succeed if no opposition)
- [x] Handle unordered units (default to hold)

### Adjudication — Retreat & Adjustment

- [x] Implement retreat phase resolution
- [x] Validate retreat destinations (unoccupied, non-contested, adjacent)
- [x] Handle retreat conflicts (two units retreat to same region → both disband)
- [x] Implement adjustment phase (build/disband)
- [x] Validate builds (only in unoccupied home centers)
- [x] Enforce disband when units > centers

### Adjudication — Tests

- [x] Test simple hold
- [x] Test simple move to empty region
- [x] Test move with equal opposition (bounce)
- [x] Test move with support succeeding
- [x] Test support hold preventing dislodge
- [x] Test support cut by attack
- [x] Test head-to-head battle
- [x] Test head-to-head with unequal support
- [x] Test dislodgement and retreat required
- [x] Test circular movement (3-way)
- [x] Test self-dislodgement prevention
- [x] Test retreat to valid region
- [x] Test retreat conflict (both disband)
- [x] Test adjustment build
- [x] Test adjustment disband
- [x] Test complex multi-region conflict scenario
- [x] Test determinism (same inputs → identical output, run 100x)
- [x] Test all units default to hold when no orders submitted

### Serialization

- [x] Implement canonical MessagePack/CBOR encoder (sorted keys, canonical integers)
- [x] Implement decoder with validation
- [x] Verify serialization round-trip for all domain models
- [x] Verify deterministic output (serialize → hash → serialize again → same hash)
- [x] Implement size limit enforcement
- [x] Write serialization tests for edge cases (empty maps, max-size payloads)

### State Hashing

- [x] Implement state hash function: hash(canonical_serialize(phase_result))
- [x] Implement hash chaining (include previous hash in current hash input)
- [x] Implement signing integration (sign hash with coordinator identity)
- [x] Implement signature verification
- [x] Test hash chain construction over multiple phases
- [x] Test hash chain verification (detect tampered state)

## Phase 3: Protocol & Transport

### Message Models

- [x] Define DISCOVER_GAME message
- [x] Define GAME_INFO message
- [x] Define JOIN_REQUEST message
- [x] Define JOIN_ACCEPTED message (with encrypted faction field)
- [x] Define JOIN_REJECTED message
- [x] Define NEGOTIATION message
- [x] Define NEGOTIATION_ACK message
- [x] Define ORDER_SUBMIT message
- [x] Define ORDER_RECEIPT message (with order hash)
- [x] Define ORDER_UPDATE message
- [x] Define ORDER_CANCEL message
- [x] Define ORDER_STATUS message
- [x] Define PHASE_START message
- [x] Define PHASE_RESULT message (with signed state)
- [x] Define PHASE_DEADLINE_WARNING message
- [x] Define STATE_REQUEST message
- [x] Define STATE_RESPONSE message
- [x] Define STATE_HASH message
- [x] Define DRAW_PROPOSE message
- [x] Define DRAW_VOTE message
- [x] Define DRAW_RESULT message
- [x] Define MATCH_START message
- [x] Define MATCH_END message
- [x] Define ERROR message
- [x] Add protocol version field to all messages
- [x] Add sequence number and timestamp fields for replay protection

### Protocol Encoding & Validation

- [x] Implement message serialization for all types
- [x] Implement message deserialization with type dispatch
- [x] Implement schema validation on all received messages
- [x] Implement size limit checks on all messages
- [x] Test round-trip encoding/decoding for every message type
- [x] Test validation rejects malformed messages
- [x] Test version checking

### Transport

- [x] Define abstract Transport interface (send, receive, announce, discover)
- [x] Implement InMemoryTransport for testing
- [x] Test InMemoryTransport message delivery
- [x] Implement ReticulumTransport with TCP/IP interface
- [ ] Set up Reticulum destinations (rns_diplomacy.game, rns_diplomacy.player) — deliberately not implemented as a separate two-aspect namespace; see shared/reticulum_transport.py's module docstring for why (LXMF fixes its own "lxmf.delivery" destination per identity, and our protocol envelope already self-describes message type/match, so a second namespace split added complexity without adding a real property)
- [x] Implement announce for match discovery
- [x] Test ReticulumTransport over local TCP/IP

## Phase 4: Coordinator

### State Machine

- [x] Implement match states: lobby → active → completed
- [x] Implement phase states within active: movement → retreat (conditional) → adjustment (conditional)
- [x] Guard invalid transitions
- [x] Test state machine transitions

### Persistence

- [x] Design SQLite schema (matches, players, factions, orders, phases, results, hash_chain)
- [x] Implement data access layer (create, read, update for all entities)
- [x] Wrap all mutations in atomic transactions
- [x] Test persistence round-trips
- [x] Test restart recovery (load state from SQLite, verify consistency)

### Join Flow

- [x] Handle JOIN_REQUEST: validate, check capacity (max 7)
- [x] Assign factions (random or configured)
- [x] Encrypt faction assignment per-player
- [x] Send JOIN_ACCEPTED with encrypted faction
- [x] Send JOIN_REJECTED with reason
- [x] Persist player-faction mapping
- [x] Broadcast MATCH_START when 7 players joined and operator starts

### Phase Management

- [x] Start movement phase with deadline
- [x] Track deadline expiration
- [x] Send PHASE_DEADLINE_WARNING before expiry
- [x] Apply default hold orders for non-submitting players at deadline
- [x] Run adjudication at phase end
- [x] Produce signed PHASE_RESULT with hash chain
- [x] Transition to retreat phase if dislodgements exist
- [x] Transition to adjustment phase if needed
- [x] Support manual phase advance (dev mode)

### Order Handling

- [x] Accept ORDER_SUBMIT: validate, store with revision number
- [x] Send ORDER_RECEIPT with hash of accepted orders
- [x] Handle ORDER_UPDATE: replace previous revision
- [x] Handle ORDER_CANCEL: clear orders for player
- [x] Respond to ORDER_STATUS requests

### Draw Handling

- [x] Handle DRAW_PROPOSE
- [x] Handle DRAW_VOTE
- [x] Determine draw outcome (unanimous or majority, configurable)
- [x] Send DRAW_RESULT
- [x] End match on accepted draw

## Phase 5: Client

### Identity Management

- [x] Generate fresh Reticulum identity per match
- [x] Store keypair locally (with appropriate file permissions)
- [x] Load existing identity for reconnection to ongoing match

### Match Discovery & Join

- [x] Listen for coordinator announces
- [ ] Display discovered matches (CLI rendering — Phase 6)
- [x] Send JOIN_REQUEST with match-scoped identity
- [x] Receive and decrypt faction assignment from JOIN_ACCEPTED
- [x] Handle JOIN_REJECTED gracefully

### Order Composition & Submission

- [ ] Display current units and valid order options (CLI rendering — Phase 6; data layer done in client/orders.py)
- [ ] Interactive order builder (select unit → select order type → select target) (CLI — Phase 6; compose_order() API ready)
- [x] Validate orders locally before submission
- [x] Submit orders and track revision number
- [x] Receive and store ORDER_RECEIPT
- [x] Support order revision and cancellation
- [ ] Display order status (CLI rendering — Phase 6)

### Negotiation

- [x] Set up LXMF for direct messaging (shared/reticulum_transport.py — client.negotiations works unmodified over it, same as everything else built against the abstract Transport)
- [x] Send encrypted negotiation messages to factions (Reticulum encrypts end-to-end to the recipient identity automatically; no separate application-layer step needed)
- [x] Receive and decrypt negotiation messages (same — transparent to client/negotiations.py)
- [x] Send NEGOTIATION_ACK on receipt
- [ ] Display negotiation history per faction (CLI rendering — Phase 6; client.negotiations.group_by_sender does the grouping)

### Verification

- [x] Receive PHASE_RESULT and verify coordinator signature
- [x] Verify hash chain continuity
- [x] Re-run adjudication locally with same inputs
- [x] Compare local result against coordinator result
- [x] Alert on mismatch (with evidence)

### Game State Display

- [ ] Display current map state (regions, units, ownership)
- [ ] Display control center status
- [ ] Display current phase and deadline
- [ ] Display phase history

## Phase 6: Integration & Polish

### CLI — Coordinator

- [ ] Implement `create` command
- [ ] Implement `start` command
- [ ] Implement `status` command
- [ ] Implement `advance-phase` command
- [ ] Implement `list-players` command
- [ ] Implement `pause` / `resume` commands
- [ ] Implement `end` command
- [ ] Add help text for all commands

### CLI — Client

- [ ] Implement `discover` command
- [ ] Implement `join` command
- [ ] Implement `status` command
- [ ] Implement `negotiate` command
- [ ] Implement `order` command
- [ ] Implement `orders` command
- [ ] Implement `verify` command
- [ ] Implement `history` command
- [ ] Implement `draw` command
- [ ] Add help text for all commands

### Integration Tests

- [ ] 7-client full game simulation with InMemoryTransport
- [ ] Test complete match lifecycle: discovery → join → negotiate → order → adjudicate → repeat
- [ ] Test retreat phase triggers correctly
- [ ] Test adjustment phase triggers correctly
- [ ] Test draw proposal and voting
- [ ] Test coordinator restart and recovery mid-match
- [ ] Test default orders on deadline expiry
- [ ] Test order revision and cancellation flow
- [ ] Test client verification catches tampered state

### Documentation

- [ ] Write README.md (overview, quick start, dependencies)
- [ ] Write docs/architecture.md (system design, data flow)
- [ ] Write docs/protocol.md (message format reference)
- [ ] Write docs/identity-model.md (identity concepts, privacy)
- [ ] Write docs/threat-model.md (attack vectors, mitigations)
- [ ] Write docs/development.md (setup, testing, contributing)

### Security Hardening

- [ ] Enforce message size limits at transport layer
- [ ] Implement replay protection (reject duplicate sequence numbers)
- [ ] Restrict filesystem permissions on SQLite and key files
- [ ] Validate all inputs at coordinator boundary
- [ ] Rate-limit JOIN_REQUEST processing
- [ ] Log security-relevant events (rejected joins, validation failures)
- [ ] Review all deserialization paths for injection/crash vectors
