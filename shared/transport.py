"""Transport abstraction for coordinator-client communication.

Defines the ``Transport`` interface used by both the coordinator and the
client so match logic stays transport-agnostic. Implementations move
opaque, already-encoded protocol message bytes between destinations; they
never interpret payloads.

``InMemoryTransport`` is an in-process mock used by unit and integration
tests. The real ``ReticulumTransport`` (TCP/IP first, per the project's
transport priority) is implemented separately on top of RNS destinations
and LXMF delivery.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class InboundMessage:
    """A message received from another destination."""

    sender: str
    data: bytes


@dataclass(frozen=True)
class Announcement:
    """A destination announcing its presence/availability (e.g. a match)."""

    destination: str
    data: bytes


class Transport(ABC):
    """Abstract transport bound to a single local destination.

    Implementations move opaque bytes between destinations and surface
    discovery announcements. They do not interpret payloads — encoding,
    validation, and dispatch happen above this layer.
    """

    @property
    @abstractmethod
    def local_destination(self) -> str:
        """The destination identifier this transport sends and receives as."""

    @abstractmethod
    def send(self, destination: str, data: bytes) -> None:
        """Send ``data`` to ``destination``."""

    @abstractmethod
    def receive(self) -> list[InboundMessage]:
        """Return and clear all messages queued for this transport since the last call."""

    @abstractmethod
    def announce(self, data: bytes = b"") -> None:
        """Announce this transport's local destination (e.g. a match becoming discoverable)."""

    @abstractmethod
    def discover(self) -> list[Announcement]:
        """Return and clear all announcements observed since the last call."""


class InMemoryNetwork:
    """Shared in-process message bus connecting a group of InMemoryTransport instances.

    Transports constructed with the same ``InMemoryNetwork`` can reach each
    other; transports on different networks are isolated, which is useful
    for testing multiple concurrent matches without cross-talk.
    """

    def __init__(self) -> None:
        self._inboxes: dict[str, list[InboundMessage]] = {}
        self._announcements: list[Announcement] = []

    def register(self, destination: str) -> None:
        self._inboxes.setdefault(destination, [])

    def deliver(self, destination: str, message: InboundMessage) -> None:
        self._inboxes.setdefault(destination, []).append(message)

    def drain_inbox(self, destination: str) -> list[InboundMessage]:
        messages = self._inboxes.get(destination, [])
        self._inboxes[destination] = []
        return messages

    def record_announcement(self, announcement: Announcement) -> None:
        self._announcements.append(announcement)

    def announcements_since(self, cursor: int) -> tuple[list[Announcement], int]:
        """Return announcements recorded after ``cursor``, and the new cursor position."""
        return self._announcements[cursor:], len(self._announcements)


class InMemoryTransport(Transport):
    """In-process ``Transport`` for unit and integration tests. No network I/O."""

    def __init__(self, destination: str, network: InMemoryNetwork | None = None) -> None:
        self._destination = destination
        self._network = network if network is not None else InMemoryNetwork()
        self._network.register(destination)
        self._announcement_cursor = 0

    @property
    def local_destination(self) -> str:
        return self._destination

    @property
    def network(self) -> InMemoryNetwork:
        """The shared network this transport is attached to (pass it to others to connect them)."""
        return self._network

    def send(self, destination: str, data: bytes) -> None:
        self._network.deliver(destination, InboundMessage(sender=self._destination, data=data))

    def receive(self) -> list[InboundMessage]:
        return self._network.drain_inbox(self._destination)

    def announce(self, data: bytes = b"") -> None:
        self._network.record_announcement(Announcement(destination=self._destination, data=data))

    def discover(self) -> list[Announcement]:
        new, cursor = self._network.announcements_since(self._announcement_cursor)
        self._announcement_cursor = cursor
        return new
