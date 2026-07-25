# Identity Model

Three distinct identity concepts, matching CLAUDE.md's spec — this document describes how
each is actually implemented.

## 1. Match coordinator identity

A `shared.identity.Identity` — a Curve25519 keypair wrapping `RNS.Identity` — created fresh
per match (`Identity.generate()`) and persisted inside that match's SQLite database
(`coordinator/persistence.py::MatchStore.create_match` / `load_coordinator_identity`), never
in a separate key file. It serves two roles simultaneously:

- **Signing**: every `PHASE_RESULT`, `STATE_RESPONSE`, and `MATCH_START` is signed with it
  (`Identity.sign`). Any client verifies with `PublicIdentity.verify`, given only the public
  key — see `client/verification.py`.
- **Network address**: `shared/reticulum_transport.py` addresses every participant by the
  hex-encoded public key of the identity to reach, so the coordinator's signing identity
  doubles as its stable address across process restarts (`coordinator/server.py` reloads it
  from the database on every CLI invocation rather than generating a new one).

Because the identity lives in the database, restarting the coordinator process never changes
its address or invalidates previously issued signatures.

## 2. Match-scoped player identity

A fresh `Identity` generated per match (`client/identity.py::create_identity`), persisted to a
local file (`identity.key`, `chmod 0600`) so a client can reconnect to the *same* match after a
restart — but never reused across matches. There are no accounts and no persistent profiles:
a player's identity is nothing more than this one keypair, scoped to one match's lifetime.

This limits the blast radius of a stolen key to a single match (see `docs/threat-model.md`) and
means the protocol has no mechanism, and makes no attempt, to correlate the same human across
two different matches at the identity layer.

**Identity resolution over the wire is not automatic on first contact.** RNS/LXMF can only
resolve a message's sender to their full public key if it has previously seen an announce from
that identity (`RNS.Identity.recall`); before that, `LXMessage.get_source()` is `None` and only
a one-way destination hash is available (`shared/reticulum_transport.py::ReticulumTransport._on_delivery`).
Two places in the protocol account for this:

- `JOIN_REQUEST` carries the player's public key in the message body
  (`player_public_key: bytes`) specifically so the coordinator never depends on having already
  recalled a brand-new player's identity — `MatchCoordinator._on_join_request` replies to that
  field, not the transport-derived sender.
- `NEGOTIATION` has no equivalent field, so a reply to a message from someone never previously
  contacted may not be possible until an announce propagates. See
  `client/negotiations.py`'s module docstring.

## 3. Public faction identity

Each joined player is assigned a faction (e.g. "Vethara", "Kholmari" — see
`engine/map.py::FACTIONS`) by `coordinator/lobby.py::handle_join_request`, using Python's
`random` module (no determinism requirement here — unlike adjudication, faction assignment is
the coordinator's sole prerogative and isn't independently reproducible or meant to be).

Other players only ever see faction names — never public keys or identity hashes. The mapping
from a player's real identity to their faction is known only to the coordinator (via
`MatchStore`) and to the player themselves. Concretely:

- `JOIN_ACCEPTED.encrypted_faction` carries the assigned faction's name, encrypted with
  `PublicIdentity.encrypt` to the *requesting player's own* public key — nobody else, including
  anyone observing the network, can decrypt it in transit even though transport-layer
  encryption already protects the link.
- `DRAW_RESULT.votes` is keyed by faction name, not identity — see `protocol/messages.py`.
- `MATCH_START.faction_names` and `JOIN_ACCEPTED.faction_names` list which factions exist in
  the match, without saying who holds which.

## What this doesn't protect against

Application-layer correlation (writing style, timing patterns, negotiation content) across
matches is explicitly out of scope, as is full traffic-analysis resistance — LXMF's
store-and-forward model provides some obfuscation but no formal guarantee. See
`docs/threat-model.md` for the full threat/mitigation table.
