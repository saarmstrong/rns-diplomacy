"""Negotiation — direct, private player-to-player messaging.

Per the identity model, negotiation never touches the coordinator: two
players exchange NEGOTIATION messages directly over their own
destinations (the abstract Transport — ``InMemoryTransport`` in tests,
real LXMF delivery via ``shared/reticulum_transport.py`` in production).
The coordinator cannot read or censor these messages because it's never
in the path.

Known limitation on true first contact: if two players have never
exchanged anything before (no prior message, no observed announce), the
recipient may only be able to identify the sender by a one-way
destination hash rather than their full public key (see
``ReticulumTransport._on_delivery``'s handling of ``LXMessage.get_source()
is None``) — enough to log who sent it, but not enough to reply to,
unlike JOIN_REQUEST which carries the sender's public key in the message
body for exactly this reason. ``Negotiation`` has no equivalent field.
In practice this resolves itself once either side has announced (which
a real LXMF client typically does periodically) or once any other
message has passed between them (e.g. a shared match's JOIN_ACCEPTED
round trip lets the coordinator recall a player, but does not
introduce two *players* to each other — they still need their own
prior contact or announce).
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
