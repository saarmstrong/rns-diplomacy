"""Envelope and per-type semantic validation tests."""

from __future__ import annotations

import dataclasses

import pytest

from protocol import messages as msgs
from protocol.errors import ValidationError, VersionMismatchError
from protocol.validation import validate_envelope, validate_message


def _valid(cls: type[msgs.Message], **extra: object) -> msgs.Message:
    return cls(sequence_number=1, timestamp=100.0, **extra)


def test_valid_message_passes() -> None:
    msg = _valid(msgs.JoinRejected, match_id="match-1", reason="match full")
    validate_message(msg)  # should not raise


def test_version_mismatch_raises() -> None:
    msg = dataclasses.replace(_valid(msgs.DiscoverGame), protocol_version=999)
    with pytest.raises(VersionMismatchError):
        validate_envelope(msg)


def test_negative_sequence_number_raises() -> None:
    msg = dataclasses.replace(_valid(msgs.DiscoverGame), sequence_number=-1)
    with pytest.raises(ValidationError):
        validate_envelope(msg)


@pytest.mark.parametrize("bad_timestamp", [0.0, -1.0])
def test_non_positive_timestamp_raises(bad_timestamp: float) -> None:
    msg = dataclasses.replace(_valid(msgs.DiscoverGame), timestamp=bad_timestamp)
    with pytest.raises(ValidationError):
        validate_envelope(msg)


def test_discover_game_does_not_require_match_id() -> None:
    validate_message(_valid(msgs.DiscoverGame))  # should not raise


def test_missing_match_id_raises() -> None:
    msg = _valid(msgs.GameInfo, match_id="")
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_join_request_requires_public_key() -> None:
    msg = _valid(msgs.JoinRequest, match_id="match-1", player_public_key=b"")
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_join_accepted_requires_encrypted_faction() -> None:
    msg = _valid(msgs.JoinAccepted, match_id="match-1", encrypted_faction=b"")
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_join_rejected_requires_reason() -> None:
    msg = _valid(msgs.JoinRejected, match_id="match-1", reason="")
    with pytest.raises(ValidationError):
        validate_message(msg)


@pytest.mark.parametrize("cls", [msgs.OrderSubmit, msgs.OrderUpdate, msgs.OrderCancel])
def test_order_messages_reject_negative_revision(cls: type[msgs.Message]) -> None:
    msg = _valid(cls, match_id="match-1", revision=-1)
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_order_receipt_requires_order_hash() -> None:
    msg = _valid(msgs.OrderReceipt, match_id="match-1", revision=1, order_hash="")
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_phase_start_requires_positive_deadline() -> None:
    msg = _valid(msgs.PhaseStart, match_id="match-1", deadline=0.0)
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_phase_result_requires_state_hash() -> None:
    msg = _valid(msgs.PhaseResult, match_id="match-1", state_hash="")
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_state_response_requires_state_hash() -> None:
    msg = _valid(msgs.StateResponse, match_id="match-1", state_hash="")
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_state_hash_requires_state_hash() -> None:
    msg = _valid(msgs.StateHash, match_id="match-1", state_hash="")
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_match_start_requires_state_hash_and_deadline() -> None:
    missing_hash = _valid(msgs.MatchStart, match_id="match-1", state_hash="", deadline=1.0)
    with pytest.raises(ValidationError):
        validate_message(missing_hash)

    missing_deadline = _valid(msgs.MatchStart, match_id="match-1", state_hash="hash-0", deadline=0.0)
    with pytest.raises(ValidationError):
        validate_message(missing_deadline)


def test_match_end_requires_reason() -> None:
    msg = _valid(msgs.MatchEnd, match_id="match-1", reason="")
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_error_message_requires_code() -> None:
    msg = _valid(msgs.ErrorMessage, code="", description="oops")
    with pytest.raises(ValidationError):
        validate_message(msg)


def test_negotiation_requires_only_match_id() -> None:
    msg = _valid(msgs.Negotiation, match_id="match-1", content="")
    validate_message(msg)  # empty free-form content is not itself invalid
