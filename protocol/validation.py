"""Validation logic for incoming protocol messages.

Checks the envelope (protocol version, sequence number, timestamp) and
per-message-type semantic constraints before a decoded message is handed
to the coordinator or client for processing. Structural/type validation
already happens during decoding (``protocol/encoding.py``); this module
covers constraints decoding alone can't express.
"""

from __future__ import annotations

from collections.abc import Callable

from protocol.constants import PROTOCOL_VERSION
from protocol.errors import ValidationError, VersionMismatchError
from protocol.messages import Message, MessageType

_TYPES_WITHOUT_MATCH_ID = frozenset({MessageType.DISCOVER_GAME, MessageType.ERROR})


def _require(value: object, field_name: str) -> None:
    if not value:
        raise ValidationError(f"{field_name} must not be empty")


def _require_non_negative(value: int, field_name: str) -> None:
    if value < 0:
        raise ValidationError(f"{field_name} must be non-negative")


def _require_positive(value: float, field_name: str) -> None:
    if value <= 0:
        raise ValidationError(f"{field_name} must be positive")


_PER_TYPE_VALIDATORS: dict[MessageType, tuple[Callable[[Message], None], ...]] = {
    MessageType.JOIN_REQUEST: (lambda m: _require(m.player_public_key, "player_public_key"),),
    MessageType.JOIN_ACCEPTED: (lambda m: _require(m.encrypted_faction, "encrypted_faction"),),
    MessageType.JOIN_REJECTED: (lambda m: _require(m.reason, "reason"),),
    MessageType.ORDER_SUBMIT: (lambda m: _require_non_negative(m.revision, "revision"),),
    MessageType.ORDER_UPDATE: (lambda m: _require_non_negative(m.revision, "revision"),),
    MessageType.ORDER_CANCEL: (lambda m: _require_non_negative(m.revision, "revision"),),
    MessageType.ORDER_RECEIPT: (lambda m: _require(m.order_hash, "order_hash"),),
    MessageType.PHASE_START: (lambda m: _require_positive(m.deadline, "deadline"),),
    MessageType.PHASE_RESULT: (lambda m: _require(m.state_hash, "state_hash"),),
    MessageType.STATE_RESPONSE: (lambda m: _require(m.state_hash, "state_hash"),),
    MessageType.STATE_HASH: (lambda m: _require(m.state_hash, "state_hash"),),
    MessageType.MATCH_START: (
        lambda m: _require(m.state_hash, "state_hash"),
        lambda m: _require_positive(m.deadline, "deadline"),
    ),
    MessageType.MATCH_END: (lambda m: _require(m.reason, "reason"),),
    MessageType.ERROR: (lambda m: _require(m.code, "code"),),
}


def validate_envelope(msg: Message) -> None:
    """Validate the fields common to every message: version, sequence, timestamp."""
    if msg.protocol_version != PROTOCOL_VERSION:
        raise VersionMismatchError(
            f"unsupported protocol_version {msg.protocol_version} (expected {PROTOCOL_VERSION})"
        )
    if msg.sequence_number < 0:
        raise ValidationError("sequence_number must be non-negative")
    if msg.timestamp <= 0:
        raise ValidationError("timestamp must be positive")


def validate_message(msg: Message) -> None:
    """Validate a decoded message before it is acted on.

    Raises ``ValidationError`` (or the more specific ``VersionMismatchError``)
    on the first constraint violated. Raises nothing if the message is valid.
    """
    validate_envelope(msg)
    if msg.message_type not in _TYPES_WITHOUT_MATCH_ID:
        _require(getattr(msg, "match_id", None), "match_id")
    for validator in _PER_TYPE_VALIDATORS.get(msg.message_type, ()):
        validator(msg)
