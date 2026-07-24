"""Real Reticulum + LXMF transport — the network-backed ``Transport`` implementation.

Everything in this codebase is built against the abstract ``Transport``
interface (``shared/transport.py``) and tested with ``InMemoryTransport``.
This module is the real thing: it moves the exact same message bytes
over an actual Reticulum network, using LXMF for delivery (chosen over
raw RNS packets because LXMF transparently handles messages larger than
a single packet's MDU via automatic resource transfer — protocol
messages like PHASE_RESULT, carrying a full canonical game state, would
not reliably fit in one raw packet).

Addressing matches ``InMemoryTransport`` exactly: a destination string is
the hex-encoded Curve25519 public key of the identity to reach. LXMF
fixes its own delivery destination namespace (``lxmf.delivery`` + the
identity's hash), so there is no separate "game" vs "player" aspect
namespace to manage — every participant (coordinator or player) is
addressed the same way, and messages self-describe via the protocol
envelope, exactly as they do over InMemoryTransport.

Verified against a real two-process TCP/IP loopback exchange during
development (see ``tests/integration/test_reticulum_transport.py``) —
two identities sharing one process/one Reticulum instance do *not*
reliably path-find to each other (RNS's path resolution treats that as
a degenerate case), so tests spawn genuinely separate processes, the
same way two real machines would talk to each other.
"""

from __future__ import annotations

import queue
import tempfile
from pathlib import Path

import LXMF
import RNS

from shared.identity import Identity
from shared.transport import Announcement, InboundMessage, Transport

_LXMF_APP_NAME = "lxmf"
_LXMF_DELIVERY_ASPECT = "delivery"
_ANNOUNCE_ASPECT_FILTER = f"{_LXMF_APP_NAME}.{_LXMF_DELIVERY_ASPECT}"


def _write_config(configdir: Path, *, listen: tuple[str, int] | None, connect_to: tuple[str, int] | None) -> None:
    interfaces = ""
    if listen is not None:
        host, port = listen
        interfaces += f"""
  [[TCP Server]]
    type = TCPServerInterface
    interface_enabled = True
    listen_ip = {host}
    listen_port = {port}
"""
    if connect_to is not None:
        host, port = connect_to
        interfaces += f"""
  [[TCP Client]]
    type = TCPClientInterface
    interface_enabled = True
    target_host = {host}
    target_port = {port}
"""
    config = f"""
[reticulum]
enable_transport = True
share_instance = False

[logging]
loglevel = 3

[interfaces]
{interfaces}
"""
    configdir.mkdir(parents=True, exist_ok=True)
    (configdir / "config").write_text(config)


def _rns_identity_for_public_key(public_key: bytes) -> RNS.Identity:
    identity = RNS.Identity(create_keys=False)
    identity.load_public_key(public_key)
    return identity


class _AnnounceHandler:
    """Bridges RNS's announce-handler protocol into a thread-safe queue."""

    def __init__(self, announcements: queue.Queue[Announcement]) -> None:
        self.aspect_filter = _ANNOUNCE_ASPECT_FILTER
        self._announcements = announcements

    def received_announce(
        self, destination_hash: bytes, announced_identity: RNS.Identity, app_data, *_args, **_kwargs
    ) -> None:
        # RNS calls this with keyword args (destination_hash=..., announced_identity=...,
        # app_data=..., announce_packet_hash=..., and possibly more across versions) —
        # accept and ignore anything beyond what we need.
        if not app_data:
            return
        self._announcements.put(Announcement(destination=announced_identity.get_public_key().hex(), data=app_data))


class ReticulumTransport(Transport):
    """``Transport`` backed by a real Reticulum network connection over TCP/IP.

    Either ``listen`` (bind a TCP server others connect to — the
    coordinator's usual role) or ``connect_to`` (dial a known
    host:port — a player's usual role) must be given; both may be given
    for a node acting as both.
    """

    def __init__(
        self,
        identity: Identity,
        *,
        listen: tuple[str, int] | None = None,
        connect_to: tuple[str, int] | None = None,
        configdir: str | Path | None = None,
    ) -> None:
        if listen is None and connect_to is None:
            raise ValueError("ReticulumTransport requires listen and/or connect_to")

        self._identity = identity
        self._configdir = Path(configdir) if configdir is not None else Path(tempfile.mkdtemp(prefix="rns-diplomacy-"))
        _write_config(self._configdir, listen=listen, connect_to=connect_to)

        self._reticulum = RNS.Reticulum(configdir=str(self._configdir))
        self._router = LXMF.LXMRouter(identity=identity.rns_identity, storagepath=str(self._configdir / "lxmf"))
        self._source = self._router.register_delivery_identity(identity.rns_identity)
        self._router.register_delivery_callback(self._on_delivery)

        self._inbox: queue.Queue[InboundMessage] = queue.Queue()
        self._announcements: queue.Queue[Announcement] = queue.Queue()
        RNS.Transport.register_announce_handler(_AnnounceHandler(self._announcements))

    @property
    def local_destination(self) -> str:
        return self._identity.public_bytes.hex()

    def send(self, destination: str, data: bytes) -> None:
        recipient = _rns_identity_for_public_key(bytes.fromhex(destination))
        rns_destination = RNS.Destination(
            recipient, RNS.Destination.OUT, RNS.Destination.SINGLE, _LXMF_APP_NAME, _LXMF_DELIVERY_ASPECT
        )
        message = LXMF.LXMessage(rns_destination, self._source, desired_method=LXMF.LXMessage.DIRECT)
        message.set_content_from_bytes(data)
        self._router.handle_outbound(message)

    def receive(self) -> list[InboundMessage]:
        messages: list[InboundMessage] = []
        while True:
            try:
                messages.append(self._inbox.get_nowait())
            except queue.Empty:
                break
        return messages

    def announce(self, data: bytes = b"") -> None:
        self._source.announce(app_data=data)

    def discover(self) -> list[Announcement]:
        announcements: list[Announcement] = []
        while True:
            try:
                announcements.append(self._announcements.get_nowait())
            except queue.Empty:
                break
        return announcements

    def _on_delivery(self, message: LXMF.LXMessage) -> None:
        # On first contact, RNS may not yet have recalled the sender's full identity
        # (it needs to have seen an announce from them first) — get_source() is then
        # None, and only the one-way destination hash is available. Callers that need
        # a reliable public key on a bootstrap message (e.g. JOIN_REQUEST) should read
        # it from the message body instead; the protocol carries it there for this
        # reason. Every other message assumes the sender has announced by then.
        source = message.get_source()
        sender = source.identity.get_public_key().hex() if source is not None else message.source_hash.hex()
        self._inbox.put(InboundMessage(sender=sender, data=message.content))
