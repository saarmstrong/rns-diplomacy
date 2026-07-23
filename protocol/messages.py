"""Message type definitions for coordinator-client communication.

Defines the ~25 typed message structures exchanged between coordinators and
clients: discovery, join flow, negotiation relays, order submission, phase
management, state verification, and draw/match lifecycle.

Every message carries a common envelope (``protocol_version``,
``sequence_number``, ``timestamp``) so that replay protection and version
checks can be applied uniformly by ``protocol/validation.py`` regardless of
message type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union

from engine.model import Phase
from engine.orders import Order
from protocol.constants import PROTOCOL_VERSION


class MessageType(Enum):
    """Discriminator tag identifying a protocol message's type."""

    DISCOVER_GAME = "DISCOVER_GAME"
    GAME_INFO = "GAME_INFO"
    JOIN_REQUEST = "JOIN_REQUEST"
    JOIN_ACCEPTED = "JOIN_ACCEPTED"
    JOIN_REJECTED = "JOIN_REJECTED"
    NEGOTIATION = "NEGOTIATION"
    NEGOTIATION_ACK = "NEGOTIATION_ACK"
    ORDER_SUBMIT = "ORDER_SUBMIT"
    ORDER_RECEIPT = "ORDER_RECEIPT"
    ORDER_UPDATE = "ORDER_UPDATE"
    ORDER_CANCEL = "ORDER_CANCEL"
    ORDER_STATUS = "ORDER_STATUS"
    PHASE_START = "PHASE_START"
    PHASE_RESULT = "PHASE_RESULT"
    PHASE_DEADLINE_WARNING = "PHASE_DEADLINE_WARNING"
    STATE_REQUEST = "STATE_REQUEST"
    STATE_RESPONSE = "STATE_RESPONSE"
    STATE_HASH = "STATE_HASH"
    DRAW_PROPOSE = "DRAW_PROPOSE"
    DRAW_VOTE = "DRAW_VOTE"
    DRAW_RESULT = "DRAW_RESULT"
    MATCH_START = "MATCH_START"
    MATCH_END = "MATCH_END"
    ERROR = "ERROR"


class MatchStatus(Enum):
    """Lifecycle status of a match, as reported in GAME_INFO."""

    LOBBY = "lobby"
    ACTIVE = "active"
    COMPLETED = "completed"


class OrderState(Enum):
    """Wire-visible states of the order lifecycle (Draft is client-local only)."""

    SENT = "sent"
    DELIVERED = "delivered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, kw_only=True)
class Message:
    """Common envelope fields present on every protocol message."""

    message_type: MessageType
    sequence_number: int
    timestamp: float
    protocol_version: int = PROTOCOL_VERSION


# --- Discovery & Join --------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class DiscoverGame(Message):
    """Client -> Coordinator: request match info."""

    message_type: MessageType = MessageType.DISCOVER_GAME


@dataclass(frozen=True, kw_only=True)
class GameInfo(Message):
    """Coordinator -> Client: match parameters, status, player count."""

    message_type: MessageType = MessageType.GAME_INFO
    match_id: str = ""
    status: MatchStatus = MatchStatus.LOBBY
    player_count: int = 0
    max_players: int = 7
    phase: Phase | None = None


@dataclass(frozen=True, kw_only=True)
class JoinRequest(Message):
    """Client -> Coordinator: request to join with a match-scoped identity."""

    message_type: MessageType = MessageType.JOIN_REQUEST
    match_id: str = ""
    player_public_key: bytes = b""
    display_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class JoinAccepted(Message):
    """Coordinator -> Client: encrypted faction assignment and match params."""

    message_type: MessageType = MessageType.JOIN_ACCEPTED
    match_id: str = ""
    encrypted_faction: bytes = b""
    faction_names: tuple[str, ...] = ()
    match_parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class JoinRejected(Message):
    """Coordinator -> Client: rejection with reason (full, started, banned key, ...)."""

    message_type: MessageType = MessageType.JOIN_REJECTED
    match_id: str = ""
    reason: str = ""


