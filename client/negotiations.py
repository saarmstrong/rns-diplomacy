"""Private negotiation message composition and conversation history."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import LXMF
import RNS

from protocol.encoding import decode_message, encode_message
from protocol.messages import Negotiation, NegotiationAck
from shared.identity import Identity, PublicIdentity
from shared.transport import Transport


@dataclass(frozen=True)
class Proposal:
    faction_id: str
    sequence_number: int
    content: str
    acknowledged: bool = False


@dataclass
class NegotiationClient:
    """Encrypts protocol payloads directly to known player destinations.

    Faction-to-destination/public-key mappings are intentionally supplied by
    the caller; the coordinator never receives conversation plaintext.
    """

    match_id: str
    identity: Identity
    transport: Transport
    peers: dict[str, tuple[str, PublicIdentity]]
    history: dict[str, list[Proposal]] = field(default_factory=dict)

    def send_proposal(self, faction_id: str, content: str, sequence_number: int, timestamp: float) -> None:
        if not content:
            raise ValueError("negotiation content must not be empty")
        destination, identity = self.peers[faction_id]
        message = Negotiation(sequence_number=sequence_number, timestamp=timestamp, match_id=self.match_id, content=content)
        self.transport.send(destination, identity.encrypt(encode_message(message)))
        self.history.setdefault(faction_id, []).append(Proposal(faction_id, sequence_number, content))

    def receive_proposals(self) -> list[Proposal]:
        received: list[Proposal] = []
        destinations = {destination: faction for faction, (destination, _) in self.peers.items()}
        for inbound in self.transport.receive():
            faction = destinations.get(inbound.sender)
            if faction is None:
                continue
            try:
                message = decode_message(self.identity.decrypt(inbound.data))
            except Exception:  # malformed/ciphertext from an untrusted peer
                continue
            if isinstance(message, Negotiation) and message.match_id == self.match_id:
                proposal = Proposal(faction, message.sequence_number, message.content)
                self.history.setdefault(faction, []).append(proposal)
                received.append(proposal)
                self.transport.send(inbound.sender, self.peers[faction][1].encrypt(encode_message(NegotiationAck(sequence_number=message.sequence_number, timestamp=message.timestamp, match_id=self.match_id, acked_sequence_number=message.sequence_number))))
        return received

    def conversation(self, faction_id: str) -> tuple[Proposal, ...]:
        return tuple(self.history.get(faction_id, ()))


@dataclass
class LXMFNegotiationClient:
    """Direct, end-to-end LXMF negotiation transport.

    Each peer entry maps a faction to its match-scoped public identity. LXMF
    encrypts delivery to that identity; the coordinator is not involved.
    ``storage_path`` should be private and persistent to retain LXMF's
    store-and-forward state across reconnects.
    """

    match_id: str
    identity: Identity
    peers: dict[str, PublicIdentity]
    storage_path: Path
    history: dict[str, list[Proposal]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.storage_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.router = LXMF.LXMRouter(identity=self.identity.rns_identity, storagepath=str(self.storage_path))
        self.delivery_destination = self.router.register_delivery_identity(self.identity.rns_identity)
        if self.delivery_destination is None:
            raise RuntimeError("could not register LXMF delivery identity")
        self.router.register_delivery_callback(self._receive_lxmf)
        self._peer_by_hash = {
            RNS.Destination(peer.rns_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, LXMF.APP_NAME, "delivery").hash: faction
            for faction, peer in self.peers.items()
        }

    def announce(self) -> None:
        """Announce the local LXMF delivery destination for direct routing."""
        self.delivery_destination.announce()

    def send_proposal(self, faction_id: str, content: str, sequence_number: int, timestamp: float) -> LXMF.LXMessage:
        if not content:
            raise ValueError("negotiation content must not be empty")
        peer = self.peers[faction_id]
        destination = RNS.Destination(peer.rns_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, LXMF.APP_NAME, "delivery")
        envelope = Negotiation(sequence_number=sequence_number, timestamp=timestamp, match_id=self.match_id, content=content)
        message = LXMF.LXMessage(destination, self.delivery_destination, content=encode_message(envelope))
        self.router.handle_outbound(message)
        self.history.setdefault(faction_id, []).append(Proposal(faction_id, sequence_number, content))
        return message

    def _receive_lxmf(self, message: LXMF.LXMessage) -> None:
        faction = self._peer_by_hash.get(message.source_hash)
        if faction is None:
            return
        try:
            envelope = decode_message(bytes(message.content))
        except Exception:
            return
        if not isinstance(envelope, Negotiation) or envelope.match_id != self.match_id:
            return
        self.history.setdefault(faction, []).append(Proposal(faction, envelope.sequence_number, envelope.content))
        peer = self.peers[faction]
        destination = RNS.Destination(peer.rns_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, LXMF.APP_NAME, "delivery")
        ack = NegotiationAck(sequence_number=envelope.sequence_number, timestamp=envelope.timestamp, match_id=self.match_id, acked_sequence_number=envelope.sequence_number)
        self.router.handle_outbound(LXMF.LXMessage(destination, self.delivery_destination, content=encode_message(ack)))
