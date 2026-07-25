# Protocol Reference

Source of truth: `protocol/messages.py` (message definitions), `protocol/encoding.py`
(wire format), `protocol/validation.py` (semantic checks), `protocol/constants.py` (limits).

## Envelope

Every message is a frozen, keyword-only dataclass inheriting from `Message`, carrying:

| Field | Type | Meaning |
|---|---|---|
| `message_type` | `MessageType` | Discriminator tag; defaulted per class, used for decode dispatch |
| `sequence_number` | `int` | Per-sender monotonically increasing counter |
| `timestamp` | `float` | Unix epoch seconds |
| `protocol_version` | `int` | Defaults to `PROTOCOL_VERSION` (currently `1`) |

## Wire format

`protocol/encoding.py::encode_message` canonicalizes a message (dataclass → sorted-key dict,
enums → their `.value`, nested dataclasses recursed — the same approach
`engine/hashing.py::canonicalize` uses for game state) and packs it with msgpack. Two
messages with identical field values always encode to identical bytes. `decode_message`
reverses this by dispatching on `message_type` through `MESSAGE_TYPES` and reconstructing each
field from the target class's resolved type hints (`Optional[T]`, `tuple[T, ...]`, enums, and
the `Order` union via `engine.orders.order_from_dict`, which dispatches on each order's own
`order_type` field).

Both encode and decode enforce `MAX_MESSAGE_SIZE` (64 KiB) — chosen because LXMF handles
chunking messages larger than one packet's MDU transparently, so this is a sanity/DoS limit,
not a hard technical ceiling.

## Validation

`protocol/validation.py::validate_message` runs on every decoded message before it's acted on:

- **Envelope**: `protocol_version` must match `PROTOCOL_VERSION`; `sequence_number >= 0`;
  `timestamp > 0`.
- **`match_id` presence**: required on every message type except `DISCOVER_GAME` and `ERROR`
  (neither is scoped to a specific match).
- **Per-type semantic checks**: e.g. `JoinRequest.player_public_key` non-empty,
  `OrderSubmit.revision >= 0`, `PhaseStart.deadline > 0`, `ErrorMessage.code` non-empty. See the
  `_PER_TYPE_VALIDATORS` table in `protocol/validation.py` for the exact list.

Structural/type validation (wrong field type, missing field, unknown `order_type`) is caught
earlier, during decoding, and raises `protocol.errors.DecodingError` rather than reaching
`validate_message` at all.

## Messages

### Discovery & join

| Message | Direction | Fields beyond the envelope |
|---|---|---|
| `DISCOVER_GAME` | Client → Coordinator | — |
| `GAME_INFO` | Coordinator → Client | `match_id`, `status: MatchStatus`, `player_count`, `max_players`, `phase: Phase \| None` |
| `JOIN_REQUEST` | Client → Coordinator | `match_id`, `player_public_key: bytes`, `display_name: str \| None` |
| `JOIN_ACCEPTED` | Coordinator → Client | `match_id`, `encrypted_faction: bytes`, `faction_names: tuple[str, ...]`, `match_parameters: dict` |
| `JOIN_REJECTED` | Coordinator → Client | `match_id`, `reason: str` |

`JOIN_ACCEPTED.encrypted_faction` is the assigned faction's name, encrypted to the requesting
player's public key (`shared.identity.PublicIdentity.encrypt`) so only that player can read it
— see `docs/identity-model.md`. `match_parameters` currently carries `max_players` and the
three phase-duration defaults.

### Negotiation

| Message | Direction | Fields beyond the envelope |
|---|---|---|
| `NEGOTIATION` | Player → Player | `match_id`, `content: str` |
| `NEGOTIATION_ACK` | Player → Player | `match_id`, `acked_sequence_number: int` |

Never routed through the coordinator — see `client/negotiations.py`.

### Orders

| Message | Direction | Fields beyond the envelope |
|---|---|---|
| `ORDER_SUBMIT` | Client → Coordinator | `match_id`, `revision: int`, `orders: tuple[Order, ...]` |
| `ORDER_RECEIPT` | Coordinator → Client | `match_id`, `revision`, `order_hash: str`, `state: OrderState` |
| `ORDER_UPDATE` | Client → Coordinator | same shape as `ORDER_SUBMIT` |
| `ORDER_CANCEL` | Client → Coordinator | `match_id`, `revision` |
| `ORDER_STATUS` | Coordinator → Client | `match_id`, `revision`, `state: OrderState`, `order_hash: str \| None` |

