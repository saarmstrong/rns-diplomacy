# Identity model

There are no accounts. A coordinator identity signs state for one match. Each player generates a new Reticulum identity for every match and stores its private key with mode `0600` only for reconnection. The coordinator maps that public key to a faction internally; `JOIN_ACCEPTED` encrypts the faction name to that player. Other players should address public faction destinations rather than expose key material in UI.
