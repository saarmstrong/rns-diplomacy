# Threat model

Messages are size-limited, decoded strictly, versioned, and replay-rejected by sequence number. Join requests are rate limited and bound to the sending identity. SQLite/key files are owner-readable only. Reticulum supplies destination encryption and identity signatures.

The coordinator is authoritative but cannot silently falsify a result: clients verify signed, chained canonical state and can re-adjudicate. This does not protect against a coordinator withholding messages, traffic analysis, compromised endpoints, or availability attacks; participants retain signed evidence of invalid state.
