"""Append-only event log for game history and auditability.

Records every significant game event (orders submitted, phases resolved,
messages sent) as an immutable log that can be replayed or audited.
"""

# TODO: Implement event log
#   - append_event(game_id, event)
#   - get_events(game_id, since_turn=None) -> list[Event]
#   - Event types: OrderSubmitted, PhaseResolved, PlayerJoined, etc.