`Order` (from `engine.orders`) is a union of `HoldOrder`, `MoveOrder`, `SupportHoldOrder`,
`SupportMoveOrder` — the four movement orders, each carrying its own `order_type` field used
for wire dispatch. **`RetreatOrder`/`BuildOrder`/`DisbandOrder` cannot travel over
`ORDER_SUBMIT`** — they have no `order_type` discriminator, so they're engine-internal only for
now (see `docs/architecture.md`'s Known Limitations).

`order_hash` (`engine.hashing.hash_orders`) lets a client verify the coordinator recorded
exactly the orders it submitted, by recomputing the same hash locally.

`OrderState` values: `sent`, `delivered`, `accepted`, `rejected`, `superseded`. `Draft` (locally
composed, not yet sent) is client-local only and never appears on the wire.

### Phase management

| Message | Direction | Fields beyond the envelope |
|---|---|---|
| `PHASE_START` | Coordinator → Client | `match_id`, `phase: Phase`, `turn: int`, `year: int`, `deadline: float` |
| `PHASE_RESULT` | Coordinator → Client | `match_id`, `phase`, `turn`, `year`, `canonical_state: bytes`, `state_hash: str`, `previous_state_hash: str \| None`, `signature: bytes` |
| `PHASE_DEADLINE_WARNING` | Coordinator → Client | `match_id`, `phase`, `seconds_remaining: float` |

`phase` is `engine.model.Phase`: `diplomacy`, `orders`, `resolution`, `retreat`, `build`.
`diplomacy` and `resolution` are transient — the coordinator never stops on them or broadcasts
a `PHASE_START` for them; see `coordinator/match.py::advance_phase`.

`PHASE_RESULT.previous_state_hash` is `None` only for the very first link in a match's chain
(seeded by `MATCH_START`); every other value chains to the prior `PHASE_RESULT` (or
`MATCH_START`)'s `state_hash`.

### State & verification

| Message | Direction | Fields beyond the envelope |
|---|---|---|
| `STATE_REQUEST` | Client → Coordinator | `match_id`, `turn: int \| None` (`None` = current) |
| `STATE_RESPONSE` | Coordinator → Client | `match_id`, `turn`, `canonical_state`, `state_hash`, `previous_state_hash`, `signature` |
| `STATE_HASH` | Coordinator → Client | `match_id`, `turn`, `state_hash` |

If a turn has multiple persisted phase results (e.g. both `orders` and `retreat` within the
same turn), `STATE_REQUEST` returns the most recently resolved one for that turn number — it
can't select a specific phase within a turn.

### Draw & meta

| Message | Direction | Fields beyond the envelope |
|---|---|---|
| `DRAW_PROPOSE` | Client → Coordinator | `match_id`, `turn: int` |
| `DRAW_VOTE` | Client → Coordinator | `match_id`, `turn`, `vote: bool` |
| `DRAW_RESULT` | Coordinator → Client | `match_id`, `turn`, `accepted: bool`, `votes: dict[str, bool]` |
| `MATCH_START` | Coordinator → Client | `match_id`, `faction_names: tuple[str, ...]`, `canonical_state`, `state_hash`, `signature`, `deadline` |
| `MATCH_END` | Coordinator → Client | `match_id`, `reason: str`, `winner: str \| None`, `final_state_hash: str \| None` |
| `ERROR` | Either direction | `code: str`, `description: str`, `related_sequence_number: int \| None` |

`DRAW_PROPOSE` counts as that player casting a "yes" `DRAW_VOTE`. `votes` is keyed by faction
*name* (not public key or identity hash), consistent with the privacy model — see
`docs/identity-model.md`. The outcome rule (unanimous by default, or majority) is a per-match
configuration choice (`coordinator.draws.DrawRule`), not part of the wire format.

## Deviations from the original spec worth knowing about

- **No separate `rns_diplomacy.game`/`rns_diplomacy.player` destination aspects.**
  `shared/reticulum_transport.py` builds on LXMF, which fixes its own destination namespace
  (`lxmf.delivery` per identity). Every participant — coordinator or player — is addressed the
  same way: by the hex-encoded public key of the identity to reach. The protocol envelope
  already fully self-describes message type and match membership, so the second namespace
  would have added complexity without adding a real property. See the module docstring in
  `shared/reticulum_transport.py`.
- **No dedicated "request order status" client message.** The message table always specified
  `ORDER_STATUS` as coordinator → client only. `coordinator.orders.handle_order_status` exists
  and is tested as a directly-callable function, but nothing in `MatchCoordinator`'s message
  dispatch triggers it from an incoming request, since there's no incoming message type to
  trigger it from.
