# rns-diplomacy — Development Checklist

## Phase 1: Foundation

### Setup

- [ ] Create pyproject.toml with project metadata and dependencies
- [ ] Set up directory structure: protocol/, engine/, coordinator/, client/, shared/, tests/, docs/
- [ ] Initialize git repository
- [ ] Add .gitignore (Python, SQLite, __pycache__, .rns/)
- [ ] Configure linting (ruff or flake8) and formatting (black or ruff format)
- [ ] Add pytest configuration
- [ ] Pin dependency versions: reticulum, lxmf, msgpack/cbor2, pydantic

### Domain Models

- [ ] Define Region model (name, type: land/sea/coastal, is_control_center, home_faction)
- [ ] Define Faction model (name, home_regions, home_centers)
- [ ] Define Unit model (type: army/fleet, faction, region)
- [ ] Define UnitType enum (ARMY, FLEET)
- [ ] Define MapGraph (regions, adjacency edges, neighbor queries)
- [ ] Implement adjacency validation (coastal connects to sea and land, armies can't enter sea, etc.)
- [ ] Define GameState model (units, ownership, control_centers, phase, turn)

### Map Design

- [ ] Design 25–35 region map with 7 balanced factions
- [ ] Name all regions (fictional names)
- [ ] Define all adjacency relationships
- [ ] Assign 10–15 control centers across the map
- [ ] Assign home territories and starting units for each faction
- [ ] Verify map connectivity (no isolated regions)
- [ ] Write map validation tests (neighbor symmetry, region type consistency)
- [ ] Test that each faction has a viable starting position

## Phase 2: Game Engine

### Adjudication — Core Orders

- [ ] Define Order types: Hold, Move, SupportHold, SupportMove
- [ ] Implement order validation (unit exists, owns unit, valid destination, adjacency)
- [ ] Implement hold resolution (strength 1 defense)
- [ ] Implement move resolution (strength comparison)
- [ ] Implement support hold (adds +1 defensive strength)
- [ ] Implement support move (adds +1 offensive strength)

### Adjudication — Conflict Resolution

- [ ] Implement bounce resolution (equal strength → both stay)
- [ ] Implement head-to-head battle (two units moving into each other)
- [ ] Implement support cutting (attacking a supporter cancels support)
- [ ] Implement dislodgement (overpowered unit marked for retreat)
- [ ] Implement self-dislodgement prevention
- [ ] Implement circular movement (A→B→C→A all succeed if no opposition)
- [ ] Handle unordered units (default to hold)

### Adjudication — Retreat & Adjustment

- [ ] Implement retreat phase resolution
- [ ] Validate retreat destinations (unoccupied, non-contested, adjacent)
- [ ] Handle retreat conflicts (two units retreat to same region → both disband)
- [ ] Implement adjustment phase (build/disband)
- [ ] Validate builds (only in unoccupied home centers)
- [ ] Enforce disband when units > centers

### Adjudication — Tests

- [ ] Test simple hold
- [ ] Test simple move to empty region
- [ ] Test move with equal opposition (bounce)
- [ ] Test move with support succeeding
- [ ] Test support hold preventing dislodge
- [ ] Test support cut by attack
- [ ] Test head-to-head battle
- [ ] Test head-to-head with unequal support
- [ ] Test dislodgement and retreat required
- [ ] Test circular movement (3-way)
- [ ] Test self-dislodgement prevention
- [ ] Test retreat to valid region
- [ ] Test retreat conflict (both disband)
- [ ] Test adjustment build
- [ ] Test adjustment disband
- [ ] Test complex multi-region conflict scenario
- [ ] Test determinism (same inputs → identical output, run 100x)
- [ ] Test all units default to hold when no orders submitted

### Serialization

- [ ] Implement canonical MessagePack/CBOR encoder (sorted keys, canonical integers)
- [ ] Implement decoder with validation
- [ ] Verify serialization round-trip for all domain models
- [ ] Verify deterministic output (serialize → hash → serialize again → same hash)
- [ ] Implement size limit enforcement
- [ ] Write serialization tests for edge cases (empty maps, max-size payloads)

### State Hashing

- [ ] Implement state hash function: hash(canonical_serialize(phase_result))
- [ ] Implement hash chaining (include previous hash in current hash input)
- [ ] Implement signing integration (sign hash with coordinator identity)
- [ ] Implement signature verification
- [ ] Test hash chain construction over multiple phases
- [ ] Test hash chain verification (detect tampered state)

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
- [ ] Implement ReticulumTransport with TCP/IP interface
- [ ] Set up Reticulum destinations (rns_diplomacy.game, rns_diplomacy.player)
- [ ] Implement announce for match discovery
- [ ] Test ReticulumTransport over local TCP/IP

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

- [ ] Generate fresh Reticulum identity per match
- [ ] Store keypair locally (with appropriate file permissions)
- [ ] Load existing identity for reconnection to ongoing match

### Match Discovery & Join

- [ ] Listen for coordinator announces
- [ ] Display discovered matches
- [ ] Send JOIN_REQUEST with match-scoped identity
- [ ] Receive and decrypt faction assignment from JOIN_ACCEPTED
- [ ] Handle JOIN_REJECTED gracefully

### Order Composition & Submission

- [ ] Display current units and valid order options
- [ ] Interactive order builder (select unit → select order type → select target)
- [ ] Validate orders locally before submission
- [ ] Submit orders and track revision number
- [ ] Receive and store ORDER_RECEIPT
- [ ] Support order revision and cancellation
- [ ] Display order status

### Negotiation

- [ ] Set up LXMF for direct messaging
- [ ] Send encrypted negotiation messages to factions
- [ ] Receive and decrypt negotiation messages
- [ ] Send NEGOTIATION_ACK on receipt
- [ ] Display negotiation history per faction

### Verification

- [ ] Receive PHASE_RESULT and verify coordinator signature
- [ ] Verify hash chain continuity
- [ ] Re-run adjudication locally with same inputs
- [ ] Compare local result against coordinator result
- [ ] Alert on mismatch (with evidence)

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
