"""Protocol-specific error types.

Custom exceptions for encoding failures, decoding failures, validation
errors, version mismatches, and oversized payloads.
"""

from __future__ import annotations


class ProtocolError(Exception):
    """Base class for all protocol-layer errors."""


class EncodingError(ProtocolError):
    """Raised when a message cannot be encoded to bytes."""


class DecodingError(ProtocolError):
    """Raised when received bytes cannot be decoded into a valid message."""


class ValidationError(ProtocolError):
    """Raised when a decoded message fails structural or semantic validation."""


class VersionMismatchError(ValidationError):
    """Raised when a message's protocol_version is not supported."""


class MessageTooLargeError(ProtocolError):
    """Raised when an encoded or received message exceeds MAX_MESSAGE_SIZE."""
