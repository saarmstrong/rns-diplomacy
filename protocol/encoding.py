"""Msgpack serialization and deserialization for protocol messages.

Handles packing protocol messages into compact binary form for LXMF transport
and unpacking received bytes back into typed message objects.
"""

# TODO: Implement encode/decode functions using msgpack
#   - encode_message(msg) -> bytes
#   - decode_message(data: bytes) -> Message
#   - Register type tags for polymorphic deserialization
