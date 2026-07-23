"""Construction, defaults, and envelope tests for all protocol message types."""

from __future__ import annotations

import dataclasses

import pytest

from engine.model import Phase
from engine.orders import HoldOrder
from protocol import messages as msgs
from protocol.constants import PROTOCOL_VERSION

# Every concrete message class, in the same order as protocol.messages.AnyMessage.
ALL_MESSAGE_CLASSES = tuple(msgs.MESSAGE_TYPES.values())


def _make(cls: type[msgs.Message], **extra: object) -> msgs.Message:
    return cls(sequence_number=1, timestamp=1234.5, **extra)


@pytest.mark.parametrize("cls", ALL_MESSAGE_CLASSES, ids=[c.__name__ for c in ALL_MESSAGE_CLASSES])
def test_message_type_tag_matches_class(cls: type[msgs.Message]) -> None:
    instance = _make(cls)
    assert msgs.MESSAGE_TYPES[instance.message_type] is cls


@pytest.mark.parametrize("cls", ALL_MESSAGE_CLASSES, ids=[c.__name__ for c in ALL_MESSAGE_CLASSES])
def test_envelope_defaults(cls: type[msgs.Message]) -> None:
    instance = _make(cls)
    assert instance.protocol_version == PROTOCOL_VERSION
    assert instance.sequence_number == 1
    assert instance.timestamp == 1234.5


@pytest.mark.parametrize("cls", ALL_MESSAGE_CLASSES, ids=[c.__name__ for c in ALL_MESSAGE_CLASSES])
def test_messages_are_frozen(cls: type[msgs.Message]) -> None:
    instance = _make(cls)
    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.sequence_number = 999  # type: ignore[misc]


def test_message_type_tags_are_unique() -> None:
    tags = [mt.value for mt in msgs.MessageType]
    assert len(tags) == len(set(tags))


def test_message_types_dict_covers_every_enum_member() -> None:
    assert set(msgs.MESSAGE_TYPES.keys()) == set(msgs.MessageType)


def test_any_message_union_covers_every_message_type() -> None:
    union_members = set(msgs.AnyMessage.__args__)
    assert union_members == set(ALL_MESSAGE_CLASSES)


def test_discover_game_minimal() -> None:
    msg = _make(msgs.DiscoverGame)
    assert msg.message_type is msgs.MessageType.DISCOVER_GAME


def test_game_info_fields() -> None:
    msg = _make(
        msgs.GameInfo,
        match_id="match-1",
        status=msgs.MatchStatus.LOBBY,
        player_count=3,
        max_players=7,
        phase=None,
    )
    assert msg.match_id == "match-1"
    assert msg.status is msgs.MatchStatus.LOBBY
    assert msg.player_count == 3
    assert msg.max_players == 7
    assert msg.phase is None


def test_join_request_fields() -> None:
    msg = _make(
        msgs.JoinRequest,
        match_id="match-1",
        player_public_key=b"\x01\x02",
        display_name="Ambassador",
    )
    assert msg.player_public_key == b"\x01\x02"
    assert msg.display_name == "Ambassador"


def test_join_accepted_fields() -> None:
    msg = _make(
        msgs.JoinAccepted,
        match_id="match-1",
        encrypted_faction=b"\xde\xad",
        faction_names=("Valdoria", "Kelmarch"),
        match_parameters={"movement_seconds": 86400},
    )
    assert msg.encrypted_faction == b"\xde\xad"
    assert msg.faction_names == ("Valdoria", "Kelmarch")
    assert msg.match_parameters == {"movement_seconds": 86400}


def test_join_rejected_fields() -> None:
    msg = _make(msgs.JoinRejected, match_id="match-1", reason="match full")
    assert msg.reason == "match full"


def test_negotiation_and_ack_fields() -> None:
    neg = _make(msgs.Negotiation, match_id="match-1", content="Shall we discuss borders?")
    assert neg.content == "Shall we discuss borders?"

    ack = _make(msgs.NegotiationAck, match_id="match-1", acked_sequence_number=neg.sequence_number)
    assert ack.acked_sequence_number == 1


