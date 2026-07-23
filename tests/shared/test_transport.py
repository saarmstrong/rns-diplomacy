"""Tests for the Transport abstraction and its InMemoryTransport implementation."""

from __future__ import annotations

import pytest

from shared.transport import Announcement, InboundMessage, InMemoryNetwork, InMemoryTransport, Transport


def test_transport_is_abstract() -> None:
    with pytest.raises(TypeError):
        Transport()  # type: ignore[abstract]


def test_local_destination_matches_constructor_argument() -> None:
    transport = InMemoryTransport("coordinator")
    assert transport.local_destination == "coordinator"


def test_send_and_receive_delivers_message() -> None:
    network = InMemoryNetwork()
    coordinator = InMemoryTransport("coordinator", network)
    client = InMemoryTransport("client-1", network)

    client.send("coordinator", b"join-request")

    assert coordinator.receive() == [InboundMessage(sender="client-1", data=b"join-request")]


def test_receive_drains_the_inbox() -> None:
    network = InMemoryNetwork()
    coordinator = InMemoryTransport("coordinator", network)
    client = InMemoryTransport("client-1", network)

    client.send("coordinator", b"join-request")
    coordinator.receive()

    assert coordinator.receive() == []


def test_receive_preserves_delivery_order() -> None:
    network = InMemoryNetwork()
    coordinator = InMemoryTransport("coordinator", network)
    client = InMemoryTransport("client-1", network)

    client.send("coordinator", b"first")
    client.send("coordinator", b"second")

    messages = coordinator.receive()
    assert [m.data for m in messages] == [b"first", b"second"]


def test_messages_are_isolated_per_destination() -> None:
    network = InMemoryNetwork()
    coordinator = InMemoryTransport("coordinator", network)
    client_a = InMemoryTransport("client-a", network)
    client_b = InMemoryTransport("client-b", network)

    client_a.send("coordinator", b"from-a")

    assert coordinator.receive() == [InboundMessage(sender="client-a", data=b"from-a")]
    assert client_b.receive() == []


def test_send_to_unregistered_destination_does_not_raise() -> None:
    network = InMemoryNetwork()
    client = InMemoryTransport("client-1", network)

    client.send("no-such-destination", b"data")  # should not raise


def test_transports_on_different_networks_are_isolated() -> None:
    coordinator = InMemoryTransport("coordinator", InMemoryNetwork())
    client = InMemoryTransport("client-1", InMemoryNetwork())

    client.send("coordinator", b"join-request")

    assert coordinator.receive() == []


def test_announce_and_discover() -> None:
    network = InMemoryNetwork()
    coordinator = InMemoryTransport("coordinator", network)
    client = InMemoryTransport("client-1", network)

    coordinator.announce(b"match-info")

    assert client.discover() == [Announcement(destination="coordinator", data=b"match-info")]


def test_discover_drains_only_new_announcements_per_transport() -> None:
    network = InMemoryNetwork()
    coordinator = InMemoryTransport("coordinator", network)
    client_a = InMemoryTransport("client-a", network)
    client_b = InMemoryTransport("client-b", network)

    coordinator.announce(b"match-info-1")
    assert client_a.discover() == [Announcement(destination="coordinator", data=b"match-info-1")]
    assert client_a.discover() == []

    coordinator.announce(b"match-info-2")
    assert client_a.discover() == [Announcement(destination="coordinator", data=b"match-info-2")]
    # client_b never called discover() before, so it should see both announcements.
    assert client_b.discover() == [
        Announcement(destination="coordinator", data=b"match-info-1"),
        Announcement(destination="coordinator", data=b"match-info-2"),
    ]


def test_default_network_is_created_when_omitted() -> None:
    solo = InMemoryTransport("solo")
    assert isinstance(solo.network, InMemoryNetwork)
