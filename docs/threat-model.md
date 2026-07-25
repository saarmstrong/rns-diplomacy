# Threat Model

## Threats & mitigations

| Threat | Mitigation | Where |
|---|---|---|
| **Stolen keys** | Match-scoped identities limit damage to one match; there's no persistent identity to steal in the first place. | `client/identity.py`, `docs/identity-model.md` |
| **Replay attacks** | Every message carries a monotonically increasing `sequence_number`; the coordinator rejects any message whose sequence number isn't strictly greater than the last one seen from that sender, and never processes it twice. | `coordinator/match.py::handle_inbound`, `coordinator/persistence.py`'s `sender_sequence_numbers` table |
| **Malformed payloads** | `MAX_MESSAGE_SIZE` (64 KiB) enforced before any parsing is attempted, on both send and receive. Decoding and validation are strict — an oversized, truncated, or structurally invalid message never reaches a handler; it's turned into a typed `DecodingError`/`ValidationError` and logged. | `protocol/encoding.py`, `protocol/validation.py` |
| **Dishonest coordinator** | Every phase result is signed and hash-chained. Any client can independently verify the signature, verify that the claimed hash matches the claimed state, and verify chain continuity across everything it has personally observed — a mismatch is cryptographic proof, not just an accusation. | `client/verification.py`, `docs/architecture.md`'s Verification model |
| **Traffic analysis** | LXMF's store-and-forward model provides some obfuscation. Full traffic-analysis resistance is explicitly out of scope. | — |
| **Identity correlation** | Fresh identity per match; no protocol-level link across matches. Application-layer correlation (play style, timing, negotiation content) is explicitly out of scope. | `docs/identity-model.md` |
| **DoS** | Message size limits (above). `JOIN_REQUEST` is rate-limited to 5 attempts per 60 seconds per sender public key. The coordinator can reject/ban keys at the operator's discretion (not automated — see Known gaps). | `coordinator/match.py::_on_join_request` |
| **Sybil attacks** | Hard cap of `MAX_PLAYERS` (7) per match; no protocol-level benefit to holding multiple identities beyond filling slots. An operator can require out-of-band coordination for who's allowed to join (not automated — see Known gaps). | `protocol/constants.py::MAX_PLAYERS` |
| **Filesystem permissions** | The coordinator's SQLite database (which holds its signing private key) is created with `0600` permissions. Client identity key files are `0600` too. | `coordinator/persistence.py::MatchStore.__init__`, `client/identity.py::save_identity` |

## Coordinator-side input handling

Every inbound message goes through the same pipeline before any handler sees it
(`coordinator/match.py::handle_inbound`):

1. **Decode** (`protocol/encoding.py::decode_message`) — size-checked first, then unpacked and
   reconstructed into a typed dataclass. Any failure (truncated bytes, wrong types, unknown
   `order_type`) raises `DecodingError`, caught and logged; the sender gets an `ERROR` reply,
   nothing crashes.
2. **Validate** (`protocol/validation.py::validate_message`) — envelope checks (protocol
   version, non-negative sequence number, positive timestamp) plus per-message-type semantic
   checks (non-empty public keys/hashes, non-negative revisions, positive deadlines). Failures
   are handled the same way as decode failures.
3. **Replay check** — the message's `sequence_number` must be strictly greater than the last
   one seen from this sender (tracked per `(match_id, public_key)`, persisted so it survives a
   coordinator restart). A non-increasing sequence number is silently dropped and logged; the
   sender gets no reply, denying an attacker confirmation of what worked.
4. **Rate limit** (`JOIN_REQUEST` only) — at most 5 attempts per 60 seconds per sender public
   key, in-memory (resets on restart — an acceptable tradeoff for a throttle, not a hard
   boundary). Excess attempts are silently dropped and logged.
5. **Dispatch** to the type-specific handler, which does its own business-level validation
   (e.g. `coordinator/orders.py::validate_orders` checks that a player only orders their own
   units — see `engine/state.py::validate_orders`, shared with the client's local
   pre-submission check).

No deserialization path in the codebase uses `pickle`, `eval`, `exec`, or dynamic imports —
msgpack (a data-only binary format with no code-execution surface) and dataclass reconstruction
are the only mechanisms involved, and every unpacking call is wrapped to convert library
exceptions into the typed errors above rather than letting them propagate uncaught.

## Known gaps

These are conscious scope boundaries, not oversights — each is called out elsewhere in the
codebase too:

- **No automated key-banning.** CLAUDE.md's spec allows a coordinator to "reject/ban keys," but
  there's no persisted banlist or CLI command for it yet. An operator would need to reject joins
  by other means (e.g. not sharing the match address further) until this lands.
- **No out-of-band invitation enforcement.** Anyone who learns a match's coordinator address
  (via `discover` or being told it) can attempt to join, up to the 7-player cap. Limiting who
  can even attempt to join isn't implemented.
- **`RetreatOrder`/`BuildOrder`/`DisbandOrder` can't be submitted over the wire** (no
  `order_type` discriminator on those three types), so an attacker gains nothing by trying —
  but it also means those phases can't yet reflect genuine player intent, only the engine's
  deterministic defaults. See `docs/architecture.md`'s Known limitations.
- **First-contact negotiation replies.** A recipient may only get a one-way destination hash
  for a sender it's never seen an announce from, making an immediate reply impossible until one
  propagates. This is an availability/usability gap, not a confidentiality one — the message
  itself is still only readable by the intended recipient.
- **JOIN_REQUEST rate limiting is in-memory and per-coordinator-process.** It resets on a
  `serve` restart and doesn't survive/coordinate across multiple coordinator processes (there's
  only ever one per match by design, so this is a non-issue in practice, but worth noting
  explicitly: it is a throttle, not a persisted security boundary like replay protection is).
