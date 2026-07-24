"""Negotiation — direct, private player-to-player messaging.

Per the identity model, negotiation never touches the coordinator: two
players exchange NEGOTIATION messages directly over their own
destinations (real transport: LXMF; here, the abstract Transport, same
as everywhere else in this codebase — real LXMF wiring is deferred
alongside ``ReticulumTransport``). The coordinator cannot read or censor
these messages because it's never in the path.
"""

from __future__ import annotations

from dataclasses import dataclass

from protocol.encoding import encode_message
from protocol.messages import Negotiation, NegotiationAck
from shared.time import now as shared_now
from shared.transport import Transport


def send_negotiation(
    transport: Transport,
    recipient_destination: str,
    match_id: str,
    content: str,
    *,
    sequence_number: int,
    timestamp: float | None = None,
) -> Negotiation:
    """Send a free-form negotiation message directly to another player."""
    message = Negotiation(
        sequence_number=sequence_number,
        timestamp=timestamp if timestamp is not None else shared_now(),
        match_id=match_id,
        content=content,
    )
    transport.send(recipient_destination, encode_message(message))
    return message


def send_negotiation_ack(
    transport: Transport,
    recipient_destination: str,
    match_id: str,
    acked_sequence_number: int,
    *,
    sequence_number: int,
    timestamp: float | None = None,
) -> NegotiationAck:
    """Acknowledge delivery of a negotiation message (not that it's been read)."""
    ack = NegotiationAck(
        sequence_number=sequence_number,
        timestamp=timestamp if timestamp is not None else shared_now(),
        match_id=match_id,
        acked_sequence_number=acked_sequence_number,
    )
    transport.send(recipient_destination, encode_message(ack))
    return ack


@dataclass(frozen=True)
class NegotiationEntry:
    """One received negotiation message, tagged with who sent it."""

    sender_destination: str
    message: Negotiation


def group_by_sender(entries: list[NegotiationEntry]) -> dict[str, list[Negotiation]]:
    """Group a flat negotiation history into a per-sender conversation thread, in received order."""
    threads: dict[str, list[Negotiation]] = {}
    for entry in entries:
        threads.setdefault(entry.sender_destination, []).append(entry.message)
    return threads
