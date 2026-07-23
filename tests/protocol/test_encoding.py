"""Round-trip, determinism, and error-path tests for message encode/decode."""

from __future__ import annotations

import msgpack
import pytest

from engine.model import Phase
from engine.orders import HoldOrder, MoveOrder
from protocol import messages as msgs
from protocol.encoding import decode_message, encode_message
from protocol.errors import DecodingError, MessageTooLargeError

# One representative, fully-populated instance per message type.
SAMPLE_MESSAGES: tuple[msgs.Message, ...] = (
    msgs.DiscoverGame(sequence_number=1, timestamp=100.0),
    msgs.GameInfo(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        status=msgs.MatchStatus.LOBBY,
        player_count=3,
        max_players=7,
        phase=Phase.ORDERS,
    ),
    msgs.JoinRequest(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        player_public_key=b"\x01\x02\x03",
        display_name="Ambassador",
    ),
    msgs.JoinAccepted(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        encrypted_faction=b"\xde\xad\xbe\xef",
        faction_names=("Valdoria", "Kelmarch"),
        match_parameters={"movement_seconds": 86400, "retreat_seconds": 43200},
    ),
    msgs.JoinRejected(sequence_number=1, timestamp=100.0, match_id="match-1", reason="match full"),
    msgs.Negotiation(sequence_number=1, timestamp=100.0, match_id="match-1", content="Truce?"),
    msgs.NegotiationAck(sequence_number=1, timestamp=100.0, match_id="match-1", acked_sequence_number=7),
    msgs.OrderSubmit(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        revision=1,
        orders=(HoldOrder(unit_region_id="valdor"), MoveOrder(unit_region_id="kelm", destination_id="ostra")),
    ),
    msgs.OrderReceipt(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        revision=1,
        order_hash="abc123",
        state=msgs.OrderState.ACCEPTED,
    ),
    msgs.OrderUpdate(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        revision=2,
        orders=(HoldOrder(unit_region_id="valdor"),),
    ),
    msgs.OrderCancel(sequence_number=1, timestamp=100.0, match_id="match-1", revision=2),
    msgs.OrderStatus(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        revision=2,
        state=msgs.OrderState.DELIVERED,
        order_hash=None,
    ),
    msgs.PhaseStart(
        sequence_number=1, timestamp=100.0, match_id="match-1", phase=Phase.ORDERS, turn=1, year=1, deadline=9999.0
    ),
    msgs.PhaseResult(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        phase=Phase.RESOLUTION,
        turn=1,
        year=1,
        canonical_state=b"\x00\x01",
        state_hash="hash-1",
        previous_state_hash=None,
        signature=b"\x02\x03",
    ),
    msgs.PhaseDeadlineWarning(
        sequence_number=1, timestamp=100.0, match_id="match-1", phase=Phase.ORDERS, seconds_remaining=300.0
    ),
    msgs.StateRequest(sequence_number=1, timestamp=100.0, match_id="match-1", turn=None),
    msgs.StateResponse(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        turn=1,
        canonical_state=b"\x00\x01",
        state_hash="hash-1",
        previous_state_hash="hash-0",
        signature=b"\x02\x03",
    ),
    msgs.StateHash(sequence_number=1, timestamp=100.0, match_id="match-1", turn=1, state_hash="hash-1"),
    msgs.DrawPropose(sequence_number=1, timestamp=100.0, match_id="match-1", turn=2),
    msgs.DrawVote(sequence_number=1, timestamp=100.0, match_id="match-1", turn=2, vote=True),
    msgs.DrawResult(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        turn=2,
        accepted=True,
        votes={"Valdoria": True, "Kelmarch": False},
    ),
    msgs.MatchStart(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        faction_names=("Valdoria", "Kelmarch"),
        canonical_state=b"\x00\x01",
        state_hash="hash-0",
        signature=b"\x02\x03",
        deadline=9999.0,
    ),
    msgs.MatchEnd(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        reason="victory",
        winner="Valdoria",
        final_state_hash="hash-9",
    ),
    msgs.ErrorMessage(
        sequence_number=1,
        timestamp=100.0,
        code="INVALID_ORDER",
        description="unit does not exist",
        related_sequence_number=5,
    ),
)


@pytest.mark.parametrize("msg", SAMPLE_MESSAGES, ids=[type(m).__name__ for m in SAMPLE_MESSAGES])
def test_round_trip(msg: msgs.Message) -> None:
    decoded = decode_message(encode_message(msg))
    assert decoded == msg


@pytest.mark.parametrize("msg", SAMPLE_MESSAGES, ids=[type(m).__name__ for m in SAMPLE_MESSAGES])
def test_encoding_is_deterministic(msg: msgs.Message) -> None:
    assert encode_message(msg) == encode_message(msg)


def test_encode_message_too_large() -> None:
    huge = msgs.Negotiation(sequence_number=1, timestamp=100.0, match_id="match-1", content="x" * 200_000)
    with pytest.raises(MessageTooLargeError):
        encode_message(huge)


def test_decode_message_too_large() -> None:
    with pytest.raises(MessageTooLargeError):
        decode_message(b"\x00" * 200_000)


def test_decode_garbage_bytes_raises_decoding_error() -> None:
    with pytest.raises(DecodingError):
        decode_message(b"\xff\xff\xff\xff")


def test_decode_non_dict_payload_raises_decoding_error() -> None:
    with pytest.raises(DecodingError):
        decode_message(msgpack.packb([1, 2, 3]))


def test_decode_missing_message_type_raises_decoding_error() -> None:
    with pytest.raises(DecodingError):
        decode_message(msgpack.packb({"sequence_number": 1, "timestamp": 100.0}))


def test_decode_unknown_message_type_raises_decoding_error() -> None:
    with pytest.raises(DecodingError):
        decode_message(msgpack.packb({"message_type": "NOT_A_REAL_TYPE"}))


def test_decode_missing_field_raises_decoding_error() -> None:
    payload = msgpack.packb({"message_type": "JOIN_REJECTED", "sequence_number": 1, "timestamp": 100.0})
    with pytest.raises(DecodingError):
        decode_message(payload)


def test_decode_order_with_unknown_order_type_raises_decoding_error() -> None:
    submit = msgs.OrderSubmit(
        sequence_number=1,
        timestamp=100.0,
        match_id="match-1",
        revision=1,
        orders=(HoldOrder(unit_region_id="valdor"),),
    )
    raw = msgpack.unpackb(encode_message(submit), raw=False)
    raw["orders"][0]["order_type"] = "convoy"
    with pytest.raises(DecodingError):
        decode_message(msgpack.packb(raw, use_bin_type=True))


def test_all_message_types_have_a_sample() -> None:
    covered = {type(m) for m in SAMPLE_MESSAGES}
    assert covered == set(msgs.MESSAGE_TYPES.values())
