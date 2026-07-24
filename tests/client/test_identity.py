"""Tests for client/identity.py — match-scoped identity generation and persistence."""

from __future__ import annotations

import stat

from client.identity import create_identity, get_display_hash, load_identity, save_identity


def test_create_identity_produces_distinct_identities():
    a = create_identity()
    b = create_identity()
    assert a.public_bytes != b.public_bytes


def test_save_and_load_round_trips(tmp_path):
    identity = create_identity()
    path = tmp_path / "identity.key"
    save_identity(identity, path)

    loaded = load_identity(path)
    assert loaded.public_bytes == identity.public_bytes
    assert loaded.identity_hash == identity.identity_hash


def test_save_identity_restricts_file_permissions(tmp_path):
    identity = create_identity()
    path = tmp_path / "identity.key"
    save_identity(identity, path)

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_save_identity_creates_parent_directories(tmp_path):
    identity = create_identity()
    path = tmp_path / "nested" / "dir" / "identity.key"
    save_identity(identity, path)
    assert path.exists()


def test_get_display_hash_is_stable_and_matches_identity_hash():
    identity = create_identity()
    assert get_display_hash(identity) == identity.identity_hash.hex()
    assert get_display_hash(identity) == get_display_hash(identity)


def test_loaded_identity_can_sign_and_be_verified(tmp_path):
    identity = create_identity()
    path = tmp_path / "identity.key"
    save_identity(identity, path)
    loaded = load_identity(path)

    signature = loaded.sign(b"orders")
    assert identity.public().verify(signature, b"orders")
