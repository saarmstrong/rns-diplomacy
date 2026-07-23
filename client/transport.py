"""Typed client messaging over the common transport abstraction."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from protocol.encoding import decode_message, encode_message
from protocol.messages import AnyMessage, Message
from shared.transport import Announcement, Transport

MessageHandler = Callable[[AnyMessage], None]


class ClientTransport:
    """Encodes, sends, receives, and dispatches typed coordinator messages."""

    def __init__(self, transport: Transport, coordinator_destination: str | None = None) -> None:
        self.transport = transport
        self.coordinator_destination = coordinator_destination
        self._handlers: dict[type[Message], list[MessageHandler]] = defaultdict(list)

    def connect(self, coordinator_address: str) -> None:
        self.coordinator_destination = coordinator_address

    def send_message(self, message: Message, destination: str | None = None) -> None:
        target = destination or self.coordinator_destination
        if target is None:
            raise RuntimeError("no coordinator destination configured")
        self.transport.send(target, encode_message(message))

    def register_handler(self, message_type: type[Message], callback: MessageHandler) -> None:
        self._handlers[message_type].append(callback)

    def poll(self) -> list[AnyMessage]:
        messages: list[AnyMessage] = []
        for inbound in self.transport.receive():
            try:
                message = decode_message(inbound.data)
            except Exception:
                continue
            messages.append(message)
            for callback in self._handlers[type(message)]:
                callback(message)
        return messages

    def discover(self) -> list[Announcement]:
        return self.transport.discover()