# --- Negotiation ---------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Negotiation(Message):
    """Player -> Player: free-form negotiation message (via LXMF, E2E encrypted)."""

    message_type: MessageType = MessageType.NEGOTIATION
    match_id: str = ""
    content: str = ""


@dataclass(frozen=True, kw_only=True)
class NegotiationAck(Message):
    """Player -> Player: delivery receipt for a negotiation message."""

    message_type: MessageType = MessageType.NEGOTIATION_ACK
    match_id: str = ""
    acked_sequence_number: int = 0


# --- Orders ---------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class OrderSubmit(Message):
    """Client -> Coordinator: submit orders for the current phase."""

    message_type: MessageType = MessageType.ORDER_SUBMIT
    match_id: str = ""
    revision: int = 0
    orders: tuple[Order, ...] = ()


@dataclass(frozen=True, kw_only=True)
class OrderReceipt(Message):
    """Coordinator -> Client: acknowledgement with a hash of the accepted orders."""

    message_type: MessageType = MessageType.ORDER_RECEIPT
    match_id: str = ""
    revision: int = 0
    order_hash: str = ""
    state: OrderState = OrderState.ACCEPTED


@dataclass(frozen=True, kw_only=True)
class OrderUpdate(Message):
    """Client -> Coordinator: revise previously submitted orders."""

    message_type: MessageType = MessageType.ORDER_UPDATE
    match_id: str = ""
    revision: int = 0
    orders: tuple[Order, ...] = ()


@dataclass(frozen=True, kw_only=True)
class OrderCancel(Message):
    """Client -> Coordinator: cancel submitted orders."""

    message_type: MessageType = MessageType.ORDER_CANCEL
    match_id: str = ""
    revision: int = 0


@dataclass(frozen=True, kw_only=True)
class OrderStatus(Message):
    """Coordinator -> Client: current order status."""

    message_type: MessageType = MessageType.ORDER_STATUS
    match_id: str = ""
    revision: int = 0
    state: OrderState = OrderState.SENT
    order_hash: str | None = None


# --- Phase management -------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PhaseStart(Message):
    """Coordinator -> Client: new phase begins, deadline info."""

    message_type: MessageType = MessageType.PHASE_START
    match_id: str = ""
    phase: Phase = Phase.ORDERS
    turn: int = 1
    year: int = 1
    deadline: float = 0.0


@dataclass(frozen=True, kw_only=True)
class PhaseResult(Message):
    """Coordinator -> Client: adjudication results, signed state."""

    message_type: MessageType = MessageType.PHASE_RESULT
    match_id: str = ""
    phase: Phase = Phase.RESOLUTION
    turn: int = 1
    year: int = 1
    canonical_state: bytes = b""
    state_hash: str = ""
    previous_state_hash: str | None = None
    signature: bytes = b""


@dataclass(frozen=True, kw_only=True)
class PhaseDeadlineWarning(Message):
    """Coordinator -> Client: deadline approaching."""

    message_type: MessageType = MessageType.PHASE_DEADLINE_WARNING
    match_id: str = ""
    phase: Phase = Phase.ORDERS
    seconds_remaining: float = 0.0


# --- State & verification -------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class StateRequest(Message):
    """Client -> Coordinator: request current or historical state."""

    message_type: MessageType = MessageType.STATE_REQUEST
    match_id: str = ""
    turn: int | None = None


@dataclass(frozen=True, kw_only=True)
class StateResponse(Message):
    """Coordinator -> Client: signed state with hash chain."""

    message_type: MessageType = MessageType.STATE_RESPONSE
    match_id: str = ""
    turn: int = 1
    canonical_state: bytes = b""
    state_hash: str = ""
    previous_state_hash: str | None = None
    signature: bytes = b""


@dataclass(frozen=True, kw_only=True)
class StateHash(Message):
    """Coordinator -> Client: current state hash for quick verification."""

    message_type: MessageType = MessageType.STATE_HASH
    match_id: str = ""
    turn: int = 1
    state_hash: str = ""


