"""Player identity management — match-scoped Reticulum identities.

Per the identity model: a fresh identity is generated per match, never
reused across matches, and there is no persistent account or profile.
The only reason to persist one to disk is so a client can reconnect to
the *same* ongoing match after a restart.
"""

from __future__ import annotations

from pathlib import Path

from shared.identity import Identity


def create_identity() -> Identity:
    """Generate a fresh, match-scoped identity. Do not reuse across matches."""
    return Identity.generate()


def save_identity(identity: Identity, path: str | Path) -> None:
    """Persist an identity's private key to ``path`` with owner-only permissions."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(identity.private_bytes())
    target.chmod(0o600)


def load_identity(path: str | Path) -> Identity:
    """Load a previously saved identity, e.g. to reconnect to an ongoing match."""
    return Identity.from_private_bytes(Path(path).read_bytes())


def get_display_hash(identity: Identity) -> str:
    """A short, human-readable fingerprint for display (never the full public key)."""
    return identity.identity_hash.hex()