def test_order_submit_and_receipt_fields() -> None:
    order = HoldOrder(unit_region_id="valdor")
    submit = _make(msgs.OrderSubmit, match_id="match-1", revision=1, orders=(order,))
    assert submit.orders == (order,)

    receipt = _make(
        msgs.OrderReceipt,
        match_id="match-1",
        revision=1,
        order_hash="abc123",
        state=msgs.OrderState.ACCEPTED,
    )
    assert receipt.order_hash == "abc123"
    assert receipt.state is msgs.OrderState.ACCEPTED


def test_order_update_cancel_status_fields() -> None:
    order = HoldOrder(unit_region_id="valdor")
    update = _make(msgs.OrderUpdate, match_id="match-1", revision=2, orders=(order,))
    assert update.revision == 2

    cancel = _make(msgs.OrderCancel, match_id="match-1", revision=2)
    assert cancel.revision == 2

    status = _make(
        msgs.OrderStatus,
        match_id="match-1",
        revision=2,
        state=msgs.OrderState.DELIVERED,
        order_hash=None,
    )
    assert status.state is msgs.OrderState.DELIVERED
    assert status.order_hash is None


def test_phase_start_result_warning_fields() -> None:
    start = _make(
        msgs.PhaseStart,
        match_id="match-1",
        phase=Phase.ORDERS,
        turn=1,
        year=1,
        deadline=9999.0,
    )
    assert start.phase is Phase.ORDERS
    assert start.deadline == 9999.0

    result = _make(
        msgs.PhaseResult,
        match_id="match-1",
        phase=Phase.RESOLUTION,
        turn=1,
        year=1,
        canonical_state=b"\x00",
        state_hash="hash-1",
        previous_state_hash=None,
        signature=b"\x01",
    )
    assert result.state_hash == "hash-1"
    assert result.previous_state_hash is None

    warning = _make(
        msgs.PhaseDeadlineWarning,
        match_id="match-1",
        phase=Phase.ORDERS,
        seconds_remaining=300.0,
    )
    assert warning.seconds_remaining == 300.0


def test_state_request_response_hash_fields() -> None:
    request = _make(msgs.StateRequest, match_id="match-1", turn=None)
    assert request.turn is None

    response = _make(
        msgs.StateResponse,
        match_id="match-1",
        turn=1,
        canonical_state=b"\x00",
        state_hash="hash-1",
        previous_state_hash="hash-0",
        signature=b"\x01",
    )
    assert response.previous_state_hash == "hash-0"

    state_hash = _make(msgs.StateHash, match_id="match-1", turn=1, state_hash="hash-1")
    assert state_hash.state_hash == "hash-1"


def test_draw_propose_vote_result_fields() -> None:
    propose = _make(msgs.DrawPropose, match_id="match-1", turn=2)
    assert propose.turn == 2

    vote = _make(msgs.DrawVote, match_id="match-1", turn=2, vote=True)
    assert vote.vote is True

    result = _make(
        msgs.DrawResult,
        match_id="match-1",
        turn=2,
        accepted=True,
        votes={"Valdoria": True, "Kelmarch": False},
    )
    assert result.accepted is True
    assert result.votes == {"Valdoria": True, "Kelmarch": False}


def test_match_start_and_end_fields() -> None:
    start = _make(
        msgs.MatchStart,
        match_id="match-1",
        faction_names=("Valdoria", "Kelmarch"),
        canonical_state=b"\x00",
        state_hash="hash-0",
        signature=b"\x01",
        deadline=9999.0,
    )
    assert start.faction_names == ("Valdoria", "Kelmarch")

    end = _make(
        msgs.MatchEnd,
        match_id="match-1",
        reason="victory",
        winner="Valdoria",
        final_state_hash="hash-9",
    )
    assert end.winner == "Valdoria"
    assert end.final_state_hash == "hash-9"


def test_error_message_fields() -> None:
    msg = _make(
        msgs.ErrorMessage,
        code="INVALID_ORDER",
        description="unit does not exist",
        related_sequence_number=5,
    )
    assert msg.code == "INVALID_ORDER"
    assert msg.related_sequence_number == 5
