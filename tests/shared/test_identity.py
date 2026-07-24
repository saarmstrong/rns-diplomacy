"""Tests for the Identity/PublicIdentity Reticulum wrapper."""

from __future__ import annotations

from shared.identity import Identity, PublicIdentity


def test_generate_produces_distinct_keypairs() -> None:
    a = Identity.generate()
    b = Identity.generate()
    assert a.public_bytes != b.public_bytes
    assert a.identity_hash != b.identity_hash


def test_private_bytes_round_trip_preserves_identity() -> None:
    original = Identity.generate()
    restored = Identity.from_private_bytes(original.private_bytes())
    assert restored.public_bytes == original.public_bytes
    assert restored.identity_hash == original.identity_hash


def test_public_derives_matching_public_identity() -> None:
    identity = Identity.generate()
    public = identity.public()
    assert isinstance(public, PublicIdentity)
    assert public.public_bytes == identity.public_bytes
    assert public.identity_hash == identity.identity_hash


def test_sign_and_verify_round_trip() -> None:
    identity = Identity.generate()
    message = b"phase-3-result-hash"
    signature = identity.sign(message)
    assert identity.public().verify(signature, message) is True


def test_verify_rejects_tampered_message() -> None:
    identity = Identity.generate()
    signature = identity.sign(b"original")
    assert identity.public().verify(signature, b"tampered") is False


def test_verify_rejects_signature_from_a_different_identity() -> None:
    signer = Identity.generate()
    other = Identity.generate()
    signature = signer.sign(b"message")
    assert other.public().verify(signature, b"message") is False


def test_encrypt_to_public_identity_then_decrypt_with_private() -> None:
    recipient = Identity.generate()
    ciphertext = recipient.public().encrypt(b"Vethara")
    assert recipient.decrypt(ciphertext) == b"Vethara"


def test_public_identity_constructed_from_raw_bytes_matches_original() -> None:
    identity = Identity.generate()
    rebuilt_public = PublicIdentity(identity.public_bytes)
    signature = identity.sign(b"message")
    assert rebuilt_public.verify(signature, b"message") is True


def test_rns_identity_property_exposes_the_same_keypair() -> None:
    identity = Identity.generate()
    assert identity.rns_identity.get_public_key() == identity.public_bytes
    assert identity.rns_identity.hash == identity.identity_hash
