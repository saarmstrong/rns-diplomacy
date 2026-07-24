"""Player-side session — discovery, join, orders, and state tracking.

Mirrors ``coordinator/match.py``'s ``MatchCoordinator`` on the client
side: all communication goes through the abstract
``shared.transport.Transport``, so this is fully testable against
``InMemoryTransport``. The real Reticulum/LXMF transport (like
``ReticulumTransport`` on the coordinator side) is deferred.
"""

from __future__ import annotations

from typing import Any

from client.negotiations import NegotiationEntry, send_negotiation, send_negotiation_ack
from engine.model import Phase
from engine.orders import Order
from protocol.encoding import decode_message, encode_message
from protocol.errors import DecodingError, ValidationError
from protocol.messages import (
    AnyMessage,
    DiscoverGame,
    DrawPropose,
    DrawResult,
    DrawVote,
    ErrorMessage,
    GameInfo,
    JoinAccepted,
    JoinRejected,
    JoinRequest,
    MatchEnd,
    MatchStart,
    MatchStatus,
    Negotiation,
    NegotiationAck,
    OrderCancel,
    OrderReceipt,
    OrderStatus,
    OrderSubmit,
    OrderUpdate,
    PhaseDeadlineWarning,
    PhaseResult,
    PhaseStart,
    StateRequest,
    StateResponse,
)
from protocol.validation import validate_message
from shared.identity import Identity
from shared.time import now as shared_now
from shared.transport import Transport


def listen_for_announces(transport: Transport) -> list[str]:
    """Check for new match announcements since the last call on this transport.

    Returns newly discovered match IDs. This precedes choosing a match to
    join — it operates directly on a ``Transport``, not a ``PlayerClient``
    (which is already bound to one match's coordinator).
    """
    return [announcement.data.decode("utf-8") for announcement in transport.discover()]


