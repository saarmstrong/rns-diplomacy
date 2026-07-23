"""Real Reticulum packet transport (TCP/IP interface configured by RNS).

RNS interface configuration belongs to the host's ``~/.reticulum/config``;
TCPClientInterface/TCPServerInterface can therefore be enabled without
changing game code. Destinations are standard RNS single destinations with
aspects ``rns_diplomacy.game`` or ``rns_diplomacy.player``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import RNS

from protocol.constants import MAX_MESSAGE_SIZE
from shared.identity import Identity
from shared.transport import Announcement, InboundMessage, Transport


class _AnnounceHandler:
    def __init__(self, aspect: str, callback: Callable[[bytes, bytes], None]) -> None:
        self.aspect_filter = aspect
        self._callback = callback

    def received_announce(self, destination_hash: bytes, announced_identity: RNS.Identity, app_data: bytes) -> None:
        self._callback(destination_hash, app_data)


class ReticulumTransport(Transport):
    """Transport backed by RNS packets and announce discovery.

    The remote destination must have announced before ``send`` can resolve
    its identity. Call ``RNS.Transport.request_path`` externally when using
    a remote TCP node whose announce is not already known.
    """

    def __init__(self, identity: Identity, aspect: str) -> None:
        if aspect not in {"game", "player"}:
            raise ValueError("aspect must be 'game' or 'player'")
        self._identity = identity
        self._aspect = aspect
        self._destination = RNS.Destination(
            identity._rns_identity, RNS.Destination.IN, RNS.Destination.SINGLE, "rns_diplomacy", aspect
        )
        self._destination.set_packet_callback(self._on_packet)
        self._lock = threading.Lock()
        self._inbox: list[InboundMessage] = []
        self._announcements: list[Announcement] = []
        self._cursor = 0
        self._announce_handler = _AnnounceHandler(f"rns_diplomacy.{aspect}", self._on_announce)
        RNS.Transport.register_announce_handler(self._announce_handler)

    @property
    def local_destination(self) -> str:
        return self._destination.hash.hex()

    def _on_packet(self, data: bytes, packet: RNS.Packet) -> None:
        if len(data) > MAX_MESSAGE_SIZE:
            return
        with self._lock:
            self._inbox.append(InboundMessage(packet.destination.hash.hex(), bytes(data)))

    def _on_announce(self, destination_hash: bytes, data: bytes) -> None:
        if len(data) > MAX_MESSAGE_SIZE:
            return
        with self._lock:
            self._announcements.append(Announcement(destination_hash.hex(), bytes(data)))

    def send(self, destination: str, data: bytes) -> None:
        if len(data) > MAX_MESSAGE_SIZE:
            raise ValueError("message exceeds transport size limit")
        try:
            destination_hash = bytes.fromhex(destination)
        except ValueError as exc:
            raise ValueError("Reticulum destination must be a hex hash") from exc
        remote_identity = RNS.Identity.recall(destination_hash)
        if remote_identity is None:
            raise RuntimeError("destination identity is unknown; await an announce or request a path")
        remote = RNS.Destination(remote_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "rns_diplomacy", self._aspect)
        RNS.Packet(remote, data).send()

    def receive(self) -> list[InboundMessage]:
        with self._lock:
            messages, self._inbox = self._inbox, []
        return messages

    def announce(self, data: bytes = b"") -> None:
        if len(data) > MAX_MESSAGE_SIZE:
            raise ValueError("announcement exceeds transport size limit")
        self._destination.announce(app_data=data)

    def discover(self) -> list[Announcement]:
        with self._lock:
            announcements = self._announcements[self._cursor :]
            self._cursor = len(self._announcements)
        return announcements
