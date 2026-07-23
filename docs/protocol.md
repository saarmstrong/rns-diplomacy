# Protocol

Messages are canonical MessagePack dataclasses with `message_type`, `protocol_version`, `sequence_number`, and positive `timestamp`. The maximum encoded size is 64 KiB. Receivers validate schema and protocol version before dispatch.

Discovery/join: `DISCOVER_GAME`, `GAME_INFO`, `JOIN_REQUEST`, `JOIN_ACCEPTED`, `JOIN_REJECTED`. Orders: `ORDER_SUBMIT`, `ORDER_UPDATE`, `ORDER_CANCEL`, `ORDER_RECEIPT`, `ORDER_STATUS`. Lifecycle: `PHASE_START`, `PHASE_RESULT`, `PHASE_DEADLINE_WARNING`. State: `STATE_REQUEST`, `STATE_RESPONSE`, `STATE_HASH`. Draw: `DRAW_PROPOSE`, `DRAW_VOTE`, `DRAW_RESULT`, `MATCH_START`, `MATCH_END`; `ERROR` reports failures.

`PHASE_RESULT` includes canonical state, its hash, previous hash, and signature over the hash. Senders must monotonically increase sequence numbers; the coordinator rejects replayed messages.
