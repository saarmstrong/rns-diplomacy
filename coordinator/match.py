"""MatchCoordinator — the per-match orchestrator.

Ties together the state machine (coordinator/phases.py), persistence
(coordinator/persistence.py), join flow (coordinator/lobby.py), order
handling (coordinator/orders.py), draw handling (coordinator/draws.py),
the deterministic adjudication engine, and the abstract Transport, to
drive one match end to end: accept joins, collect movement orders,
adjudicate, sign and hash-chain results, and broadcast them.

Every persisted phase result is keyed by (turn, phase) and represents
the game state *as of entering* that phase. The (turn, ORDERS) row for
the current turn is therefore always "the state before this turn's
moves" — the anchor ``advance_phase`` re-derives movement/retreat from,
so recovery after a restart never depends on anything held only in
memory.

TODO: retreat and adjustment orders are not yet collected from players
over the wire — ``engine.orders.RetreatOrder``/``BuildOrder``/
``DisbandOrder`` have no ``order_type`` discriminator, so they can't
travel through ORDER_SUBMIT's ``Order`` union yet. Both phases currently
always resolve with the engine's deterministic defaults (forced disband
on retreat, civil-disorder disband/no-build on adjustment).
"""

from __future__ import annotations

from dataclasses import dataclass

from coordinator import draws, lobby
from coordinator import orders as order_handling
from coordinator import phases
from coordinator.draws import DrawRule
from coordinator.persistence import MatchStore
from coordinator.phases import InvalidTransitionError, MatchState
from engine import adjudicator
from engine.hashing import canonical_bytes, game_state_from_canonical_bytes, hash_game_state_hex
from engine.map import create_default_map
from engine.model import GameState, Phase
from protocol.constants import (
    DEADLINE_WARNING_LEAD_SECONDS,
    DEFAULT_ADJUSTMENT_PHASE_SECONDS,
    DEFAULT_MOVEMENT_PHASE_SECONDS,
    DEFAULT_RETREAT_PHASE_SECONDS,
    MAX_PLAYERS,
)
from protocol.encoding import decode_message, encode_message
from protocol.errors import DecodingError, ValidationError
from protocol.messages import (
    AnyMessage,
    DiscoverGame,
    DrawPropose,
    DrawResult,
    DrawVote,
    ErrorMessage,
    GameInfo,
    JoinRequest,
    MatchEnd,
    MatchStatus,
    OrderCancel,
    OrderSubmit,
    OrderUpdate,
    PhaseDeadlineWarning,
    PhaseResult,
    PhaseStart,
    StateRequest,
    StateResponse,
)
from protocol.validation import validate_message
from shared import time as shared_time
from shared.identity import Identity
from shared.logging import protocol_logger
from shared.time import Deadline
from shared.transport import InboundMessage, Transport


@dataclass(frozen=True)
class MatchConfig:
    """Per-match configuration: phase durations and draw rule."""

    movement_phase_seconds: float = DEFAULT_MOVEMENT_PHASE_SECONDS
    retreat_phase_seconds: float = DEFAULT_RETREAT_PHASE_SECONDS
    adjustment_phase_seconds: float = DEFAULT_ADJUSTMENT_PHASE_SECONDS
    draw_rule: DrawRule = DrawRule.UNANIMOUS


