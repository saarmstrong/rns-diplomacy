# Architecture

`engine` is deterministic and has no I/O. `protocol` canonically encodes validated messages. A `MatchCoordinator` owns lifecycle, SQLite persistence, deadlines, signatures, and adjudication; it uses the `Transport` interface rather than network APIs directly. `InMemoryTransport` supports tests and `ReticulumTransport` uses RNS packet destinations (`rns_diplomacy.game` / `rns_diplomacy.player`).

Players use a fresh RNS keypair per match. Coordinator state is canonical MessagePack, SHA-256 hash chained, and signed. Negotiation is direct encrypted player traffic and is not coordinator data.
