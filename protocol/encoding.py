"""Msgpack serialization and deserialization for protocol messages.

Mirrors the canonical-encoding approach used for game-state hashing
(``engine/hashing.py``): dataclasses are flattened to sorted-key dicts and
enums reduce to their string value before packing, so encoding a given
logical message always produces identical bytes. Decoding reverses this
using each message class's type hints, dispatching on the ``message_type``
tag via ``protocol.messages.MESSAGE_TYPES``.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from enum import Enum
from typing import Any

import msgpack

from engine.hashing import canonicalize
from engine.orders import Order, order_from_dict
from protocol.constants import MAX_MESSAGE_SIZE
from protocol.errors import DecodingError, EncodingError, MessageTooLargeError
from protocol.messages import MESSAGE_TYPES, Message, MessageType


def encode_message(msg: Message) -> bytes:
    """Pack a protocol message into canonical, deterministic msgpack bytes."""
    try:
        data = msgpack.packb(canonicalize(msg), use_bin_type=True)
    except (TypeError, ValueError) as exc:
        raise EncodingError(f"failed to encode {type(msg).__name__}: {exc}") from exc
    if len(data) > MAX_MESSAGE_SIZE:
        raise MessageTooLargeError(
            f"encoded {type(msg).__name__} is {len(data)} bytes (max {MAX_MESSAGE_SIZE})"
        )
    return data


def _decode_order(raw: Any) -> Order:
    if not isinstance(raw, dict) or "order_type" not in raw:
        raise DecodingError(f"malformed order payload: {raw!r}")
    try:
        return order_from_dict(raw)
    except (KeyError, ValueError, TypeError) as exc:
        raise DecodingError(f"malformed order payload: {raw!r}") from exc


def _convert_value(raw: Any, expected: Any) -> Any:
    if expected is Order:
        return _decode_order(raw)

    origin = typing.get_origin(expected)

    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(expected) if a is not type(None)]
        if raw is None:
            return None
        # Only Optional[T] (a single non-None arm) appears in this protocol.
        return _convert_value(raw, args[0])

    if isinstance(expected, type) and issubclass(expected, Enum):
        return expected(raw)

    if origin is tuple:
        args = typing.get_args(expected)
        elem_type = args[0] if args else Any
        return tuple(_convert_value(v, elem_type) for v in raw)

    if origin is list:
        args = typing.get_args(expected)
        elem_type = args[0] if args else Any
        return [_convert_value(v, elem_type) for v in raw]

    if origin is dict:
        return dict(raw)

    return raw


def decode_message(data: bytes) -> Message:
    """Unpack msgpack bytes into a typed, validated protocol message instance."""
    if len(data) > MAX_MESSAGE_SIZE:
        raise MessageTooLargeError(f"received message is {len(data)} bytes (max {MAX_MESSAGE_SIZE})")

    try:
        raw = msgpack.unpackb(data, raw=False)
    except (msgpack.exceptions.UnpackException, ValueError, TypeError) as exc:
        raise DecodingError(f"failed to unpack message bytes: {exc}") from exc

    if not isinstance(raw, dict) or "message_type" not in raw:
        raise DecodingError("decoded payload is not a valid message envelope")

    try:
        tag = MessageType(raw["message_type"])
    except ValueError as exc:
        raise DecodingError(f"unknown message_type: {raw.get('message_type')!r}") from exc
    cls = MESSAGE_TYPES[tag]

    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name == "message_type":
            continue
        if f.name not in raw:
            raise DecodingError(f"{cls.__name__} payload missing field {f.name!r}")
        kwargs[f.name] = _convert_value(raw[f.name], hints[f.name])

    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise DecodingError(f"malformed {cls.__name__} payload: {exc}") from exc