class MatchCoordinator:
    """Drives one match's lifecycle: join flow, orders, adjudication, draws."""

    def __init__(
        self, match_id: str, store: MatchStore, transport: Transport, *, config: MatchConfig | None = None
    ) -> None:
        self.match_id = match_id
        self.store = store
        self.transport = transport
        self.config = config or MatchConfig()
        self._sequence_number = 0
        self._warned_this_phase = False
        self._logger = protocol_logger()
        # Highest accepted sequence per sending identity. This state is only
        # an in-process fast-path; durable order revisions remain the final
        # replay guard across coordinator restarts.
        self._last_sequence_by_sender: dict[bytes, int] = {}
        self._join_attempts: dict[bytes, list[float]] = {}
        self._handlers = {
            DiscoverGame: self._on_discover_game,
            JoinRequest: self._on_join_request,
            OrderSubmit: self._on_order_submit,
            OrderUpdate: self._on_order_submit,
            OrderCancel: self._on_order_cancel,
            StateRequest: self._on_state_request,
            DrawPropose: self._on_draw_propose,
            DrawVote: self._on_draw_vote,
        }

    @classmethod
    def create(
        cls,
        match_id: str,
        store: MatchStore,
        transport: Transport,
        *,
        config: MatchConfig | None = None,
        now: float | None = None,
    ) -> MatchCoordinator:
        """Register a brand-new match (fresh coordinator identity) and return its coordinator."""
        store.create_match(match_id, Identity.generate(), created_at=now if now is not None else shared_time.now())
        coordinator = cls(match_id, store, transport, config=config)
        coordinator.announce_game()
        return coordinator

    def announce_game(self) -> None:
        """Publish a discoverable lobby/active-match summary on the transport."""
        record = self._require_match_state()
        self.transport.announce(
            encode_message(
                GameInfo(
                    sequence_number=self._next_sequence(), timestamp=self._now(), match_id=self.match_id,
                    status=record.status, player_count=len(self.store.list_players(self.match_id)),
                    max_players=MAX_PLAYERS, phase=record.phase if record.status is MatchStatus.ACTIVE else None,
                )
            )
        )

    # --- Inbound message handling ---------------------------------------

    def poll(self) -> None:
        """Process every currently-queued inbound message, then check the deadline."""
        for inbound in self.transport.receive():
            self.handle_inbound(inbound)
        if self._is_active():
            self.check_deadline()

    def handle_inbound(self, inbound: InboundMessage) -> None:
        try:
            public_key = bytes.fromhex(inbound.sender)
            message = decode_message(inbound.data)
            validate_message(message)
        except (ValueError, DecodingError, ValidationError) as exc:
            # A malformed sender cannot be replied to safely.
            try:
                public_key = bytes.fromhex(inbound.sender)
            except ValueError:
                self._logger.warning("discarded malformed message from invalid sender %r", inbound.sender)
                return
            self._logger.warning("rejected malformed message from %s: %s", inbound.sender, exc)
            self._reply(
                public_key,
                ErrorMessage(
                    sequence_number=self._next_sequence(),
                    timestamp=self._now(),
                    code="MALFORMED_MESSAGE",
                    description=str(exc),
                ),
            )
            return

        last_sequence = self._last_sequence_by_sender.get(public_key, -1)
        if message.sequence_number <= last_sequence:
            self._logger.warning("rejected replay from %s at sequence %d", inbound.sender, message.sequence_number)
            self._reply(
                public_key,
                ErrorMessage(
                    sequence_number=self._next_sequence(),
                    timestamp=self._now(),
                    code="REPLAYED_MESSAGE",
                    description="sequence number was already processed",
                    related_sequence_number=message.sequence_number,
                ),
            )
            return
        self._last_sequence_by_sender[public_key] = message.sequence_number
        handler = self._handlers.get(type(message))
        if handler is not None:
            handler(public_key, message)

    def _on_discover_game(self, public_key: bytes, message: DiscoverGame) -> None:
        record = self.store.get_match(self.match_id)
        if record is None:
            return
        response = GameInfo(
            sequence_number=self._next_sequence(),
            timestamp=self._now(),
            match_id=self.match_id,
            status=record.state.status,
            player_count=len(self.store.list_players(self.match_id)),
            max_players=MAX_PLAYERS,
            phase=record.state.phase if record.state.status is MatchStatus.ACTIVE else None,
        )
        self._reply(public_key, response)

    def _on_join_request(self, public_key: bytes, message: JoinRequest) -> None:
        current_time = self._now()
        if message.player_public_key != public_key:
            self._logger.warning("rejected join with mismatched identity from %s", public_key.hex())
            self._reply(
                public_key,
                ErrorMessage(
                    sequence_number=self._next_sequence(), timestamp=current_time,
                    code="IDENTITY_MISMATCH", description="JOIN_REQUEST key must match the sending destination",
                ),
            )
            return
        attempts = [at for at in self._join_attempts.get(public_key, []) if current_time - at < 60.0]
        if len(attempts) >= 5:
            self._logger.warning("rate-limited join requests from %s", public_key.hex())
            self._join_attempts[public_key] = attempts
            self._reply(
                public_key,
                ErrorMessage(
                    sequence_number=self._next_sequence(), timestamp=current_time,
                    code="JOIN_RATE_LIMITED", description="too many join requests; retry in one minute",
                ),
            )
            return
        attempts.append(current_time)
        self._join_attempts[public_key] = attempts
        response = lobby.handle_join_request(
            self.store, self.match_id, message, sequence_number=self._next_sequence(), timestamp=self._now()
        )
        self._reply(public_key, response)

    def _on_order_submit(self, public_key: bytes, message: OrderSubmit | OrderUpdate) -> None:
        match_state = self._require_match_state()
        game_state = self._load_game_state()
        receipt = order_handling.handle_order_submit(
            self.store,
            game_state,
            self.match_id,
            public_key,
            message,
            current_turn=match_state.turn,
            current_phase=match_state.phase,
            sequence_number=self._next_sequence(),
            timestamp=self._now(),
        )
        self._reply(public_key, receipt)

    def _on_order_cancel(self, public_key: bytes, message: OrderCancel) -> None:
        match_state = self._require_match_state()
        receipt = order_handling.handle_order_cancel(
            self.store,
            self.match_id,
            public_key,
            message,
            current_turn=match_state.turn,
            current_phase=match_state.phase,
            sequence_number=self._next_sequence(),
            timestamp=self._now(),
        )
        self._reply(public_key, receipt)

    def _on_state_request(self, public_key: bytes, message: StateRequest) -> None:
        if message.turn is None:
            record = self.store.get_latest_phase_result(self.match_id)
        else:
            matches = [r for r in self.store.get_hash_chain(self.match_id) if r.turn == message.turn]
            record = matches[-1] if matches else None

        if record is None:
            self._reply(
                public_key,
                ErrorMessage(
                    sequence_number=self._next_sequence(),
                    timestamp=self._now(),
                    code="NO_SUCH_STATE",
                    description=f"no state recorded for turn {message.turn}",
                ),
            )
            return

        self._reply(
            public_key,
            StateResponse(
                sequence_number=self._next_sequence(),
                timestamp=self._now(),
                match_id=self.match_id,
                turn=record.turn,
                canonical_state=record.canonical_state,
                state_hash=record.state_hash,
                previous_state_hash=record.previous_state_hash,
                signature=record.signature,
            ),
        )

    def _on_draw_propose(self, public_key: bytes, message: DrawPropose) -> None:
        result = draws.handle_draw_propose(
            self.store,
            self.match_id,
            public_key,
            message,
            sequence_number=self._next_sequence(),
            timestamp=self._now(),
            rule=self.config.draw_rule,
        )
        self._finalize_draw(result)

    def _on_draw_vote(self, public_key: bytes, message: DrawVote) -> None:
        result = draws.handle_draw_vote(
            self.store,
            self.match_id,
            public_key,
            message,
            sequence_number=self._next_sequence(),
            timestamp=self._now(),
            rule=self.config.draw_rule,
        )
        self._finalize_draw(result)

    def _finalize_draw(self, result: DrawResult) -> None:
        self._broadcast_to_all(result)
        if not result.accepted:
            return
        match_state = self._require_match_state()
        draws.apply_result(self.store, self.match_id, match_state, result)
        latest = self.store.get_latest_phase_result(self.match_id)
        self._broadcast_to_all(
            MatchEnd(
                sequence_number=self._next_sequence(),
                timestamp=self._now(),
                match_id=self.match_id,
                reason="draw accepted",
                winner=None,
                final_state_hash=latest.state_hash if latest else None,
            )
        )

    # --- Match lifecycle ---------------------------------------------------

    def start_match(self) -> None:
        """Operator action: start the match once full. Broadcasts MATCH_START then opens turn 1's orders."""
        identity = self.store.load_coordinator_identity(self.match_id)
        current_time = self._now()
        seed_deadline = Deadline.after(self.config.movement_phase_seconds, start=current_time)
        match_start = lobby.start_match(
            self.store,
            self.match_id,
            identity,
            sequence_number=self._next_sequence(),
            timestamp=current_time,
            deadline=seed_deadline.expires_at,
        )
        self._broadcast_to_all(match_start)

        diplomacy_state = self._require_match_state()
        orders_state = phases.advance_phase(diplomacy_state)  # DIPLOMACY -> ORDERS, unconditional
        self.store.save_match_state(self.match_id, orders_state)

        game_state = create_default_map()
        canonical, state_hash, previous_hash, signature = self._persist_phase_result(
            orders_state.turn, orders_state.phase, game_state, identity, current_time
        )
        deadline = Deadline.after(self.config.movement_phase_seconds, start=current_time)
        self.store.set_deadline(self.match_id, deadline.expires_at)
        self._warned_this_phase = False
        self._broadcast_to_all(
            PhaseStart(
                sequence_number=self._next_sequence(),
                timestamp=current_time,
                match_id=self.match_id,
                phase=orders_state.phase,
                turn=orders_state.turn,
                year=orders_state.year,
                deadline=deadline.expires_at,
            )
        )

    def pause(self, *, now: float | None = None) -> None:
        """Pause an active deadline while preserving its remaining duration."""
        expires_at = self.store.get_deadline(self.match_id)
        if expires_at is None:
            raise InvalidTransitionError("match is not running a deadline")
        current_time = now if now is not None else self._now()
        self.store.pause_deadline(self.match_id, remaining_seconds=max(0.0, expires_at - current_time))

    def resume(self, *, now: float | None = None) -> None:
        """Resume a paused deadline from its persisted remaining duration."""
        remaining = self.store.get_paused_remaining(self.match_id)
        if remaining is None:
            raise InvalidTransitionError("match is not paused")
        current_time = now if now is not None else self._now()
        self.store.resume_deadline(self.match_id, expires_at=current_time + remaining)
        self._warned_this_phase = False

    def check_deadline(self, *, now: float | None = None) -> None:
        """Send a deadline warning if due, or advance the phase once the deadline has passed."""
        current_time = now if now is not None else self._now()
        expires_at = self.store.get_deadline(self.match_id)
        if expires_at is None:
            return
        deadline = Deadline(expires_at=expires_at)
        if deadline.is_expired(current_time):
            self.advance_phase(now=current_time)
        elif deadline.warning_due(DEADLINE_WARNING_LEAD_SECONDS, current_time) and not self._warned_this_phase:
            self._warned_this_phase = True
            match_state = self._require_match_state()
            self._broadcast_to_all(
                PhaseDeadlineWarning(
                    sequence_number=self._next_sequence(),
                    timestamp=current_time,
                    match_id=self.match_id,
                    phase=match_state.phase,
                    seconds_remaining=deadline.remaining(current_time),
                )
            )

    def advance_phase(self, *, now: float | None = None) -> None:
        """Resolve the current phase and move to the next one.

        Callable directly for the coordinator operator's manual dev-mode
        advance (bypassing the deadline), or by ``check_deadline`` once
        the deadline has passed. Missing orders default deterministically
        (Hold for movement, forced disband for retreat/adjustment — see
        the module docstring).
        """
        current_time = now if now is not None else self._now()
        match_state = self._require_match_state()
        identity = self.store.load_coordinator_identity(self.match_id)

        has_dislodgements = False
        adjustment_needed = False

        if match_state.phase is Phase.ORDERS:
            pre_movement_state = self._turn_start_state(match_state.turn)
            effective_orders = order_handling.get_effective_orders(self.store, self.match_id, match_state.turn)
            movement_result = adjudicator.resolve_movement(pre_movement_state, effective_orders)
            game_state = adjudicator.apply_movement_result(pre_movement_state, movement_result)
            game_state = adjudicator.update_supply_center_ownership(game_state)
            has_dislodgements = bool(movement_result.dislodged())
            if not has_dislodgements:
                adjustment_needed = self._adjustment_needed(game_state)
            landing_state = phases.advance_phase(match_state)
            landing_state = phases.advance_phase(
                landing_state, has_dislodgements=has_dislodgements, adjustment_needed=adjustment_needed
            )
        elif match_state.phase is Phase.RETREAT:
            pre_movement_state = self._turn_start_state(match_state.turn)
            effective_orders = order_handling.get_effective_orders(self.store, self.match_id, match_state.turn)
            movement_result = adjudicator.resolve_movement(pre_movement_state, effective_orders)
            post_movement = adjudicator.apply_movement_result(pre_movement_state, movement_result)
            retreat_result = adjudicator.resolve_retreats(post_movement, movement_result, {})
            game_state = adjudicator.apply_retreat_result(post_movement, retreat_result)
            game_state = adjudicator.update_supply_center_ownership(game_state)
            adjustment_needed = self._adjustment_needed(game_state)
            landing_state = phases.advance_phase(match_state, adjustment_needed=adjustment_needed)
        elif match_state.phase is Phase.BUILD:
            game_state = self._load_game_state()  # already fully resolved through movement/retreat
            adjustment_result = adjudicator.resolve_adjustments(game_state, [], [])
            game_state = adjudicator.apply_adjustment_result(game_state, adjustment_result)
            landing_state = phases.advance_phase(match_state)
        else:
            raise InvalidTransitionError(f"cannot resolve phase {match_state.phase}")

        if landing_state.phase is Phase.DIPLOMACY:
            landing_state = phases.advance_phase(landing_state)  # transparently -> next turn's ORDERS

        self.store.save_match_state(self.match_id, landing_state)
        canonical, state_hash, previous_hash, signature = self._persist_phase_result(
            landing_state.turn, landing_state.phase, game_state, identity, current_time
        )
        deadline = Deadline.after(self._phase_duration(landing_state.phase), start=current_time)
        self.store.set_deadline(self.match_id, deadline.expires_at)
        self._warned_this_phase = False

        self._broadcast_to_all(
            PhaseResult(
                sequence_number=self._next_sequence(),
                timestamp=current_time,
                match_id=self.match_id,
                phase=landing_state.phase,
                turn=landing_state.turn,
                year=landing_state.year,
                canonical_state=canonical,
                state_hash=state_hash,
                previous_state_hash=previous_hash,
                signature=signature,
            )
        )
        self._broadcast_to_all(
            PhaseStart(
                sequence_number=self._next_sequence(),
                timestamp=current_time,
                match_id=self.match_id,
                phase=landing_state.phase,
                turn=landing_state.turn,
                year=landing_state.year,
                deadline=deadline.expires_at,
            )
        )

    # --- Helpers -------------------------------------------------------

    def _adjustment_needed(self, game_state: GameState) -> bool:
        return any(delta != 0 for delta in adjudicator.compute_adjustment_deltas(game_state).values())

    def _phase_duration(self, phase: Phase) -> float:
        return {
            Phase.ORDERS: self.config.movement_phase_seconds,
            Phase.RETREAT: self.config.retreat_phase_seconds,
            Phase.BUILD: self.config.adjustment_phase_seconds,
        }[phase]

    def _turn_start_state(self, turn: int) -> GameState:
        record = self.store.get_phase_result(self.match_id, turn, Phase.ORDERS)
        if record is None:
            raise RuntimeError(f"missing turn-start state for match {self.match_id!r} turn {turn}")
        return game_state_from_canonical_bytes(record.canonical_state)

    def _load_game_state(self) -> GameState:
        match_state = self._require_match_state()
        record = self.store.get_phase_result(self.match_id, match_state.turn, match_state.phase)
        if record is None:
            record = self.store.get_latest_phase_result(self.match_id)
        if record is None:
            raise RuntimeError(f"no game state recorded yet for match {self.match_id!r}")
        return game_state_from_canonical_bytes(record.canonical_state)

    def _persist_phase_result(
        self, turn: int, phase: Phase, game_state: GameState, identity: Identity, resolved_at: float
    ) -> tuple[bytes, str, str | None, bytes]:
        previous = self.store.get_latest_phase_result(self.match_id)
        previous_hash_bytes = bytes.fromhex(previous.state_hash) if previous else None
        canonical = canonical_bytes(game_state)
        state_hash = hash_game_state_hex(game_state, previous_hash=previous_hash_bytes)
        signature = identity.sign(state_hash.encode("utf-8"))
        previous_hash = previous.state_hash if previous else None
        self.store.record_phase_result(
            self.match_id, turn, phase, canonical, state_hash, previous_hash, signature, resolved_at=resolved_at
        )
        return canonical, state_hash, previous_hash, signature

    def _require_match_state(self) -> MatchState:
        record = self.store.get_match(self.match_id)
        if record is None:
            raise KeyError(f"no such match: {self.match_id!r}")
        return record.state

    def _is_active(self) -> bool:
        record = self.store.get_match(self.match_id)
        return record is not None and record.state.status is MatchStatus.ACTIVE

    def _now(self) -> float:
        return shared_time.now()

    def _next_sequence(self) -> int:
        self._sequence_number += 1
        return self._sequence_number

    def _reply(self, public_key: bytes, message: AnyMessage) -> None:
        self.transport.send(public_key.hex(), encode_message(message))

    def _broadcast_to_all(self, message: AnyMessage) -> None:
        encoded = encode_message(message)
        for player in self.store.list_players(self.match_id):
            self.transport.send(player.public_key.hex(), encoded)