# --- Draw & meta ------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class DrawPropose(Message):
    """Client -> Coordinator: propose a draw."""

    message_type: MessageType = MessageType.DRAW_PROPOSE
    match_id: str = ""
    turn: int = 1


@dataclass(frozen=True, kw_only=True)
class DrawVote(Message):
    """Client -> Coordinator: vote on a draw proposal."""

    message_type: MessageType = MessageType.DRAW_VOTE
    match_id: str = ""
    turn: int = 1
    vote: bool = False


@dataclass(frozen=True, kw_only=True)
class DrawResult(Message):
    """Coordinator -> Client: draw vote outcome."""

    message_type: MessageType = MessageType.DRAW_RESULT
    match_id: str = ""
    turn: int = 1
    accepted: bool = False
    votes: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class MatchStart(Message):
    """Coordinator -> Client: match begins, initial state."""

    message_type: MessageType = MessageType.MATCH_START
    match_id: str = ""
    faction_names: tuple[str, ...] = ()
    canonical_state: bytes = b""
    state_hash: str = ""
    signature: bytes = b""
    deadline: float = 0.0


@dataclass(frozen=True, kw_only=True)
class MatchEnd(Message):
    """Coordinator -> Client: match over, final results."""

    message_type: MessageType = MessageType.MATCH_END
    match_id: str = ""
    reason: str = ""
    winner: str | None = None
    final_state_hash: str | None = None


@dataclass(frozen=True, kw_only=True)
class ErrorMessage(Message):
    """Either direction: error with code and description."""

    message_type: MessageType = MessageType.ERROR
    code: str = ""
    description: str = ""
    related_sequence_number: int | None = None


# Union of all message types for type annotations.
AnyMessage = Union[
    DiscoverGame,
    GameInfo,
    JoinRequest,
    JoinAccepted,
    JoinRejected,
    Negotiation,
    NegotiationAck,
    OrderSubmit,
    OrderReceipt,
    OrderUpdate,
    OrderCancel,
    OrderStatus,
    PhaseStart,
    PhaseResult,
    PhaseDeadlineWarning,
    StateRequest,
    StateResponse,
    StateHash,
    DrawPropose,
    DrawVote,
    DrawResult,
    MatchStart,
    MatchEnd,
    ErrorMessage,
]

# Maps each discriminator tag to its message class, for decode-time dispatch.
MESSAGE_TYPES: dict[MessageType, type[Message]] = {
    MessageType.DISCOVER_GAME: DiscoverGame,
    MessageType.GAME_INFO: GameInfo,
    MessageType.JOIN_REQUEST: JoinRequest,
    MessageType.JOIN_ACCEPTED: JoinAccepted,
    MessageType.JOIN_REJECTED: JoinRejected,
    MessageType.NEGOTIATION: Negotiation,
    MessageType.NEGOTIATION_ACK: NegotiationAck,
    MessageType.ORDER_SUBMIT: OrderSubmit,
    MessageType.ORDER_RECEIPT: OrderReceipt,
    MessageType.ORDER_UPDATE: OrderUpdate,
    MessageType.ORDER_CANCEL: OrderCancel,
    MessageType.ORDER_STATUS: OrderStatus,
    MessageType.PHASE_START: PhaseStart,
    MessageType.PHASE_RESULT: PhaseResult,
    MessageType.PHASE_DEADLINE_WARNING: PhaseDeadlineWarning,
    MessageType.STATE_REQUEST: StateRequest,
    MessageType.STATE_RESPONSE: StateResponse,
    MessageType.STATE_HASH: StateHash,
    MessageType.DRAW_PROPOSE: DrawPropose,
    MessageType.DRAW_VOTE: DrawVote,
    MessageType.DRAW_RESULT: DrawResult,
    MessageType.MATCH_START: MatchStart,
    MessageType.MATCH_END: MatchEnd,
    MessageType.ERROR: ErrorMessage,
}
