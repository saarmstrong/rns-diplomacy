"""Reticulum identity wrapper: keypairs, signing, and asymmetric encryption.

Thin wrapper around ``RNS.Identity`` used for both the coordinator's
signing identity and match-scoped player identities. Key generation,
signing, verification, and public-key encryption all work standalone —
none of it requires a running Reticulum network or ``RNS.Transport``.

Two types, matching the two roles a Curve25519 keypair plays here:

- ``Identity`` holds a private key: it can sign and decrypt.
- ``PublicIdentity`` holds only a public key: it can verify signatures
  and encrypt messages *to* the holder of the matching private key.
"""

from __future__ import annotations

import RNS


class PublicIdentity:
    """The public half of an identity — verify signatures, encrypt to it."""

    def __init__(self, public_bytes: bytes) -> None:
        self._public_bytes = public_bytes
        self._rns_identity = RNS.Identity(create_keys=False)
        self._rns_identity.load_public_key(public_bytes)

    @property
    def public_bytes(self) -> bytes:
        return self._public_bytes

    @property
    def identity_hash(self) -> bytes:
        return self._rns_identity.hash

    def verify(self, signature: bytes, message: bytes) -> bool:
        """Return True if ``signature`` is a valid signature of ``message`` by this identity."""
        return bool(self._rns_identity.validate(signature, message))

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt ``plaintext`` so only the holder of the matching private key can read it."""
        return self._rns_identity.encrypt(plaintext)


class Identity:
    """A full identity, holding a private key: sign, decrypt, and derive a PublicIdentity."""

    def __init__(self, rns_identity: RNS.Identity) -> None:
        self._rns_identity = rns_identity

    @classmethod
    def generate(cls) -> Identity:
        """Create a fresh identity with a newly generated keypair."""
        return cls(RNS.Identity())

    @classmethod
    def from_private_bytes(cls, data: bytes) -> Identity:
        """Reconstruct an identity from its exported private key bytes."""
        rns_identity = RNS.Identity(create_keys=False)
        rns_identity.load_private_key(data)
        return cls(rns_identity)

    def private_bytes(self) -> bytes:
        """Export the private key for persistence. Handle with the same care as any secret key."""
        return self._rns_identity.get_private_key()

    @property
    def public_bytes(self) -> bytes:
        return self._rns_identity.get_public_key()

    @property
    def identity_hash(self) -> bytes:
        return self._rns_identity.hash

    def public(self) -> PublicIdentity:
        """This identity's public half, shareable with others."""
        return PublicIdentity(self.public_bytes)

    def sign(self, message: bytes) -> bytes:
        return self._rns_identity.sign(message)

    def decrypt(self, ciphertext: bytes) -> bytes:
        return self._rns_identity.decrypt(ciphertext)
