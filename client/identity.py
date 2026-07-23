"""Match-scoped player identity creation and secure local persistence."""

from __future__ import annotations

import os
from pathlib import Path

from shared.identity import Identity


def create_identity() -> Identity:
    """Create a fresh identity. Call once for each match, never globally."""
    return Identity.generate()


def save_identity(identity: Identity, path: str | Path) -> None:
    """Persist an identity with owner-only filesystem permissions."""
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(target, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(identity.private_bytes())
    finally:
        # fdopen closes on normal and exceptional writes; tolerate either.
        try:
            os.chmod(target, 0o600)
        except FileNotFoundError:
            pass


def load_identity(path: str | Path) -> Identity:
    """Load an existing match identity, rejecting keys readable by others."""
    target = Path(path)
    mode = target.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError(f"identity file {target} must not be group/world readable")
    data = target.read_bytes()
    if not data:
        raise ValueError(f"identity file {target} is empty")
    return Identity.from_private_bytes(data)


def get_display_hash(identity: Identity) -> str:
    """Return a short non-secret fingerprint suitable for terminal display."""
    return identity.identity_hash.hex()