class PlayerClient:
    """One player's session against a single match's coordinator."""

    def __init__(
        self, identity: Identity, transport: Transport, coordinator_destination: str, match_id: str
    ) -> None:
        self.identity = identity
        self.transport = transport
        self.coordinator_destination = coordinator_destination
        self.match_id = match_id
        self._sequence_number = 0

        self.game_info: GameInfo | None = None
        self.faction_name: str | None = None
        self.faction_names: tuple[str, ...] = ()
        self.match_parameters: dict[str, Any] = {}
        self.join_rejected_reason: str | None = None

        self.match_status: MatchStatus | None = None
        self.phase: Phase | None = None
        self.turn: int | None = None
        self.year: int | None = None
        self.deadline: float | None = None

        self.match_start: MatchStart | None = None
        self.phase_results: list[PhaseResult] = []
        self.deadline_warnings: list[PhaseDeadlineWarning] = []
        self.order_receipts: list[OrderReceipt] = []
        self.order_status: OrderStatus | None = None
        self.state_responses: list[StateResponse] = []
        self.draw_results: list[DrawResult] = []
        self.match_end: MatchEnd | None = None
        self.errors: list[ErrorMessage] = []
        self.negotiations: list[NegotiationEntry] = []
        self.negotiation_acks: list[NegotiationAck] = []

        self._handlers = {
            GameInfo: self._on_game_info,
            JoinAccepted: self._on_join_accepted,
            JoinRejected: self._on_join_rejected,
            MatchStart: self._on_match_start,
            PhaseStart: self._on_phase_start,
            PhaseResult: self._on_phase_result,
            PhaseDeadlineWarning: self._on_phase_deadline_warning,
            OrderReceipt: self._on_order_receipt,
            OrderStatus: self._on_order_status,
            StateResponse: self._on_state_response,
            DrawResult: self._on_draw_result,
            MatchEnd: self._on_match_end,
            ErrorMessage: self._on_error,
            Negotiation: self._on_negotiation,
            NegotiationAck: self._on_negotiation_ack,
        }

    # --- Outbound actions ------------------------------------------------

    def discover(self) -> None:
        self._send(DiscoverGame(sequence_number=self._next_sequence(), timestamp=self._now()))

    def join(self, display_name: str | None = None) -> None:
        self._send(
            JoinRequest(
                sequence_number=self._next_sequence(),
                timestamp=self._now(),
                match_id=self.match_id,
                player_public_key=self.identity.public_bytes,
                display_name=display_name,
            )
        )

    def submit_orders(self, orders: tuple[Order, ...], revision: int) -> None:
        self._send(
            OrderSubmit(
                sequence_number=self._next_sequence(),
                timestamp=self._now(),
                match_id=self.match_id,
                revision=revision,
                orders=orders,
            )
        )

    def update_orders(self, orders: tuple[Order, ...], revision: int) -> None:
        self._send(
            OrderUpdate(
                sequence_number=self._next_sequence(),
                timestamp=self._now(),
                match_id=self.match_id,
                revision=revision,
                orders=orders,
            )
        )

    def cancel_orders(self, revision: int) -> None:
        self._send(
            OrderCancel(
                sequence_number=self._next_sequence(), timestamp=self._now(), match_id=self.match_id, revision=revision
            )
        )

    def request_state(self, turn: int | None = None) -> None:
        self._send(
            StateRequest(sequence_number=self._next_sequence(), timestamp=self._now(), match_id=self.match_id, turn=turn)
        )

    def propose_draw(self, turn: int) -> None:
        self._send(
            DrawPropose(sequence_number=self._next_sequence(), timestamp=self._now(), match_id=self.match_id, turn=turn)
        )

    def vote_draw(self, turn: int, vote: bool) -> None:
        self._send(
            DrawVote(
                sequence_number=self._next_sequence(), timestamp=self._now(), match_id=self.match_id, turn=turn, vote=vote
            )
        )

    def send_negotiation(self, recipient_destination: str, content: str) -> Negotiation:
        """Send a free-form negotiation message directly to another player (never via the coordinator)."""
        return send_negotiation(
            self.transport, recipient_destination, self.match_id, content, sequence_number=self._next_sequence(),
            timestamp=self._now(),
        )

    def ack_negotiation(self, recipient_destination: str, acked_sequence_number: int) -> NegotiationAck:
        return send_negotiation_ack(
            self.transport, recipient_destination, self.match_id, acked_sequence_number,
            sequence_number=self._next_sequence(), timestamp=self._now(),
        )

    # --- Inbound processing ------------------------------------------------

    def poll(self) -> list[AnyMessage]:
        """Drain and process every message currently queued for this client.

        Malformed or invalid messages are silently skipped (a Byzantine or
        buggy coordinator shouldn't be able to crash the client); anything
        it explicitly reports is delivered as an ERROR message instead.
        """
        processed: list[AnyMessage] = []
        for inbound in self.transport.receive():
            try:
                message = decode_message(inbound.data)
                validate_message(message)
            except (DecodingError, ValidationError):
                continue
            handler = self._handlers.get(type(message))
            if handler is not None:
                handler(message, inbound.sender)
            processed.append(message)
        return processed

    def _on_game_info(self, message: GameInfo, sender: str) -> None:
        self.game_info = message
        self.match_status = message.status

    def _on_join_accepted(self, message: JoinAccepted, sender: str) -> None:
        self.faction_name = self.identity.decrypt(message.encrypted_faction).decode("utf-8")
        self.faction_names = message.faction_names
        self.match_parameters = dict(message.match_parameters)
        self.join_rejected_reason = None

    def _on_join_rejected(self, message: JoinRejected, sender: str) -> None:
        self.join_rejected_reason = message.reason

    def _on_match_start(self, message: MatchStart, sender: str) -> None:
        self.match_start = message
        self.match_status = MatchStatus.ACTIVE
        self.faction_names = message.faction_names
        self.deadline = message.deadline

    def _on_phase_start(self, message: PhaseStart, sender: str) -> None:
        self.phase = message.phase
        self.turn = message.turn
        self.year = message.year
        self.deadline = message.deadline

    def _on_phase_result(self, message: PhaseResult, sender: str) -> None:
        self.phase_results.append(message)

    def _on_phase_deadline_warning(self, message: PhaseDeadlineWarning, sender: str) -> None:
        self.deadline_warnings.append(message)

    def _on_order_receipt(self, message: OrderReceipt, sender: str) -> None:
        self.order_receipts.append(message)

    def _on_order_status(self, message: OrderStatus, sender: str) -> None:
        self.order_status = message

    def _on_state_response(self, message: StateResponse, sender: str) -> None:
        self.state_responses.append(message)

    def _on_draw_result(self, message: DrawResult, sender: str) -> None:
        self.draw_results.append(message)

    def _on_match_end(self, message: MatchEnd, sender: str) -> None:
        self.match_end = message
        self.match_status = MatchStatus.COMPLETED

    def _on_error(self, message: ErrorMessage, sender: str) -> None:
        self.errors.append(message)

    def _on_negotiation(self, message: Negotiation, sender: str) -> None:
        self.negotiations.append(NegotiationEntry(sender_destination=sender, message=message))

    def _on_negotiation_ack(self, message: NegotiationAck, sender: str) -> None:
        self.negotiation_acks.append(message)

    # --- Helpers -----------------------------------------------------------

    def _now(self) -> float:
        return shared_now()

    def _next_sequence(self) -> int:
        self._sequence_number += 1
        return self._sequence_number

    def _send(self, message: AnyMessage) -> None:
        self.transport.send(self.coordinator_destination, encode_message(message))
