"""Stateful player client built on the transport-agnostic protocol layer."""

from __future__ import annotations

from dataclasses import dataclass, field

from client.orders import SubmissionTracker, validate_order_locally
from client.transport import ClientTransport
from engine.hashing import game_state_from_canonical_bytes
from engine.model import GameState, Phase
from engine.orders import Order
from protocol.encoding import decode_message
from protocol.messages import (
    AnyMessage,
    GameInfo,
    JoinAccepted,
    JoinRejected,
    JoinRequest,
    MatchStart,
    OrderCancel,
    OrderReceipt,
    OrderStatus,
    OrderSubmit,
    PhaseResult,
    PhaseStart,
)
from shared.identity import Identity
from shared.transport import Announcement, Transport


@dataclass
class PlayerClient:
    """Client-side match state, join flow, and movement-order lifecycle."""

    identity: Identity
    transport: ClientTransport
    sequence_number: int = 0
    match_id: str | None = None
    coordinator_destination: str | None = None
    faction_name: str | None = None
    game_state: GameState | None = None
    current_phase: Phase | None = None
    deadline: float | None = None
    history: list[PhaseResult] = field(default_factory=list)
    submission: SubmissionTracker = field(default_factory=SubmissionTracker)
    last_rejection: str | None = None
    order_status: OrderStatus | None = None

    @classmethod
    def create(cls, identity: Identity, raw_transport: Transport) -> PlayerClient:
        return cls(identity=identity, transport=ClientTransport(raw_transport))

    def _next_sequence(self) -> int:
        self.sequence_number += 1
        return self.sequence_number

    def discover(self) -> list[tuple[Announcement, GameInfo]]:
        """Decode coordinator announcements, ignoring malformed/unrelated data."""
        found: list[tuple[Announcement, GameInfo]] = []
        for announcement in self.transport.discover():
            try:
                message = decode_message(announcement.data)
            except Exception:
                continue
            if isinstance(message, GameInfo):
                found.append((announcement, message))
        return found

    def join(self, announcement: Announcement, info: GameInfo, timestamp: float, display_name: str | None = None) -> None:
        """Request a seat in a discovered lobby using this match-scoped key."""
        self.match_id = info.match_id
        self.coordinator_destination = announcement.destination
        self.transport.connect(announcement.destination)
        self.transport.send_message(
            JoinRequest(
                sequence_number=self._next_sequence(), timestamp=timestamp,
                match_id=info.match_id, player_public_key=self.identity.public_bytes, display_name=display_name,
            )
        )

    def submit_orders(self, orders: tuple[Order, ...], timestamp: float) -> int:
        """Locally validate then submit a complete replacement order set."""
        if self.match_id is None or self.game_state is None or self.faction_name is None:
            raise RuntimeError("join and receive match state before submitting orders")
        faction_id = next(
            (faction.id for faction in self.game_state.factions.values() if faction.name == self.faction_name), None
        )
        if faction_id is None or not all(validate_order_locally(order, self.game_state, faction_id) for order in orders):
            raise ValueError("order set contains an invalid local order")
        revision = self.submission.next_revision()
        self.transport.send_message(
            OrderSubmit(sequence_number=self._next_sequence(), timestamp=timestamp, match_id=self.match_id, revision=revision, orders=orders)
        )
        return revision

    def cancel_orders(self, timestamp: float) -> int:
        if self.match_id is None:
            raise RuntimeError("not joined")
        revision = self.submission.next_revision()
        self.transport.send_message(
            OrderCancel(sequence_number=self._next_sequence(), timestamp=timestamp, match_id=self.match_id, revision=revision)
        )
        return revision

    def poll(self) -> list[AnyMessage]:
        """Apply incoming coordinator messages and return them for UI rendering."""
        messages = self.transport.poll()
        for message in messages:
            if isinstance(message, JoinAccepted):
                self.match_id = message.match_id
                self.faction_name = self.identity.decrypt(message.encrypted_faction).decode("utf-8")
                self.last_rejection = None
            elif isinstance(message, JoinRejected):
                self.last_rejection = message.reason
            elif isinstance(message, (MatchStart, PhaseResult)):
                self.game_state = game_state_from_canonical_bytes(message.canonical_state)
                if isinstance(message, PhaseResult):
                    self.history.append(message)
            elif isinstance(message, PhaseStart):
                self.current_phase, self.deadline = message.phase, message.deadline
            elif isinstance(message, OrderReceipt):
                self.submission.record_receipt(message)
            elif isinstance(message, OrderStatus):
                self.order_status = message
        return messages

    def display_status(self) -> str:
        """Return a compact terminal-safe representation of current game state."""
        if self.game_state is None:
            return "No game state received."
        units = ", ".join(f"{unit.unit_type.value} {unit.region_id}" for unit in self.game_state.units if self._owns(unit.faction_id))
        centers = sum(1 for owner in self.game_state.supply_centers.values() if self._owns(owner))
        phase = self.current_phase.value if self.current_phase else "unknown"
        return f"{self.faction_name or 'Unassigned'} | {phase} | units: {units or 'none'} | centers: {centers}"

    def _owns(self, faction_id: str) -> bool:
        return any(faction.id == faction_id and faction.name == self.faction_name for faction in (self.game_state.factions.values() if self.game_state else ()))
