"""Tests for client/negotiations.py and PlayerClient's negotiation handling.

Negotiation is direct player-to-player messaging that never touches the
coordinator — these tests exercise two PlayerClients talking straight to
each other over the shared InMemoryNetwork, with no MatchCoordinator
involved at all.
"""

from __future__ import annotations

from client.identity import create_identity
from client.negotiations import group_by_sender
from client.transport import PlayerClient
from protocol.messages import Negotiation, NegotiationAck
from shared.transport import InMemoryNetwork, InMemoryTransport


def _make_client(network: InMemoryNetwork, match_id: str = "match-1") -> PlayerClient:
    identity = create_identity()
    transport = InMemoryTransport(identity.public_bytes.hex(), network)
    return PlayerClient(identity, transport, "coordinator", match_id)


def test_negotiation_never_touches_a_coordinator_destination():
    network = InMemoryNetwork()
    coordinator_transport = InMemoryTransport("coordinator", network)
    alice = _make_client(network)
    bob = _make_client(network)

    alice.send_negotiation(bob.transport.local_destination, "Shall we discuss the northern border?")

    # Nothing was ever sent to "coordinator".
    assert coordinator_transport.receive() == []


def test_recipient_receives_and_decodes_negotiation():
    network = InMemoryNetwork()
    alice = _make_client(network)
    bob = _make_client(network)

    alice.send_negotiation(bob.transport.local_destination, "Truce?")
    processed = bob.poll()

    assert len(processed) == 1
    assert isinstance(processed[0], Negotiation)
    assert processed[0].content == "Truce?"
    assert len(bob.negotiations) == 1
    assert bob.negotiations[0].sender_destination == alice.transport.local_destination


def test_ack_negotiation_round_trip():
    network = InMemoryNetwork()
    alice = _make_client(network)
    bob = _make_client(network)

    sent = alice.send_negotiation(bob.transport.local_destination, "Truce?")
    bob.poll()

    bob.ack_negotiation(alice.transport.local_destination, sent.sequence_number)
    processed = alice.poll()

    assert len(processed) == 1
    assert isinstance(processed[0], NegotiationAck)
    assert alice.negotiation_acks[0].acked_sequence_number == sent.sequence_number


def test_group_by_sender_threads_conversation_history():
    network = InMemoryNetwork()
    alice = _make_client(network)
    bob = _make_client(network)
    carol = _make_client(network)

    alice.send_negotiation(bob.transport.local_destination, "Hi from Alice (1)")
    carol.send_negotiation(bob.transport.local_destination, "Hi from Carol")
    alice.send_negotiation(bob.transport.local_destination, "Hi from Alice (2)")
    bob.poll()

    threads = group_by_sender(bob.negotiations)
    assert set(threads.keys()) == {alice.transport.local_destination, carol.transport.local_destination}
    alice_thread = threads[alice.transport.local_destination]
    assert [m.content for m in alice_thread] == ["Hi from Alice (1)", "Hi from Alice (2)"]
    assert len(threads[carol.transport.local_destination]) == 1


def test_multiple_players_can_negotiate_independently():
    network = InMemoryNetwork()
    alice = _make_client(network)
    bob = _make_client(network)
    carol = _make_client(network)

    alice.send_negotiation(bob.transport.local_destination, "Alice to Bob")
    alice.send_negotiation(carol.transport.local_destination, "Alice to Carol")

    bob.poll()
    carol.poll()

    assert len(bob.negotiations) == 1
    assert bob.negotiations[0].message.content == "Alice to Bob"
    assert len(carol.negotiations) == 1
    assert carol.negotiations[0].message.content == "Alice to Carol"
